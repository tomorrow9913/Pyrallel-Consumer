from __future__ import annotations

import asyncio
import textwrap
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from pyrallel_consumer.dto import TopicPartition, WorkItem


def _write_module(package_dir: Path, name: str, body: str) -> None:
    (package_dir / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")


def _make_workload_package(tmp_path: Path) -> tuple[Path, str]:
    package_name = f"workload_fixtures_{tmp_path.name.replace('-', '_')}"
    package_dir = tmp_path / package_name
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    return package_dir, package_name


def _work_item(payload: bytes = b"payload") -> WorkItem:
    return WorkItem(
        id="item-1",
        tp=TopicPartition(topic="topic", partition=0),
        offset=0,
        epoch=0,
        key="key-1",
        payload=payload,
    )


def _picklable_baseline_worker(payload: bytes) -> None:
    payload.decode("utf-8")


async def _picklable_async_worker(item: WorkItem) -> None:
    (item.payload or b"").decode("utf-8")


def _picklable_process_worker(item: WorkItem) -> None:
    (item.payload or b"").decode("utf-8")


def _noop_workload_class(name: str, label: str = "Demo") -> str:
    return f"""
        from dataclasses import dataclass

        from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext


        @dataclass(frozen=True, slots=True)
        class {label}Options:
            pass

        def baseline(payload: bytes) -> None:
            payload.decode("utf-8")

        async def async_worker(item) -> None:
            (item.payload or b"").decode("utf-8")

        def process_worker(item) -> None:
            (item.payload or b"").decode("utf-8")

        class {label}Workload(BenchmarkWorkload):
            name = "{name}"
            label = "{label}"
            description = "{label} workload"
            options_type = {label}Options

            def baseline_worker(self, context: WorkloadContext[{label}Options]):
                return baseline

            def async_worker(self, context: WorkloadContext[{label}Options]):
                return async_worker

            def process_worker(self, context: WorkloadContext[{label}Options]):
                return process_worker
    """


@dataclass(frozen=True, slots=True)
class _TestOptions:
    value: int = field(default=3)


def test_builtin_workloads_are_discovered_as_available() -> None:
    from benchmarks.workloads import (
        all_records,
        available_names,
        get_available,
        records,
    )
    from benchmarks.workloads.base import BenchmarkWorkload

    assert available_names() == ("sleep", "cpu", "io")
    assert records() == all_records()

    records_by_name = {record.name: record for record in all_records()}
    assert set(records_by_name) >= {"sleep", "cpu", "io"}
    for name in ("sleep", "cpu", "io"):
        assert records_by_name[name].available is True
        assert records_by_name[name].error is None
        assert (
            records_by_name[name].workload_cls is records_by_name[name].workload_class
        )
        assert issubclass(get_available(name), BenchmarkWorkload)


def test_builtin_workloads_expose_valid_option_schemas() -> None:
    from benchmarks.workloads import all_records
    from benchmarks.workloads.base import describe_workload_options

    by_name = {record.name: record for record in all_records()}

    sleep_schema = describe_workload_options(by_name["sleep"].workload_cls)
    cpu_schema = describe_workload_options(by_name["cpu"].workload_cls)
    io_schema = describe_workload_options(by_name["io"].workload_cls)

    assert sleep_schema[0].canonical_name == "sleep.sleep_ms"
    assert sleep_schema[0].default == 0.5
    assert sleep_schema[0].metadata.legacy_flags == ("--worker-sleep-ms",)
    assert cpu_schema[0].canonical_name == "cpu.iterations"
    assert cpu_schema[0].default == 1000
    assert io_schema[0].canonical_name == "io.sleep_ms"


def test_build_workload_options_coerces_and_validates_values() -> None:
    from benchmarks.workloads.base import build_workload_options
    from benchmarks.workloads.sleep import SleepWorkload

    options = build_workload_options(
        SleepWorkload,
        workload_options={"sleep": {"sleep_ms": "1.25"}},
    )

    assert options.sleep_ms == 1.25

    with pytest.raises(ValueError, match="sleep.sleep_ms"):
        build_workload_options(
            SleepWorkload,
            workload_options={"sleep": {"sleep_ms": "nan"}},
        )


@pytest.mark.parametrize("workload", ["sleep", "cpu", "io"])
def test_select_workers_preserves_three_callable_tuple_contract(workload: str) -> None:
    from benchmarks.workloads import select_workers

    baseline_worker, async_worker, process_worker = select_workers(
        workload=workload,
        sleep_ms=0,
        cpu_iterations=1,
        io_sleep_ms=0,
    )

    assert callable(baseline_worker)
    assert callable(async_worker)
    assert callable(process_worker)
    baseline_worker(b'{"key":"value"}')
    asyncio.run(
        cast(
            Coroutine[Any, Any, None],
            async_worker(_work_item(b'{"key":"value"}')),
        )
    )
    process_worker(_work_item(b'{"key":"value"}'))


def test_select_workers_rejects_non_callable_worker_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchmarks.workloads as workloads
    from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext

    class BadWorkload(BenchmarkWorkload):
        name = "bad"
        label = "Bad"
        description = "Returns an invalid baseline worker"

        def baseline_worker(self, context: WorkloadContext) -> object:  # type: ignore[override]
            return None

        def async_worker(self, context: WorkloadContext):
            return _picklable_async_worker

        def process_worker(self, context: WorkloadContext):
            return _picklable_process_worker

    monkeypatch.setattr(workloads, "get_available", lambda name: BadWorkload)

    with pytest.raises(ValueError, match="non-callable"):
        workloads.select_workers(
            workload="bad",
            sleep_ms=0,
            cpu_iterations=1,
            io_sleep_ms=0,
        )


def test_select_workers_rejects_non_picklable_worker_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchmarks.workloads as workloads
    from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext

    class BadWorkload(BenchmarkWorkload):
        name = "bad"
        label = "Bad"
        description = "Returns a non-picklable worker"

        def baseline_worker(self, context: WorkloadContext):
            return lambda payload: None

        def async_worker(self, context: WorkloadContext):
            async def run(item) -> None:
                return None

            return run

        def process_worker(self, context: WorkloadContext):
            def run(item) -> None:
                return None

            return run

    monkeypatch.setattr(workloads, "get_available", lambda name: BadWorkload)

    with pytest.raises(ValueError, match="non-picklable"):
        workloads.select_workers(
            workload="bad",
            sleep_ms=0,
            cpu_iterations=1,
            io_sleep_ms=0,
        )


def test_select_workers_allows_non_picklable_in_process_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchmarks.workloads as workloads
    from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext

    class ClosureFriendlyWorkload(BenchmarkWorkload):
        name = "closure_friendly"
        label = "Closure Friendly"
        description = "Uses closures for in-process workers only"

        def baseline_worker(self, context: WorkloadContext):
            multiplier = 2

            def run(payload: bytes) -> None:
                payload.decode("utf-8")
                assert multiplier == 2

            return run

        def async_worker(self, context: WorkloadContext):
            marker = "ok"

            async def run(item: WorkItem) -> None:
                (item.payload or b"").decode("utf-8")
                assert marker == "ok"

            return run

        def process_worker(self, context: WorkloadContext):
            return _picklable_process_worker

    monkeypatch.setattr(
        workloads, "get_available", lambda name: ClosureFriendlyWorkload
    )

    baseline_worker, async_worker, process_worker = workloads.select_workers(
        workload="closure_friendly",
        sleep_ms=0,
        cpu_iterations=1,
        io_sleep_ms=0,
    )

    baseline_worker(b'{"key":"value"}')
    asyncio.run(
        cast(
            Coroutine[Any, Any, None],
            async_worker(_work_item(b'{"key":"value"}')),
        )
    )
    process_worker(_work_item(b'{"key":"value"}'))


def test_get_available_unknown_workload_reports_clear_error() -> None:
    from benchmarks.workloads import get_available

    with pytest.raises(ValueError, match="Unknown workload"):
        get_available("missing")


def test_workload_registry_facade_caches_discovery_until_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchmarks.workloads as workloads
    from benchmarks.workloads.registry import WorkloadRecord, WorkloadRegistry

    calls = 0

    def fake_discover_workloads() -> WorkloadRegistry:
        nonlocal calls
        calls += 1
        return WorkloadRegistry(
            (
                WorkloadRecord(
                    name=f"demo_{calls}",
                    label="Demo",
                    description="Demo workload",
                    module_name="demo",
                    available=False,
                ),
            )
        )

    workloads.reset_registry_cache()
    monkeypatch.setattr(workloads, "discover_workloads", fake_discover_workloads)
    try:
        assert workloads.all_records()[0].name == "demo_1"
        assert workloads.records()[0].name == "demo_1"
        assert calls == 1

        workloads.reset_registry_cache()

        assert workloads.all_records()[0].name == "demo_2"
        assert calls == 2
    finally:
        workloads.reset_registry_cache()


def test_registry_discovers_multiple_workload_classes_in_one_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads_from

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(
        package_dir,
        "combined",
        _noop_workload_class("alpha", "Alpha")
        + "\n"
        + _noop_workload_class("beta", "Beta"),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads_from(package_dir, package_name)

    assert registry.available_names() == ("alpha", "beta")
    assert registry.get_available("alpha").name == "alpha"
    assert registry.get_available("beta").name == "beta"


def test_duplicate_workload_names_are_visible_unavailable_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(package_dir, "first", _noop_workload_class("dupe", "First"))
    _write_module(package_dir, "second", _noop_workload_class("dupe", "Second"))
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    duplicate_records = [
        record for record in registry.all_records() if record.name == "dupe"
    ]
    assert len(duplicate_records) == 1
    assert duplicate_records[0].available is False
    assert duplicate_records[0].error is not None
    assert "duplicate" in duplicate_records[0].error.lower()
    assert package_name in duplicate_records[0].error
    with pytest.raises(ValueError, match="unavailable"):
        registry.get_available("dupe")


def test_import_failures_are_visible_using_file_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(package_dir, "broken_import", 'raise RuntimeError("boom")')
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    [record] = registry.all_records()
    assert record.name == "broken_import"
    assert record.available is False
    assert record.error is not None
    assert "import" in record.error.lower()
    assert "boom" in record.error


def test_invalid_workload_class_is_visible_with_short_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(
        package_dir,
        "invalid_class",
        """
        from dataclasses import dataclass

        from benchmarks.workloads.base import BenchmarkWorkload

        @dataclass(frozen=True, slots=True)
        class InvalidOptions:
            pass

        class InvalidWorkload(BenchmarkWorkload):
            name = "invalid"
            label = "Invalid"
            description = "Missing worker methods"
            options_type = InvalidOptions
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    [record] = registry.all_records()
    assert record.name == "invalid"
    assert record.available is False
    assert record.error is not None
    assert "baseline_worker" in record.error


def test_hyphenated_workload_name_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(package_dir, "bad_name", _noop_workload_class("bad-name", "BadName"))
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    [record] = registry.all_records()
    assert record.name == "bad-name"
    assert record.available is False
    assert record.error is not None
    assert "underscores" in record.error


@pytest.mark.parametrize("module_name", ["__init__", "base", "registry"])
def test_infrastructure_modules_are_not_exposed_as_workloads(
    module_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(package_dir, module_name, _noop_workload_class(module_name, "Hidden"))
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    assert registry.all_records() == ()
    assert registry.available_names() == ()


def test_api_module_name_is_available_for_custom_workloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(package_dir, "api", _noop_workload_class("api", "Api"))
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    assert registry.available_names() == ("api",)
    assert registry.get_available("api").name == "api"


def test_workload_context_carries_builtin_parameters() -> None:
    from benchmarks.workloads.base import WorkloadContext

    context = WorkloadContext(options=_TestOptions(value=7))

    assert context.options.value == 7


def test_option_schema_requires_dataclass_options_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(
        package_dir,
        "bad_options",
        """
        from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext

        def baseline(payload: bytes) -> None:
            return None

        async def async_worker(item) -> None:
            return None

        def process_worker(item) -> None:
            return None

        class BadOptionsWorkload(BenchmarkWorkload):
            name = "bad_options"
            label = "Bad Options"
            description = "Bad options workload"
            options_type = object

            def baseline_worker(self, context: WorkloadContext):
                return baseline

            def async_worker(self, context: WorkloadContext):
                return async_worker

            def process_worker(self, context: WorkloadContext):
                return process_worker
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    [record] = registry.all_records()
    assert record.available is False
    assert record.error is not None
    assert "options_type" in record.error


def test_option_schema_rejects_missing_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(
        package_dir,
        "missing_metadata",
        """
        from dataclasses import dataclass

        from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext

        @dataclass(frozen=True, slots=True)
        class MissingMetadataOptions:
            value: int = 1

        def baseline(payload: bytes) -> None:
            return None

        async def async_worker(item) -> None:
            return None

        def process_worker(item) -> None:
            return None

        class MissingMetadataWorkload(BenchmarkWorkload):
            name = "missing_metadata"
            label = "Missing Metadata"
            description = "Missing metadata workload"
            options_type = MissingMetadataOptions

            def baseline_worker(self, context: WorkloadContext):
                return baseline

            def async_worker(self, context: WorkloadContext):
                return async_worker

            def process_worker(self, context: WorkloadContext):
                return process_worker
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    [record] = registry.all_records()
    assert record.available is False
    assert record.error is not None
    assert "workload_option" in record.error


def test_option_schema_rejects_unsupported_field_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(
        package_dir,
        "unsupported_option",
        """
        from dataclasses import dataclass, field

        from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext, WorkloadOptionMetadata

        @dataclass(frozen=True, slots=True)
        class UnsupportedOptions:
            values: tuple[str, ...] = field(default=(), metadata={"workload_option": WorkloadOptionMetadata(label="Values")})

        def baseline(payload: bytes) -> None:
            return None

        async def async_worker(item) -> None:
            return None

        def process_worker(item) -> None:
            return None

        class UnsupportedOptionWorkload(BenchmarkWorkload):
            name = "unsupported_option"
            label = "Unsupported Option"
            description = "Unsupported option workload"
            options_type = UnsupportedOptions

            def baseline_worker(self, context: WorkloadContext):
                return baseline

            def async_worker(self, context: WorkloadContext):
                return async_worker

            def process_worker(self, context: WorkloadContext):
                return process_worker
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    [record] = registry.all_records()
    assert record.available is False
    assert record.error is not None
    assert "unsupported type" in record.error


def test_option_schema_marks_unresolved_forward_refs_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(
        package_dir,
        "unresolved_option",
        """
        from __future__ import annotations

        from dataclasses import dataclass, field

        from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext, WorkloadOptionMetadata

        @dataclass(frozen=True, slots=True)
        class UnresolvedOptions:
            value: MissingOptionType = field(default=1, metadata={"workload_option": WorkloadOptionMetadata(label="Value")})

        def baseline(payload: bytes) -> None:
            return None

        async def async_worker(item) -> None:
            return None

        def process_worker(item) -> None:
            return None

        class UnresolvedOptionWorkload(BenchmarkWorkload):
            name = "unresolved_option"
            label = "Unresolved Option"
            description = "Unresolved option workload"
            options_type = UnresolvedOptions

            def baseline_worker(self, context: WorkloadContext):
                return baseline

            def async_worker(self, context: WorkloadContext):
                return async_worker

            def process_worker(self, context: WorkloadContext):
                return process_worker
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    [record] = registry.all_records()
    assert record.available is False
    assert record.error is not None
    assert "NameError" in record.error


def test_option_schema_marks_malformed_forward_refs_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(
        package_dir,
        "malformed_option",
        """
        from dataclasses import dataclass, field

        from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext, WorkloadOptionMetadata

        @dataclass(frozen=True, slots=True)
        class MalformedOptions:
            value: "1+" = field(default=1, metadata={"workload_option": WorkloadOptionMetadata(label="Value")})

        def baseline(payload: bytes) -> None:
            return None

        async def async_worker(item) -> None:
            return None

        def process_worker(item) -> None:
            return None

        class MalformedOptionWorkload(BenchmarkWorkload):
            name = "malformed_option"
            label = "Malformed Option"
            description = "Malformed option workload"
            options_type = MalformedOptions

            def baseline_worker(self, context: WorkloadContext):
                return baseline

            def async_worker(self, context: WorkloadContext):
                return async_worker

            def process_worker(self, context: WorkloadContext):
                return process_worker
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    [record] = registry.all_records()
    assert record.available is False
    assert record.error is not None
    assert "SyntaxError" in record.error


def test_option_schema_rejects_default_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(
        package_dir,
        "factory_option",
        """
        from dataclasses import dataclass, field

        from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext, WorkloadOptionMetadata

        @dataclass(frozen=True, slots=True)
        class FactoryOptions:
            value: str = field(default_factory=str, metadata={"workload_option": WorkloadOptionMetadata(label="Value")})

        def baseline(payload: bytes) -> None:
            return None

        async def async_worker(item) -> None:
            return None

        def process_worker(item) -> None:
            return None

        class FactoryOptionWorkload(BenchmarkWorkload):
            name = "factory_option"
            label = "Factory Option"
            description = "Factory option workload"
            options_type = FactoryOptions

            def baseline_worker(self, context: WorkloadContext):
                return baseline

            def async_worker(self, context: WorkloadContext):
                return async_worker

            def process_worker(self, context: WorkloadContext):
                return process_worker
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    [record] = registry.all_records()
    assert record.available is False
    assert record.error is not None
    assert "explicit default" in record.error


def test_option_schema_rejects_init_false_fields() -> None:
    from benchmarks.workloads.base import (
        WorkloadOptionMetadata,
        describe_workload_options,
    )

    @dataclass(frozen=True, slots=True)
    class InitFalseOptions:
        value: int = field(
            default=1,
            init=False,
            metadata={"workload_option": WorkloadOptionMetadata(label="Value")},
        )

    class InitFalseWorkload:
        name = "init_false"
        options_type = InitFalseOptions

    with pytest.raises(ValueError, match="init=True"):
        describe_workload_options(cast(Any, InitFalseWorkload))


def test_option_schema_rejects_invalid_choice_default() -> None:
    from benchmarks.workloads.base import (
        WorkloadOptionMetadata,
        describe_workload_options,
    )

    @dataclass(frozen=True, slots=True)
    class ChoiceOptions:
        mode: str = field(
            default="invalid",
            metadata={
                "workload_option": WorkloadOptionMetadata(
                    label="Mode", choices=("fast", "slow")
                )
            },
        )

    class ChoiceWorkload:
        name = "choice"
        options_type = ChoiceOptions

    with pytest.raises(ValueError, match="default must be in choices"):
        describe_workload_options(cast(Any, ChoiceWorkload))


def test_registry_marks_duplicate_legacy_flags_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    module_template = """
        from dataclasses import dataclass, field

        from benchmarks.workloads.base import BenchmarkWorkload, WorkloadContext, WorkloadOptionMetadata

        @dataclass(frozen=True, slots=True)
        class {class_name}Options:
            value: int = field(default=1, metadata={{"workload_option": WorkloadOptionMetadata(label="Value", legacy_flags=("--shared-flag",))}})

        def baseline(payload: bytes) -> None:
            return None

        async def async_worker(item) -> None:
            return None

        def process_worker(item) -> None:
            return None

        class {class_name}Workload(BenchmarkWorkload):
            name = "{name}"
            label = "{class_name}"
            description = "{class_name} workload"
            options_type = {class_name}Options

            def baseline_worker(self, context: WorkloadContext):
                return baseline

            def async_worker(self, context: WorkloadContext):
                return async_worker

            def process_worker(self, context: WorkloadContext):
                return process_worker
    """
    _write_module(
        package_dir,
        "first_legacy",
        module_template.format(class_name="FirstLegacy", name="first_legacy"),
    )
    _write_module(
        package_dir,
        "second_legacy",
        module_template.format(class_name="SecondLegacy", name="second_legacy"),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    records = registry.all_records()
    assert {record.name for record in records} == {"first_legacy", "second_legacy"}
    assert all(not record.available for record in records)
    assert all(
        record.error and "duplicate legacy flag" in record.error for record in records
    )


def test_records_are_sorted_builtins_first_then_name_for_custom_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.workloads.registry import discover_workloads

    package_dir, package_name = _make_workload_package(tmp_path)
    _write_module(package_dir, "zeta", _noop_workload_class("zeta", "Zeta"))
    _write_module(package_dir, "alpha", _noop_workload_class("alpha", "Alpha"))
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = discover_workloads(package_dir=package_dir, package_name=package_name)

    assert registry.available_names() == ("alpha", "zeta")
