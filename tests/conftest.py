import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _local_backends() -> None:
    """Tests never touch real stores or model APIs."""
    os.environ["STORE_BACKEND"] = "local"
    os.environ["MODEL_PROVIDER"] = "mock"
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
