# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_work_manager_submission_routing.py
# Role: Verifies WorkManager message submission, route-batch scheduling, poison-message routing, and submit errors.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._work_manager_support import (
    BatchSubmitError,
    CompletionEvent,
    CompletionStatus,
    MagicMock,
    OffsetTracker,
    OrderingMode,
    PoisonMessageCircuitBreaker,
    WorkManager,
    _open_poison_circuit_for_key,
    patch,
    pytest,
)


@pytest.mark.asyncio
async def test_submit_message(
    work_manager, mock_dto_topic_partition, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for submit message.
    # When: The control-plane behavior is exercised for submit message.
    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        # Configure the mock to return a mock instance when called
        mock_tracker_instance = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,  # dummy args for spec
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )
        MockOffsetTrackerClass.return_value = mock_tracker_instance
        # Ensure no blocking offsets by default
        mock_tracker_instance.get_gaps.return_value = []

        # Assign the topic-partition first
        work_manager.on_assign([mock_dto_topic_partition])
        work_manager._offset_trackers[mock_dto_topic_partition] = mock_tracker_instance

        offset = 10
        epoch = 1
        key = b"message-key"
        payload = b"test-payload"

        # Submit the message (it only queues it internally now)
        await work_manager.submit_message(
            mock_dto_topic_partition, offset, epoch, key, payload
        )

        # Verify that the message is in the internal queue and tracked
        virtual_queue = work_manager._virtual_partition_queues[
            mock_dto_topic_partition
        ][key]
        # Then: The expected submit message behavior is asserted.
        assert virtual_queue.qsize() == 1
        queued_work_item = await virtual_queue.get()
        assert queued_work_item.offset == offset
        assert queued_work_item.epoch == epoch
        assert queued_work_item.key == key
        assert queued_work_item.payload == payload
        # Put it back for schedule to pick up
        await virtual_queue.put(queued_work_item)

        assert len(work_manager._in_flight_work_items) == 1
        assert queued_work_item.id in work_manager._in_flight_work_items
        assert (
            work_manager._in_flight_work_items[queued_work_item.id] == queued_work_item
        )
        assert (
            work_manager._current_in_flight_count == 0
        )  # No messages are in-flight yet
        assert work_manager.get_total_queued_messages() == 1

        # Now, explicitly trigger the submission process
        await work_manager.schedule()

        # Verify that the item was now submitted to the execution engine
        mock_execution_engine.submit.assert_awaited_once()
        submitted_work_item = mock_execution_engine.submit.call_args[0][0]
        assert submitted_work_item.id == queued_work_item.id
        assert work_manager._current_in_flight_count == 1
        assert work_manager.get_total_queued_messages() == 0


@pytest.mark.asyncio
async def test_batch_dispatch_enabled_uses_submit_batch_for_single_item_lease(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: WorkManager is in public batch-worker dispatch mode with one queued item.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
        max_in_flight_messages=10,
        route_batch_size=64,
        batch_dispatch_enabled=True,
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
    )
    work_manager.on_assign({mock_dto_topic_partition: tracker})
    await work_manager.submit_message(
        mock_dto_topic_partition,
        10,
        tracker.get_current_epoch(),
        b"message-key",
        b"payload",
    )

    await work_manager.schedule()

    # Then: even a singleton lease enters the batch dispatch path.
    mock_execution_engine.submit.assert_not_awaited()
    mock_execution_engine.submit_batch.assert_awaited_once()
    submitted_batch = mock_execution_engine.submit_batch.await_args.args[0]
    assert len(submitted_batch) == 1
    assert submitted_batch[0].offset == 10
    assert work_manager.get_total_in_flight_count() == 1
    assert work_manager.get_total_queued_messages() == 0


@pytest.mark.asyncio
async def test_submit_message_tracks_tp_offset_index(
    work_manager, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for submit message tracks tp offset index.
    work_manager.on_assign([mock_dto_topic_partition])

    await work_manager.submit_message(
        mock_dto_topic_partition, 7, 1, b"message-key", b"payload"
    )

    # When: The control-plane behavior is exercised for submit message tracks tp offset index.
    work_item = next(iter(work_manager._in_flight_work_items.values()))
    # Then: The expected submit message tracks tp offset index behavior is asserted.
    assert (
        work_manager._work_item_ids_by_tp_offset[(mock_dto_topic_partition, 7)]
        == work_item.id
    )


@pytest.mark.asyncio
async def test_submit_message_batch_tracks_partition_queue_state(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for submit message batch tracks partition queue state.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.PARTITION,
    )
    work_manager.on_assign([mock_dto_topic_partition])

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, mock_dto_topic_partition.partition): [
                (7, 1, b"payload-7"),
                (8, 1, b"payload-8"),
            ]
        }
    )

    queue_key = (mock_dto_topic_partition, mock_dto_topic_partition.partition)
    virtual_queue = work_manager._virtual_partition_queues[mock_dto_topic_partition][
        mock_dto_topic_partition.partition
    ]
    # When: The control-plane behavior is exercised for submit message batch tracks partition queue state.
    queued_items = [await virtual_queue.get(), await virtual_queue.get()]

    # Then: The expected submit message batch tracks partition queue state behavior is asserted.
    assert [item.offset for item in queued_items] == [7, 8]
    assert work_manager.get_total_queued_messages() == 2
    assert len(work_manager._in_flight_work_items) == 2
    assert work_manager._head_offsets[queue_key] == 7
    assert work_manager._head_queue_keys_by_offset[(mock_dto_topic_partition, 7)] == (
        queue_key
    )
    assert work_manager._runnable_queue_keys.count(queue_key) == 1


@pytest.mark.asyncio
async def test_submit_message_batch_updates_last_fetched_offset_once_per_tp(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for submit message batch updates last fetched offset once per tp.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
    )
    tracker = MagicMock(spec=OffsetTracker)
    tracker.get_gaps.return_value = []
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, b"key-a"): [
                (5, 1, b"payload-5"),
                (7, 1, b"payload-7"),
            ],
            (mock_dto_topic_partition, b"key-b"): [
                (6, 1, b"payload-6"),
            ],
        }
    )

    # When: The control-plane behavior is exercised for submit message batch updates last fetched offset once per tp.
    tracker.update_last_fetched_offset.assert_called_once_with(7)
    # Then: The expected submit message batch updates last fetched offset once per tp behavior is asserted.
    assert work_manager.get_total_queued_messages() == 3


@pytest.mark.asyncio
async def test_submit_message_batch_unassigned_tp_raises_error(
    work_manager, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for submit message batch unassigned tp raises error.
    # When: The control-plane behavior is exercised for submit message batch unassigned tp raises error.
    # Then: The expected submit message batch unassigned tp raises error behavior is asserted.
    with pytest.raises(ValueError, match="not assigned"):
        await work_manager.submit_message_batch(
            {
                (mock_dto_topic_partition, b"key"): [
                    (1, 1, b"payload"),
                ]
            }
        )


@pytest.mark.asyncio
async def test_submit_message_batch_schedule_preserves_partition_order(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for submit message batch schedule preserves partition order.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.PARTITION,
        max_in_flight_messages=10,
    )
    work_manager.on_assign([mock_dto_topic_partition])

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, mock_dto_topic_partition.partition): [
                (3, 1, b"payload-3"),
                (4, 1, b"payload-4"),
            ]
        }
    )

    await work_manager.schedule()

    # When: The control-plane behavior is exercised for submit message batch schedule preserves partition order.
    mock_execution_engine.submit.assert_awaited_once()
    submitted_item = mock_execution_engine.submit.call_args.args[0]
    # Then: The expected submit message batch schedule preserves partition order behavior is asserted.
    assert submitted_item.offset == 3
    assert work_manager.get_total_queued_messages() == 1


@pytest.mark.asyncio
async def test_submit_message_batch_schedule_preserves_key_hash_parallelism(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for submit message batch schedule preserves key hash parallelism.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
        max_in_flight_messages=10,
    )
    work_manager.on_assign([mock_dto_topic_partition])

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, b"key-a"): [
                (10, 1, b"payload-10"),
                (11, 1, b"payload-11"),
            ],
            (mock_dto_topic_partition, b"key-b"): [
                (12, 1, b"payload-12"),
            ],
        }
    )

    # When: The control-plane behavior is exercised for submit message batch schedule preserves key hash parallelism.
    await work_manager.schedule()

    submitted_offsets = [
        call.args[0].offset for call in mock_execution_engine.submit.await_args_list
    ]
    # Then: The expected submit message batch schedule preserves key hash parallelism behavior is asserted.
    assert submitted_offsets == [10, 12]
    assert work_manager.get_total_queued_messages() == 1


@pytest.mark.asyncio
async def test_poison_message_circuit_forces_same_key_without_engine_resubmit(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for poison message circuit forces same key without engine resubmit.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
        max_in_flight_messages=10,
        poison_message_circuit=PoisonMessageCircuitBreaker(
            enabled=True,
            failure_threshold=1,
            cooldown_ms=60000,
            forced_failure_attempt=3,
        ),
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
    )
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, b"hot-key"): [
                (0, tracker.get_current_epoch(), b"payload-0"),
                (1, tracker.get_current_epoch(), b"payload-1"),
            ],
            (mock_dto_topic_partition, b"healthy-key"): [
                (2, tracker.get_current_epoch(), b"payload-2"),
            ],
        }
    )
    # When: The control-plane behavior is exercised for poison message circuit forces same key without engine resubmit.
    await work_manager.schedule()

    submitted_offsets = [
        call.args[0].offset for call in mock_execution_engine.submit.await_args_list
    ]
    # Then: The expected poison message circuit forces same key without engine resubmit behavior is asserted.
    assert submitted_offsets == [0, 2]
    failed_item = mock_execution_engine.submit.await_args_list[0].args[0]
    failure = CompletionEvent(
        id=failed_item.id,
        tp=failed_item.tp,
        offset=failed_item.offset,
        epoch=failed_item.epoch,
        status=CompletionStatus.FAILURE,
        error="permanent failure",
        attempt=3,
    )
    mock_execution_engine.poll_completed_events.return_value = [failure]

    completed_events = await work_manager.poll_completed_events()

    assert [event.offset for event in completed_events] == [0, 1]
    forced_event = completed_events[1]
    assert forced_event.status == CompletionStatus.FAILURE
    assert forced_event.attempt == 3
    assert "Poison message circuit open" in str(forced_event.error)
    assert [
        call.args[0].offset for call in mock_execution_engine.submit.await_args_list
    ] == [0, 2]


@pytest.mark.asyncio
async def test_poison_message_circuit_uses_original_key_under_partition_ordering(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for poison message circuit uses original key under partition ordering.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.PARTITION,
        max_in_flight_messages=10,
        poison_message_circuit=PoisonMessageCircuitBreaker(
            enabled=True,
            failure_threshold=1,
            cooldown_ms=60000,
            forced_failure_attempt=3,
        ),
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
    )
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, mock_dto_topic_partition.partition): [
                (0, tracker.get_current_epoch(), b"payload-0", b"hot-key"),
                (1, tracker.get_current_epoch(), b"payload-1", b"healthy-key"),
            ]
        }
    )
    await work_manager.schedule()
    failed_item = mock_execution_engine.submit.await_args_list[0].args[0]
    failure = CompletionEvent(
        id=failed_item.id,
        tp=failed_item.tp,
        offset=failed_item.offset,
        epoch=failed_item.epoch,
        status=CompletionStatus.FAILURE,
        error="permanent failure",
        attempt=3,
    )
    mock_execution_engine.poll_completed_events.return_value = [failure]

    # When: The control-plane behavior is exercised for poison message circuit uses original key under partition ordering.
    completed_events = await work_manager.poll_completed_events()

    # Then: The expected poison message circuit uses original key under partition ordering behavior is asserted.
    assert [event.offset for event in completed_events] == [0]
    assert [
        call.args[0].offset for call in mock_execution_engine.submit.await_args_list
    ] == [0, 1]


@pytest.mark.asyncio
async def test_unordered_route_batch_truncates_before_poison_force_fail_candidate(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for unordered route batch truncates before poison force fail candidate.
    poison_circuit = PoisonMessageCircuitBreaker(
        enabled=True,
        failure_threshold=1,
        cooldown_ms=60000,
        forced_failure_attempt=3,
    )
    _open_poison_circuit_for_key(poison_circuit, mock_dto_topic_partition, b"bad-key")
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.UNORDERED,
        max_in_flight_messages=10,
        poison_message_circuit=poison_circuit,
        route_batch_size=3,
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
    )
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, b"route-key"): [
                (0, tracker.get_current_epoch(), b"payload-0", b"good-key"),
                (1, tracker.get_current_epoch(), b"payload-1", b"bad-key"),
                (2, tracker.get_current_epoch(), b"payload-2", b"later-key"),
            ]
        }
    )

    # When: The control-plane behavior is exercised for unordered route batch truncates before poison force fail candidate.
    await work_manager.schedule()

    submitted_offsets = [
        call.args[0].offset for call in mock_execution_engine.submit.await_args_list
    ]
    # Then: The expected unordered route batch truncates before poison force fail candidate behavior is asserted.
    assert submitted_offsets == [0]
    mock_execution_engine.submit_batch.assert_not_awaited()
    assert work_manager.get_total_in_flight_count() == 1
    assert work_manager.get_total_queued_messages() == 2


@pytest.mark.asyncio
async def test_unordered_route_batch_truncation_does_not_stop_other_routes(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for unordered route batch truncation does not stop other routes.
    poison_circuit = PoisonMessageCircuitBreaker(
        enabled=True,
        failure_threshold=1,
        cooldown_ms=60000,
        forced_failure_attempt=3,
    )
    _open_poison_circuit_for_key(poison_circuit, mock_dto_topic_partition, b"bad-key")
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.UNORDERED,
        max_in_flight_messages=10,
        poison_message_circuit=poison_circuit,
        route_batch_size=3,
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
    )
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, b"route-key"): [
                (0, tracker.get_current_epoch(), b"payload-0", b"good-key"),
                (1, tracker.get_current_epoch(), b"payload-1", b"bad-key"),
            ],
            (mock_dto_topic_partition, b"other-route"): [
                (2, tracker.get_current_epoch(), b"payload-2", b"other-key"),
            ],
        }
    )

    # When: The control-plane behavior is exercised for unordered route batch truncation does not stop other routes.
    await work_manager.schedule()

    submitted_offsets = [
        call.args[0].offset for call in mock_execution_engine.submit.await_args_list
    ]
    # Then: The expected unordered route batch truncation does not stop other routes behavior is asserted.
    assert submitted_offsets == [0, 2]
    assert work_manager.get_total_in_flight_count() == 2
    assert work_manager.get_total_queued_messages() == 1


@pytest.mark.asyncio
async def test_work_manager_batch_submit_error_accounts_only_accepted_count(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for work manager batch submit error accounts only accepted count.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.UNORDERED,
        max_in_flight_messages=10,
        route_batch_size=3,
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
    )
    work_manager.on_assign({mock_dto_topic_partition: tracker})
    mock_execution_engine.submit_batch.side_effect = BatchSubmitError(
        accepted_count=1,
        original_error=RuntimeError("partial submit failed"),
    )

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, b"route-key"): [
                (0, tracker.get_current_epoch(), b"payload-0", b"key-0"),
                (1, tracker.get_current_epoch(), b"payload-1", b"key-1"),
                (2, tracker.get_current_epoch(), b"payload-2", b"key-2"),
            ]
        }
    )

    # When: The control-plane behavior is exercised for work manager batch submit error accounts only accepted count.
    await work_manager.schedule()

    # Then: The expected work manager batch submit error accounts only accepted count behavior is asserted.
    assert mock_execution_engine.submit_batch.await_count == 1
    assert work_manager.get_total_in_flight_count() == 1
    assert work_manager.get_total_queued_messages() == 2
    assert len(work_manager._dispatch_timestamps) == 1
    dispatched_item_id = next(iter(work_manager._dispatch_timestamps))
    assert work_manager._in_flight_work_items[dispatched_item_id].offset == 0


@pytest.mark.asyncio
async def test_work_manager_generic_submit_batch_exception_counts_zero_accepted(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for work manager generic submit batch exception counts zero accepted.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.UNORDERED,
        max_in_flight_messages=10,
        route_batch_size=3,
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
    )
    work_manager.on_assign({mock_dto_topic_partition: tracker})
    mock_execution_engine.submit_batch.side_effect = RuntimeError(
        "generic submit failed"
    )

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, b"route-key"): [
                (0, tracker.get_current_epoch(), b"payload-0", b"key-0"),
                (1, tracker.get_current_epoch(), b"payload-1", b"key-1"),
                (2, tracker.get_current_epoch(), b"payload-2", b"key-2"),
            ]
        }
    )

    # When: The control-plane behavior is exercised for work manager generic submit batch exception counts zero accepted.
    await work_manager.schedule()

    # Then: The expected work manager generic submit batch exception counts zero accepted behavior is asserted.
    assert mock_execution_engine.submit_batch.await_count == 1
    assert work_manager.get_total_in_flight_count() == 0
    assert work_manager.get_total_queued_messages() == 3
    assert work_manager._dispatch_timestamps == {}


@pytest.mark.asyncio
async def test_unordered_route_batch_first_poison_candidate_uses_forced_failure_path(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for unordered route batch first poison candidate uses forced failure path.
    poison_circuit = PoisonMessageCircuitBreaker(
        enabled=True,
        failure_threshold=1,
        cooldown_ms=60000,
        forced_failure_attempt=3,
    )
    _open_poison_circuit_for_key(poison_circuit, mock_dto_topic_partition, b"bad-key")
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.UNORDERED,
        max_in_flight_messages=10,
        poison_message_circuit=poison_circuit,
        route_batch_size=3,
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
    )
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    await work_manager.submit_message_batch(
        {
            (mock_dto_topic_partition, b"route-key"): [
                (0, tracker.get_current_epoch(), b"payload-0", b"bad-key"),
            ]
        }
    )

    await work_manager.schedule()
    completed_events = await work_manager.poll_completed_events(
        schedule_after_release=False
    )

    mock_execution_engine.submit.assert_not_awaited()
    # When: The control-plane behavior is exercised for unordered route batch first poison candidate uses forced failure path.
    mock_execution_engine.submit_batch.assert_not_awaited()
    # Then: The expected unordered route batch first poison candidate uses forced failure path behavior is asserted.
    assert [event.offset for event in completed_events] == [0]
    forced_event = completed_events[0]
    assert forced_event.status == CompletionStatus.FAILURE
    assert forced_event.attempt == 3
    assert "Poison message circuit open" in str(forced_event.error)
