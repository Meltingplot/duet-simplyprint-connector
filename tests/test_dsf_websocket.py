# -*- coding: utf-8 -*-
"""Tests for Duet Software Framework (DSF) WebSocket subscription."""

import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import aiohttp

from .context import DuetSoftwareFramework


@pytest.fixture
def mock_session():
    """Create a mock session for DSF API."""
    session = AsyncMock(aiohttp.ClientSession)
    session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value={'sessionKey': 'test-key'})
    session.get.return_value.__aenter__.return_value.status = 200
    session.closed = False
    session.headers = {'X-Session-Key': 'test-key'}
    return session


@pytest.fixture
def dsf(mock_session):
    """Create DSF API instance with mock session."""
    return DuetSoftwareFramework(session=mock_session)


def create_mock_ws_message(msg_type, data=None):
    """Create a mock WebSocket message."""
    msg = MagicMock()
    msg.type = msg_type
    msg.data = data
    return msg


@pytest.mark.asyncio
async def test_subscribe_receives_full_model(dsf, mock_session):
    """Verify subscribe yields the full object model on first message."""
    full_model = {'state': {'status': 'idle'}, 'seqs': {'state': 1}}
    full_model_json = '{"state": {"status": "idle"}, "seqs": {"state": 1}}'

    # Create mock WebSocket
    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()

    # Mock messages - full model then close
    messages = [
        create_mock_ws_message(aiohttp.WSMsgType.TEXT, full_model_json),
        create_mock_ws_message(aiohttp.WSMsgType.CLOSE, None),
    ]

    async def mock_iter():
        for msg in messages:
            yield msg

    mock_ws.__aiter__ = lambda self: mock_iter()
    mock_session.ws_connect = AsyncMock(return_value=mock_ws)

    received = []
    async for data in dsf.subscribe():
        received.append(data)

    assert len(received) == 1
    assert received[0] == full_model
    mock_ws.send_str.assert_called_with('OK\n')


@pytest.mark.asyncio
async def test_subscribe_receives_patches(dsf, mock_session):
    """Verify subscribe yields subsequent patches after full model."""
    full_model_json = '{"state": {"status": "idle"}, "seqs": {"state": 1}}'
    patch_json = '{"state": {"status": "processing"}, "seqs": {"state": 2}}'

    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()

    messages = [
        create_mock_ws_message(aiohttp.WSMsgType.TEXT, full_model_json),
        create_mock_ws_message(aiohttp.WSMsgType.TEXT, patch_json),
        create_mock_ws_message(aiohttp.WSMsgType.CLOSE, None),
    ]

    async def mock_iter():
        for msg in messages:
            yield msg

    mock_ws.__aiter__ = lambda self: mock_iter()
    mock_session.ws_connect = AsyncMock(return_value=mock_ws)

    received = []
    async for data in dsf.subscribe():
        received.append(data)

    assert len(received) == 2
    assert received[0]['state']['status'] == 'idle'
    assert received[1]['state']['status'] == 'processing'
    assert mock_ws.send_str.call_count == 2  # OK sent for each message


@pytest.mark.asyncio
async def test_subscribe_sends_ok_acknowledgment(dsf, mock_session):
    """Verify client sends OK\\n after receiving each message."""
    full_model_json = '{"state": {"status": "idle"}}'

    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()

    messages = [
        create_mock_ws_message(aiohttp.WSMsgType.TEXT, full_model_json),
        create_mock_ws_message(aiohttp.WSMsgType.CLOSE, None),
    ]

    async def mock_iter():
        for msg in messages:
            yield msg

    mock_ws.__aiter__ = lambda self: mock_iter()
    mock_session.ws_connect = AsyncMock(return_value=mock_ws)

    async for _ in dsf.subscribe():
        pass

    mock_ws.send_str.assert_called_with('OK\n')


@pytest.mark.asyncio
async def test_subscribe_ignores_pong_responses(dsf, mock_session):
    """Verify PONG responses are ignored and not yielded."""
    full_model_json = '{"state": {"status": "idle"}}'

    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()

    messages = [
        create_mock_ws_message(aiohttp.WSMsgType.TEXT, full_model_json),
        create_mock_ws_message(aiohttp.WSMsgType.TEXT, 'PONG\n'),  # Should be ignored
        create_mock_ws_message(aiohttp.WSMsgType.CLOSE, None),
    ]

    async def mock_iter():
        for msg in messages:
            yield msg

    mock_ws.__aiter__ = lambda self: mock_iter()
    mock_session.ws_connect = AsyncMock(return_value=mock_ws)

    received = []
    async for data in dsf.subscribe():
        received.append(data)

    # Only the JSON model should be received, not PONG
    assert len(received) == 1
    assert received[0] == {'state': {'status': 'idle'}}


@pytest.mark.asyncio
async def test_ping_pong(dsf, mock_session):
    """Verify PING/PONG keepalive works."""
    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    dsf._ws = mock_ws
    dsf._ws_connected = True

    result = await dsf.send_ping()

    assert result is True
    mock_ws.send_str.assert_called_with('PING\n')


@pytest.mark.asyncio
async def test_send_ping_returns_false_when_not_connected(dsf):
    """Verify send_ping returns False when not connected."""
    dsf._ws = None
    dsf._ws_connected = False

    result = await dsf.send_ping()

    assert result is False


@pytest.mark.asyncio
async def test_send_ping_returns_false_when_ws_closed(dsf):
    """Verify send_ping returns False when WebSocket is closed."""
    mock_ws = MagicMock()
    mock_ws.closed = True
    dsf._ws = mock_ws
    dsf._ws_connected = True

    result = await dsf.send_ping()

    assert result is False


@pytest.mark.asyncio
async def test_unsubscribe_closes_connection(dsf, mock_session):
    """Verify unsubscribe closes the WebSocket connection."""
    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.close = AsyncMock()
    dsf._ws = mock_ws
    dsf._ws_connected = True

    await dsf.unsubscribe()

    mock_ws.close.assert_called_once()
    assert dsf._ws is None
    assert dsf._ws_connected is False


@pytest.mark.asyncio
async def test_unsubscribe_handles_already_closed(dsf):
    """Verify unsubscribe handles already closed connection gracefully."""
    mock_ws = MagicMock()
    mock_ws.closed = True
    dsf._ws = mock_ws
    dsf._ws_connected = True

    await dsf.unsubscribe()

    assert dsf._ws is None
    assert dsf._ws_connected is False


@pytest.mark.asyncio
async def test_ws_connected_property(dsf):
    """Verify ws_connected property tracks connection state."""
    # Initially not connected
    assert dsf.ws_connected is False

    # With ws object but not marked connected
    mock_ws = MagicMock()
    mock_ws.closed = False
    dsf._ws = mock_ws
    dsf._ws_connected = False
    assert dsf.ws_connected is False

    # Fully connected
    dsf._ws_connected = True
    assert dsf.ws_connected is True

    # WS object marked closed
    mock_ws.closed = True
    assert dsf.ws_connected is False


@pytest.mark.asyncio
async def test_subscribe_handles_ws_error(dsf, mock_session):
    """Verify subscribe handles WebSocket errors gracefully."""
    full_model_json = '{"state": {"status": "idle"}}'

    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    mock_ws.exception = MagicMock(return_value=Exception("Connection lost"))
    mock_ws.close = AsyncMock()

    messages = [
        create_mock_ws_message(aiohttp.WSMsgType.TEXT, full_model_json),
        create_mock_ws_message(aiohttp.WSMsgType.ERROR, None),
    ]

    async def mock_iter():
        for msg in messages:
            yield msg

    mock_ws.__aiter__ = lambda self: mock_iter()
    mock_session.ws_connect = AsyncMock(return_value=mock_ws)

    received = []
    async for data in dsf.subscribe():
        received.append(data)

    assert len(received) == 1
    assert dsf._ws_connected is False


@pytest.mark.asyncio
async def test_subscribe_handles_invalid_json(dsf, mock_session):
    """Verify subscribe handles invalid JSON gracefully."""
    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    mock_ws.close = AsyncMock()

    messages = [
        create_mock_ws_message(aiohttp.WSMsgType.TEXT, 'not valid json {{{'),
        create_mock_ws_message(aiohttp.WSMsgType.TEXT, '{"state": {"status": "idle"}}'),
        create_mock_ws_message(aiohttp.WSMsgType.CLOSE, None),
    ]

    async def mock_iter():
        for msg in messages:
            yield msg

    mock_ws.__aiter__ = lambda self: mock_iter()
    mock_session.ws_connect = AsyncMock(return_value=mock_ws)

    received = []
    async for data in dsf.subscribe():
        received.append(data)

    # Invalid JSON should be skipped, valid JSON should be received
    assert len(received) == 1
    assert received[0] == {'state': {'status': 'idle'}}


@pytest.mark.asyncio
async def test_subscribe_builds_correct_ws_url(dsf, mock_session):
    """Verify subscribe builds the correct WebSocket URL."""
    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    mock_ws.close = AsyncMock()

    messages = [create_mock_ws_message(aiohttp.WSMsgType.CLOSE, None)]

    async def mock_iter():
        for msg in messages:
            yield msg

    mock_ws.__aiter__ = lambda self: mock_iter()
    mock_session.ws_connect = AsyncMock(return_value=mock_ws)

    async for _ in dsf.subscribe():
        pass

    # Check ws_connect was called with correct URL
    call_args = mock_session.ws_connect.call_args
    ws_url = call_args[0][0]
    assert ws_url == 'ws://10.42.0.2/machine?sessionKey=test-key'
    assert call_args[1]['heartbeat'] == 30.0


@pytest.mark.asyncio
async def test_subscribe_builds_https_ws_url():
    """Verify subscribe converts https to wss for WebSocket URL."""
    mock_session = AsyncMock(aiohttp.ClientSession)
    mock_session.closed = False
    mock_session.headers = {'X-Session-Key': 'test-key'}

    dsf = DuetSoftwareFramework(address='https://secure.printer.local', session=mock_session)

    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    mock_ws.close = AsyncMock()

    messages = [create_mock_ws_message(aiohttp.WSMsgType.CLOSE, None)]

    async def mock_iter():
        for msg in messages:
            yield msg

    mock_ws.__aiter__ = lambda self: mock_iter()
    mock_session.ws_connect = AsyncMock(return_value=mock_ws)

    async for _ in dsf.subscribe():
        pass

    call_args = mock_session.ws_connect.call_args
    ws_url = call_args[0][0]
    assert ws_url.startswith('wss://')


@pytest.mark.asyncio
async def test_subscribe_connection_error(dsf, mock_session):
    """Verify subscribe raises on connection error."""
    mock_session.ws_connect = AsyncMock(side_effect=aiohttp.ClientError("Connection refused"))

    with pytest.raises(aiohttp.ClientError):
        async for _ in dsf.subscribe():
            pass


@pytest.mark.asyncio
async def test_subscribe_cleans_up_on_error(dsf, mock_session):
    """Verify WebSocket is cleaned up after connection error."""
    mock_session.ws_connect = AsyncMock(side_effect=aiohttp.ClientError("Connection refused"))

    try:
        async for _ in dsf.subscribe():
            pass
    except aiohttp.ClientError:
        pass

    assert dsf._ws is None
    assert dsf._ws_connected is False
