"""Domain types shared by the broker, the fake provider and the API."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


PRIORITY_RANK = {Priority.LOW: 0, Priority.NORMAL: 1, Priority.HIGH: 2}


class NodeState(str, Enum):
    LAUNCHING = "launching"  # intent recorded, provider call not yet confirmed
    BOOTING = "booting"
    READY = "ready"
    DRAINING = "draining"  # interruptible node received its reclaim notice
    TERMINATED = "terminated"


ALIVE_NODE_STATES = {NodeState.LAUNCHING, NodeState.BOOTING, NodeState.READY, NodeState.DRAINING}


@dataclass(frozen=True)
class NodeType:
    name: str
    gpus: int
    price_per_hour: float


NODE_TYPES: dict[str, NodeType] = {
    "gpu-s": NodeType("gpu-s", 1, 1.00),
    "gpu-m": NodeType("gpu-m", 4, 4.50),
    "gpu-l": NodeType("gpu-l", 8, 8.50),
}
NODE_TYPES_BY_SIZE = sorted(NODE_TYPES.values(), key=lambda t: t.gpus)
INTERRUPTIBLE_PRICE_FACTOR = 0.35
ALLOWED_GPUS = (1, 2, 4, 8)


def node_price(type_name: str, interruptible: bool) -> float:
    price = NODE_TYPES[type_name].price_per_hour
    return price * INTERRUPTIBLE_PRICE_FACTOR if interruptible else price


@dataclass
class Job:
    id: str
    team: str
    gpus: int
    estimated_minutes: int
    priority: Priority
    submitted_at: float
    status: JobStatus = JobStatus.QUEUED
    idempotency_key: str | None = None
    node_id: str | None = None  # set while reserved on a booting node or running
    started_at: float | None = None
    ended_at: float | None = None
    # Simulation only: how long the job really runs and how it ends. None means
    # the job ends only when a client reports completion through the API.
    actual_minutes: float | None = None
    planned_outcome: JobStatus | None = None
    attempts: int = 0
    queued_since: float | None = None
    queued_seconds: float = 0.0  # total time spent waiting, across attempts
    first_started_at: float | None = None
    pinned_on_demand: bool = False  # set after losing a run to a reclaim
    seq: int = 0

    def __post_init__(self) -> None:
        self.priority = Priority(self.priority)
        self.status = JobStatus(self.status)
        if self.planned_outcome is not None:
            self.planned_outcome = JobStatus(self.planned_outcome)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Node:
    id: str  # broker-side id, also the idempotent client token sent to the provider
    team: str
    type: str
    interruptible: bool
    price_per_hour: float
    state: NodeState
    launched_at: float
    provider_id: str | None = None
    ready_at: float | None = None
    terminated_at: float | None = None
    idle_since: float | None = None
    termination_reason: str | None = None

    def __post_init__(self) -> None:
        self.state = NodeState(self.state)

    @property
    def gpus(self) -> int:
        return NODE_TYPES[self.type].gpus

    @property
    def alive(self) -> bool:
        return self.state in ALIVE_NODE_STATES

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Run:
    """One attempt of a job on a node. Cost and GPU-hours are computed from runs."""

    id: str
    job_id: str
    node_id: str
    team: str
    gpus: int
    started_at: float
    ended_at: float | None = None
    outcome: str | None = None  # succeeded | failed | cancelled | lost

    def to_dict(self) -> dict:
        return asdict(self)
