from pyrallel_consumer.dto import ResourceSignalStatus
from pyrallel_consumer.resource_signals import NullResourceSignalProvider


def test_null_resource_signal_provider_returns_fail_open_unavailable_snapshot() -> None:
    provider = NullResourceSignalProvider()

    snapshot = provider.snapshot()

    assert snapshot.status == ResourceSignalStatus.UNAVAILABLE
    assert snapshot.cpu_utilization is None
    assert snapshot.memory_utilization is None
    assert snapshot.is_actionable_for_tuning is False


def test_resource_signal_non_available_states_are_not_actionable_for_tuning() -> None:
    provider = NullResourceSignalProvider()
    snapshot_type = type(provider.snapshot())

    for status in (
        ResourceSignalStatus.UNAVAILABLE,
        ResourceSignalStatus.STALE,
        ResourceSignalStatus.FIRST_SAMPLE_PENDING,
    ):
        snapshot = snapshot_type(status=status)
        assert snapshot.is_actionable_for_tuning is False

    assert (
        snapshot_type(
            status=ResourceSignalStatus.AVAILABLE,
            cpu_utilization=0.5,
            memory_utilization=0.75,
        ).is_actionable_for_tuning
        is True
    )
