from __future__ import annotations

import importlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .base import (
    BenchmarkWorkload,
    describe_workload_options,
    validate_workload_options_type,
)

_BUILTIN_ORDER: tuple[str, ...] = ("sleep", "cpu", "io")
_INFRASTRUCTURE_MODULES: frozenset[str] = frozenset({"__init__", "base", "registry"})
_WORKLOAD_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_]*$")
_REQUIRED_METHODS: tuple[str, ...] = (
    "baseline_worker",
    "async_worker",
    "process_worker",
)


@dataclass(frozen=True, slots=True)
class WorkloadRecord:
    """Handle WorkloadRecord for benchmark workload discovery."""

    name: str
    label: str
    description: str
    module_name: str
    class_name: str | None = None
    workload_cls: type[BenchmarkWorkload] | None = None
    available: bool = True
    error: str | None = None

    @property
    def workload_class(self) -> type[BenchmarkWorkload] | None:
        """Backward-compatible alias for callers using the earlier draft name."""
        return self.workload_cls


class WorkloadRegistry:
    """Handle WorkloadRegistry for benchmark workload discovery."""

    def __init__(self, records: tuple[WorkloadRecord, ...]) -> None:
        self._records = records
        self._available_by_name = {
            record.name: record.workload_cls
            for record in records
            if record.available and record.workload_cls is not None
        }
        self._unavailable_by_name = {
            record.name: record for record in records if not record.available
        }

    def all_records(self) -> tuple[WorkloadRecord, ...]:
        """Handle all records for benchmark workload discovery."""
        return self._records

    def records(self) -> tuple[WorkloadRecord, ...]:
        """Handle records for benchmark workload discovery."""
        return self._records

    def available_records(self) -> tuple[WorkloadRecord, ...]:
        """Handle available records for benchmark workload discovery."""
        return tuple(record for record in self._records if record.available)

    def available_names(self) -> tuple[str, ...]:
        """Handle available names for benchmark workload discovery."""
        return tuple(record.name for record in self._records if record.available)

    def get_available(self, name: str) -> type[BenchmarkWorkload]:
        """Handle get available for benchmark workload discovery."""
        workload_class = self._available_by_name.get(name)
        if workload_class is not None:
            return workload_class
        if name in self._unavailable_by_name:
            record = self._unavailable_by_name[name]
            reason = f": {record.error}" if record.error else ""
            raise ValueError(f"Workload unavailable: {name}{reason}")
        raise ValueError(f"Unknown workload: {name}")


def discover_workloads_from(directory: Path, package_name: str) -> WorkloadRegistry:
    """Discover workloads from a direct package directory scan."""
    records: list[WorkloadRecord] = []
    for module_path in sorted(directory.glob("*.py"), key=lambda path: path.stem):
        if module_path.stem in _INFRASTRUCTURE_MODULES or module_path.stem.startswith(
            "_"
        ):
            continue
        records.extend(_discover_module(module_path.stem, package_name))

    return WorkloadRegistry(
        _sort_records(_mark_duplicates(_mark_duplicate_legacy_flags(records)))
    )


def discover_workloads(
    *,
    package_dir: Path | None = None,
    package_name: str = "benchmarks.workloads",
) -> WorkloadRegistry:
    """Handle discover workloads for benchmark workload discovery."""
    if package_dir is None:
        package_dir = Path(__file__).resolve().parent

    return discover_workloads_from(package_dir, package_name)


def _discover_module(module_stem: str, package_name: str) -> list[WorkloadRecord]:
    """Handle  discover module for benchmark workload discovery."""
    module_name = f"{package_name}.{module_stem}"
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - registry must expose broken workloads.
        return [
            WorkloadRecord(
                name=module_stem,
                label=module_stem,
                description="",
                module_name=module_name,
                available=False,
                error=f"ImportError: {exc.__class__.__name__}: {_short_error(exc)}",
            )
        ]

    workload_classes = _iter_workload_classes(module)
    return [_record_for_class(cls, module_name) for cls in workload_classes]


def _iter_workload_classes(module: ModuleType) -> list[type[BenchmarkWorkload]]:
    """Handle  iter workload classes for benchmark workload discovery."""
    classes: list[type[BenchmarkWorkload]] = []
    for _, value in inspect.getmembers(module, inspect.isclass):
        if value is BenchmarkWorkload:
            continue
        if not issubclass(value, BenchmarkWorkload):
            continue
        if value.__module__ != module.__name__:
            continue
        classes.append(value)
    return sorted(classes, key=lambda cls: cls.__name__)


def _record_for_class(
    workload_class: type[BenchmarkWorkload], module_name: str
) -> WorkloadRecord:
    """Handle  record for class for benchmark workload discovery."""
    raw_name = getattr(workload_class, "name", None)
    class_name = workload_class.__name__
    name = raw_name if isinstance(raw_name, str) and raw_name else class_name
    label = getattr(workload_class, "label", name)
    description = getattr(workload_class, "description", "")

    error = _validate_class(workload_class)
    if error is not None:
        return WorkloadRecord(
            name=name,
            label=str(label),
            description=str(description),
            module_name=module_name,
            class_name=class_name,
            available=False,
            error=f"ValidationError: {error}",
        )

    return WorkloadRecord(
        name=name,
        label=str(label),
        description=str(description),
        module_name=module_name,
        class_name=class_name,
        workload_cls=workload_class,
        available=True,
    )


def _validate_class(workload_class: type[BenchmarkWorkload]) -> str | None:
    """Handle  validate class for benchmark workload discovery."""
    name = getattr(workload_class, "name", None)
    if not isinstance(name, str) or not name:
        return "missing non-empty name"
    if _WORKLOAD_NAME_RE.fullmatch(name) is None:
        return "invalid name: use letters, numbers, and underscores only"
    for attr in ("label", "description"):
        value = getattr(workload_class, attr, None)
        if not isinstance(value, str) or not value:
            return f"missing non-empty {attr}"
    for method_name in _REQUIRED_METHODS:
        method = workload_class.__dict__.get(method_name)
        if method is None:
            return f"missing {method_name}"
        if method is getattr(BenchmarkWorkload, method_name):
            return f"missing {method_name}"
    options_type = getattr(workload_class, "options_type", None)
    option_error = validate_workload_options_type(name, options_type)
    if option_error is not None:
        return option_error
    return None


def _mark_duplicate_legacy_flags(records: list[WorkloadRecord]) -> list[WorkloadRecord]:
    """Mark workloads unavailable when option legacy flags are claimed twice."""
    claims: dict[str, list[WorkloadRecord]] = {}
    for record in records:
        if not record.available or record.workload_cls is None:
            continue
        for option in describe_workload_options(record.workload_cls):
            for flag in option.metadata.legacy_flags:
                claims.setdefault(flag, []).append(record)

    duplicate_flags = {
        flag: grouped for flag, grouped in claims.items() if len(grouped) > 1
    }
    if not duplicate_flags:
        return records

    duplicate_names = {
        record.name for grouped in duplicate_flags.values() for record in grouped
    }
    marked: list[WorkloadRecord] = []
    for record in records:
        if record.name not in duplicate_names:
            marked.append(record)
            continue
        flag_descriptions = []
        for flag, grouped in duplicate_flags.items():
            if record not in grouped:
                continue
            origins = ", ".join(
                "%s.%s" % (origin.module_name, origin.class_name or "<module>")
                for origin in grouped
            )
            flag_descriptions.append("%s in %s" % (flag, origins))
        marked.append(
            WorkloadRecord(
                name=record.name,
                label=record.label,
                description=record.description,
                module_name=record.module_name,
                class_name=record.class_name,
                available=False,
                error="ValidationError: duplicate legacy flag %s"
                % "; ".join(flag_descriptions),
            )
        )
    return marked


def _mark_duplicates(records: list[WorkloadRecord]) -> list[WorkloadRecord]:
    """Handle  mark duplicates for benchmark workload discovery."""
    by_name: dict[str, list[WorkloadRecord]] = {}
    for record in records:
        by_name.setdefault(record.name, []).append(record)

    duplicate_names = {name for name, grouped in by_name.items() if len(grouped) > 1}
    if not duplicate_names:
        return records

    marked: list[WorkloadRecord] = []
    emitted_duplicates: set[str] = set()
    for record in records:
        if record.name not in duplicate_names:
            marked.append(record)
            continue
        if record.name in emitted_duplicates:
            continue
        emitted_duplicates.add(record.name)
        origins = ", ".join(
            "%s.%s" % (duplicate.module_name, duplicate.class_name or "<module>")
            for duplicate in by_name[record.name]
        )
        marked.append(
            WorkloadRecord(
                name=record.name,
                label=record.label,
                description=record.description,
                module_name=record.module_name,
                class_name=record.class_name,
                available=False,
                error=f"ValidationError: duplicate workload name in {origins}",
            )
        )
    return marked


def _sort_records(records: list[WorkloadRecord]) -> tuple[WorkloadRecord, ...]:
    """Handle  sort records for benchmark workload discovery."""
    builtin_index = {name: index for index, name in enumerate(_BUILTIN_ORDER)}
    return tuple(
        sorted(
            records,
            key=lambda record: (
                0 if record.name in builtin_index else 1,
                builtin_index.get(record.name, record.name),
                record.name,
                record.module_name,
                record.class_name or "",
            ),
        )
    )


def _short_error(exc: Exception) -> str:
    """Handle  short error for benchmark workload discovery."""
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    return message.splitlines()[0]
