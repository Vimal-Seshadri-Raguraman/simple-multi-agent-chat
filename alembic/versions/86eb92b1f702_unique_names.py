"""unique names

Revision ID: 86eb92b1f702
Revises: d2b0475785e7
Create Date: 2026-08-03 10:15:45.191580

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "86eb92b1f702"
down_revision: Union[str, Sequence[str], None] = "d2b0475785e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dedupe_names(conn, table, name_col, pk_col, scope_col=None):  # type: ignore[no-untyped-def]
    """Oldest row keeps its name; later case-insensitive duplicates get a
    numeric suffix. Suffix candidates are collision-checked within the
    same scope (global for workspaces, per-workspace for channels)."""
    scope_select = f", {scope_col}" if scope_col else ""
    rows = conn.execute(
        sa.text(
            f"SELECT {pk_col}, {name_col}{scope_select} FROM {table} "
            f"ORDER BY created_at, {pk_col}"
        )
    ).fetchall()
    seen: dict[str, set[str]] = {}
    for row in rows:
        pk, name = row[0], row[1]
        scope = row[2] if scope_col else ""
        taken = seen.setdefault(scope, set())
        key = name.lower()
        if key not in taken:
            taken.add(key)
            continue
        n = 2
        while f"{key}{n}" in taken:
            n += 1
        conn.execute(
            sa.text(f"UPDATE {table} SET {name_col} = :n WHERE {pk_col} = :pk"),
            {"n": f"{name}{n}", "pk": pk},
        )
        taken.add(f"{key}{n}")


def upgrade() -> None:
    """Dedupe pre-existing case-insensitive name collisions, backfill the
    lower(name) shadow key columns, then enforce case-insensitive
    uniqueness via plain unique constraints on those shadow keys.

    Expression indexes on lower(name) were attempted first and BLOCKED:
    SQLAlchemy's SQLite dialect cannot reflect expression indexes (verified
    through 2.0.51), which would leave the schema-drift guard permanently
    blind to them. Shadow columns are the controller-approved fallback --
    they reflect normally, so drift can actually be detected.
    """
    conn = op.get_bind()
    _dedupe_names(conn, "workspaces", "workspace_name", "workspace_id")
    _dedupe_names(
        conn, "channels", "channel_name", "channel_id", scope_col="workspace_id"
    )
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.add_column(
            sa.Column(
                "workspace_name_key", sa.String(), nullable=False, server_default=""
            )
        )
    with op.batch_alter_table("channels") as batch_op:
        batch_op.add_column(
            sa.Column(
                "channel_name_key", sa.String(), nullable=False, server_default=""
            )
        )
    conn.execute(
        sa.text("UPDATE workspaces SET workspace_name_key = lower(workspace_name)")
    )
    conn.execute(sa.text("UPDATE channels SET channel_name_key = lower(channel_name)"))
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.create_unique_constraint(
            "uq_workspaces_name_ci", ["workspace_name_key"]
        )
    with op.batch_alter_table("channels") as batch_op:
        batch_op.create_unique_constraint(
            "uq_channels_workspace_name_ci", ["workspace_id", "channel_name_key"]
        )


def downgrade() -> None:
    """Drop both unique constraints and their shadow key columns."""
    with op.batch_alter_table("channels") as batch_op:
        batch_op.drop_constraint("uq_channels_workspace_name_ci", type_="unique")
        batch_op.drop_column("channel_name_key")
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.drop_constraint("uq_workspaces_name_ci", type_="unique")
        batch_op.drop_column("workspace_name_key")
