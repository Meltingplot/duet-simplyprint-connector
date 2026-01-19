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
    session.post.return_value.__aenter__.return_value.text = AsyncMock(return_value='ok')
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
    async for chunk in duet_printer._api_download(filepath='0:/gcodes/test.gcode'):
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
    """Verify tick falls back to polling when WebSocket task is done and not connected."""
    dsf_api = DuetSoftwareFramework(session=mock_dsf_session)
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    # Set initial object model (skip initialization)
    duet_printer.om = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    duet_printer.seqs = {'state': 1}

    # Simulate WebSocket task completed/failed (ws_task is None or done)
    object.__setattr__(duet_printer, '_ws_task', None)

    # ws_connected should return False (not connected)
    dsf_api._ws_connected = False

    # Mock the update model call
    updated_model = {'state': {'status': 'processing'}, 'seqs': {'state': 2}}
    mock_dsf_session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value=updated_model)

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


@pytest.mark.asyncio
async def test_websocket_loop_with_seqs_changes():
    """Verify _websocket_loop handles seqs changes by calling _handle_om_changes."""
    dsf_api = DuetSoftwareFramework()
    duet_printer = DuetPrinter(api=dsf_api, sbc=True)

    full_model = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    patch_model = {'state': {'status': 'processing'}, 'seqs': {'state': 2}}

    async def mock_subscribe():
        yield full_model
        yield patch_model

    dsf_api.subscribe = mock_subscribe

    # Mock _handle_om_changes to avoid API calls
    with patch.object(DuetPrinter, '_handle_om_changes', new_callable=AsyncMock) as mock_handle:
        events_emitted = []
        duet_printer.events.on(DuetModelEvents.objectmodel, lambda old_om: events_emitted.append(old_om))

        await duet_printer._websocket_loop()

        # _handle_om_changes should have been called with the changed seqs
        mock_handle.assert_called_once()
        call_args = mock_handle.call_args[0]
        assert 'state' in call_args[0]  # Changes dict contains 'state' key
