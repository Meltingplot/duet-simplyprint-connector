# -*- coding: utf-8 -*-
"""Tests for Duet Control Socket API module."""

import asyncio
import io
import json
import os
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from .context import DuetControlSocket, _resolve_dsf_path, _SocketReceiver
from meltingplot.duet_simplyprint_connector.duet.base import DuetAPIBase

# --- Path resolution tests ---


class TestResolveDsfPath:
    """Tests for _resolve_dsf_path helper."""

    def test_resolve_volume_prefix(self):
        result = _resolve_dsf_path('0:/gcodes/test.gcode', '/opt/dsf/sd')
        assert result == '/opt/dsf/sd/gcodes/test.gcode'

    def test_resolve_without_volume_prefix(self):
        result = _resolve_dsf_path('/gcodes/test.gcode', '/opt/dsf/sd')
        assert result == '/opt/dsf/sd/gcodes/test.gcode'

    def test_resolve_bare_path(self):
        result = _resolve_dsf_path('gcodes/test.gcode', '/opt/dsf/sd')
        assert result == '/opt/dsf/sd/gcodes/test.gcode'

    def test_resolve_sys_path(self):
        result = _resolve_dsf_path('0:/sys/config.g', '/opt/dsf/sd')
        assert result == '/opt/dsf/sd/sys/config.g'

    def test_resolve_traversal_blocked(self):
        with pytest.raises(ValueError, match='Path traversal detected'):
            _resolve_dsf_path('0:/../../etc/passwd', '/opt/dsf/sd')


# --- Socket receiver tests ---


class TestSocketReceiver:
    """Tests for _SocketReceiver JSON message parsing."""

    @pytest.mark.asyncio
    async def test_receive_single_json(self):
        reader = AsyncMock(asyncio.StreamReader)
        reader.read = AsyncMock(return_value=b'{"id":1,"version":11}')
        receiver = _SocketReceiver(reader)
        result = await receiver.receive_json()
        assert result == {'id': 1, 'version': 11}

    @pytest.mark.asyncio
    async def test_receive_fragmented_json(self):
        reader = AsyncMock(asyncio.StreamReader)
        reader.read = AsyncMock(side_effect=[b'{"id":', b'1,"version":11}'])
        receiver = _SocketReceiver(reader)
        result = await receiver.receive_json()
        assert result == {'id': 1, 'version': 11}

    @pytest.mark.asyncio
    async def test_receive_multiple_json_messages(self):
        reader = AsyncMock(asyncio.StreamReader)
        two_messages = b'{"id":1}{"id":2}'
        reader.read = AsyncMock(return_value=two_messages)
        receiver = _SocketReceiver(reader)

        first = await receiver.receive_json()
        assert first == {'id': 1}

        second = await receiver.receive_json()
        assert second == {'id': 2}

    @pytest.mark.asyncio
    async def test_receive_json_with_nested_braces(self):
        reader = AsyncMock(asyncio.StreamReader)
        data = b'{"result":{"state":{"status":"idle"}}}'
        reader.read = AsyncMock(return_value=data)
        receiver = _SocketReceiver(reader)
        result = await receiver.receive_json()
        assert result == {'result': {'state': {'status': 'idle'}}}

    @pytest.mark.asyncio
    async def test_receive_json_with_string_braces(self):
        reader = AsyncMock(asyncio.StreamReader)
        data = b'{"code":"M291 P\\"{test}\\" S2"}'
        reader.read = AsyncMock(return_value=data)
        receiver = _SocketReceiver(reader)
        result = await receiver.receive_json()
        assert result['code'] == 'M291 P"{test}" S2'

    @pytest.mark.asyncio
    async def test_connection_closed_raises(self):
        reader = AsyncMock(asyncio.StreamReader)
        reader.read = AsyncMock(return_value=b'')
        receiver = _SocketReceiver(reader)
        with pytest.raises(ConnectionError, match='Socket connection closed'):
            await receiver.receive_json()

    def test_find_json_end_complete(self):
        assert _SocketReceiver._find_json_end('{"a":1}') == 6

    def test_find_json_end_nested(self):
        assert _SocketReceiver._find_json_end('{"a":{"b":2}}') == 12

    def test_find_json_end_incomplete(self):
        assert _SocketReceiver._find_json_end('{"a":1') == -1

    def test_find_json_end_string_with_braces(self):
        assert _SocketReceiver._find_json_end('{"a":"{"}') == 8

    def test_find_json_end_escaped_quote(self):
        s = '{"a":"val\\"ue"}'
        assert _SocketReceiver._find_json_end(s) == len(s) - 1


# --- DuetControlSocket tests ---


@pytest.fixture
def mock_reader():
    reader = AsyncMock(asyncio.StreamReader)
    return reader


@pytest.fixture
def mock_writer():
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    return writer


@pytest.fixture
def socket_api(tmp_path):
    """Create a DuetControlSocket with a temp directory as sd_base_dir."""
    return DuetControlSocket(
        address='socket:///var/run/dsf/dcs.sock',
        socket_path='/var/run/dsf/dcs.sock',
        sd_base_dir=str(tmp_path),
    )


def _make_server_init(version=11):
    return json.dumps({'id': 1, 'version': version}).encode('utf-8')


def _make_success():
    return json.dumps({'success': True}).encode('utf-8')


def _make_error(msg='error'):
    return json.dumps({
        'success': False,
        'errorType': 'TestError',
        'errorMessage': msg,
    }).encode('utf-8')


class TestDuetControlSocketConnection:
    """Tests for socket connection handshake."""

    @pytest.mark.asyncio
    async def test_init_command_connection(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(side_effect=[
            _make_server_init(),
            _make_success(),
        ])
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            await socket_api._init_command_connection()

        assert socket_api._cmd_writer is mock_writer
        assert socket_api._cmd_reader is mock_reader
        assert socket_api._cmd_receiver is not None

        # Check init message was sent
        write_calls = mock_writer.write.call_args_list
        init_msg = json.loads(write_calls[0][0][0].decode('utf-8'))
        assert init_msg['mode'] == 'Command'

    @pytest.mark.asyncio
    async def test_init_command_connection_rejected(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(side_effect=[
            _make_server_init(),
            _make_error('rejected'),
        ])
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            with pytest.raises(ConnectionError, match='rejected'):
                await socket_api._init_command_connection()

    @pytest.mark.asyncio
    async def test_incompatible_version_raises(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(return_value=_make_server_init(version=5))
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            with pytest.raises(ConnectionError, match='Incompatible'):
                await socket_api._init_command_connection()

    @pytest.mark.asyncio
    async def test_connect_returns_is_emulated(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(side_effect=[
            _make_server_init(),
            _make_success(),
        ])
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            result = await socket_api.connect()

        assert result == {'isEmulated': True}

    @pytest.mark.asyncio
    async def test_reconnect(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(side_effect=[
            _make_server_init(),
            _make_success(),
        ])
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            result = await socket_api.reconnect()

        assert result == {'isEmulated': True}

    @pytest.mark.asyncio
    async def test_close(self, socket_api, mock_writer):
        socket_api._cmd_writer = mock_writer
        socket_api._cmd_reader = MagicMock()
        socket_api._cmd_receiver = MagicMock()
        socket_api._sub_writer = mock_writer
        socket_api._sub_reader = MagicMock()
        socket_api._sub_receiver = MagicMock()

        await socket_api.close()

        assert socket_api._cmd_writer is None
        assert socket_api._sub_writer is None


class TestDuetControlSocketGcode:
    """Tests for G-code execution via socket."""

    @pytest.mark.asyncio
    async def test_send_gcode(self, socket_api, mock_reader, mock_writer):
        # Set up an established connection
        mock_reader.read = AsyncMock(
            side_effect=[
                _make_server_init(),
                _make_success(),
                json.dumps({
                    'success': True,
                    'result': 'ok'
                }).encode('utf-8'),
            ]
        )
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            result = await socket_api.send_gcode('G28', no_reply=False)

        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_send_gcode_async(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(
            side_effect=[
                _make_server_init(),
                _make_success(),
                json.dumps({
                    'success': True,
                    'result': ''
                }).encode('utf-8'),
            ]
        )
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            result = await socket_api.send_gcode('G28', no_reply=True)

        assert result == ''
        # Verify async flag was set
        write_calls = mock_writer.write.call_args_list
        cmd_msg = json.loads(write_calls[1][0][0].decode('utf-8'))
        assert cmd_msg['async'] is True

    @pytest.mark.asyncio
    async def test_send_command_error(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(
            side_effect=[
                _make_server_init(),
                _make_success(),
                _make_error('command failed'),
            ]
        )
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            with pytest.raises(ConnectionError, match='command failed'):
                await socket_api.send_gcode('M999')


class TestDuetControlSocketModel:
    """Tests for object model retrieval via socket."""

    @pytest.mark.asyncio
    async def test_get_model(self, socket_api, mock_reader, mock_writer):
        model_data = {'state': {'status': 'idle'}, 'seqs': {}}
        mock_reader.read = AsyncMock(
            side_effect=[
                _make_server_init(),
                _make_success(),
                json.dumps({
                    'success': True,
                    'result': model_data
                }).encode('utf-8'),
            ]
        )
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            result = await socket_api.model()

        assert result == model_data

    @pytest.mark.asyncio
    async def test_get_model_sends_correct_command(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(
            side_effect=[
                _make_server_init(),
                _make_success(),
                json.dumps({
                    'success': True,
                    'result': {}
                }).encode('utf-8'),
            ]
        )
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            await socket_api.model()

        write_calls = mock_writer.write.call_args_list
        cmd_msg = json.loads(write_calls[1][0][0].decode('utf-8'))
        assert cmd_msg['command'] == 'GetObjectModel'


class TestDuetControlSocketSubscribe:
    """Tests for subscribe connection."""

    @pytest.mark.asyncio
    async def test_subscribe_yields_model(self, socket_api, mock_reader, mock_writer):
        full_model = {'state': {'status': 'idle'}, 'seqs': {}}
        patch_data = {'state': {'status': 'processing'}}

        mock_reader.read = AsyncMock(
            side_effect=[
                # Subscribe handshake
                _make_server_init(),
                _make_success(),
                # First message: full model
                json.dumps(full_model).encode('utf-8'),
                # Second message: patch
                json.dumps(patch_data).encode('utf-8'),
                # Connection close
                b'',
            ]
        )

        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            messages = []
            try:
                async for data in socket_api.subscribe():
                    messages.append(data)
                    if len(messages) >= 2:
                        break
            except ConnectionError:
                pass

        assert len(messages) >= 2
        assert messages[0] == full_model
        assert messages[1] == patch_data

    @pytest.mark.asyncio
    async def test_subscribe_sends_acknowledgment(self, socket_api, mock_reader, mock_writer):
        model1 = {'state': {'status': 'idle'}}
        model2 = {'state': {'status': 'processing'}}

        mock_reader.read = AsyncMock(
            side_effect=[
                _make_server_init(),
                _make_success(),
                json.dumps(model1).encode('utf-8'),
                # After ack for model1, server sends model2
                json.dumps(model2).encode('utf-8'),
                b'',  # Connection close
            ]
        )

        messages = []
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            try:
                async for data in socket_api.subscribe():
                    messages.append(data)
            except ConnectionError:
                pass

        # After receiving the first model, an ack must be sent before the
        # second model is received. We expect at least one ack.
        ack_calls = [c for c in mock_writer.write.call_args_list if b'Acknowledge' in c[0][0]]
        assert len(ack_calls) >= 1
        assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_subscribe_init_sends_patch_mode(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(side_effect=[
            _make_server_init(),
            _make_success(),
            b'',  # Close immediately
        ])

        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            try:
                async for _ in socket_api.subscribe():
                    pass
            except ConnectionError:
                pass

        # Check subscribe init message
        write_calls = mock_writer.write.call_args_list
        init_msg = json.loads(write_calls[0][0][0].decode('utf-8'))
        assert init_msg['mode'] == 'Subscribe'
        assert init_msg['SubscriptionMode'] == 'Patch'

    @pytest.mark.asyncio
    async def test_unsubscribe(self, socket_api, mock_writer):
        socket_api._sub_writer = mock_writer
        socket_api._sub_reader = MagicMock()
        socket_api._sub_receiver = MagicMock()

        await socket_api.unsubscribe()

        assert socket_api._sub_writer is None
        assert socket_api._sub_reader is None

    def test_ws_connected_false_when_no_connection(self, socket_api):
        assert socket_api.ws_connected is False

    def test_ws_connected_true_when_connected(self, socket_api, mock_writer):
        socket_api._sub_writer = mock_writer
        assert socket_api.ws_connected is True


class TestDuetControlSocketFileIO:
    """Tests for direct filesystem file operations."""

    @pytest.mark.asyncio
    async def test_upload_stream(self, socket_api, tmp_path):
        content = b'G28\nG1 X10 Y10\n'
        file = io.BytesIO(content)

        # Create the gcodes directory
        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()

        await socket_api.upload_stream('0:/gcodes/test.gcode', file)

        result_path = gcodes_dir / 'test.gcode'
        assert result_path.exists()
        assert result_path.read_bytes() == content

    @pytest.mark.asyncio
    async def test_upload_stream_creates_directories(self, socket_api, tmp_path):
        content = b'G28\n'
        file = io.BytesIO(content)

        await socket_api.upload_stream('0:/gcodes/subdir/test.gcode', file)

        result_path = tmp_path / 'gcodes' / 'subdir' / 'test.gcode'
        assert result_path.exists()
        assert result_path.read_bytes() == content

    @pytest.mark.asyncio
    async def test_upload_stream_with_progress(self, socket_api, tmp_path):
        content = b'x' * 1024
        file = io.BytesIO(content)
        progress_values = []

        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()

        await socket_api.upload_stream(
            '0:/gcodes/test.gcode',
            file,
            progress=lambda p: progress_values.append(p),
        )

        assert len(progress_values) > 0
        assert progress_values[-1] == 100.0

    @pytest.mark.asyncio
    async def test_download(self, socket_api, tmp_path):
        content = b'G28\nG1 X10\n'
        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()
        (gcodes_dir / 'test.gcode').write_bytes(content)

        chunks = []
        async for chunk in socket_api.download('0:/gcodes/test.gcode'):
            chunks.append(chunk)

        assert b''.join(chunks) == content

    @pytest.mark.asyncio
    async def test_download_chunked(self, socket_api, tmp_path):
        content = b'x' * 100
        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()
        (gcodes_dir / 'test.gcode').write_bytes(content)

        chunks = []
        async for chunk in socket_api.download('0:/gcodes/test.gcode', chunk_size=10):
            chunks.append(chunk)

        assert len(chunks) == 10
        assert b''.join(chunks) == content

    @pytest.mark.asyncio
    async def test_delete_file(self, socket_api, tmp_path):
        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()
        test_file = gcodes_dir / 'test.gcode'
        test_file.write_bytes(b'G28')

        await socket_api.delete('0:/gcodes/test.gcode')
        assert not test_file.exists()

    @pytest.mark.asyncio
    async def test_delete_directory(self, socket_api, tmp_path):
        subdir = tmp_path / 'gcodes' / 'subdir'
        subdir.mkdir(parents=True)
        (subdir / 'file.gcode').write_bytes(b'G28')

        await socket_api.delete('0:/gcodes/subdir')
        assert not subdir.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_file(self, socket_api, tmp_path):
        # Should not raise
        await socket_api.delete('0:/gcodes/nonexistent.gcode')

    @pytest.mark.asyncio
    async def test_filelist(self, socket_api, tmp_path):
        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()
        (gcodes_dir / 'a.gcode').write_bytes(b'G28')
        (gcodes_dir / 'b.gcode').write_bytes(b'G28\nG1')
        (gcodes_dir / 'subdir').mkdir()

        result = await socket_api.filelist('0:/gcodes')

        names = {f['name'] for f in result}
        assert names == {'a.gcode', 'b.gcode', 'subdir'}

        dirs = [f for f in result if f['type'] == 'd']
        files = [f for f in result if f['type'] == 'f']
        assert len(dirs) == 1
        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_filelist_empty_dir(self, socket_api, tmp_path):
        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()

        result = await socket_api.filelist('0:/gcodes')
        assert result == []

    @pytest.mark.asyncio
    async def test_filelist_nonexistent_dir(self, socket_api, tmp_path):
        result = await socket_api.filelist('0:/nonexistent')
        assert result == []

    @pytest.mark.asyncio
    async def test_mkdir(self, socket_api, tmp_path):
        await socket_api.mkdir('0:/gcodes/newfolder')
        assert (tmp_path / 'gcodes' / 'newfolder').is_dir()

    @pytest.mark.asyncio
    async def test_mkdir_nested(self, socket_api, tmp_path):
        await socket_api.mkdir('0:/gcodes/a/b/c')
        assert (tmp_path / 'gcodes' / 'a' / 'b' / 'c').is_dir()

    @pytest.mark.asyncio
    async def test_move(self, socket_api, tmp_path):
        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()
        (gcodes_dir / 'old.gcode').write_bytes(b'G28')

        await socket_api.move('0:/gcodes/old.gcode', '0:/gcodes/new.gcode')

        assert not (gcodes_dir / 'old.gcode').exists()
        assert (gcodes_dir / 'new.gcode').read_bytes() == b'G28'

    @pytest.mark.asyncio
    async def test_move_no_overwrite(self, socket_api, tmp_path):
        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()
        (gcodes_dir / 'old.gcode').write_bytes(b'old')
        (gcodes_dir / 'new.gcode').write_bytes(b'new')

        with pytest.raises(IOError, match='Destination already exists'):
            await socket_api.move('0:/gcodes/old.gcode', '0:/gcodes/new.gcode')

    @pytest.mark.asyncio
    async def test_move_with_overwrite(self, socket_api, tmp_path):
        gcodes_dir = tmp_path / 'gcodes'
        gcodes_dir.mkdir()
        (gcodes_dir / 'old.gcode').write_bytes(b'old')
        (gcodes_dir / 'new.gcode').write_bytes(b'new')

        await socket_api.move(
            '0:/gcodes/old.gcode',
            '0:/gcodes/new.gcode',
            overwrite=True,
        )
        assert (gcodes_dir / 'new.gcode').read_bytes() == b'old'

    @pytest.mark.asyncio
    async def test_fileinfo(self, socket_api, mock_reader, mock_writer):
        file_info = {
            'fileName': '0:/gcodes/test.gcode',
            'size': 1234,
            'height': 10.0,
            'layerHeight': 0.2,
        }
        mock_reader.read = AsyncMock(
            side_effect=[
                _make_server_init(),
                _make_success(),
                json.dumps({
                    'success': True,
                    'result': file_info
                }).encode('utf-8'),
            ]
        )
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            result = await socket_api.fileinfo('0:/gcodes/test.gcode')

        assert result == file_info

    @pytest.mark.asyncio
    async def test_fileinfo_not_found(self, socket_api, mock_reader, mock_writer):
        mock_reader.read = AsyncMock(
            side_effect=[
                _make_server_init(),
                _make_success(),
                json.dumps({
                    'success': True,
                    'result': {}
                }).encode('utf-8'),
            ]
        )
        with patch('asyncio.open_unix_connection', return_value=(mock_reader, mock_writer)):
            with pytest.raises(FileNotFoundError):
                await socket_api.fileinfo('0:/gcodes/nonexistent.gcode')


class TestDuetControlSocketIsBase:
    """Test that DuetControlSocket is a proper DuetAPIBase subclass."""

    def test_is_duet_api_base(self, socket_api):
        assert isinstance(socket_api, DuetAPIBase)

    def test_address_validation(self):
        api = DuetControlSocket(address='socket:///var/run/dsf/dcs.sock')
        assert api.address == 'socket:///var/run/dsf/dcs.sock'

    def test_invalid_address_rejected(self):
        with pytest.raises(ValueError, match='Address must start with'):
            DuetControlSocket(address='invalid')
