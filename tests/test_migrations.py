"""Migrations are the schema's source of truth for on-disk databases."""

from alembic import command
from sqlalchemy import create_engine, inspect

from app.database import _alembic_config
from app.models import Base


def _upgraded_engine(tmp_path):
    url = f"sqlite:///{tmp_path}/mig.db"
    command.upgrade(_alembic_config(url), "head")
    return create_engine(url)


def test_upgrade_head_builds_full_schema(tmp_path):
    engine = _upgraded_engine(tmp_path)
    migrated = set(inspect(engine).get_table_names())
    expected = set(Base.metadata.tables.keys())
    assert expected <= migrated  # everything the models define exists
    assert "alembic_version" in migrated  # plus alembic's bookkeeping table


def test_upgrade_is_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path}/mig.db"
    command.upgrade(_alembic_config(url), "head")
    command.upgrade(_alembic_config(url), "head")  # second run must not raise
    engine = create_engine(url)
    assert "members" in inspect(engine).get_table_names()


def test_migrations_match_models(tmp_path):
    """The baseline (+ future revisions) must produce the same columns as the models."""
    engine = _upgraded_engine(tmp_path)
    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        migrated_cols = {c["name"] for c in inspector.get_columns(table_name)}
        model_cols = {c.name for c in table.columns}
        assert model_cols == migrated_cols, f"drift in {table_name}"


def test_no_model_migration_drift(tmp_path):
    """Alembic's own comparator must see zero differences of ANY kind."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = _upgraded_engine(tmp_path)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        diffs = compare_metadata(ctx, Base.metadata)
    assert diffs == [], f"schema drift between models and migrations: {diffs}"


def test_handle_backfill_for_existing_members(tmp_path):
    url = f"sqlite:///{tmp_path}/backfill.db"
    command.upgrade(_alembic_config(url), "64c28a528e54")  # pre-handles schema
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO workspaces (workspace_id, workspace_name, visibility, created_at)"
            " VALUES ('w1', 'Acme', 'private', '2026-01-01 00:00:00')"
        )
        for member_id, first, last, name in [
            ("m1", "Rohan", "Mode", "Rohan Mode"),
            ("m2", "Rita", "Mode", "Rita Mode"),  # collides -> rmode2
            ("m3", None, None, "Helper Bot"),
        ]:
            conn.exec_driver_sql(
                "INSERT INTO members (member_id, member_name, member_type,"
                " workspace_id, is_admin, first_name, last_name, created_at)"
                f" VALUES ('{member_id}', '{name}', 'human', 'w1', 0,"
                f" {'NULL' if first is None else repr(first)},"
                f" {'NULL' if last is None else repr(last)}, '2026-01-01 00:00:00')"
            )
    command.upgrade(_alembic_config(url), "head")
    with engine.begin() as conn:
        handles = dict(
            conn.exec_driver_sql(
                "SELECT member_id, handle FROM members ORDER BY member_id"
            ).fetchall()
        )
    assert handles == {"m1": "rmode", "m2": "rmode2", "m3": "helper-bot"}
