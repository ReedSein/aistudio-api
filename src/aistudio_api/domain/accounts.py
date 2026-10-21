"""Known account benefits; absence is an unknown value, never Free."""

from enum import StrEnum
from typing import ClassVar


class BenefitTier(StrEnum):
    FREE = "Free"
    PLUS = "Plus"
    PRO = "Pro"
    ULTRA = "Ultra"


class TierRefreshError(Exception):
    """Stable, credential-free categories for account metadata refresh failures."""

    STATUS: ClassVar[dict[str, int]] = {
        "not_found": 404,
        "stale": 409,
        "rate_limited": 429,
        "authentication": 502,
        "invalid_response": 502,
        "unavailable": 503,
        "credentials": 503,
        "storage": 503,
    }

    def __init__(self, reason: str):
        self.reason = reason
        self.status_code = self.STATUS[reason]
        super().__init__(reason)
