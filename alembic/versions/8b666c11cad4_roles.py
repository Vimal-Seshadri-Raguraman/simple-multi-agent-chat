"""roles

Revision ID: 8b666c11cad4
Revises: cc9df896e9a9
Create Date: 2026-08-06 12:30:12.906415

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b666c11cad4"
down_revision: Union[str, Sequence[str], None] = "cc9df896e9a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """SMAC-92: `is_admin` (bool) -> `role` (str) on `members`.

    Add the column NOT NULL DEFAULT 'member' (so any row inserted mid-
    migration by a concurrent writer already lands correctly), backfill
    `role='admin'` for every row that had `is_admin=1`, then drop
    `is_admin` (SQLite: batch mode, same discipline as cc9df896e9a9 --
    `alembic/env.py` runs with `PRAGMA foreign_keys` OFF so the
    recreate-and-drop survives a populated, FK-referenced `members`
    table).

    `agent_admin` is a brand-new role with no legacy analogue -- nothing
    backfills into it; every pre-existing admin becomes plain `admin`,
    every non-admin becomes `member`, matching spec §3.
    """
    with op.batch_alter_table("members") as batch_op:
        batch_op.add_column(
            sa.Column("role", sa.String(), nullable=False, server_default="member")
        )

    conn = op.get_bind()
    conn.execute(sa.text("UPDATE members SET role = 'admin' WHERE is_admin = 1"))

    with op.batch_alter_table("members") as batch_op:
        batch_op.drop_column("is_admin")


def downgrade() -> None:
    """Recreate `is_admin` from `role`. Documented, accepted data loss:
    `agent_admin` has no pre-roles analogue, so it collapses to
    `is_admin=0` (non-admin) -- the same bucket `member` lands in.
    """
    with op.batch_alter_table("members") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_admin", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )

    conn = op.get_bind()
    conn.execute(sa.text("UPDATE members SET is_admin = 1 WHERE role = 'admin'"))

    with op.batch_alter_table("members") as batch_op:
        batch_op.drop_column("role")
