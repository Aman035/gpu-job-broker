import pytest

from broker.broker import Broker
from broker.config import BrokerConfig
from broker.provider import FakeProvider, ProviderConfig
from broker.store import Store


def reliable_provider_config(**overrides) -> ProviderConfig:
    """No random failures and a fixed 5-minute boot, so tests can assert exact times."""
    base = dict(
        baseline_failure_prob=0.0,
        outage_start_prob_per_hour=0.0,
        interruptible_outage_start_prob_per_hour=0.0,
        never_ready_prob=0.0,
        boot_min_s=300.0,
        boot_max_s=300.0,
        reclaim_rate_per_node_hour=0.0001,
    )
    base.update(overrides)
    return ProviderConfig(**base)


@pytest.fixture
def make_broker():
    def factory(provider_config: ProviderConfig | None = None, **config) -> Broker:
        provider = FakeProvider(provider_config or reliable_provider_config())
        return Broker(provider, Store(), BrokerConfig(**config))

    return factory
