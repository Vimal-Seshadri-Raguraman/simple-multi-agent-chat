"""Typed errors raised by `smac_cli.api.SmacApi`.

Every method on `SmacApi` raises one of these instead of letting an
`httpx` exception or a raw error envelope leak out, so callers (the
Textual app in a later task, the tests here) can `except` a specific
class instead of string-matching a `.code`.

`SmacError.code` mirrors the server's error envelope `{"error": {"code":
..., "message": ...}}` (see `app/errors.py`) for the errors that come
from a real HTTP response. Several distinct server codes intentionally
map to the same client-side class (e.g. every `*_taken` conflict becomes
`NameTakenError`) because the TUI only ever needs to branch on the
*kind* of failure, not the exact server code -- `.code` is kept around
verbatim so a caller that *does* care can still inspect it.

`Unreachable` and `SessionExpired` are raised client-side (no server
response at all, or a failed token refresh) rather than mapped from an
envelope.
"""

from __future__ import annotations


class SmacError(Exception):
    """Base class for every error `SmacApi` raises.

    `str(error) == error.message`, via `Exception.__str__`'s default
    behavior of stringifying a single constructor argument.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class AuthError(SmacError):
    """Bad credentials or an invalid/expired token was rejected outright.

    Covers the server's `unauthorized`, `invalid_credentials`, and
    `invalid_token` codes -- distinct from `SessionExpired`, which is
    only raised after a refresh-on-401 retry has already failed.
    """


class NotFoundError(SmacError):
    """The requested resource (or an invite) doesn't exist -- or the
    caller isn't allowed to know that it does.

    Covers the server's `not_found` and `invalid_invite` codes.
    """


class NotAMemberError(SmacError):
    """The caller isn't a member of the workspace/channel it addressed."""


class RateLimitedError(SmacError):
    """The caller is posting too fast; retry after a short pause."""


class NameTakenError(SmacError):
    """A workspace/channel/email/handle name collided with an existing one.

    Covers the server's `workspace_name_taken`, `channel_name_taken`,
    `email_taken`, and `handle_taken` codes.
    """


class ValidationError(SmacError):
    """The request body failed validation, or a required confirmation
    (e.g. `?confirm=delete`) was missing.

    Covers the server's `invalid_message` and `confirmation_required`
    codes.
    """


class SessionExpired(SmacError):
    """Refresh-on-401 was attempted and failed too.

    The caller's saved session file has already been deleted by the time
    this is raised -- there is nothing left to retry, only `/login`.
    """

    def __init__(self, message: str = "Session expired — run /login again.") -> None:
        super().__init__("session_expired", message)


class NoWorkspaceError(SmacError):
    """Identity v2 (SMAC-79 Task 3): the caller has a live ACCOUNT session
    but hasn't entered a workspace yet (spec §6 -- `/register`'s landing
    state: account created, no workspace).

    Deliberately distinct from `SessionExpired`: the account itself is
    perfectly valid here, nothing needs deleting or re-logging-in -- the
    caller just needs `/workspace create <name>`, `/join <code>`, or
    `/login` to browse. Raised purely client-side by `SmacApi` (never
    mapped from a server envelope) whenever a workspace-tier call is
    attempted with no workspace token in the session yet.
    """

    def __init__(
        self,
        message: str = (
            "no workspace yet — /workspace create <name>, /join <code>, "
            "or /login to browse"
        ),
    ) -> None:
        super().__init__("no_workspace", message)


class Unreachable(SmacError):
    """The server at `url` could not be reached at all (no HTTP response).

    Raised on connection failures (refused connection, DNS failure,
    timeout, etc.) -- anything that never got as far as a status code.
    """

    def __init__(self, url: str) -> None:
        super().__init__(
            "unreachable",
            f"SMAC server is not reachable at {url} — run: smac-server --start",
        )


#: Envelope `code` string -> client-side exception class. Codes not listed
#: here (e.g. `forbidden_member_type`, `already_a_member`,
#: `not_workspace_admin`, `last_admin`, `conflict`, `http_error`,
#: `internal_error`) fall through to the `SmacError` base class.
#:
#: Identity v2 (SMAC-79 Task 3) adds `workspace_token_required`/
#: `account_token_required` (`app/errors.py`'s new wrong-tier 401s,
#: spec §2) to `AuthError` -- both mean a real, valid credential was
#: presented at the wrong tier, the same family of failure as
#: `invalid_token`. In normal operation `SmacApi` never presents a token
#: at the wrong tier itself (workspace-tier calls always use `access_
#: token`, account-tier calls always use `account_access_token`), so
#: seeing either here would indicate a genuine client bug rather than an
#: expected condition -- still worth a typed class rather than falling
#: through to the base `SmacError`.
_CODE_TO_CLASS: dict[str, type[SmacError]] = {
    "unauthorized": AuthError,
    "invalid_credentials": AuthError,
    "invalid_token": AuthError,
    "workspace_token_required": AuthError,
    "account_token_required": AuthError,
    "not_found": NotFoundError,
    "invalid_invite": NotFoundError,
    "not_a_member": NotAMemberError,
    "rate_limited": RateLimitedError,
    "workspace_name_taken": NameTakenError,
    "channel_name_taken": NameTakenError,
    "email_taken": NameTakenError,
    "handle_taken": NameTakenError,
    "invalid_message": ValidationError,
    "confirmation_required": ValidationError,
}


def from_envelope(code: str, message: str) -> SmacError:
    """Build the right `SmacError` subclass for a server error envelope.

    Unrecognized codes (including future ones the client doesn't know
    about yet) fall back to the `SmacError` base class rather than
    raising a `KeyError` -- an unmapped server error should still surface
    as *some* typed error, just without a more specific class.
    """
    cls = _CODE_TO_CLASS.get(code, SmacError)
    return cls(code, message)
