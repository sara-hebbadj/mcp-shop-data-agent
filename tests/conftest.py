"""Shared fixtures: every test run builds its own fresh database (about 0.4 s)."""

import pytest

from shop_data_mcp import config
from shop_data_mcp.generate_data import generate
from shop_data_mcp.tools import ShopData


@pytest.fixture(scope="session")
def shop_db(tmp_path_factory):
    """Generate shop.db into a temp folder and point config.DB_PATH at it."""
    path = tmp_path_factory.mktemp("data") / "shop.db"
    generate(path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(config, "DB_PATH", path)
        yield path


@pytest.fixture
def shop(shop_db, tmp_path):
    """A ShopData with a short time limit and its own query log."""
    return ShopData(db_path=shop_db, log_path=tmp_path / "query_log.jsonl", timeout_s=0.5)
