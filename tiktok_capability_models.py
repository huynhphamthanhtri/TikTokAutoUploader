"""Canonical, redacted TikTok Insights capability models."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple


class CapabilityState(str, Enum):
    PENDING = "PENDING"
    CHECKING = "CHECKING"
    SUCCESS = "SUCCESS"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    PARTIAL = "PARTIAL"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    CHECKPOINT = "CHECKPOINT"
    RATE_LIMITED = "RATE_LIMITED"
    DEVICE_REGISTRATION_REQUIRED = "DEVICE_REGISTRATION_REQUIRED"
    ENDPOINT_CHANGED = "ENDPOINT_CHANGED"
    UNAVAILABLE = "UNAVAILABLE"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class KycState(str, Enum):
    VERIFIED = "VERIFIED"
    PENDING = "PENDING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    NOT_STARTED = "NOT_STARTED"
    UNKNOWN = "UNKNOWN"


class PaymentState(str, Enum):
    LINKED = "LINKED"
    PENDING = "PENDING"
    NOT_LINKED = "NOT_LINKED"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MoneyAmount:
    minor_units: int
    currency: str = ""
    scale: int = 2
    formatted: str = ""

    @property
    def decimal(self):
        return Decimal(self.minor_units).scaleb(-self.scale)


@dataclass(frozen=True)
class DashboardCapability:
    total_amount: Optional[MoneyAmount] = None
    estimated_revenue: Optional[MoneyAmount] = None
    rpm: Optional[MoneyAmount] = None
    qualified_views: Optional[int] = None


@dataclass(frozen=True)
class BalanceCapability:
    balance: Optional[MoneyAmount] = None
    frozen_balance: Optional[MoneyAmount] = None
    total_payable: Optional[MoneyAmount] = None
    payout_threshold: Optional[MoneyAmount] = None
    next_payout_at: str = ""


@dataclass(frozen=True)
class CreativeRewardsRequirement:
    key: str = ""
    status: Optional[int] = None
    description: str = ""


@dataclass(frozen=True)
class CreativeRewardsCapability:
    enabled: Optional[bool] = None
    profile_status: Optional[int] = None
    all_requirements_met: Optional[bool] = None
    requirements: Tuple[CreativeRewardsRequirement, ...] = ()


@dataclass(frozen=True)
class TrafficSource:
    name: str
    percentage: Decimal


@dataclass(frozen=True)
class TrafficCapability:
    sources: Tuple[TrafficSource, ...] = ()


@dataclass(frozen=True)
class KycCapability:
    state: KycState = KycState.UNKNOWN
    created: Optional[bool] = None
    cdd_status: Optional[int] = None
    screen_status: Optional[int] = None
    requires_resubmit: Optional[bool] = None


@dataclass(frozen=True)
class PaymentCapability:
    state: PaymentState = PaymentState.UNKNOWN
    confirmed: Optional[bool] = None
    method_present: Optional[bool] = None
    pi_bind_status: Optional[int] = None
    kyc_status: Optional[int] = None


@dataclass(frozen=True)
class PayoutCapability:
    summary: Optional[MoneyAmount] = None
    flexible_enabled: Optional[bool] = None
    pending_count: int = 0
    breakdown_count: int = 0


@dataclass(frozen=True)
class ViolationsCapability:
    count: int = 0


@dataclass(frozen=True)
class CapabilityResult:
    capability: str
    state: CapabilityState
    value: object = None
    endpoint_id: str = ""
    checked_at: str = ""
    schema_hash: str = ""
    adapter_version: int = 1
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountCapabilities:
    results: Tuple[CapabilityResult, ...] = ()

    def get(self, name):
        for result in self.results:
            if result.capability == name:
                return result
        return None
