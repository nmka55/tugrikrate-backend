from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.api import _rate_limit_hits, app
from app.db.database import get_db
from app.models.exchange_rate import CurrencyDetail, Rate
from app.models.snapshot import Base
from app.sources.models import CrawlResult, Quote
from app.sources.registry import BY_ID


@pytest.fixture(scope="function")
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    db = TestSession()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def client(test_db, monkeypatch):
    # The app's lifespan creates the real schema and starts the
    # scheduler. Neither belongs in a test: init_db would write to the
    # configured database rather than the in-memory one, and the
    # scheduler would fire live crawls at real bank sites.
    monkeypatch.setattr("app.api.api.init_db", lambda: None)
    monkeypatch.setattr("app.api.api.scheduler.start", lambda: None)
    monkeypatch.setattr("app.api.api.scheduler.shutdown", lambda: None)

    _rate_limit_hits.clear()
    with TestClient(app) as test_client:
        yield test_client
    _rate_limit_hits.clear()


@pytest.fixture
def khanbank_spec():
    return BY_ID["khanbank"]


@pytest.fixture
def mongolbank_spec():
    return BY_ID["mongolbank"]


@pytest.fixture
def sendmn_spec():
    return BY_ID["sendmn"]


def make_detail(
    cash_buy=None, cash_sell=None, noncash_buy=None, noncash_sell=None
) -> CurrencyDetail:
    """Build the crawler-shaped value the adapter consumes."""

    def dec(value):
        return None if value is None else Decimal(str(value))

    return CurrencyDetail(
        cash=Rate(buy=dec(cash_buy), sell=dec(cash_sell)),
        noncash=Rate(buy=dec(noncash_buy), sell=dec(noncash_sell)),
    )


@pytest.fixture
def make_result():
    def _make(source_id="khanbank", quotes=None, payload=b"{}", **kwargs):
        return CrawlResult(
            source_id=source_id,
            quotes=(
                quotes
                if quotes is not None
                else [
                    Quote("USD", "cash", "buy", Decimal("3450.50")),
                    Quote("USD", "cash", "sell", Decimal("3480.00")),
                ]
            ),
            payload=payload,
            **kwargs,
        )

    return _make
