import pytest

MIN = 60.0
HOUR = 3600.0


def test_cost_splits_used_and_idle(make_broker):
    b = make_broker(idle_timeout_s=10 * MIN)
    b.submit("nlp", 1, 60, actual_minutes=60)
    b.run_until_idle()
    # Node: boot 5 min + run 60 min + idle 10 min = 75 min of gpu-s at 1.00/h.
    t = b.cost_report().teams["nlp"]
    assert t.gpu_hours == pytest.approx(1.0)
    assert t.used_cost == pytest.approx(1.0)
    assert t.idle_cost == pytest.approx(15 / 60)
    assert t.total_cost == pytest.approx(b.provider.billed_cost(b.now))


def test_partly_filled_node_counts_unused_gpus_as_idle(make_broker):
    b = make_broker()
    b.submit("vision", 2, 60, actual_minutes=60)  # gets a gpu-m (4 GPUs)
    b.advance(65 * MIN)
    t = b.cost_report().teams["vision"]
    assert t.gpu_hours == pytest.approx(2.0)
    assert t.used_cost == pytest.approx(4.5 / 2)  # half the node for one hour


def test_minimum_billing_top_up_is_charged(make_broker):
    b = make_broker(idle_timeout_s=0)
    job, _ = b.submit("nlp", 1, 1)
    b.cancel(job.id)  # node no longer needed while booting
    b.advance(HOUR)
    report = b.cost_report()
    assert report.total_cost == pytest.approx(10 / 60)
    assert report.total_cost == pytest.approx(b.provider.billed_cost(b.now))


def test_window_only_counts_overlap(make_broker):
    b = make_broker()
    b.submit("nlp", 1, 120, actual_minutes=120)
    b.run_until_idle()
    first_hour = b.cost_report(0, HOUR).teams["nlp"]
    assert first_hour.gpu_hours == pytest.approx(55 / 60)
    whole = b.cost_report().teams["nlp"]
    assert whole.gpu_hours == pytest.approx(2.0)
