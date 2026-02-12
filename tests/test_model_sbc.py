"""Tests for DuetPrinter SBC mode integration."""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from .context import DuetPrinter, DuetSoftwareFramework, RepRapFirmware, DuetModelEvents


@pytest.fixture
def mock_rrf_session():
    """Create a mock session for RepRapFirmware API."""
    session = AsyncMock(aiohttp.ClientSession)
    session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value={'err': 0, 'isEmulated': True})
    session.closed = False
    return session


@pytest.fixture
def mock_dsf_session():
    """Create a mock session for DSF API."""
    session = AsyncMock(aiohttp.ClientSession)
    session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={
            'state': {
                'status': 'idle'
            },
            'seqs': {}
        }
    )
    session.get.return_value.__aenter__.return_value.raise_for_status = MagicMock()
    session.post.return_value.__aenter__.return_value.text = AsyncMock(return_value='ok')
    session.post.return_value.__aenter__.return_value.raise_for_status = MagicMock()
    session.closed = False
    return session


@pytest.fixture
def rrf_api(mock_rrf_session):
    """Create RepRapFirmware API instance with mock session."""
    return RepRapFirmware(session=mock_rrf_session)


@pytest.fixture
def duet_printer(rrf_api):
    """Create DuetPrinter with RepRapFirmware API."""
    return DuetPrinter(api=rrf_api)


@pytest.mark.asyncio
async def test_connect_switches_to_dsf_api(duet_printer, mock_rrf_session):
    """Verify API switches to DSF when isEmulated is present in connect response."""
    # Mock connect response with isEmulated
    mock_rrf_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={
            'err': 0,
            'isEmulated': True
        }
    )

    # Mock full model fetch response for DSF
    full_model = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    with patch.object(DuetSoftwareFramework, 'model', new_callable=AsyncMock) as mock_model:
        mock_model.return_value = full_model

        await duet_printer.connect()

        # Verify SBC mode was detected
        assert duet_printer.sbc is True

        # Verify API was switched to DSF
        assert isinstance(duet_printer.api, DuetSoftwareFramework)

        # Verify object model was set
        assert duet_printer.om == full_model


@pytest.mark.asyncio
async def test_gcode_sbc_returns_reply_directly(mock_dsf_session):
    """Verify DSF code() returns reply directly without polling."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set object model to avoid connect
    duet_printer.om = {'state': {'status': 'idle'}}

    expected_reply = 'ok'
    mock_dsf_session.post.return_value.__aenter__.return_value.text = AsyncMock(return_value=expected_reply)

    # Test with reply requested
    result = await duet_printer.gcode('G28', no_reply=False)

    assert result == expected_reply
    assert duet_printer._reply == expected_reply
    assert duet_printer._wait_for_reply.is_set()


@pytest.mark.asyncio
async def test_gcode_sbc_no_reply(mock_dsf_session):
    """Verify DSF gcode with no_reply=True."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set object model to avoid connect
    duet_printer.om = {'state': {'status': 'idle'}}

    mock_dsf_session.post.return_value.__aenter__.return_value.text = AsyncMock(return_value='')

    result = await duet_printer.gcode('G28', no_reply=True)

    assert result == ''


@pytest.mark.asyncio
async def test_fetch_model_sbc_wraps_response(mock_dsf_session):
    """Verify response format normalization for SBC mode."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    full_model = {'state': {'status': 'idle'}, 'move': {'axes': []}, 'seqs': {'state': 1}}
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value=full_model)

    # Fetch full model
    result = await duet_printer._fetch_objectmodel_recursive(key='', depth=1)

    assert 'result' in result
    assert result['result'] == full_model
    assert result['next'] == 0


@pytest.mark.asyncio
async def test_fetch_model_sbc_extracts_key(mock_dsf_session):
    """Verify key extraction works in SBC mode."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    full_model = {
        'state': {
            'status': 'idle',
            'upTime': 12345
        },
        'move': {
            'axes': [{
                'letter': 'X'
            }]
        },
        'seqs': {
            'state': 1
        }
    }
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value=full_model)

    # Fetch specific key
    result = await duet_printer._fetch_objectmodel_recursive(key='state', depth=2)

    assert result['result'] == {'status': 'idle', 'upTime': 12345}


@pytest.mark.asyncio
async def test_update_object_model_sbc(mock_dsf_session):
    """Verify model updates work in SBC mode."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set initial object model
    duet_printer.om = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    duet_printer.seqs = {'state': 1}

    # Mock updated model
    updated_model = {'state': {'status': 'processing'}, 'seqs': {'state': 2}}
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value=updated_model)

    events_emitted = []
    duet_printer.events.on(DuetModelEvents.objectmodel, lambda old_om: events_emitted.append(old_om))

    await duet_printer._update_object_model()

    # Verify model was updated
    assert duet_printer.om['state']['status'] == 'processing'
    assert len(events_emitted) == 1


@pytest.mark.asyncio
async def test_update_object_model_old_om_is_deep_copy(mock_dsf_session):
    """Verify old_om in _update_object_model is a deep copy.

    With a shallow copy, nested dicts in old_om would be shared
    references to self.om, so after merge_dictionary updates self.om
    the old_om values would also change — breaking state change detection.
    """
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set initial object model with nested data
    duet_printer.om = {
        'state': {'status': 'idle'},
        'heat': {'heaters': [{'current': 20.0, 'state': 'off'}]},
        'seqs': {'state': 1},
    }
    duet_printer.seqs = {'state': 1}

    # Mock updated model with changed nested values
    updated_model = {
        'state': {'status': 'processing'},
        'heat': {'heaters': [{'current': 60.0, 'state': 'active'}]},
        'seqs': {'state': 1},
    }
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value=updated_model)

    events_emitted = []
    duet_printer.events.on(DuetModelEvents.objectmodel, lambda old_om: events_emitted.append(old_om))

    await duet_printer._update_object_model()

    assert len(events_emitted) == 1
    old_om = events_emitted[0]

    # old_om must retain pre-merge values
    assert old_om['state']['status'] == 'idle', \
        "old_om should retain the pre-merge state (shallow copy bug)"
    assert old_om['heat']['heaters'][0]['current'] == 20.0, \
        "old_om should retain original heater temperature (shallow copy bug)"
    assert old_om['heat']['heaters'][0]['state'] == 'off', \
        "old_om should retain original heater state (shallow copy bug)"

    # Current om should have the new values
    assert duet_printer.om['state']['status'] == 'processing'
    assert duet_printer.om['heat']['heaters'][0]['current'] == 60.0


@pytest.mark.asyncio
async def test_download_sbc_mode(mock_dsf_session):
    """Verify file downloads work in SBC mode."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    async def chunks(size):
        data = [b'chunk1', b'chunk2']
        for item in data:
            yield item

    mock_dsf_session.get.return_value.__aenter__.return_value.content.iter_chunked = chunks

    downloaded = []
    async for chunk in duet_printer.download(filepath='0:/gcodes/test.gcode'):
        downloaded.append(chunk)

    assert downloaded == [b'chunk1', b'chunk2']


@pytest.mark.asyncio
async def test_handle_om_changes_sbc_no_rr_reply(mock_dsf_session):
    """Verify SBC mode doesn't call rr_reply for reply changes."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)
    duet_printer.om = {'state': {'status': 'idle'}}

    # Mock the model fetch for state change
    state_model = {'status': 'idle'}
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={
            'state': state_model,
            'seqs': {}
        }
    )

    # Pre-set reply (DSF sets it directly during gcode())
    duet_printer._reply = 'test reply'

    changes = {'reply': 1, 'state': 2}
    await duet_printer._handle_om_changes(changes)

    # Verify wait_for_reply was set
    assert duet_printer._wait_for_reply.is_set()
    # Verify reply was not overwritten (DSF doesn't have rr_reply)
    assert duet_printer._reply == 'test reply'


@pytest.mark.asyncio
async def test_http_503_callback_sbc_sleeps():
    """Verify SBC mode just sleeps on 503 errors."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # This should not raise and should just sleep
    error = aiohttp.ClientResponseError(MagicMock(), (), status=503, message='Service Unavailable')

    # Should complete without error (just sleeps)
    with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
        await duet_printer._http_503_callback(error)
        mock_sleep.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_connect_preserves_session():
    """Verify session is transferred from RRF to DSF API."""
    mock_session = AsyncMock(aiohttp.ClientSession)
    mock_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value={'err': 0, 'isEmulated': True})
    mock_session.closed = False

    rrf_api = RepRapFirmware(session=mock_session)
    duet_printer = DuetPrinter(api=rrf_api)

    # Mock full model fetch
    with patch.object(DuetSoftwareFramework, 'model', new_callable=AsyncMock) as mock_model:
        mock_model.return_value = {'state': {'status': 'idle'}, 'seqs': {}}

        await duet_printer.connect()

        # Verify session was transferred
        assert duet_printer.api.session == mock_session
        # Verify original API session was cleared
        assert rrf_api.session is None


# WebSocket integration tests

@pytest.mark.asyncio
async def test_tick_starts_websocket_in_sbc_mode(mock_dsf_session):
    """Verify WebSocket subscription starts after initial model fetch in SBC mode."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Mock model() for initial fetch
    full_model = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value=full_model)

    # Track if _start_websocket_subscription was called via patching the class method
    with patch.object(DuetPrinter, '_start_websocket_subscription', new_callable=AsyncMock) as mock_start_ws:
        await duet_printer.tick()

        # Verify WebSocket subscription was started
        mock_start_ws.assert_called_once()


@pytest.mark.asyncio
async def test_tick_does_not_start_websocket_when_disabled(mock_dsf_session):
    """Verify WebSocket subscription is not started when _ws_enabled is False."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)
    # Disable WebSocket after creation
    object.__setattr__(duet_printer, '_ws_enabled', False)

    full_model = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value=full_model)

    with patch.object(DuetPrinter, '_start_websocket_subscription', new_callable=AsyncMock) as mock_start_ws:
        await duet_printer.tick()

        # Verify WebSocket subscription was not started
        mock_start_ws.assert_not_called()


@pytest.mark.asyncio
async def test_websocket_fallback_to_polling(mock_dsf_session):
    """Verify tick falls back to polling when WebSocket task is done and retry time not reached."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set initial object model (skip initialization)
    duet_printer.om = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    duet_printer.seqs = {'state': 1}

    # Simulate WebSocket task completed/failed (ws_task is None or done)
    object.__setattr__(duet_printer, '_ws_task', None)

    # Set retry time in the future (so it falls back to polling)
    duet_printer._ws_retry_at = 2000.0
    duet_printer._ws_retry_count = 1

    # Mock the update model call
    updated_model = {'state': {'status': 'processing'}, 'seqs': {'state': 2}}
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value=updated_model)

    # Current time is before retry time
    with patch('time.monotonic', return_value=1000.0):
        with patch.object(DuetPrinter, '_update_object_model', new_callable=AsyncMock) as mock_update:
            await duet_printer.tick()

            # Verify fallback to polling
            mock_update.assert_called_once()


@pytest.mark.asyncio
async def test_tick_restarts_websocket_when_still_connected(mock_dsf_session):
    """Verify tick restarts WebSocket when task is done but API still connected."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set initial object model
    duet_printer.om = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    duet_printer.seqs = {'state': 1}

    # Simulate WebSocket task completed
    object.__setattr__(duet_printer, '_ws_task', None)

    # But ws_connected is still True (shouldn't happen normally, but test the logic)
    dsf_api._ws = MagicMock()
    dsf_api._ws.closed = False
    dsf_api._ws_connected = True

    with patch.object(DuetPrinter, '_start_websocket_subscription', new_callable=AsyncMock) as mock_start_ws:
        await duet_printer.tick()

        # Should restart WebSocket
        mock_start_ws.assert_called_once()


@pytest.mark.asyncio
async def test_tick_skips_update_when_websocket_running(mock_dsf_session):
    """Verify tick does nothing when WebSocket task is running."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set initial object model
    duet_printer.om = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}

    # Simulate WebSocket task running
    mock_task = MagicMock()
    mock_task.done.return_value = False
    object.__setattr__(duet_printer, '_ws_task', mock_task)

    with patch.object(DuetPrinter, '_update_object_model', new_callable=AsyncMock) as mock_update:
        with patch.object(DuetPrinter, '_start_websocket_subscription', new_callable=AsyncMock) as mock_start_ws:
            await duet_printer.tick()

            # Neither update nor restart should be called
            mock_update.assert_not_called()
            mock_start_ws.assert_not_called()


@pytest.mark.asyncio
async def test_close_stops_websocket(mock_dsf_session):
    """Verify close() stops WebSocket subscription."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    with patch.object(DuetPrinter, '_stop_websocket_subscription', new_callable=AsyncMock) as mock_stop_ws:
        await duet_printer.close()

        mock_stop_ws.assert_called_once()


@pytest.mark.asyncio
async def test_stop_websocket_cancels_task():
    """Verify _stop_websocket_subscription cancels the task."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Create a real async task that we can cancel
    started_event = asyncio.Event()

    async def dummy_task():
        started_event.set()  # Signal task has started
        await asyncio.sleep(100)  # Long sleep, will be cancelled

    task = asyncio.create_task(dummy_task())

    # Wait for task to start
    await started_event.wait()

    object.__setattr__(duet_printer, '_ws_task', task)

    # Mock unsubscribe
    dsf_api.unsubscribe = AsyncMock()

    await duet_printer._stop_websocket_subscription()

    # Task should have been cancelled
    assert task.cancelled()
    dsf_api.unsubscribe.assert_called_once()
    assert duet_printer._ws_task is None


@pytest.mark.asyncio
async def test_websocket_loop_processes_full_model():
    """Verify _websocket_loop processes first message as full model."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    full_model = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}

    async def mock_subscribe():
        yield full_model

    dsf_api.subscribe = mock_subscribe

    events_emitted = []
    duet_printer.events.on(DuetModelEvents.objectmodel, lambda old_om: events_emitted.append(old_om))

    await duet_printer._websocket_loop()

    assert duet_printer.om == full_model
    assert duet_printer.seqs == {'state': 1}
    assert len(events_emitted) == 1
    assert events_emitted[0] is None  # First message, old_om is None


@pytest.mark.asyncio
async def test_websocket_loop_processes_patches():
    """Verify _websocket_loop merges subsequent messages as patches."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Use same seqs values to avoid triggering _handle_om_changes
    # which would try to fetch from the API
    full_model = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    patch_model = {'state': {'status': 'processing'}, 'seqs': {'state': 1}}  # Same seqs

    async def mock_subscribe():
        yield full_model
        yield patch_model

    dsf_api.subscribe = mock_subscribe

    events_emitted = []
    duet_printer.events.on(DuetModelEvents.objectmodel, lambda old_om: events_emitted.append(old_om))

    await duet_printer._websocket_loop()

    # Final state should be merged
    assert duet_printer.om['state']['status'] == 'processing'
    assert len(events_emitted) == 2


@pytest.mark.asyncio
async def test_websocket_old_om_is_deep_copy():
    """Verify old_om passed to event listeners is a deep copy.

    A shallow copy would share nested dict references with self.om,
    so after merge the old_om would reflect the new state — silently
    dropping state change events.
    """
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    full_model = {
        'state': {'status': 'idle'},
        'heat': {'heaters': [{'current': 20.0, 'state': 'off'}]},
        'seqs': {'state': 1},
    }
    patch_model = {
        'state': {'status': 'processing'},
        'heat': {'heaters': [{'current': 60.0, 'state': 'active'}]},
        'seqs': {'state': 1},
    }

    async def mock_subscribe():
        yield full_model
        yield patch_model

    dsf_api.subscribe = mock_subscribe

    events_emitted = []
    duet_printer.events.on(DuetModelEvents.objectmodel, lambda old_om: events_emitted.append(old_om))

    await duet_printer._websocket_loop()

    # The patch event should have old_om with the ORIGINAL values
    old_om = events_emitted[1]
    assert old_om['state']['status'] == 'idle', \
        "old_om should retain the pre-merge state (shallow copy bug)"
    assert old_om['heat']['heaters'][0]['current'] == 20.0, \
        "old_om should retain original heater temperature (shallow copy bug)"
    assert old_om['heat']['heaters'][0]['state'] == 'off', \
        "old_om should retain original heater state (shallow copy bug)"

    # Current om should have the new values
    assert duet_printer.om['state']['status'] == 'processing'
    assert duet_printer.om['heat']['heaters'][0]['current'] == 60.0


@pytest.mark.asyncio
async def test_websocket_loop_handles_merge_error():
    """Verify _websocket_loop handles merge errors gracefully."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    full_model = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    # Invalid patch - merge_dictionary will fail with ValueError for list length mismatch
    # when trying to merge lists of different sizes
    invalid_patch = {'items': [1, 2, 3], 'seqs': {'state': 1}}  # New list
    # Valid model after recovery
    recovery_model = {'state': {'status': 'paused'}, 'seqs': {'state': 1}}

    async def mock_subscribe():
        yield full_model
        # Inject an invalid patch after setting up initial model
        # The merge will fail because om doesn't have 'items' with matching list length
        yield {'items': [1, 2, 3], 'seqs': {'state': 1}}  # om has no 'items', adds it
        yield {'items': [1], 'seqs': {'state': 1}}  # Shorter list will cause ValueError
        yield recovery_model  # Should be treated as full model after error

    dsf_api.subscribe = mock_subscribe

    await duet_printer._websocket_loop()

    # After error, recovery_model should be set as full model
    assert duet_printer.om['state']['status'] == 'paused'


# WebSocket reconnection with exponential backoff tests

@pytest.mark.asyncio
async def test_websocket_schedules_retry_on_failure():
    """Verify retry is scheduled after WebSocket fails."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Mock subscribe to fail immediately
    async def mock_subscribe():
        raise ConnectionError("WebSocket failed")
        yield  # Make it a generator

    dsf_api.subscribe = mock_subscribe

    with patch('time.monotonic', return_value=1000.0):
        await duet_printer._websocket_loop()

    # Verify retry was scheduled
    assert duet_printer._ws_retry_at == 1000.0 + 60  # First retry after 60s
    assert duet_printer._ws_retry_count == 1


@pytest.mark.asyncio
async def test_websocket_retry_backoff_increases():
    """Verify backoff increases: 60s -> 300s -> 1800s."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    with patch('time.monotonic', return_value=1000.0):
        # First retry
        duet_printer._schedule_ws_retry()
        assert duet_printer._ws_retry_at == 1060.0  # 1000 + 60
        assert duet_printer._ws_retry_count == 1

        # Second retry
        duet_printer._schedule_ws_retry()
        assert duet_printer._ws_retry_at == 1300.0  # 1000 + 300
        assert duet_printer._ws_retry_count == 2

        # Third retry
        duet_printer._schedule_ws_retry()
        assert duet_printer._ws_retry_at == 2800.0  # 1000 + 1800
        assert duet_printer._ws_retry_count == 3


@pytest.mark.asyncio
async def test_websocket_retry_backoff_caps_at_max():
    """Verify backoff doesn't exceed 30 minutes."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set retry count beyond the delay list
    duet_printer._ws_retry_count = 10

    with patch('time.monotonic', return_value=1000.0):
        duet_printer._schedule_ws_retry()

    # Should use max delay (1800s = 30 minutes)
    assert duet_printer._ws_retry_at == 1000.0 + 1800
    assert duet_printer._ws_retry_count == 11


@pytest.mark.asyncio
async def test_websocket_retry_resets_on_success():
    """Verify backoff resets on successful reconnection before next failure."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set up retry state as if we've been failing (count=3 means next delay would be 1800s)
    duet_printer._ws_retry_count = 3
    duet_printer._ws_retry_at = 5000.0

    full_model = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}

    async def mock_subscribe():
        yield full_model
        # Generator ends normally after one message

    dsf_api.subscribe = mock_subscribe

    with patch('time.monotonic', return_value=1000.0):
        await duet_printer._websocket_loop()

    # After successful message reception, counters were reset to 0.
    # Then when loop exits (generator exhausted), a new retry is scheduled.
    # The key is that retry_count is now 1 (not 4), meaning backoff was reset.
    assert duet_printer._ws_retry_count == 1
    # Retry scheduled with first delay (60s), not third delay (1800s)
    assert duet_printer._ws_retry_at == 1000.0 + 60


@pytest.mark.asyncio
async def test_tick_polls_until_retry_time(mock_dsf_session):
    """Verify polling continues until retry time reached."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set initial object model
    duet_printer.om = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    duet_printer.seqs = {'state': 1}

    # Set retry time in the future
    duet_printer._ws_retry_at = 2000.0
    duet_printer._ws_retry_count = 1

    # Mock model response
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    )

    # Current time is before retry time
    with patch('time.monotonic', return_value=1000.0):
        with patch.object(DuetPrinter, '_update_object_model', new_callable=AsyncMock) as mock_update:
            with patch.object(DuetPrinter, '_start_websocket_subscription', new_callable=AsyncMock) as mock_start_ws:
                await duet_printer.tick()

                # Should poll, not attempt WebSocket
                mock_update.assert_called_once()
                mock_start_ws.assert_not_called()


@pytest.mark.asyncio
async def test_tick_attempts_websocket_at_retry_time(mock_dsf_session):
    """Verify WebSocket attempt at retry time."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set initial object model
    duet_printer.om = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    duet_printer.seqs = {'state': 1}

    # Set retry time in the past
    duet_printer._ws_retry_at = 500.0
    duet_printer._ws_retry_count = 1

    # Current time is after retry time
    with patch('time.monotonic', return_value=1000.0):
        with patch.object(DuetPrinter, '_start_websocket_subscription', new_callable=AsyncMock) as mock_start_ws:
            await duet_printer.tick()

            # Should attempt WebSocket reconnection
            mock_start_ws.assert_called_once()


@pytest.mark.asyncio
async def test_close_resets_retry_state(mock_dsf_session):
    """Verify close() resets retry state."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set up retry state
    duet_printer._ws_retry_count = 3
    duet_printer._ws_retry_at = 5000.0

    await duet_printer.close()

    # Verify retry state was reset
    assert duet_printer._ws_retry_count == 0
    assert duet_printer._ws_retry_at is None


@pytest.mark.asyncio
async def test_should_retry_websocket_returns_true_when_not_scheduled():
    """Verify _should_retry_websocket returns True when no retry is scheduled."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # No retry scheduled
    duet_printer._ws_retry_at = None

    assert duet_printer._should_retry_websocket() is True


@pytest.mark.asyncio
async def test_should_retry_websocket_returns_false_before_time():
    """Verify _should_retry_websocket returns False before retry time."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Retry scheduled in the future
    duet_printer._ws_retry_at = 2000.0

    with patch('time.monotonic', return_value=1000.0):
        assert duet_printer._should_retry_websocket() is False


@pytest.mark.asyncio
async def test_should_retry_websocket_returns_true_after_time():
    """Verify _should_retry_websocket returns True after retry time."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Retry scheduled in the past
    duet_printer._ws_retry_at = 500.0

    with patch('time.monotonic', return_value=1000.0):
        assert duet_printer._should_retry_websocket() is True


# --- Additional coverage tests ---


def test_merge_dictionary_non_dict_destination():
    """Test merge_dictionary returns deepcopy of source when destination is not a dict."""
    from meltingplot.duet_simplyprint_connector.duet.model import merge_dictionary
    source = {'key': 'value', 'nested': {'a': 1}}
    result = merge_dictionary(source, "not a dict")
    assert result == source
    assert result is not source
    assert result['nested'] is not source['nested']


def test_state_property_when_om_is_none():
    """Test state property returns disconnected when om is None."""
    from meltingplot.duet_simplyprint_connector.duet.model import DuetState
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, om=None)
    assert duet_printer.state == DuetState.disconnected


@pytest.mark.asyncio
async def test_track_state_invalid_old_state():
    """Test _track_state returns early on invalid old_om state value."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(
        api=dsf_api,
        om={'state': {'status': 'idle'}},
    )
    # Mock events to check emit is not called
    duet_printer.events = MagicMock()

    # old_om with invalid state value
    old_om = {'state': {'status': 'totally_invalid'}}
    await duet_printer._track_state(old_om)

    # Should return early due to ValueError, not emit
    duet_printer.events.emit.assert_not_called()


@pytest.mark.asyncio
async def test_track_state_old_om_none():
    """Test _track_state returns early when old_om is None."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(
        api=dsf_api,
        om={'state': {'status': 'idle'}},
    )
    duet_printer.events = MagicMock()

    await duet_printer._track_state(None)

    duet_printer.events.emit.assert_not_called()


def test_connected_returns_false_when_session_none():
    """Test connected() returns False when session is None."""
    rrf_api = RepRapFirmware()
    rrf_api.session = None
    duet_printer = DuetPrinter(api=rrf_api)
    assert duet_printer.connected() is False


def test_connected_returns_false_when_session_closed():
    """Test connected() returns False when session is closed."""
    rrf_api = RepRapFirmware()
    rrf_api.session = MagicMock()
    rrf_api.session.closed = True
    duet_printer = DuetPrinter(api=rrf_api)
    assert duet_printer.connected() is False


def test_connected_returns_true_when_session_open():
    """Test connected() returns True when session is open."""
    rrf_api = RepRapFirmware()
    rrf_api.session = MagicMock()
    rrf_api.session.closed = False
    duet_printer = DuetPrinter(api=rrf_api)
    assert duet_printer.connected() is True


@pytest.mark.asyncio
async def test_gcode_rrf_no_reply():
    """Test gcode() in RRF mode with no_reply=True returns empty string."""
    rrf_api = RepRapFirmware()
    rrf_api.session = MagicMock()
    rrf_api.session.closed = False
    duet_printer = DuetPrinter(api=rrf_api, sbc=False)
    duet_printer.api.send_gcode = AsyncMock(return_value='ok')

    result = await duet_printer.gcode('G28', no_reply=True)
    assert result == ''


@pytest.mark.asyncio
async def test_gcode_rrf_with_reply():
    """Test gcode() in RRF mode with no_reply=False waits for reply."""
    rrf_api = RepRapFirmware()
    rrf_api.session = MagicMock()
    rrf_api.session.closed = False
    duet_printer = DuetPrinter(api=rrf_api, sbc=False)
    duet_printer.api.send_gcode = AsyncMock(return_value='')

    # Simulate reply arriving shortly after gcode is sent
    async def simulate_reply():
        await asyncio.sleep(0.01)
        duet_printer._reply = 'M115 reply'
        duet_printer._wait_for_reply.set()

    asyncio.create_task(simulate_reply())
    result = await duet_printer.gcode('M115', no_reply=False)
    assert result == 'M115 reply'


@pytest.mark.asyncio
async def test_reply_returns_reply_value():
    """Test reply() waits for event and returns _reply."""
    rrf_api = RepRapFirmware()
    duet_printer = DuetPrinter(api=rrf_api)

    async def set_reply():
        await asyncio.sleep(0.01)
        duet_printer._reply = 'the reply'
        duet_printer._wait_for_reply.set()

    asyncio.create_task(set_reply())
    result = await duet_printer.reply()
    assert result == 'the reply'


@pytest.mark.asyncio
async def test_handle_om_changes_reply_rrf(mock_rrf_session):
    """Test _handle_om_changes processes reply in RRF mode."""
    rrf_api = RepRapFirmware(session=mock_rrf_session)
    duet_printer = DuetPrinter(api=rrf_api, sbc=False)
    duet_printer.om = {'state': {'status': 'idle'}, 'seqs': {}}

    # Mock rr_reply
    mock_rrf_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value={'result': {}})
    mock_rrf_session.get.return_value.__aenter__.return_value.text = AsyncMock(return_value='G28 reply text')
    mock_rrf_session.get.return_value.__aenter__.return_value.raise_for_status = MagicMock()

    changes = {'reply': 1}
    await duet_printer._handle_om_changes(changes)

    assert duet_printer._wait_for_reply.is_set()
    assert 'reply' not in changes
