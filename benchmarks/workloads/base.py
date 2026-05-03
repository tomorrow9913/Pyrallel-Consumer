from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import MISSING, dataclass, fields, is_dataclass
from typing import Any, ClassVar, Generic, TypeVar, cast, get_type_hints

from pyrallel_consumer.dto import WorkItem

_OPTION_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


@dataclass(frozen=True, slots=True)
class WorkloadOptionMetadata:
    """Describe one workload-specific benchmark option."""

    label: str
    description: str = ""
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[str, ...] = ()
    legacy_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkloadOptionSchema:
    """Resolved workload option schema used by CLI and TUI adapters."""

    workload_name: str
    field_name: str
    canonical_name: str
    annotation: type
    default: object
    metadata: WorkloadOptionMetadata


@dataclass(frozen=True, slots=True)
class NoOptions:
    """Explicit empty option model for workloads without configurable options."""


TOptions = TypeVar("TOptions")


@dataclass(frozen=True, slots=True)
class WorkloadContext(Generic[TOptions]):
    """Handle WorkloadContext for benchmark workload discovery."""

    options: TOptions


class BenchmarkWorkload(Generic[TOptions]):
    """Handle BenchmarkWorkload for benchmark workload discovery."""

    name: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    options_type: ClassVar[type[Any]] = NoOptions

    def baseline_worker(
        self, context: WorkloadContext[TOptions]
    ) -> Callable[[bytes], None]:
        """Handle baseline worker for benchmark workload discovery."""
        raise NotImplementedError

    def async_worker(
        self, context: WorkloadContext[TOptions]
    ) -> Callable[[WorkItem], Awaitable[None]]:
        """Handle async worker for benchmark workload discovery."""
        raise NotImplementedError

    def process_worker(
        self, context: WorkloadContext[TOptions]
    ) -> Callable[[WorkItem], None]:
        """Handle process worker for benchmark workload discovery."""
        raise NotImplementedError


def describe_workload_options(
    workload_cls: type[BenchmarkWorkload[Any]] | None,
) -> tuple[WorkloadOptionSchema, ...]:
    """Return resolved option schemas for a workload class."""
    if workload_cls is None:
        return ()
    option_type = getattr(workload_cls, "options_type", None)
    return _describe_option_type(workload_cls.name, option_type)


def validate_workload_options_type(
    workload_name: str, option_type: object
) -> str | None:
    """Return a validation error for an invalid workload option model."""
    try:
        _describe_option_type(workload_name, option_type)
    except ValueError as exc:
        return str(exc)
    return None


def build_workload_options(
    workload_cls: type[BenchmarkWorkload[TOptions]],
    *,
    workload_options: Mapping[str, Mapping[str, object]] | None = None,
    legacy_values: Mapping[str, object | None] | None = None,
) -> TOptions:
    """Build a typed options dataclass instance for one workload."""
    schema = describe_workload_options(workload_cls)
    values = {item.field_name: item.default for item in schema}
    explicit: set[str] = set()
    workload_name = workload_cls.name

    if legacy_values is not None:
        for item in schema:
            for flag in item.metadata.legacy_flags:
                raw_value = legacy_values.get(item.canonical_name)
                if raw_value is None:
                    raw_value = legacy_values.get(flag)
                if raw_value is None:
                    continue
                values[item.field_name] = _coerce_option_value(item, raw_value)
                explicit.add(item.field_name)

    if workload_options is not None:
        for unknown_workload in sorted(set(workload_options) - {workload_name}):
            if workload_options[unknown_workload]:
                raise ValueError("Unknown workload option %r" % unknown_workload)
        for field_name, raw_value in workload_options.get(workload_name, {}).items():
            selected_item = _schema_by_field(schema).get(field_name)
            if selected_item is None:
                raise ValueError(
                    "Unknown workload option %r"
                    % ("%s.%s" % (workload_name, field_name))
                )
            if field_name in explicit:
                raise ValueError(
                    "Duplicate workload option %r" % selected_item.canonical_name
                )
            values[field_name] = _coerce_option_value(selected_item, raw_value)
            explicit.add(field_name)

    return cast(TOptions, workload_cls.options_type(**values))


def _describe_option_type(
    workload_name: str, option_type: object
) -> tuple[WorkloadOptionSchema, ...]:
    """Resolve and validate the option schema for one workload option dataclass."""
    if not isinstance(option_type, type) or not is_dataclass(option_type):
        raise ValueError("options_type must be a dataclass type")
    type_hints = get_type_hints(option_type, include_extras=True)
    schemas: list[WorkloadOptionSchema] = []
    for option_field in fields(option_type):
        metadata = option_field.metadata.get("workload_option")
        field_name = option_field.name
        canonical_name = "%s.%s" % (workload_name, field_name)
        if not isinstance(metadata, WorkloadOptionMetadata):
            raise ValueError(
                "invalid option %s: missing metadata['workload_option']"
                % canonical_name
            )
        if _OPTION_NAME_RE.fullmatch(field_name) is None:
            raise ValueError(
                "invalid option %s: field name must start with a letter and use letters, numbers, or underscores"
                % canonical_name
            )
        if not option_field.init:
            raise ValueError(
                "invalid option %s: field must use init=True" % canonical_name
            )
        if (
            option_field.default is MISSING
            or option_field.default_factory is not MISSING
        ):
            raise ValueError(
                "invalid option %s: explicit default required" % canonical_name
            )
        annotation = type_hints.get(field_name)
        if annotation not in (int, float, str, bool):
            raise ValueError("invalid option %s: unsupported type" % canonical_name)
        _validate_metadata(canonical_name, annotation, option_field.default, metadata)
        schemas.append(
            WorkloadOptionSchema(
                workload_name=workload_name,
                field_name=field_name,
                canonical_name=canonical_name,
                annotation=annotation,
                default=option_field.default,
                metadata=metadata,
            )
        )
    return tuple(schemas)


def _validate_metadata(
    canonical_name: str,
    annotation: type,
    default: object,
    metadata: WorkloadOptionMetadata,
) -> None:
    """Validate metadata/default compatibility for one workload option."""
    if not metadata.label:
        raise ValueError("invalid option %s: label required" % canonical_name)
    if annotation is bool:
        if not isinstance(default, bool):
            raise ValueError("invalid option %s: default must be bool" % canonical_name)
        _reject_bounds(canonical_name, metadata)
        _reject_choices(canonical_name, metadata)
        return
    if annotation is int:
        if not isinstance(default, int) or isinstance(default, bool):
            raise ValueError("invalid option %s: default must be int" % canonical_name)
        _validate_numeric_bounds(canonical_name, default, metadata, allow_float=False)
        _reject_choices(canonical_name, metadata)
        return
    if annotation is float:
        if not isinstance(default, (int, float)) or isinstance(default, bool):
            raise ValueError(
                "invalid option %s: default must be float" % canonical_name
            )
        _validate_finite(canonical_name, float(default))
        _validate_numeric_bounds(
            canonical_name, float(default), metadata, allow_float=True
        )
        _reject_choices(canonical_name, metadata)
        return
    if annotation is str:
        if not isinstance(default, str):
            raise ValueError("invalid option %s: default must be str" % canonical_name)
        _reject_bounds(canonical_name, metadata)
        if metadata.choices:
            if any(not choice for choice in metadata.choices):
                raise ValueError(
                    "invalid option %s: choices must be non-empty strings"
                    % canonical_name
                )
            if default not in metadata.choices:
                raise ValueError(
                    "invalid option %s: default must be in choices" % canonical_name
                )


def _coerce_option_value(schema: WorkloadOptionSchema, raw_value: object) -> object:
    """Coerce a raw CLI/TUI value according to one workload option schema."""
    try:
        if schema.annotation is bool:
            return _coerce_bool(schema.canonical_name, raw_value)
        if schema.annotation is int:
            if not isinstance(raw_value, (str, int, float)) or isinstance(
                raw_value, bool
            ):
                raise ValueError
            int_value = int(raw_value)
            _validate_numeric_bounds(
                schema.canonical_name, int_value, schema.metadata, False
            )
            return int_value
        if schema.annotation is float:
            if not isinstance(raw_value, (str, int, float)) or isinstance(
                raw_value, bool
            ):
                raise ValueError
            float_value = float(raw_value)
            _validate_finite(schema.canonical_name, float_value)
            _validate_numeric_bounds(
                schema.canonical_name, float_value, schema.metadata, True
            )
            return float_value
        if schema.annotation is str:
            str_value = str(raw_value)
            if schema.metadata.choices and str_value not in schema.metadata.choices:
                raise ValueError(
                    "Invalid workload option %r: expected one of %s"
                    % (schema.canonical_name, ", ".join(schema.metadata.choices))
                )
            return str_value
    except ValueError as exc:
        if str(exc).startswith("Invalid workload option"):
            raise
        raise ValueError(
            "Invalid workload option %r: expected %s"
            % (schema.canonical_name, schema.annotation.__name__)
        ) from exc
    raise ValueError(
        "Invalid workload option %r: unsupported type" % schema.canonical_name
    )


def _coerce_bool(canonical_name: str, raw_value: object) -> bool:
    """Coerce common textual bool spellings for a workload option."""
    if isinstance(raw_value, bool):
        return raw_value
    normalized = str(raw_value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError("Invalid workload option %r: expected bool" % canonical_name)


def _validate_numeric_bounds(
    canonical_name: str,
    value: int | float,
    metadata: WorkloadOptionMetadata,
    allow_float: bool,
) -> None:
    """Validate numeric bounds metadata and a concrete numeric option value."""
    minimum = metadata.minimum
    maximum = metadata.maximum
    if minimum is not None:
        _validate_bound(canonical_name, "minimum", minimum, allow_float)
    if maximum is not None:
        _validate_bound(canonical_name, "maximum", maximum, allow_float)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError(
            "invalid option %s: minimum must be <= maximum" % canonical_name
        )
    if minimum is not None and value < minimum:
        raise ValueError(
            "Invalid workload option %r: must be >= %s" % (canonical_name, minimum)
        )
    if maximum is not None and value > maximum:
        raise ValueError(
            "Invalid workload option %r: must be <= %s" % (canonical_name, maximum)
        )


def _validate_bound(
    canonical_name: str, label: str, value: int | float, allow_float: bool
) -> None:
    """Validate one numeric bound metadata value."""
    if allow_float:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        valid = isinstance(value, int) and not isinstance(value, bool)
    if not valid:
        raise ValueError(
            "invalid option %s: %s has invalid type" % (canonical_name, label)
        )
    if allow_float:
        _validate_finite(canonical_name, float(value))


def _validate_finite(canonical_name: str, value: float) -> None:
    """Reject non-finite float option values."""
    if not math.isfinite(value):
        raise ValueError(
            "Invalid workload option %r: value must be finite" % canonical_name
        )


def _reject_bounds(canonical_name: str, metadata: WorkloadOptionMetadata) -> None:
    """Reject numeric bounds on non-numeric workload options."""
    if metadata.minimum is not None or metadata.maximum is not None:
        raise ValueError(
            "invalid option %s: numeric bounds are not supported" % canonical_name
        )


def _reject_choices(canonical_name: str, metadata: WorkloadOptionMetadata) -> None:
    """Reject string choices on non-string workload options."""
    if metadata.choices:
        raise ValueError(
            "invalid option %s: choices are not supported" % canonical_name
        )


def _schema_by_field(
    schema: tuple[WorkloadOptionSchema, ...]
) -> dict[str, WorkloadOptionSchema]:
    """Return workload option schemas keyed by dataclass field name."""
    return {item.field_name: item for item in schema}
