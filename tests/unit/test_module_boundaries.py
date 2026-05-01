from benchmarks import benchmark_artifacts, benchmark_cli, benchmark_output
from benchmarks.tui import app as tui_app
from benchmarks.tui.screens import options as tui_options
from benchmarks.tui.screens import run as tui_run
from pyrallel_consumer.control_plane import broker_dlq_publisher
from pyrallel_consumer.execution_plane import process_batching, process_worker_runtime


def test_benchmark_helpers_live_outside_entrypoint() -> None:
    assert benchmark_cli.build_parser is not None
    assert benchmark_artifacts.build_artifact_metadata is not None
    assert benchmark_output.print_table is not None


def test_tui_screens_are_split_from_app_facade() -> None:
    assert tui_app.OptionsScreen is tui_options.OptionsScreen
    assert tui_app.RunScreen is tui_run.RunScreen


def test_process_engine_helpers_are_split_from_engine_facade() -> None:
    assert process_batching.BatchAccumulator is not None
    assert process_worker_runtime.worker_loop is not None


def test_broker_dlq_publisher_lives_outside_poller() -> None:
    assert broker_dlq_publisher.publish_to_dlq is not None
