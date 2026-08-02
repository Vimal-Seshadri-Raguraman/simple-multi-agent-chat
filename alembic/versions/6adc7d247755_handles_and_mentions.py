"""handles and mentions

Revision ID: 6adc7d247755
Revises: 64c28a528e54
Create Date: 2026-08-02 15:16:06.169950

"""

# Backfill note: this migration bakes a frozen copy of the handle-slug rules;
# app/handles.py may evolve, this snapshot must not.

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.models

# revision identifiers, used by Alembic.
revision: str = "6adc7d247755"
down_revision: Union[str, Sequence[str], None] = "64c28a528e54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slug(text: str, taken: set) -> str:
    """Frozen copy of app.handles' slugify + suffix rules, for the backfill only."""
    base = (re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32]) or "member"
    candidate, counter = base, 1
    while candidate in taken:
        counter += 1
        candidate = f"{base[: 32 - len(str(counter))]}{counter}"
    return candidate


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("members", sa.Column("handle", sa.String(), nullable=True))

    members = sa.table(
        "members",
        sa.column("member_id", sa.String),
        sa.column("workspace_id", sa.String),
        sa.column("member_name", sa.String),
        sa.column("first_name", sa.String),
        sa.column("last_name", sa.String),
        sa.column("handle", sa.String),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(
            members.c.member_id,
            members.c.workspace_id,
            members.c.member_name,
            members.c.first_name,
            members.c.last_name,
        ).order_by(members.c.workspace_id, members.c.member_id)
    ).fetchall()
    taken_per_ws: dict = {}
    for member_id, workspace_id, member_name, first_name, last_name in rows:
        taken = taken_per_ws.setdefault(workspace_id, set())
        base_text = (
            f"{first_name[0]}{last_name}" if first_name and last_name else member_name
        )
        handle = _slug(base_text, taken)
        taken.add(handle)
        conn.execute(
            members.update()
            .where(members.c.member_id == member_id)
            .values(handle=handle)
        )

    with op.batch_alter_table("members") as batch:
        batch.alter_column("handle", nullable=False)
        batch.create_unique_constraint(
            "uq_members_workspace_handle", ["workspace_id", "handle"]
        )

    op.create_table(
        "mentions",
        sa.Column("mention_id", sa.String(), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(),
            sa.ForeignKey("messages.message_id"),
            nullable=False,
        ),
        sa.Column(
            "mentioned_member_id",
            sa.String(),
            sa.ForeignKey("members.member_id"),
            nullable=False,
        ),
        sa.Column("created_at", app.models.UTCDateTime(), nullable=False),
        sa.Column("acknowledged_at", app.models.UTCDateTime(), nullable=True),
    )
    op.create_index("ix_mentions_message_id", "mentions", ["message_id"])
    op.create_index(
        "ix_mentions_mentioned_member_id", "mentions", ["mentioned_member_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("mentions")
    with op.batch_alter_table("members") as batch:
        batch.drop_constraint("uq_members_workspace_handle", type_="unique")
        batch.drop_column("handle")
