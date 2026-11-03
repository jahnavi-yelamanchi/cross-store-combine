"""DynamoDB adapter over boto3. Local backend: moto (`mock_aws`), no real AWS.

Single table `xstore`, hash key `pk`; native records carry the key prefix
(`CUSTOMER#`, `ORDER#`). Writes are one TransactWriteItems: data put, conditional
idempotency put (`IDEMPOTENCY#<key>`, attribute_not_exists) and a stream log item
(`STREAM#<seq>`). Real mode also enables DynamoDB Streams (NEW_AND_OLD_IMAGES);
`stream_record_to_change_event` translates those records for the CDC adapter.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from xstore.entities import load_mapping
from xstore.stores.base import ChangeEvent, Op, WriteResult, now_ms

TABLE = "xstore"
IDEMPOTENCY = "IDEMPOTENCY#"
STREAM = "STREAM#"
COUNTER = "COUNTER#stream"
STREAM_OPS: dict[str, Op] = {"INSERT": "c", "MODIFY": "u", "REMOVE": "d"}

_ser = TypeSerializer()
_deser = TypeDeserializer()


def _to_item_value(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _to_item_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_to_item_value(x) for x in v]
    return v


def _from_item_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else v
    if isinstance(v, dict):
        return {k: _from_item_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_from_item_value(x) for x in v]
    return v


def _serialize(record: dict[str, Any]) -> dict[str, Any]:
    return {k: _ser.serialize(_to_item_value(v)) for k, v in record.items()}


def _deserialize(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {k: _from_item_value(_deser.deserialize(v)) for k, v in item.items()}


def _split_key(pk: str) -> tuple[str, str]:
    """`ORDER#o1` -> ("order", "o1") using the dynamodb mapping prefixes."""
    for entity_type, m in load_mapping("dynamodb").entities.items():
        if m.key_prefix and pk.startswith(m.key_prefix):
            return entity_type, pk.removeprefix(m.key_prefix)
    raise ValueError(f"no entity mapping for key {pk!r}")


def stream_record_to_change_event(record: dict[str, Any]) -> ChangeEvent:
    """Translate one DynamoDB Streams record (AttributeValue JSON) into a ChangeEvent."""
    ddb = record["dynamodb"]
    keys = _deserialize(ddb["Keys"]) or {}
    entity_type, key = _split_key(str(keys["pk"]))
    return ChangeEvent(
        store="dynamodb",
        entity_type=entity_type,
        key=key,
        op=STREAM_OPS[record["eventName"]],
        seq=int(ddb["SequenceNumber"]),
        ts_ms=int(float(ddb["ApproximateCreationDateTime"]) * 1000),
        before=_deserialize(ddb.get("OldImage")),
        after=_deserialize(ddb.get("NewImage")),
    )


class DynamoDBStore:
    name = "dynamodb"

    def __init__(self, client: Any, mock: Any = None, streams: bool = False) -> None:
        self.client = client
        self.mock = mock  # moto mock_aws kept alive for the store's lifetime
        self.mapping = load_mapping(self.name)
        self._ensure_table(streams)

    def _ensure_table(self, streams: bool) -> None:
        try:
            self.client.describe_table(TableName=TABLE)
            return
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        kwargs: dict[str, Any] = {}
        if streams:
            kwargs["StreamSpecification"] = {
                "StreamEnabled": True,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            }
        self.client.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
            **kwargs,
        )
        self.client.get_waiter("table_exists").wait(TableName=TABLE)

    def _get(self, pk: str) -> dict[str, Any] | None:
        res = self.client.get_item(TableName=TABLE, Key={"pk": {"S": pk}}, ConsistentRead=True)
        return _deserialize(res.get("Item"))

    def _next_seq(self) -> int:
        res = self.client.update_item(
            TableName=TABLE,
            Key={"pk": {"S": COUNTER}},
            UpdateExpression="ADD seq :one",
            ExpressionAttributeValues={":one": {"N": "1"}},
            ReturnValues="UPDATED_NEW",
        )
        return int(res["Attributes"]["seq"]["N"])

    def _apply(
        self,
        entity_type: str,
        pk: str,
        op: Op,
        data_action: dict[str, Any],
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        idempotency_key: str,
    ) -> WriteResult:
        seq = self._next_seq()
        event = {
            "pk": f"{STREAM}{seq:020d}",
            "seq": seq,
            "entity_type": entity_type,
            "key": _split_key(pk)[1],
            "op": op,
            "ts_ms": now_ms(),
            "before": before,
            "after": after,
        }
        try:
            self.client.transact_write_items(
                TransactItems=[
                    data_action,
                    {
                        "Put": {
                            "TableName": TABLE,
                            "Item": {"pk": {"S": f"{IDEMPOTENCY}{idempotency_key}"}},
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {"Put": {"TableName": TABLE, "Item": _serialize(event)}},
                ]
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                return WriteResult(applied=False, idempotency_key=idempotency_key)
            raise
        return WriteResult(applied=True, idempotency_key=idempotency_key)

    def _claimed(self, idempotency_key: str) -> bool:
        return self._get(f"{IDEMPOTENCY}{idempotency_key}") is not None

    def read(self, entity_type: str, key: str) -> dict[str, Any] | None:
        return self._get(self.mapping.for_entity(entity_type).native_key(key))

    def scan(self, entity_type: str) -> Iterator[dict[str, Any]]:
        prefix = self.mapping.for_entity(entity_type).key_prefix
        for page in self.client.get_paginator("scan").paginate(
            TableName=TABLE,
            FilterExpression="begins_with(pk, :p)",
            ExpressionAttributeValues={":p": {"S": prefix}},
            ConsistentRead=True,
        ):
            for item in page["Items"]:
                yield _deserialize(item) or {}

    def write(self, entity_type: str, record: dict[str, Any], idempotency_key: str) -> WriteResult:
        if self._claimed(idempotency_key):
            return WriteResult(applied=False, idempotency_key=idempotency_key)
        pk = str(record[self.mapping.for_entity(entity_type).key])
        before = self._get(pk)
        put = {"Put": {"TableName": TABLE, "Item": _serialize(record)}}
        op: Op = "c" if before is None else "u"
        return self._apply(entity_type, pk, op, put, before, record, idempotency_key)

    def delete(self, entity_type: str, key: str, idempotency_key: str) -> WriteResult:
        if self._claimed(idempotency_key):
            return WriteResult(applied=False, idempotency_key=idempotency_key)
        pk = self.mapping.for_entity(entity_type).native_key(key)
        before = self._get(pk)
        if before is None:
            return WriteResult(applied=True, idempotency_key=idempotency_key)
        delete = {"Delete": {"TableName": TABLE, "Key": {"pk": {"S": pk}}}}
        return self._apply(entity_type, pk, "d", delete, before, None, idempotency_key)

    def stream_changes(self, since: int = 0) -> Iterator[ChangeEvent]:
        events: list[dict[str, Any]] = []
        for page in self.client.get_paginator("scan").paginate(
            TableName=TABLE,
            FilterExpression="begins_with(pk, :p) AND seq > :s",
            ExpressionAttributeValues={":p": {"S": STREAM}, ":s": {"N": str(since)}},
            ConsistentRead=True,
        ):
            events.extend(_deserialize(item) or {} for item in page["Items"])
        for e in sorted(events, key=lambda e: e["seq"]):
            e.pop("pk")
            yield ChangeEvent(store=self.name, **e)


def create_store(backend: str, client: Any = None) -> DynamoDBStore:
    if client is not None:
        return DynamoDBStore(client)
    if backend == "local":
        from moto import mock_aws

        mock = mock_aws()
        mock.start()
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        return DynamoDBStore(client, mock=mock)
    region = os.environ.get("AWS_REGION")
    return DynamoDBStore(boto3.client("dynamodb", region_name=region), streams=True)
