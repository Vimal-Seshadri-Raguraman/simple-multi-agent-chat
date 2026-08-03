"""Migrations are the schema's source of truth for on-disk databases."""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

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


def test_read_cursor_backfill_sets_caught_up(tmp_path):
    """Upgrading a populated DB backfills last_read_seq to each channel's
    current max seq (0 for channels with no messages)."""
    url = f"sqlite:///{tmp_path}/backfill.db"
    command.upgrade(_alembic_config(url), "6adc7d247755")  # pre-read-cursors schema
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO workspaces (workspace_id, workspace_name, visibility, created_at)"
            " VALUES ('w1', 'Acme', 'private', '2026-01-01 00:00:00')"
        )
        for member_id, name in [("m1", "Rohan Mode"), ("m2", "Rita Mode")]:
            conn.exec_driver_sql(
                "INSERT INTO members (member_id, member_name, member_type,"
                " workspace_id, is_admin, handle, created_at)"
                f" VALUES ('{member_id}', '{name}', 'human', 'w1', 0,"
                f" '{member_id}', '2026-01-01 00:00:00')"
            )
        for channel_id, name in [("c1", "general"), ("c2", "empty")]:
            conn.exec_driver_sql(
                "INSERT INTO channels (channel_id, workspace_id, channel_name,"
                " created_at) VALUES"
                f" ('{channel_id}', 'w1', '{name}', '2026-01-01 00:00:00')"
            )
        for channel_id in ("c1", "c2"):
            conn.exec_driver_sql(
                "INSERT INTO channel_members (channel_id, member_id)"
                f" VALUES ('{channel_id}', 'm1')"
            )
        for seq in (1, 2, 3):
            conn.exec_driver_sql(
                "INSERT INTO messages (message_id, seq, channel_id,"
                " sender_member_id, message_text, created_at) VALUES"
                f" ('msg{seq}', {seq}, 'c1', 'm1', 'hello {seq}',"
                " '2026-01-01 00:00:00')"
            )
    command.upgrade(_alembic_config(url), "head")
    with engine.begin() as conn:
        cursors = dict(
            conn.exec_driver_sql(
                "SELECT channel_id, last_read_seq FROM channel_members"
                " ORDER BY channel_id"
            ).fetchall()
        )
    assert cursors == {"c1": 3, "c2": 0}


def test_dedupe_and_shadow_key_backfill_for_unique_names(tmp_path):
    """Pre-existing case-insensitive duplicate names get suffixed in age
    order (collision-checked against candidates, not just the raw counter),
    workspace_name_key/channel_name_key are backfilled to lower(name) for
    every row (using Python's Unicode-aware str.lower(), not SQLite's
    ASCII-only lower(), so accented case variants like 'Équipe'/'équipe'
    dedupe and key correctly), and both unique constraints are enforced
    afterward."""
    url = f"sqlite:///{tmp_path}/backfill.db"
    command.upgrade(_alembic_config(url), "d2b0475785e7")  # pre-unique-names schema
    engine = create_engine(url)
    with engine.begin() as conn:
        for workspace_id, name, created_at in [
            ("w1", "Finance", "2026-01-01 00:00:00"),  # oldest -> keeps name
            ("w2", "finance", "2026-01-01 00:00:01"),  # -> finance2
            ("w3", "FINANCE", "2026-01-01 00:00:02"),  # "finance2" taken -> FINANCE3
            ("w4", "Ops", "2026-01-01 00:00:03"),  # no collision, untouched
            ("w5", "Équipe", "2026-01-01 00:00:04"),  # oldest -> keeps name
            ("w6", "équipe", "2026-01-01 00:00:05"),  # -> équipe2 (dedupe already
            # used Python lower(); the regression this guards is the *backfilled
            # key* for the kept "Équipe" row: SQLite's lower() leaves 'É'
            # untouched, so under the bug workspace_name_key stayed "Équipe"
            # instead of "équipe", and a later app-created "Équipe"/"équipe"
            # would then NOT collide against it)
        ]:
            conn.exec_driver_sql(
                "INSERT INTO workspaces (workspace_id, workspace_name, visibility,"
                f" created_at) VALUES ('{workspace_id}', '{name}', 'private',"
                f" '{created_at}')"
            )
        for channel_id, name, created_at in [
            ("c1", "reports", "2026-01-01 00:00:00"),  # older -> keeps name
            ("c2", "Reports", "2026-01-01 00:00:01"),  # -> Reports2
        ]:
            conn.exec_driver_sql(
                "INSERT INTO channels (channel_id, workspace_id, channel_name,"
                f" created_at) VALUES ('{channel_id}', 'w4', '{name}',"
                f" '{created_at}')"
            )

    command.upgrade(_alembic_config(url), "head")

    with engine.begin() as conn:
        workspace_names = dict(
            conn.exec_driver_sql(
                "SELECT workspace_id, workspace_name FROM workspaces"
                " ORDER BY workspace_id"
            ).fetchall()
        )
        workspace_keys = dict(
            conn.exec_driver_sql(
                "SELECT workspace_id, workspace_name_key FROM workspaces"
                " ORDER BY workspace_id"
            ).fetchall()
        )
        channel_names = dict(
            conn.exec_driver_sql(
                "SELECT channel_id, channel_name FROM channels ORDER BY channel_id"
            ).fetchall()
        )
        channel_keys = dict(
            conn.exec_driver_sql(
                "SELECT channel_id, channel_name_key FROM channels"
                " ORDER BY channel_id"
            ).fetchall()
        )

    assert workspace_names == {
        "w1": "Finance",
        "w2": "finance2",
        "w3": "FINANCE3",
        "w4": "Ops",
        "w5": "Équipe",
        "w6": "équipe2",
    }
    assert workspace_keys == {
        wid: name.lower() for wid, name in workspace_names.items()
    }
    # Explicit Unicode assertions: str.lower() (not SQLite's ASCII-only
    # lower()) must have produced these, since 'É'.lower() == 'é'.
    assert workspace_keys["w5"] == "équipe"
    assert workspace_keys["w6"] == "équipe2"
    assert channel_names == {"c1": "reports", "c2": "Reports2"}
    assert channel_keys == {cid: name.lower() for cid, name in channel_names.items()}

    inspector = inspect(engine)
    workspace_constraints = {
        uc["name"] for uc in inspector.get_unique_constraints("workspaces")
    }
    channel_constraints = {
        uc["name"] for uc in inspector.get_unique_constraints("channels")
    }
    assert "uq_workspaces_name_ci" in workspace_constraints
    assert "uq_channels_workspace_name_ci" in channel_constraints

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO workspaces (workspace_id, workspace_name,"
                " workspace_name_key, visibility, created_at) VALUES"
                " ('w9', 'FiNaNcE', 'finance', 'private', '2026-01-01 00:00:06')"
            )

    # Unicode collision: 'ÉQUIPE'.lower() == 'équipe', already claimed by w5
    # above. If the backfill had used SQLite's ASCII-only lower() this would
    # NOT collide (the stored key for w5 would be the untouched 'Équipe').
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO workspaces (workspace_id, workspace_name,"
                " workspace_name_key, visibility, created_at) VALUES"
                " ('w10', 'ÉQUIPE', 'équipe', 'private', '2026-01-01 00:00:07')"
            )


def test_identity_v2_migration_a_backfill(tmp_path):
    """Migration A backfill (spec §5 steps 1-3, SMAC-79 Task 1):

    - same-email-two-workspaces with DIFFERENT passwords collapse into
      ONE account; the OLDER member (by created_at then member_id)
      donates its password_hash AND its email's original casing; both
      members link to that one account via account_id.
    - a same-email-same-password pair also collapses to one (different)
      account, proving grouping is by email, not incidentally by hash.
    - each agent/bot member gets its own BRAND-NEW account (no
      cross-workspace merging), with no email/password.
    - uq_accounts_email_ci is enforced afterward.
    """
    url = f"sqlite:///{tmp_path}/backfill.db"
    command.upgrade(_alembic_config(url), "86eb92b1f702")  # pre-identity-v2 schema
    engine = create_engine(url)
    with engine.begin() as conn:
        for workspace_id, name, key, created_at in [
            ("w1", "Acme", "acme", "2026-01-01 00:00:00"),
            ("w2", "Beta", "beta", "2026-01-01 00:00:01"),
        ]:
            conn.exec_driver_sql(
                "INSERT INTO workspaces (workspace_id, workspace_name,"
                " workspace_name_key, visibility, created_at) VALUES"
                f" ('{workspace_id}', '{name}', '{key}', 'private', '{created_at}')"
            )
        # Same email (case-variant), DIFFERENT passwords -- m1 is older,
        # so it must donate both its password_hash and its email casing.
        for member_id, workspace_id, email, password_hash, handle, created_at in [
            (
                "m1",
                "w1",
                "Dup@Example.com",
                "hash-oldest",
                "dupuser",
                "2026-01-01 00:00:00",
            ),
            (
                "m2",
                "w2",
                "dup@example.com",
                "hash-newest",
                "dupuser2",
                "2026-01-01 00:00:05",
            ),
        ]:
            conn.exec_driver_sql(
                "INSERT INTO members (member_id, member_name, member_type,"
                " workspace_id, is_admin, handle, email, password_hash,"
                " created_at) VALUES"
                f" ('{member_id}', 'Dup User', 'human', '{workspace_id}', 0,"
                f" '{handle}', '{email}', '{password_hash}', '{created_at}')"
            )
        # A second, distinct email shared by two members with the SAME
        # password -- must also collapse to one (different) account.
        for member_id, workspace_id, handle, created_at in [
            ("m3", "w1", "sameuser", "2026-01-01 00:00:02"),
            ("m4", "w2", "sameuser2", "2026-01-01 00:00:03"),
        ]:
            conn.exec_driver_sql(
                "INSERT INTO members (member_id, member_name, member_type,"
                " workspace_id, is_admin, handle, email, password_hash,"
                " created_at) VALUES"
                f" ('{member_id}', 'Same User', 'human', '{workspace_id}', 0,"
                f" '{handle}', 'same@example.com', 'hash-same', '{created_at}')"
            )
        # An agent member: own brand-new account, no email/password.
        conn.exec_driver_sql(
            "INSERT INTO members (member_id, member_name, member_type,"
            " workspace_id, is_admin, handle, created_at) VALUES"
            " ('m5', 'Agent Bot', 'agent', 'w1', 0, 'agentbot',"
            " '2026-01-01 00:00:04')"
        )

    command.upgrade(_alembic_config(url), "head")

    with engine.begin() as conn:
        member_accounts = dict(
            conn.exec_driver_sql(
                "SELECT member_id, account_id FROM members ORDER BY member_id"
            ).fetchall()
        )
        accounts_by_id = {
            row[0]: row
            for row in conn.exec_driver_sql(
                "SELECT account_id, account_type, email, email_key,"
                " password_hash FROM accounts"
            ).fetchall()
        }

    # Every member links to a real account.
    assert all(account_id is not None for account_id in member_accounts.values())
    assert set(member_accounts.values()) <= set(accounts_by_id.keys())

    # m1 (oldest) and m2 share one account; m1 donated its password AND
    # its email's original casing (NOT lowercased/overwritten).
    assert member_accounts["m1"] == member_accounts["m2"]
    dup_account = accounts_by_id[member_accounts["m1"]]
    assert dup_account[1] == "human"  # account_type
    assert dup_account[2] == "Dup@Example.com"  # email casing from oldest row
    assert dup_account[3] == "dup@example.com"  # email_key
    assert dup_account[4] == "hash-oldest"  # oldest member's password wins

    # m3 and m4 (distinct email, same password) also collapse to one
    # account -- a DIFFERENT account than the m1/m2 group.
    assert member_accounts["m3"] == member_accounts["m4"]
    assert member_accounts["m3"] != member_accounts["m1"]
    same_account = accounts_by_id[member_accounts["m3"]]
    assert same_account[4] == "hash-same"

    # The agent gets its own brand-new account: no email/password, and
    # not merged with either human group.
    agent_account = accounts_by_id[member_accounts["m5"]]
    assert agent_account[1] == "agent"
    assert agent_account[2] is None
    assert agent_account[4] is None
    assert member_accounts["m5"] not in (member_accounts["m1"], member_accounts["m3"])

    inspector = inspect(engine)
    account_constraints = {
        uc["name"] for uc in inspector.get_unique_constraints("accounts")
    }
    assert "uq_accounts_email_ci" in account_constraints

    # Case-insensitive uniqueness is enforced going forward.
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO accounts (account_id, account_type, email,"
                " email_key, password_hash, created_at) VALUES"
                " ('acct-x', 'human', 'DUP@EXAMPLE.COM', 'dup@example.com',"
                " 'x', '2026-01-01 00:00:06')"
            )


def test_drift_guard_catches_missing_migration_a(tmp_path):
    """Red -> green proof for migration A: with the migration chain
    stopped one revision short (at 86eb92b1f702, the pre-Identity-v2
    head), the model/migration comparator MUST report drift, since
    Base.metadata now includes `accounts`, `members.account_id`, and the
    refresh_tokens additions that 86eb92b1f702 alone doesn't create. This
    is the "red" half of the red -> green proof that
    test_no_model_migration_drift (green, against `head`) is the "green"
    half of."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    url = f"sqlite:///{tmp_path}/pre_identity_v2.db"
    command.upgrade(_alembic_config(url), "86eb92b1f702")
    engine = create_engine(url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        diffs = compare_metadata(ctx, Base.metadata)
    assert diffs != [], "expected drift against the pre-Identity-v2 schema, found none"
