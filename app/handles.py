"""Handle generation: mutable display sugar, unique per workspace.

Storage never references handles (messages store <@member_id> tokens), so
these are safe to regenerate or edit; only at-this-moment uniqueness inside
a workspace matters, so @handle typing resolves unambiguously.
"""

import re

from sqlalchemy.orm import Session

from app.models import Member

_MAX_HANDLE_LENGTH = 32
_FALLBACK = "member"


def slugify(text: str) -> str:
    """Lowercase; runs of anything outside [a-z0-9] become single dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:_MAX_HANDLE_LENGTH] or _FALLBACK


def generate_unique_handle(db: Session, workspace_id: str, base_text: str) -> str:
    """The slug, or slug2/slug3/... — first form free in this workspace."""
    base = slugify(base_text)
    candidate, counter = base, 1
    while (
        db.query(Member)
        .filter(Member.workspace_id == workspace_id, Member.handle == candidate)
        .first()
        is not None
    ):
        counter += 1
        candidate = f"{base[: _MAX_HANDLE_LENGTH - len(str(counter))]}{counter}"
    return candidate
