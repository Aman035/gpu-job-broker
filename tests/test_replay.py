from broker.config import BrokerConfig
from broker.models import TERMINAL_STATUSES
from broker.provider import ProviderConfig
from broker.replay import DEFAULT_TRACE, load_trace, percentile, replay


def test_trace_is_sorted_and_duplicates_collapsed():
    trace = load_trace(DEFAULT_TRACE)
    assert trace.duplicates == 13
    times = [r.submitted_at for r in trace.rows]
    assert times == sorted(times)
    assert len(load_trace(DEFAULT_TRACE, keep_duplicates=True).rows) == 494


def test_full_replay_finishes_every_job_and_matches_provider_bill():
    trace = load_trace(DEFAULT_TRACE)
    result = replay(trace, BrokerConfig(), ProviderConfig(seed=1))
    broker = result.broker
    assert [line for line, _ in result.rejected] == [88, 393]  # gpus=3 and gpus=16
    assert len(broker.jobs) == len(trace.rows) - 2
    assert all(j.status in TERMINAL_STATUSES for j in broker.jobs.values())
    assert not broker.alive_nodes()
    report = broker.cost_report()
    assert abs(report.total_cost - broker.provider.billed_cost(broker.now)) < 1e-6


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([5], 0.9) == 5
