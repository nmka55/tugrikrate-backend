"""Database engine configuration."""

from sqlalchemy import create_engine, text

from app.db.database import engine


def test_pool_checks_connections_before_use():
    """Neon suspends idle compute and kills pooled connections; without
    a pre-ping the next request gets a dead connection and a 500."""
    assert engine.pool._pre_ping is True


def test_a_dead_pooled_connection_is_replaced_not_raised(tmp_path):
    """Simulate the server killing a pooled connection: the engine with
    pre-ping must hand out a fresh one instead of the corpse."""
    probe = create_engine(f"sqlite:///{tmp_path / 'p.db'}", pool_pre_ping=True)
    with probe.connect() as conn:
        conn.execute(text("select 1"))
        raw = conn.connection.dbapi_connection
    raw.close()  # the "administrator command"
    with probe.connect() as conn:
        assert conn.execute(text("select 1")).scalar() == 1
