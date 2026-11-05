import ast
from pathlib import Path

import pytest
from dagster import RunRequest, SensorResult, SkipReason, build_sensor_context, materialize

from xstore.orchestration import definitions as d
from xstore.orchestration import steps

DAG = Path(__file__).resolve().parents[2] / "orchestration/airflow/dags/xstore_reconcile.py"


@pytest.mark.smoke
def test_assets_materialize_and_sensor_requests_run_on_lag() -> None:
    stores = d.StoresResource(namespace="orchestration-smoke")
    cdc = d.CdcResource(bootstrap_servers="")
    resources = {"stores": stores, "cdc": cdc, "proposals_api": d.ProposalsApiResource()}
    result = materialize(
        [*d.ASSETS, *d.CHECKS],
        resources=resources,
        run_config={"ops": {"ingest": {"config": {"limit": 30}}}},
    )
    assert result.success
    done = {e.asset_key for e in result.get_asset_materialization_events()}
    assert done == {a.key for a in d.ASSETS}
    assert all(e.passed for e in result.get_asset_check_evaluations())
    assert len(result.output_for_node("detect")) > 0
    assert result.output_for_node("propose")["errors"] == 0

    ctx = build_sensor_context(resources={"stores": stores, "cdc": cdc})
    assert isinstance(d.cdc_lag_sensor(ctx), SkipReason)  # bus drained by diff
    rt = stores.runtime()
    rec = next(iter(rt.view.records[("postgres", "customer")].values()))
    rt.stores["postgres"].write("customer", {**rec, "name": "Lagging"}, "smoke-lag")
    assert steps.cdc_events(rt)["lag"] == 1
    out = d.cdc_lag_sensor(ctx)
    assert isinstance(out, SensorResult) and isinstance(out.run_requests[0], RunRequest)
    assert out.cursor
    ctx = build_sensor_context(resources={"stores": stores, "cdc": cdc}, cursor=out.cursor)
    assert isinstance(d.cdc_lag_sensor(ctx), SkipReason)  # same offsets: no second run

    tree = ast.parse(DAG.read_text())
    compile(tree, str(DAG), "exec")
    kwargs = {
        k.arg: k.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "dag"
        for k in n.keywords
    }
    assert ast.literal_eval(kwargs["dag_id"]) == "xstore_reconcile"
    assert ast.literal_eval(kwargs["schedule"]) == "*/15 * * * *"
    assert ast.literal_eval(kwargs["catchup"]) is False
    tasks = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and any(getattr(x, "id", None) == "task" for x in n.decorator_list)
    }
    assert tasks == {"ingest", "cdc_events", "diff", "detect", "matches", "propose"}
