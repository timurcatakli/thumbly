import sys
import tempfile

import pytest

from thumbly.config import Config
from thumbly.service import Service


def pytest_configure(config):
    if sys.platform == "darwin":
        # macOS caps Unix socket paths at 104 bytes; .tmp/pytest inside a checkout is too deep
        # for the worker socket tests, so use a short root under /tmp.
        config.option.basetemp = tempfile.mkdtemp(prefix="thumbly-", dir="/tmp")
        return
    # pytest creates basetemp itself, but a fresh checkout has no ignored .tmp parent.
    (config.rootpath / ".tmp").mkdir(exist_ok=True)


@pytest.fixture
async def service(tmp_path):
    config = Config(
        backend="simulated",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
    )
    service = Service(config)
    await service.start()
    yield service
    await service.close()


@pytest.fixture
async def lease(service):
    return (await service.phone.acquire_control("owner"))["lease_token"]
