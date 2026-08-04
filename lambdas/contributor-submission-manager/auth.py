"""
Auth helpers for the contributor submission manager.

API Gateway is expected to attach a Cognito User Pool authorizer in front of
this Lambda, which surfaces the user's claims on
``event["requestContext"]["authorizer"]["claims"]``. We read the JWT claims
out of that envelope rather than verifying tokens ourselves; the existing
``cognito-rest-api`` Lambda uses the same pattern.

The ``cognito:groups`` claim is delivered as a string with whitespace- and
comma-separated group names depending on the API Gateway integration; we
split on whitespace to be safe and pass the resulting set to the route
handler.
"""

from __future__ import annotations

import os
from typing import Any


def get_claims(event: dict) -> dict[str, Any]:
    """Return the claims attached by the API Gateway Cognito authorizer.

    Returns an empty dict when the authorizer is absent. The caller is
    responsible for treating an empty claims dict as unauthenticated.
    """
    return event.get("requestContext", {}).get("authorizer", {}).get("claims", {}) or {}


def get_user_id(event: dict) -> str | None:
    """Return the Cognito ``sub`` for the caller, or ``None`` if absent."""
    sub = get_claims(event).get("sub")
    return sub if isinstance(sub, str) and sub else None


def get_user_email(event: dict) -> str | None:
    """Return the caller's verified email claim, or ``None`` if absent."""
    email = get_claims(event).get("email")
    return email if isinstance(email, str) and email else None


def get_groups(event: dict) -> set[str]:
    """Return the set of Cognito groups the caller belongs to.

    The ``cognito:groups`` claim can arrive as a list (REST API authorizer)
    or as a bracketed string ``"[group1 group2]"`` (HTTP API authorizer).
    Normalize both shapes to a plain set of group names.
    """
    raw = get_claims(event).get("cognito:groups")
    if raw is None:
        return set()
    if isinstance(raw, list):
        return {g for g in raw if isinstance(g, str) and g}
    if isinstance(raw, str):
        cleaned = raw.strip().lstrip("[").rstrip("]")
        return {part for part in cleaned.replace(",", " ").split() if part}
    return set()


def is_authentication_required() -> bool:
    """Default to enforcing authentication; opt out with REQUIRE_AUTHORIZER=false.

    The existing ``cognito-rest-api`` Lambda defaults this OFF for legacy
    parity. New endpoints handling contributor uploads default it ON: there
    is no legacy parity to preserve and the contributor flow expects an
    authenticated principal on every request.
    """
    return os.environ.get("REQUIRE_AUTHORIZER", "true").lower() != "false"
