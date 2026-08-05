"""identity v2 cutover

Revision ID: cc9df896e9a9
Revises: d379652f77fb
Create Date: 2026-08-03 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cc9df896e9a9"
down_revision: Union[str, Sequence[str], None] = "d379652f77fb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Finish the Identity v2 cutover (spec §5 steps 4-5, SMAC-79 Task 2).

    Migration A (d379652f77fb) linked every member to an account but
    changed nothing legacy; this migration is where the old shape
    actually goes away:

    1. Purge every `refresh_tokens` row (spec Decision: sessions re-login
       rather than trying to retrofit scope onto old rows -- one release
       note). Every workspace endpoint now REQUIRES a scoped token
       anyway (`app.auth.resolve_member`), so a surviving legacy row
       would be dead weight at best.
    2. Batch-drop `members.email`/`password_hash` and the old
       `uq_members_workspace_email` constraint, make `account_id` NOT
       NULL (migration A's backfill already populated it for every
       existing row), and add `uq_members_workspace_account` -- one
       profile per account per workspace, replacing per-workspace email
       uniqueness as the birth-door invariant.

    Both member changes happen inside one `batch_alter_table` block:
    SQLite's batch mode recreates the table via copy/drop/rename, and
    `alembic/env.py` runs migrations with `PRAGMA foreign_keys` OFF
    specifically so that recreate-and-drop survives a populated,
    FK-referenced `members` table (see env.py's docstring) -- unchanged
    by this migration, still load-bearing here.
    """
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM refresh_tokens"))

    with op.batch_alter_table("members") as batch_op:
        batch_op.drop_constraint("uq_members_workspace_email", type_="unique")
        batch_op.drop_index("ix_members_email")
        batch_op.drop_column("email")
        batch_op.drop_column("password_hash")
        batch_op.alter_column("account_id", existing_type=sa.String(), nullable=False)
        batch_op.create_unique_constraint(
            "uq_members_workspace_account", ["workspace_id", "account_id"]
        )


def downgrade() -> None:
    """Identity v2's cutover is one-way: `members.email`/`password_hash`
    are gone and `refresh_tokens` was purged, so there is no data left to
    reconstruct a downgrade from -- restoring the pre-cutover shape means
    restoring from backup, not running this migration in reverse (first
    irreversible migration in this codebase, called out deliberately).
    """
    raise NotImplementedError("Identity v2 migration is one-way — restore from backup")
