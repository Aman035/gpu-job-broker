"""The contract between the broker and a cloud provider.

The broker only talks to this interface. `broker.sim.fake_provider` implements
it for tests and replay; a real adapter (EC2, GCE, ...) would implement the
same methods and map its own errors onto the ones below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    pass


class CapacityUnavailable(ProviderError):
    """The provider has no capacity of the requested type right now."""


class QuotaExceeded(ProviderError):
    """The account already has the maximum number of nodes alive."""


@dataclass
class NodeStatus:
    id: str
    token: str  # the client token the node was launched with
    status: str  # pending | running | reclaim_notice | terminated
    launched_at: float
    ready_at: float | None
    terminated_at: float | None
    termination_reason: str | None


class CloudProvider(Protocol):
    def launch_node(self, type_name: str, now: float, *, token: str, interruptible: bool = False) -> str:
        """Start a node and return its id. Must be idempotent on `token`."""

    def terminate_node(self, node_id: str, now: float) -> None: ...

    def describe(self, node_id: str, now: float) -> NodeStatus:
        """Current state of one node. Raises KeyError if the provider does not know it."""

    def list_nodes(self, now: float) -> list[NodeStatus]: ...

    def next_event_after(self, now: float) -> float | None:
        """Earliest future time a node changes state, or None.

        Only meaningful for a simulated provider, which knows the future. A
        real adapter returns None and the broker polls on a timer instead.
        """
