import pytest

from broker import policy
from broker.models import Job, Priority

HOUR = 3600.0


def job(team: str, priority: str = "normal", at: float = 0.0, seq: int = 0, gpus: int = 1) -> Job:
    return Job(
        id=f"j{seq}", team=team, gpus=gpus, estimated_minutes=30, priority=Priority(priority), submitted_at=at, seq=seq
    )


def test_low_priority_ages_and_pinned_jobs_are_promoted():
    j = job("a", "low", at=0.0)
    assert policy.effective_priority(j, now=59 * 60, low_aging_s=HOUR) == Priority.LOW
    assert policy.effective_priority(j, now=HOUR, low_aging_s=HOUR) == Priority.NORMAL
    j.pinned_on_demand = True
    assert policy.effective_priority(j, now=0.0, low_aging_s=HOUR) == Priority.NORMAL


def test_fair_share_interleaves_teams_least_used_first():
    jobs = [job("heavy", seq=i) for i in range(3)] + [job("light", seq=10 + i) for i in range(2)]
    ordered = policy.order_fair_share(jobs, usage={"heavy": 100.0, "light": 1.0}, weights={}, now=0, low_aging_s=HOUR)
    assert [j.team for j in ordered] == ["light", "heavy", "light", "heavy", "heavy"]


def test_priority_only_reorders_inside_a_team():
    jobs = [job("heavy", "high", seq=1), job("light", "low", seq=2)]
    ordered = policy.order_fair_share(jobs, usage={"heavy": 100.0}, weights={}, now=0, low_aging_s=HOUR)
    assert ordered[0].team == "light"  # a high job does not jump another team
    assert policy.order_fifo(jobs)[0].team == "heavy"  # FIFO: global priority


def test_team_weight_scales_usage():
    jobs = [job("a", seq=1), job("b", seq=2)]
    ordered = policy.order_fair_share(jobs, usage={"a": 10.0, "b": 15.0}, weights={"b": 2.0}, now=0, low_aging_s=HOUR)
    assert ordered[0].team == "b"


def test_usage_decays_by_half_each_half_life_and_integrates_load():
    after = policy.decay_usage({"a": 100.0}, {}, dt=4 * HOUR, half_life_s=4 * HOUR)
    assert after["a"] == pytest.approx(50.0)
    # 2 GPUs for a very short interval adds about 2 GPU-seconds per second.
    grown = policy.decay_usage({}, {"a": 2}, dt=1.0, half_life_s=4 * HOUR)
    assert grown["a"] == pytest.approx(2.0, rel=1e-3)


def test_slot_share():
    assert policy.slot_share(10, 3) == 4
    assert policy.slot_share(10, 0) == 10


@pytest.mark.parametrize(
    "job_gpus,waiting,booting,alive,pressure,expected",
    [
        (1, 1, 0, 0, None, 1),  # lone small job: smallest node
        (1, 2, 1, 1, None, 3),  # burst: cover booting capacity too
        (1, 20, 0, 0, None, 8),  # capped at the largest node
        (4, 1, 0, 0, None, 4),  # never smaller than the job
        (1, 1, 0, 8, 0.8, 8),  # slot pressure: largest node
        (1, 1, 0, 7, 0.8, 1),  # below the pressure threshold
    ],
)
def test_gpus_to_launch(job_gpus, waiting, booting, alive, pressure, expected):
    assert policy.gpus_to_launch(job_gpus, waiting, booting, alive, 10, pressure) == expected
