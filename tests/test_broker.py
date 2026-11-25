import pytest

from broker.broker import Broker, IdempotencyConflict, InvalidTransition, ValidationError
from broker.config import BrokerConfig
from broker.models import JobStatus, NodeState
from broker.provider import FakeProvider
from broker.store import Store
from conftest import reliable_provider_config

MIN = 60.0


# ---- submission ---------------------------------------------------------------


def test_same_idempotency_key_returns_same_job(make_broker):
    b = make_broker()
    job1, created1 = b.submit("nlp", 1, 30, "normal", idempotency_key="k1")
    job2, created2 = b.submit("nlp", 1, 30, "normal", idempotency_key="k1")
    assert created1 and not created2
    assert job1.id == job2.id
    assert len(b.jobs) == 1


def test_reused_key_with_different_body_is_rejected(make_broker):
    b = make_broker()
    b.submit("nlp", 1, 30, "normal", idempotency_key="k1")
    with pytest.raises(IdempotencyConflict):
        b.submit("nlp", 2, 30, "normal", idempotency_key="k1")


@pytest.mark.parametrize(
    "team,gpus,minutes,priority",
    [("nlp", 3, 30, "normal"), ("nlp", 16, 30, "normal"), ("nlp", 1, 0, "normal"),
     ("", 1, 30, "normal"), ("NLP team", 1, 30, "normal"), ("nlp", 1, 30, "urgent")],
)
def test_invalid_submissions_are_rejected(make_broker, team, gpus, minutes, priority):
    with pytest.raises(ValidationError):
        make_broker().submit(team, gpus, minutes, priority)


# ---- lifecycle ------------------------------------------------------------------


def test_job_runs_through_full_lifecycle(make_broker):
    b = make_broker()
    job, _ = b.submit("nlp", 1, 30, actual_minutes=20)
    assert job.status == JobStatus.QUEUED
    node = b.alive_nodes()[0]
    assert node.type == "gpu-s" and node.state == NodeState.BOOTING

    b.advance(5 * MIN)
    assert job.status == JobStatus.RUNNING and job.started_at == pytest.approx(5 * MIN)

    b.advance(20 * MIN)
    assert job.status == JobStatus.SUCCEEDED
    assert job.queued_seconds == pytest.approx(5 * MIN)


def test_client_reported_completion(make_broker):
    b = make_broker()
    job, _ = b.submit("nlp", 1, 30)
    b.advance(6 * MIN)
    b.complete(job.id, "failed")
    assert job.status == JobStatus.FAILED
    with pytest.raises(InvalidTransition):
        b.complete(job.id, "succeeded")


def test_cancel_queued_and_running_jobs(make_broker):
    b = make_broker()
    queued, _ = b.submit("nlp", 1, 30)
    b.cancel(queued.id)
    assert queued.status == JobStatus.CANCELLED

    running, _ = b.submit("nlp", 1, 30)
    b.advance(6 * MIN)
    assert running.status == JobStatus.RUNNING
    b.cancel(running.id)
    assert running.status == JobStatus.CANCELLED
    assert b.cancel(running.id).status == JobStatus.CANCELLED  # idempotent
    run = b.runs[f"{running.id}#1"]
    assert run.outcome == "cancelled" and run.ended_at == pytest.approx(b.now)


# ---- placement ------------------------------------------------------------------


def test_burst_grows_node_size_instead_of_one_node_per_job(make_broker):
    b = make_broker()
    for _ in range(8):
        b.submit("vision", 1, 30, actual_minutes=30)
    assert [n.type for n in b.alive_nodes()] == ["gpu-s", "gpu-m", "gpu-l"]
    b.advance(5 * MIN)
    assert all(j.status == JobStatus.RUNNING for j in b.jobs.values())


def test_teams_never_share_a_node(make_broker):
    b = make_broker()
    for team in ["nlp", "vision", "nlp", "speech", "vision"]:
        b.submit(team, 1, 30, actual_minutes=60)
    b.advance(10 * MIN)
    for node in b.alive_nodes():
        assert {j.team for j in b.jobs_on(node.id)} <= {node.team}


def test_never_more_than_ten_nodes(make_broker):
    b = make_broker()
    for i in range(40):
        b.submit(f"team{i % 12}", 8, 60, actual_minutes=60)
    for _ in range(30):
        assert sum(1 for n in b.nodes.values() if n.alive) <= 10
        b.advance(5 * MIN)
    b.run_until_idle()
    assert all(j.status == JobStatus.SUCCEEDED for j in b.jobs.values())


# ---- reaper -----------------------------------------------------------------------


def test_idle_node_is_reaped_after_timeout(make_broker):
    b = make_broker(idle_timeout_s=15 * MIN)
    b.submit("nlp", 1, 30, actual_minutes=20)
    b.advance(25 * MIN)  # job ended at 25 min
    node = next(iter(b.nodes.values()))
    assert node.state == NodeState.READY
    b.advance(15 * MIN - 1)
    assert node.state == NodeState.READY
    b.advance(1)
    assert node.state == NodeState.TERMINATED and node.termination_reason == "idle"


def test_idle_node_is_kept_until_minimum_billing_is_used(make_broker):
    b = make_broker(idle_timeout_s=1 * MIN)
    b.submit("nlp", 1, 30, actual_minutes=1)
    b.advance(6 * MIN)  # job ran 5:00-6:00
    node = next(iter(b.nodes.values()))
    b.advance(3 * MIN)
    assert node.state == NodeState.READY  # 9 minutes old: already paid until 10
    b.advance(1 * MIN)
    assert node.state == NodeState.TERMINATED


# ---- provider failures -------------------------------------------------------------


def test_capacity_outage_keeps_jobs_queued_then_recovers(make_broker):
    b = make_broker(reliable_provider_config(baseline_failure_prob=1.0))
    job, _ = b.submit("nlp", 1, 30, actual_minutes=10)
    assert job.status == JobStatus.QUEUED and not b.alive_nodes()
    b.provider.config.baseline_failure_prob = 0.0  # capacity returns
    b.advance(60 * MIN)
    assert job.status == JobStatus.SUCCEEDED


def test_failed_type_backs_off_and_larger_type_is_tried(make_broker):
    b = make_broker()
    real_launch = b.provider.launch_node

    def no_small_nodes(type_name, now, **kw):
        if type_name == "gpu-s":
            from broker.provider import CapacityUnavailable
            raise CapacityUnavailable("none")
        return real_launch(type_name, now, **kw)

    b.provider.launch_node = no_small_nodes
    b.submit("nlp", 1, 30)
    assert [n.type for n in b.alive_nodes()] == ["gpu-m"]


def test_stuck_boot_is_abandoned_and_replaced(make_broker):
    b = make_broker(reliable_provider_config(never_ready_prob=1.0))
    job, _ = b.submit("nlp", 1, 30, actual_minutes=10)
    first = b.alive_nodes()[0]
    b.provider.config.never_ready_prob = 0.0
    b.advance(9 * MIN)
    assert first.termination_reason == "boot_timeout"
    assert job.status == JobStatus.QUEUED and job.node_id is not None  # reserved on replacement
    b.advance(5 * MIN)
    assert job.status == JobStatus.RUNNING


# ---- interruptible ------------------------------------------------------------------


def test_low_priority_uses_interruptible_and_is_requeued_on_reclaim(make_broker):
    b = make_broker(reliable_provider_config(reclaim_rate_per_node_hour=6.0))
    job, _ = b.submit("nlp", 1, 30, "low", actual_minutes=600)
    node = b.alive_nodes()[0]
    assert node.interruptible and node.price_per_hour == pytest.approx(0.35)
    b.advance(4 * 3600)
    assert node.termination_reason == "reclaimed"
    assert job.pinned_on_demand
    assert b.runs[f"{job.id}#1"].outcome == "lost"
    assert not b.nodes[job.node_id].interruptible  # second attempt on regular capacity


def test_normal_priority_never_uses_interruptible(make_broker):
    b = make_broker()
    b.submit("nlp", 1, 30, "normal")
    b.submit("nlp", 1, 30, "high")
    assert not any(n.interruptible for n in b.alive_nodes())


# ---- fairness ----------------------------------------------------------------------


def test_light_team_is_not_stuck_behind_heavy_team_burst(make_broker):
    b = make_broker(use_interruptible=False)
    for _ in range(200):
        b.submit("vision", 8, 120, actual_minutes=120)
    b.advance(30 * MIN)
    late, _ = b.submit("forecast", 1, 30, actual_minutes=10)
    b.advance(150 * MIN)
    assert late.first_started_at is not None
    assert late.first_started_at - late.submitted_at < 130 * MIN


def test_fifo_makes_light_team_wait_for_whole_burst(make_broker):
    b = make_broker(policy="fifo", use_interruptible=False)
    for _ in range(40):
        b.submit("vision", 8, 120, actual_minutes=120)
    b.advance(30 * MIN)
    late, _ = b.submit("forecast", 1, 30, actual_minutes=10)
    b.run_until_idle()
    # 40 jobs, 10 nodes: 4 waves of ~2 hours; the late job starts with the last wave.
    assert late.first_started_at - late.submitted_at > 5 * 60 * MIN


def test_high_priority_reorders_within_team_only(make_broker):
    b = make_broker(max_nodes=1, use_interruptible=False)
    b.submit("vision", 8, 60, actual_minutes=60)
    normal, _ = b.submit("vision", 8, 60, "normal", actual_minutes=60)
    high, _ = b.submit("vision", 8, 60, "high", actual_minutes=60)
    other, _ = b.submit("nlp", 8, 60, "normal", actual_minutes=60)
    b.run_until_idle()
    assert high.first_started_at < normal.first_started_at
    assert other.first_started_at <= high.first_started_at  # vision used more recently


def test_low_priority_ages_into_normal(make_broker):
    b = make_broker(use_interruptible=False)
    job, _ = b.submit("nlp", 1, 30, "low")
    assert b.effective_priority(job).value == "low"
    b.advance(61 * MIN)
    assert b.effective_priority(job).value == "normal"


# ---- restart safety ----------------------------------------------------------------


def test_restart_keeps_jobs_nodes_and_cost(tmp_path):
    broker_db, provider_db = str(tmp_path / "broker.db"), str(tmp_path / "provider.db")
    pcfg = reliable_provider_config()

    b1 = Broker(FakeProvider(pcfg, Store(provider_db)), Store(broker_db), BrokerConfig())
    done, _ = b1.submit("nlp", 1, 30, actual_minutes=10, idempotency_key="a")
    running, _ = b1.submit("vision", 2, 60, actual_minutes=120, idempotency_key="b")
    b1.advance(20 * MIN)
    report_before = b1.cost_report().to_dict()
    snapshot = {j.id: j.status for j in b1.jobs.values()}
    del b1

    b2 = Broker(FakeProvider(pcfg, Store(provider_db)), Store(broker_db), BrokerConfig())
    assert b2.now == pytest.approx(20 * MIN)
    assert {j.id: j.status for j in b2.jobs.values()} == snapshot
    assert b2.cost_report().to_dict() == report_before
    assert b2.submit("nlp", 1, 30, actual_minutes=10, idempotency_key="a")[0].id == done.id
    b2.run_until_idle()
    assert b2.get_job(running.id).status == JobStatus.SUCCEEDED
    assert b2.cost_report().total_cost == pytest.approx(b2.provider.billed_cost(b2.now))


def test_crash_between_launch_and_record_is_reconciled(tmp_path):
    provider = FakeProvider(reliable_provider_config(), Store(str(tmp_path / "p.db")))
    store = Store(str(tmp_path / "b.db"))
    b1 = Broker(provider, store, BrokerConfig())
    b1.submit("nlp", 1, 30, actual_minutes=10)
    node = b1.alive_nodes()[0]
    # Simulate a crash right after the provider call: the record still says LAUNCHING.
    node.state, node.provider_id = NodeState.LAUNCHING, None
    store.put("node", node.id, node.to_dict())
    store.commit()
    orphan = provider.launch_node("gpu-s", 0.0, token="never-recorded")

    b2 = Broker(FakeProvider(provider.config, Store(str(tmp_path / "p.db"))), Store(str(tmp_path / "b.db")))
    adopted = b2.nodes[node.id]
    assert adopted.state == NodeState.BOOTING and adopted.provider_id is not None
    assert b2.provider.describe(orphan, b2.now).status == "terminated"
    b2.run_until_idle()
    assert all(j.status == JobStatus.SUCCEEDED for j in b2.jobs.values())
