class AppError(Exception):
    """Base for all application errors that map to the standard error envelope."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenMemberTypeError(AppError):
    status_code = 403
    code = "forbidden_member_type"


class NotAMemberError(AppError):
    status_code = 403
    code = "not_a_member"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class AlreadyAMemberError(AppError):
    status_code = 409
    code = "already_a_member"


class InvalidMessageError(AppError):
    status_code = 422
    code = "invalid_message"


class InvalidCredentialsError(AppError):
    status_code = 401
    code = "invalid_credentials"


class InvalidTokenError(AppError):
    status_code = 401
    code = "invalid_token"


class WorkspaceTokenRequiredError(AppError):
    """A real, valid credential was presented -- just the wrong tier
    (account-scope) for a workspace-scope endpoint (spec §2)."""

    status_code = 401
    code = "workspace_token_required"


class AccountTokenRequiredError(AppError):
    """A real, valid credential was presented -- just the wrong tier
    (workspace/legacy-scope) for an account-scope endpoint (spec §2)."""

    status_code = 401
    code = "account_token_required"


class EmailTakenError(AppError):
    status_code = 409
    code = "email_taken"


class HandleTakenError(AppError):
    status_code = 409
    code = "handle_taken"


class InvalidInviteError(AppError):
    status_code = 404
    code = "invalid_invite"


class CapabilityDeniedError(AppError):
    """The single 403 raised by `app.capabilities.require_cap` (SMAC-92,
    spec §2) -- every privileged route funnels through this one class now,
    replacing the old per-check `NotWorkspaceAdminError`."""

    status_code = 403
    code = "forbidden"


class LastAdminError(AppError):
    status_code = 409
    code = "last_admin"


class ConfirmationRequiredError(AppError):
    status_code = 422
    code = "confirmation_required"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class WorkspaceNameTakenError(AppError):
    status_code = 409
    code = "workspace_name_taken"


class ChannelNameTakenError(AppError):
    status_code = 409
    code = "channel_name_taken"
