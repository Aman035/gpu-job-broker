"""Broker configuration. Every policy knob discussed in DECISIONS.md lives here."""
from __future__ import annotations

from dataclasses import dataclass, field

MINUTE = 60.0
HOUR = 3600.0


@dataclass
class BrokerConfig:
    policy: str = "fairshare"  # "fairshare" or "fifo"
    max_nodes: int = 10
    idle_timeout_s: float = 10 * MINUTE
    min_billing_s: float = 10 * MINUTE
    boot_timeout_s: float = 9 * MINUTE  # provider boots within 8 minutes
    fairshare_half_life_s: float = 4 * HOUR
    low_priority_aging_s: float = 60 * MINUTE
    use_interruptible: bool = True
    backoff_base_s: float = 1 * MINUTE
    backoff_max_s: float = 30 * MINUTE
    team_weights: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.policy not in ("fairshare", "fifo"):
            raise ValueError(f"unknown policy {self.policy!r}")
