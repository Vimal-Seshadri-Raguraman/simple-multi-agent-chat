"""nullable message and mention member refs

Revision ID: 7a3b580f5d0c
Revises: 8b666c11cad4
Create Date: 2026-08-06 13:01:39.869591

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "7a3b580f5d0c"
down_revision: Union[str, Sequence[str], None] = "8b666c11cad4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """SMAC-92: `messages.sender_member_id` and `mentions.mentioned_member_id`
    NOT NULL -> nullable.

    Member removal (`DELETE /workspaces/{id}/members/{id}`) deletes the
    `members` row, but chat history must survive it (spec: "messages/
    mentions REMAIN"). With SQLite `PRAGMA foreign_keys=ON` (enforced by
    both the production and test engines), a NOT NULL FK-referenced column
    blocks deleting the referenced row outright -- verified directly: an
    attempt to delete a `Member` who has ever sent a message raises
    `IntegrityError: FOREIGN KEY constraint failed` under the pre-this-
    migration schema. `remove_member` nulls both columns for the departing
    member's rows *before* deleting the row, so the FK is satisfied by
    construction; the app layer (not an `ON DELETE SET NULL` clause) owns
    the nulling, so no FK behavior changes for any other write path.
    `app/schemas.py:build_message_payload` renders a placeholder sender for
    a null `sender_member_id`.
    """
    with op.batch_alter_table("messages") as batch_op:
        batch_op.alter_column(
            "sender_member_id", existing_type=sa.String(), nullable=True
        )
    with op.batch_alter_table("mentions") as batch_op:
        batch_op.alter_column(
            "mentioned_member_id", existing_type=sa.String(), nullable=True
        )


def downgrade() -> None:
    """Revert to NOT NULL. Will fail (as SQLite/Alembic surfaces it) if any
    row currently holds NULL in either column -- e.g. history belonging to
    an already-removed member -- same accepted-caveat shape as this
    codebase's other downgrades (see 8b666c11cad4's `agent_admin` collapse)."""
    with op.batch_alter_table("mentions") as batch_op:
        batch_op.alter_column(
            "mentioned_member_id", existing_type=sa.String(), nullable=False
        )
    with op.batch_alter_table("messages") as batch_op:
        batch_op.alter_column(
            "sender_member_id", existing_type=sa.String(), nullable=False
        )
