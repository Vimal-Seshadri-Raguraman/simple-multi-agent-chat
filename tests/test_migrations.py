"""Migrations are the schema's source of truth for on-disk databases."""

import os
import tempfile

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
