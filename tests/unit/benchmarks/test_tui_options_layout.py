# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_tui_options_layout.py
# Role: Verifies benchmark TUI options screen layout, labels, sections, and visible controls.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._tui_app_support import (
    BenchmarkTuiApp,
    Button,
    Input,
    Label,
    SelectionList,
    Static,
    Switch,
    _ancestor_ids,
    _block_child_types,
    cast,
    pytest,
)


@pytest.mark.asyncio
async def test_options_screen_orders_input_blocks_label_help_control() -> None:
    # Given: Inputs and test doubles are prepared for options screen orders input blocks label help control.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options screen orders input blocks label help control.
    async with app.run_test() as pilot:
        del pilot
        # Then: The expected options screen orders input blocks label help control behavior is asserted.
        assert _block_child_types(app, "bootstrap-servers") == [
            "Label",
            "Static",
            "Input",
            "Static",
        ]
        assert _block_child_types(app, "workloads") == [
            "Label",
            "Static",
            "SelectionList",
            "Static",
        ]
        assert _block_child_types(app, "execution-modes") == [
            "Label",
            "Static",
            "SelectionList",
            "Static",
        ]
        assert _block_child_types(app, "json-output") == [
            "Label",
            "Static",
            "Container",
            "Static",
        ]


@pytest.mark.asyncio
async def test_options_screen_orders_checkbox_blocks_label_help_control() -> None:
    # Given: Inputs and test doubles are prepared for options screen orders checkbox blocks label help control.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options screen orders checkbox blocks label help control.
    async with app.run_test() as pilot:
        del pilot
        # Then: The expected options screen orders checkbox blocks label help control behavior is asserted.
        assert _block_child_types(app, "profiling-enabled") == [
            "Label",
            "Static",
            "Switch",
            "Static",
        ]


@pytest.mark.asyncio
async def test_option_blocks_expand_to_show_controls() -> None:
    # Given: Inputs and test doubles are prepared for option blocks expand to show controls.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for option blocks expand to show controls.
    async with app.run_test() as pilot:
        del pilot
        input_block = app.screen.query_one("#option-block-bootstrap-servers")
        checkbox_block = app.screen.query_one("#option-block-profiling-enabled")
        input_block_height = input_block.region.height
        checkbox_block_height = checkbox_block.region.height

    # Then: The expected option blocks expand to show controls behavior is asserted.
    assert input_block_height > 1
    assert checkbox_block_height > 1


@pytest.mark.asyncio
async def test_options_footer_does_not_overlap_scroll_region() -> None:
    # Given: Inputs and test doubles are prepared for options footer does not overlap scroll region.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options footer does not overlap scroll region.
    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.pause()
        options_screen = app.screen.query_one("#options-screen")
        options_footer = app.screen.query_one("#options-footer")
        options_screen_bottom = options_screen.region.y + options_screen.region.height
        options_footer_top = options_footer.region.y

    # Then: The expected options footer does not overlap scroll region behavior is asserted.
    assert options_screen_bottom <= options_footer_top


@pytest.mark.asyncio
async def test_default_workload_option_is_visible_above_options_footer() -> None:
    # Given: Inputs and test doubles are prepared for default workload option is visible above options footer.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for default workload option is visible above options footer.
    async with app.run_test(size=(100, 80)) as pilot:
        await pilot.pause()
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        options_footer = app.screen.query_one("#options-footer")
        sleep_option_bottom = sleep_option.region.y + sleep_option.region.height
        options_footer_top = options_footer.region.y

    # Then: The expected default workload option is visible above options footer behavior is asserted.
    assert sleep_option_bottom <= options_footer_top


@pytest.mark.asyncio
async def test_ordering_modes_remain_visible_with_selected_workload_options() -> None:
    # Given: Inputs and test doubles are prepared for ordering modes remain visible with selected workload options.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for ordering modes remain visible with selected workload options.
    async with app.run_test(size=(100, 80)) as pilot:
        await pilot.pause()
        ordering_modes = app.screen.query_one("#ordering-modes", SelectionList)
        options_footer = app.screen.query_one("#options-footer")
        ordering_bottom = ordering_modes.region.y + ordering_modes.region.height
        options_footer_top = options_footer.region.y

    # Then: The expected ordering modes remain visible with selected workload options behavior is asserted.
    assert ordering_bottom <= options_footer_top


@pytest.mark.asyncio
async def test_workload_option_group_uses_content_height() -> None:
    # Given: Inputs and test doubles are prepared for workload option group uses content height.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for workload option group uses content height.
    async with app.run_test(size=(100, 80)) as pilot:
        await pilot.pause()
        group = app.screen.query_one("#workload-options-sleep")
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)

    # Then: The expected workload option group uses content height behavior is asserted.
    assert group.region.height < 6
    assert sleep_option.region.y + sleep_option.region.height <= (
        group.region.y + group.region.height
    )


@pytest.mark.asyncio
async def test_options_screen_hides_empty_field_errors() -> None:
    # Given: Inputs and test doubles are prepared for options screen hides empty field errors.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options screen hides empty field errors.
    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        error = app.screen.query_one("#error-num-messages", Static)

        # Then: The expected options screen hides empty field errors behavior is asserted.
        assert error.display is False

        num_messages.value = "oops"
        await pilot.pause()
        assert error.display is True

        num_messages.value = "100"
        await pilot.pause()

    assert error.display is False


@pytest.mark.asyncio
async def test_benchmark_tui_app_mounts_with_run_button() -> None:
    # Given: Inputs and test doubles are prepared for benchmark tui app mounts with run button.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for benchmark tui app mounts with run button.
    async with app.run_test() as pilot:
        del pilot
        run_button = app.screen.query_one("#run-button", Button)
        # Then: The expected benchmark tui app mounts with run button behavior is asserted.
        assert str(run_button.label) == "Run benchmark"


@pytest.mark.asyncio
async def test_options_screen_shows_human_readable_field_labels() -> None:
    # Given: Inputs and test doubles are prepared for options screen shows human readable field labels.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options screen shows human readable field labels.
    async with app.run_test() as pilot:
        del pilot
        labels = [str(label.render()) for label in app.screen.query(Label)]

    # Then: The expected options screen shows human readable field labels behavior is asserted.
    assert "Bootstrap servers" in labels
    assert "Number of messages" in labels
    assert "Number of keys" in labels
    assert "Number of partitions" in labels
    assert "Timeout (sec)" in labels
    assert "Workloads" in labels
    assert "Ordering modes" in labels
    assert "Process count" in labels
    assert "Process batch size" in labels
    assert "Process max batch wait (ms)" in labels
    assert "Process route batch size" in labels


@pytest.mark.asyncio
async def test_options_screen_uses_prominent_title_and_helper_text() -> None:
    # Given: Inputs and test doubles are prepared for options screen uses prominent title and helper text.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options screen uses prominent title and helper text.
    async with app.run_test() as pilot:
        del pilot
        title = app.screen.query_one("#options-title", Static)
        help_texts = [
            str(cast(Static, widget).content)
            for widget in app.screen.query(".option-help")
        ]
        field_labels = [str(label.render()) for label in app.screen.query(Label)]
        switches = list(app.screen.query(Switch))

    # Then: The expected options screen uses prominent title and helper text behavior is asserted.
    assert title.has_class("screen-title")
    assert "Connect to the Kafka cluster" in help_texts
    assert "benchmark messages" in " ".join(help_texts).lower()
    assert "Profiling enabled" in field_labels
    assert switches


@pytest.mark.asyncio
async def test_options_screen_groups_fields_under_section_headings() -> None:
    # Given: Inputs and test doubles are prepared for options screen groups fields under section headings.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options screen groups fields under section headings.
    async with app.run_test() as pilot:
        del pilot
        section_titles = [
            str(widget.render()) for widget in app.screen.query(".option-section-title")
        ]

    # Then: The expected options screen groups fields under section headings behavior is asserted.
    assert section_titles == [
        "Cluster & workload",
        "Output & execution",
        "Profiling",
        "Advanced options",
    ]


@pytest.mark.asyncio
async def test_options_screen_places_representative_fields_in_expected_sections() -> (
    None
):
    # Given: Inputs and test doubles are prepared for options screen places representative fields in expected sections.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options screen places representative fields in expected sections.
    async with app.run_test() as pilot:
        del pilot
        bootstrap_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-bootstrap-servers")
        )
        sleep_ancestors = _ancestor_ids(app.screen.query_one("#option-block-workloads"))
        ordering_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-ordering-modes")
        )
        execution_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-execution-modes")
        )
        output_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-json-output")
        )
        process_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-process-route-batch-size")
        )
        profiling_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-profiling-enabled")
        )
        topic_prefix_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-topic-prefix")
        )

    # Then: The expected options screen places representative fields in expected sections behavior is asserted.
    assert "option-section-cluster-workload" in bootstrap_ancestors
    assert "option-section-cluster-workload" in sleep_ancestors
    assert "option-section-cluster-workload" in ordering_ancestors
    assert "option-section-cluster-workload" in execution_ancestors
    assert "option-section-output-execution" in output_ancestors
    assert "option-section-output-execution" in process_ancestors
    assert "option-section-profiling" in profiling_ancestors
    assert "option-section-advanced-options" in topic_prefix_ancestors


@pytest.mark.asyncio
async def test_options_screen_places_mode_selectors_before_workload_options() -> None:
    # Given: Inputs and test doubles are prepared for options screen places mode selectors before workload options.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options screen places mode selectors before workload options.
    async with app.run_test() as pilot:
        await pilot.pause()
        ordering_modes = app.screen.query_one("#option-block-ordering-modes")
        execution_modes = app.screen.query_one("#option-block-execution-modes")
        workloads = app.screen.query_one("#option-block-workloads")
        workload_options = app.screen.query_one("#workload-options")
        bootstrap = app.screen.query_one("#option-block-bootstrap-servers")
        positions = (
            ordering_modes.region.y,
            execution_modes.region.y,
            workloads.region.y,
            workload_options.region.y,
            bootstrap.region.y,
        )

    # Then: The expected options screen places mode selectors before workload options behavior is asserted.
    assert positions == tuple(sorted(positions))


@pytest.mark.asyncio
async def test_options_screen_uses_selection_lists_for_workloads_and_ordering() -> None:
    # Given: Inputs and test doubles are prepared for options screen uses selection lists for workloads and ordering.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI options layout path is exercised for options screen uses selection lists for workloads and ordering.
    async with app.run_test() as pilot:
        del pilot
        workloads = app.screen.query_one("#workloads", SelectionList)
        ordering = app.screen.query_one("#ordering-modes", SelectionList)
        execution = app.screen.query_one("#execution-modes", SelectionList)

    # Then: The expected options screen uses selection lists for workloads and ordering behavior is asserted.
    assert workloads.selected == ["sleep"]
    assert ordering.selected == ["key_hash"]
    assert execution.selected == ["baseline", "async", "process"]
