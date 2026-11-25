import pytest

from broker.cloud import CapacityUnavailable, QuotaExceeded
from broker.sim.fake_provider import FakeProvider, ProviderConfig
from broker.store import Store

MIN = 60.0
HOUR = 3600.0


def reliable(**overrides) -> ProviderConfig:
    """A provider with no random launch failures, for tests that need certainty."""
    base = dict(
        baseline_failure_prob=0.0,
        outage_start_prob_per_hour=0.0,
        interruptible_outage_start_prob_per_hour=0.0,
        never_ready_prob=0.0,
    )
    base.update(overrides)
    return ProviderConfig(**base)


def test_node_boots_within_4_to_8_minutes():
    p = FakeProvider(reliable())
    for i in range(50):
        node_id = p.launch_node("gpu-s", now=0.0, token=f"t{i}")
        assert p.describe(node_id, 4 * MIN - 1).status == "pending"
        assert p.describe(node_id, 8 * MIN).status == "running"
        p.terminate_node(node_id, 8 * MIN)


def test_launch_is_idempotent_on_token():
    p = FakeProvider(reliable())
    assert p.launch_node("gpu-m", 0.0, token="abc") == p.launch_node("gpu-m", 5.0, token="abc")
    assert len(p.list_nodes(10.0)) == 1


def test_quota_of_ten_alive_nodes():
    p = FakeProvider(reliable())
    ids = [p.launch_node("gpu-s", 0.0, token=f"t{i}") for i in range(10)]
    with pytest.raises(QuotaExceeded):
        p.launch_node("gpu-s", 0.0, token="t11")
    p.terminate_node(ids[0], 60.0)
    p.launch_node("gpu-s", 60.0, token="t11")


def test_never_ready_node_fails_after_15_minutes_and_is_billed():
    p = FakeProvider(reliable(never_ready_prob=1.0))
    node_id = p.launch_node("gpu-l", 0.0, token="x")
    assert p.describe(node_id, 14 * MIN).status == "pending"
    status = p.describe(node_id, 15 * MIN)
    assert status.status == "terminated" and status.termination_reason == "never_ready"
    assert p.billed_cost(now=HOUR) == pytest.approx(8.5 * 0.25)


def test_minimum_billing_is_ten_minutes():
    p = FakeProvider(reliable())
    node_id = p.launch_node("gpu-s", 0.0, token="x")
    p.terminate_node(node_id, 60.0)
    assert p.billed_cost(now=HOUR) == pytest.approx(1.0 * 10 / 60)


def test_interruptible_costs_35_percent_and_gets_notice_before_reclaim():
    p = FakeProvider(reliable(reclaim_rate_per_node_hour=1000.0))  # reclaim almost at once
    node_id = p.launch_node("gpu-s", 0.0, token="x", interruptible=True)
    node = p._nodes[node_id]
    assert node.price_per_hour == pytest.approx(0.35)
    assert node.reclaim_at - node.notice_at == pytest.approx(2 * MIN)
    assert p.describe(node_id, node.notice_at).status == "reclaim_notice"
    assert p.describe(node_id, node.reclaim_at).termination_reason == "reclaimed"


def test_reclaim_rate_is_about_five_percent_of_node_hours():
    p = FakeProvider(reliable())
    lifetimes = []
    for i in range(2000):
        node_id = p.launch_node("gpu-s", 0.0, token=f"t{i}", interruptible=True)
        node = p._nodes[node_id]
        lifetimes.append(node.notice_at - node.ready_at)
        p.terminate_node(node_id, 0.0)
    mean_hours = sum(lifetimes) / len(lifetimes) / HOUR
    assert 17 < mean_hours < 23  # one reclaim per ~20 node-hours


def test_capacity_failures_come_in_bursts():
    p = FakeProvider(ProviderConfig(seed=1, never_ready_prob=0.0))
    outcomes = []
    for minute in range(7 * 24 * 60):
        try:
            node_id = p.launch_node("gpu-l", minute * MIN, token=f"t{minute}")
            p.terminate_node(node_id, minute * MIN)
            outcomes.append(False)
        except CapacityUnavailable:
            outcomes.append(True)
    failures = sum(outcomes)
    after_failure = [b for a, b in zip(outcomes, outcomes[1:], strict=False) if a]
    assert 0 < failures < len(outcomes) / 2
    # A failure makes the next attempt far more likely to fail than average.
    assert sum(after_failure) / len(after_failure) > 3 * failures / len(outcomes)


def test_state_survives_restart(tmp_path):
    path = str(tmp_path / "provider.db")
    p = FakeProvider(reliable(), Store(path))
    node_id = p.launch_node("gpu-m", 0.0, token="x")
    p2 = FakeProvider(reliable(), Store(path))
    assert p2.describe(node_id, HOUR).status == "running"
    assert p2.billed_cost(HOUR) == pytest.approx(4.5)
