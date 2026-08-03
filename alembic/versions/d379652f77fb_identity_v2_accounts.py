"""identity v2 accounts

Revision ID: d379652f77fb
Revises: 86eb92b1f702
Create Date: 2026-08-03 12:00:00.000000

"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.models

# revision identifiers, used by Alembic.
revision: str = "d379652f77fb"
down_revision: Union[str, Sequence[str], None] = "86eb92b1f702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _new_id() -> str:
    """Frozen copy of app.models.new_id -- migrations must not depend on
    application code that could later change shape (same discipline as
    6adc7d247755's frozen `_slug` helper)."""
    return str(uuid.uuid4())


def _backfill_human_accounts(conn) -> None:  # type: ignore[no-untyped-def]
    """Spec §5 step 2: one account per distinct lower(email) among human
    members, ordered by member `created_at` then `member_id` -- the
    OLDEST row in each group donates its `password_hash` (and its email's
    original casing) to the new account; every member sharing that
    lower(email) links to it via `account_id`. Later duplicates' own
    passwords are NOT copied anywhere -- they stop working, by design
    (spec Decision 3)."""
    rows = conn.execute(
        sa.text(
            "SELECT member_id, email, password_hash, created_at FROM members "
            "WHERE member_type = 'human' AND email IS NOT NULL "
            "ORDER BY created_at, member_id"
        )
    ).fetchall()
    account_id_by_key: dict[str, str] = {}
    for member_id, email, password_hash, created_at in rows:
        key = email.lower()
        account_id = account_id_by_key.get(key)
        if account_id is None:
            account_id = _new_id()
            account_id_by_key[key] = account_id
            conn.execute(
                sa.text(
                    "INSERT INTO accounts (account_id, account_type, email,"
                    " email_key, password_hash, created_at) VALUES"
                    " (:account_id, 'human', :email, :email_key,"
                    " :password_hash, :created_at)"
                ),
                {
                    "account_id": account_id,
                    "email": email,
                    "email_key": key,
                    "password_hash": password_hash,
                    "created_at": created_at,
                },
            )
        conn.execute(
            sa.text(
                "UPDATE members SET account_id = :account_id"
                " WHERE member_id = :member_id"
            ),
            {"account_id": account_id, "member_id": member_id},
        )


def _backfill_agent_accounts(conn) -> None:  # type: ignore[no-untyped-def]
    """Spec §5 step 3: one BRAND-NEW identity-only account per existing
    agent/bot_app member -- no retroactive cross-workspace merging (that's
    explicitly out of scope; forward-looking only, via the new
    `account_id` param on agent/bot creation, Task 3+)."""
    rows = conn.execute(
        sa.text(
            "SELECT member_id, member_type, created_at FROM members "
            "WHERE member_type IN ('agent', 'bot_app')"
        )
    ).fetchall()
    for member_id, member_type, created_at in rows:
        account_id = _new_id()
        conn.execute(
            sa.text(
                "INSERT INTO accounts (account_id, account_type, email,"
                " email_key, password_hash, created_at) VALUES"
                " (:account_id, :account_type, NULL, NULL, NULL, :created_at)"
            ),
            {
                "account_id": account_id,
                "account_type": member_type,
                "created_at": created_at,
            },
        )
        conn.execute(
            sa.text(
                "UPDATE members SET account_id = :account_id"
                " WHERE member_id = :member_id"
            ),
            {"account_id": account_id, "member_id": member_id},
        )


def upgrade() -> None:
    """Additive-only (SMAC-79 Task 1): create `accounts`, backfill it from
    every existing member (spec §5 steps 1-3), and link each member via
    the new nullable `members.account_id`. Also grows `refresh_tokens`
    with `scope`/`workspace_id`/`account_id` (and makes `member_id`
    nullable, since an account-scope row has no member) so both auth
    tiers can issue tokens. Nothing legacy is touched or dropped here --
    `members.email`/`password_hash` and the old unique constraint stay
    exactly as they are; a later migration finishes the cutover.
    """
    op.create_table(
        "accounts",
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("account_type", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("email_key", sa.String(), nullable=True),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.Column("created_at", app.models.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("account_id"),
        sa.UniqueConstraint("email_key", name="uq_accounts_email_ci"),
    )

    with op.batch_alter_table("members") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(), nullable=True))
        batch_op.create_index("ix_members_account_id", ["account_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_members_account_id_accounts",
            "accounts",
            ["account_id"],
            ["account_id"],
        )

    conn = op.get_bind()
    _backfill_human_accounts(conn)
    _backfill_agent_accounts(conn)

    with op.batch_alter_table("refresh_tokens") as batch_op:
        batch_op.alter_column("member_id", existing_type=sa.String(), nullable=True)
        batch_op.add_column(sa.Column("account_id", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("scope", sa.String(), nullable=False, server_default="workspace")
        )
        batch_op.add_column(sa.Column("workspace_id", sa.String(), nullable=True))
        batch_op.create_index(
            "ix_refresh_tokens_account_id", ["account_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_refresh_tokens_account_id_accounts",
            "accounts",
            ["account_id"],
            ["account_id"],
        )
        batch_op.create_foreign_key(
            "fk_refresh_tokens_workspace_id_workspaces",
            "workspaces",
            ["workspace_id"],
            ["workspace_id"],
        )


def downgrade() -> None:
    """Drop everything this migration added. Purely additive going in, so
    (unlike the batch-drop migration that finishes the cutover) this is
    safely reversible -- as long as no account-scope refresh token
    (NULL member_id) exists when downgrading, since member_id reverts to
    NOT NULL.
    """
    with op.batch_alter_table("refresh_tokens") as batch_op:
        batch_op.drop_constraint(
            "fk_refresh_tokens_workspace_id_workspaces", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_refresh_tokens_account_id_accounts", type_="foreignkey"
        )
        batch_op.drop_index("ix_refresh_tokens_account_id")
        batch_op.drop_column("workspace_id")
        batch_op.drop_column("scope")
        batch_op.drop_column("account_id")
        batch_op.alter_column("member_id", existing_type=sa.String(), nullable=False)
    with op.batch_alter_table("members") as batch_op:
        batch_op.drop_constraint("fk_members_account_id_accounts", type_="foreignkey")
        batch_op.drop_index("ix_members_account_id")
        batch_op.drop_column("account_id")
    op.drop_table("accounts")
