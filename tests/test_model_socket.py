# -*- coding: utf-8 -*-
"""Tests for DuetPrinter socket connection integration."""

import asyncio
import json
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from .context import DuetPrinter, DuetModelEvents, RepRapFirmware, DuetControlSocket


@pytest.fixture
def mock_rrf_session():
    """Create a mock session for RepRapFirmware API."""
    session = AsyncMock(aiohttp.ClientSession)
    session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={'err': 0},
    )
    session.closed = False
    return session


@pytest.fixture
def rrf_api(mock_rrf_session):
    """Create RepRapFirmware API instance with mock session."""
    return RepRapFirmware(session=mock_rrf_session)


@pytest.fixture
def duet_printer(rrf_api):
    """Create DuetPrinter with RepRapFirmware API."""
    return DuetPrinter(api=rrf_api, socket_path='/nonexistent/dcs.sock')


@pytest.fixture
def duet_printer_with_socket(rrf_api, tmp_path):
    """Create DuetPrinter with a socket path that exists."""
    sock_path = tmp_path / 'dcs.sock'
    sock_path.touch()  # Create the file to simulate socket existence
    return DuetPrinter(
        api=rrf_api,
        socket_path=str(sock_path),
    )


@pytest.mark.asyncio
async def test_connect_no_socket_uses_http(duet_printer, mock_rrf_session):
    """When socket doesn't exist, fall back to HTTP API."""
    # Mock rr_connect
    mock_rrf_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={'err': 0},
    )

    # Mock _fetch_full_status to avoid recursive model fetching
    full_status = {
        'result': {
            'state': {
                'status': 'idle'
            },
            'seqs': {},
            'boards': [{
                'uniqueId': 'test'
            }],
        },
    }
    with patch.object(DuetPrinter, '_fetch_full_status', return_value=full_status):
        await duet_printer.connect()

    assert isinstance(duet_printer.api, RepRapFirmware)
    assert duet_printer.sbc is False


@pytest.mark.asyncio
async def test_connect_socket_exists_uses_socket_api(duet_printer_with_socket):
    """When DCS socket exists, connect via socket directly."""
    mock_reader = AsyncMock()
    mock_writer = MagicMock()
    mock_writer.write = MagicMock()
    mock_writer.drain = AsyncMock()
    mock_writer.close = MagicMock()

    server_init = json.dumps({'id': 1, 'version': 11}).encode('utf-8')
    success = json.dumps({'success': True}).encode('utf-8')
    full_model = json.dumps(
        {
            'success': True,
            'result': {
                'state': {
                    'status': 'idle'
                },
                'seqs': {},
                'boards': [{
                    'uniqueId': 'test'
                }],
            },
        }
    ).encode('utf-8')

    mock_reader.read = AsyncMock(side_effect=[
        server_init,
        success,
        full_model,
    ])

    with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
        await duet_printer_with_socket.connect()

    assert isinstance(duet_printer_with_socket.api, DuetControlSocket)
    assert duet_printer_with_socket.sbc is True
    assert duet_printer_with_socket.om is not None
    assert duet_printer_with_socket.om['state']['status'] == 'idle'


@pytest.mark.asyncio
async def test_connect_socket_fails_falls_back_to_http(duet_printer_with_socket, mock_rrf_session):
    """When socket connection fails, fall back to HTTP."""
    # Mock rr_connect
    mock_rrf_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={'err': 0},
    )

    full_status = {
        'result': {
            'state': {
                'status': 'idle'
            },
            'seqs': {},
        },
    }
    with patch('asyncio.open_unix_connection', side_effect=ConnectionError('refused')):
        with patch.object(DuetPrinter, '_fetch_full_status', return_value=full_status):
            await duet_printer_with_socket.connect()

    assert isinstance(duet_printer_with_socket.api, RepRapFirmware)
    assert duet_printer_with_socket.sbc is False


@pytest.mark.asyncio
async def test_connected_with_socket_api():
    """Test connected() returns True when socket command writer exists."""
    mock_reader = AsyncMock()
    mock_writer = MagicMock()

    socket_api = DuetControlSocket(
        address='socket:///var/run/dsf/dcs.sock',
    )
    socket_api._cmd_writer = mock_writer

    printer = DuetPrinter(api=socket_api)
    assert printer.connected() is True


@pytest.mark.asyncio
async def test_connected_with_socket_api_disconnected():
    """Test connected() returns False when socket is disconnected."""
    socket_api = DuetControlSocket(
        address='socket:///var/run/dsf/dcs.sock',
    )

    printer = DuetPrinter(api=socket_api)
    assert printer.connected() is False


@pytest.mark.asyncio
async def test_socket_connect_emits_connect_event(duet_printer_with_socket):
    """Socket connection emits the connect event."""
    mock_reader = AsyncMock()
    mock_writer = MagicMock()
    mock_writer.write = MagicMock()
    mock_writer.drain = AsyncMock()
    mock_writer.close = MagicMock()

    server_init = json.dumps({'id': 1, 'version': 11}).encode('utf-8')
    success = json.dumps({'success': True}).encode('utf-8')
    full_model = json.dumps({
        'success': True,
        'result': {
            'state': {
                'status': 'idle'
            },
            'seqs': {},
        },
    }).encode('utf-8')

    mock_reader.read = AsyncMock(side_effect=[
        server_init,
        success,
        full_model,
    ])

    connect_event = asyncio.Event()

    async def on_connect():
        connect_event.set()

    duet_printer_with_socket.events.on(DuetModelEvents.connect, on_connect)

    with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
        await duet_printer_with_socket.connect()

    # Give event loop a tick to process the event
    await asyncio.sleep(0)
    assert connect_event.is_set()


@pytest.mark.asyncio
async def test_gcode_via_socket():
    """Test sending G-code through socket API."""
    socket_api = DuetControlSocket(
        address='socket:///var/run/dsf/dcs.sock',
    )

    mock_reader = AsyncMock()
    mock_writer = MagicMock()
    mock_writer.write = MagicMock()
    mock_writer.drain = AsyncMock()

    from meltingplot.duet_simplyprint_connector.duet.dsf_socket import _SocketReceiver
    socket_api._cmd_reader = mock_reader
    socket_api._cmd_writer = mock_writer
    socket_api._cmd_receiver = _SocketReceiver(mock_reader)

    mock_reader.read = AsyncMock(
        return_value=json.dumps({
            'success': True,
            'result': 'ok'
        }).encode('utf-8'),
    )

    printer = DuetPrinter(api=socket_api, sbc=True)
    result = await printer.gcode('G28', no_reply=False)

    assert result == 'ok'
