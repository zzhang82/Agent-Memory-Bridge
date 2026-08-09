from __future__ import annotations

from typing import Any

GOVERNED_V2_VERIFICATION_PROFILE = "governed-v2"


def is_strong_verified_outcome(outcome: Any) -> bool:
    """Return whether an outcome carries the governed-v2 authority required for strong use.

    Schema-v8/v1 outcomes do not have server-minted verification receipts. They remain
    readable as declared outcomes, but cannot authorize regression targets, consolidation,
    utility credit, or future evaluated exports.
    """

    return (
        _value(outcome, "outcome_type") == "verified_success"
        and _value(outcome, "verification_profile") == GOVERNED_V2_VERIFICATION_PROFILE
        and bool(_value(outcome, "verification_receipt_id"))
    )


def outcome_authority_class(outcome: Any) -> str:
    if is_strong_verified_outcome(outcome):
        return "strong_verified"
    if _value(outcome, "outcome_type") == "verified_success":
        return "legacy_declared"
    return "observational"


def _value(outcome: Any, key: str) -> Any:
    if outcome is None:
        return None
    if isinstance(outcome, dict):
        return outcome.get(key)
    keys = getattr(outcome, "keys", None)
    if callable(keys) and key in keys():
        return outcome[key]
    return getattr(outcome, key, None)
