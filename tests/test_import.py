def test_import_package() -> None:
    import reflexor  # noqa: F401


def test_import_version() -> None:
    import reflexor
    from reflexor.version import __version__

    assert isinstance(__version__, str)
    assert reflexor.__version__ == __version__
    assert reflexor.get_version() == __version__


def test_executor_state_shim_points_to_domain() -> None:
    from reflexor.domain.execution_state import (
        complete_canceled as complete_canceled_domain,
    )
    from reflexor.executor.state import (
        complete_canceled as complete_canceled_executor,
    )

    assert complete_canceled_executor is complete_canceled_domain


def test_executor_idempotency_shim_points_to_storage() -> None:
    from reflexor.executor.idempotency import (
        OutcomeToCache as ExecutorOutcomeToCache,
    )
    from reflexor.storage.idempotency import (
        OutcomeToCache as StorageOutcomeToCache,
    )

    assert ExecutorOutcomeToCache is StorageOutcomeToCache


def test_api_metrics_shim_points_to_observability() -> None:
    from reflexor.api.metrics import ApiMetrics
    from reflexor.observability.metrics import ReflexorMetrics

    assert ApiMetrics is ReflexorMetrics
