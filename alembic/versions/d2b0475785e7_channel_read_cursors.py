"""channel read cursors

Revision ID: d2b0475785e7
Revises: 6adc7d247755
Create Date: 2026-08-02 21:41:00.048323

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2b0475785e7"
down_revision: Union[str, Sequence[str], None] = "6adc7d247755"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add last_read_seq and backfill existing members to caught-up."""
    with op.batch_alter_table("channel_members") as batch_op:
        batch_op.add_column(
            sa.Column("last_read_seq", sa.Integer(), nullable=False, server_default="0")
        )
    # Existing members start caught up: nobody wakes to ancient badges.
    op.execute(
        "UPDATE channel_members SET last_read_seq = COALESCE("
        "(SELECT MAX(seq) FROM messages "
        "WHERE messages.channel_id = channel_members.channel_id), 0)"
    )


def downgrade() -> None:
    """Drop last_read_seq."""
    with op.batch_alter_table("channel_members") as batch_op:
        batch_op.drop_column("last_read_seq")
