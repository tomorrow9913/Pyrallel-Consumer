# -*- coding: utf-8 -*-
# File: tests/unit/test_module_boundaries.py
# Role: Guards architectural module boundaries and facade re-exports.
# Extend here when helper modules move or facade export contracts change.

from benchmarks import benchmark_artifacts, benchmark_cli, benchmark_output
from benchmarks.tui import app as tui_app
from benchmarks.tui.screens import options as tui_options
from benchmarks.tui.screens import run as tui_run
from pyrallel_consumer.control_plane import broker_dlq_publisher
from pyrallel_consumer.execution_plane import process_batching, process_worker_runtime


def test_benchmark_helpers_live_outside_entrypoint() -> None:
    # Given: benchmark helper modules are imported separately from the CLI entrypoint.
    # When: their public helper functions are inspected.
    # Then: benchmark CLI parsing, artifact metadata, and output rendering remain outside one monolithic entrypoint.
    assert benchmark_cli.build_parser is not None
    assert benchmark_artifacts.build_artifact_metadata is not None
    assert benchmark_output.print_table is not None


def test_tui_screens_are_split_from_app_facade() -> None:
    # Given: the TUI app facade and concrete screen modules are imported.
    # When: screen attributes exported by the facade are compared to the concrete modules.
    # Then: OptionsScreen and RunScreen remain split from the app facade while being re-exported.
    assert tui_app.OptionsScreen is tui_options.OptionsScreen
    assert tui_app.RunScreen is tui_run.RunScreen


def test_process_engine_helpers_are_split_from_engine_facade() -> None:
    # Given: process batching and worker runtime helper modules are imported.
    # When: their public helper symbols are inspected.
    # Then: process engine batching and worker loop helpers remain outside the engine facade.
    assert process_batching.BatchAccumulator is not None
    assert process_worker_runtime.worker_loop is not None


def test_broker_dlq_publisher_lives_outside_poller() -> None:
    # Given: the broker DLQ publisher module is imported separately from the poller.
    # When: the DLQ publish helper symbol is inspected.
    # Then: DLQ publishing remains outside the broker poller implementation.
    assert broker_dlq_publisher.publish_to_dlq is not None
