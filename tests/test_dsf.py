# -*- coding: utf-8 -*-
"""Tests for Duet Software Framework (DSF) API module."""

import pytest
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from .context import DuetSoftwareFramework
from meltingplot.duet_simplyprint_connector.duet.base import DuetAPIBase


@pytest.fixture
def mock_session():
    session = AsyncMock(aiohttp.ClientSession)
    session.get.return_value.__aenter__.return_value.json = AsyncMock(return_value={'sessionKey': 'test-key'})
    session.get.return_value.__aenter__.return_value.status = 200
    session.get.return_value.__aenter__.return_value.text = AsyncMock(return_value='ok')
    session.get.return_value.__aenter__.return_value.raise_for_status = MagicMock()
    session.post.return_value.__aenter__.return_value.status = 200
    session.post.return_value.__aenter__.return_value.text = AsyncMock(return_value='ok')
    session.post.return_value.__aenter__.return_value.json = AsyncMock(return_value={})
    session.post.return_value.__aenter__.return_value.raise_for_status = MagicMock()
    session.put.return_value.__aenter__.return_value.status = 201
    session.put.return_value.__aenter__.return_value.raise_for_status = MagicMock()
    session.delete.return_value.__aenter__.return_value.status = 204
    session.delete.return_value.__aenter__.return_value.raise_for_status = MagicMock()
    session.patch.return_value.__aenter__.return_value.status = 204
    session.patch.return_value.__aenter__.return_value.raise_for_status = MagicMock()
    session.closed = False
    session.headers = {}
    return session


@pytest.fixture
def dsf(mock_session):
    return DuetSoftwareFramework(session=mock_session)


@pytest.mark.asyncio
async def test_connect(dsf, mock_session):
    response = await dsf.connect()
    assert response == {'sessionKey': 'test-key'}
    mock_session.get.assert_called_once_with(
        'http://10.42.0.2/machine/connect',
        params={'password': 'reprap'},
    )


@pytest.mark.asyncio
async def test_disconnect(dsf, mock_session):
    mock_session.get.return_value.__aenter__.return_value.status = 204
    await dsf.disconnect()
    mock_session.get.assert_called_once_with('http://10.42.0.2/machine/disconnect')


@pytest.mark.asyncio
async def test_noop(dsf, mock_session):
    mock_session.get.return_value.__aenter__.return_value.status = 204
    await dsf.noop()
    mock_session.get.assert_called_once_with('http://10.42.0.2/machine/noop')


@pytest.mark.asyncio
async def test_model(dsf, mock_session):
    mock_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={'state': {
            'status': 'idle'
        }},
    )
    response = await dsf.model()
    assert response == {'state': {'status': 'idle'}}
    mock_session.get.assert_called_once_with('http://10.42.0.2/machine/model')


@pytest.mark.asyncio
async def test_status(dsf, mock_session):
    mock_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={'state': {
            'status': 'idle'
        }},
    )
    response = await dsf.status()
    assert response == {'state': {'status': 'idle'}}
    mock_session.get.assert_called_once_with('http://10.42.0.2/machine/status')


@pytest.mark.asyncio
async def test_code(dsf, mock_session):
    mock_session.post.return_value.__aenter__.return_value.text = AsyncMock(return_value='ok')
    response = await dsf.code('G28')
    assert response == 'ok'
    mock_session.post.assert_called_once_with(
        'http://10.42.0.2/machine/code',
        data='G28',
        params=None,
    )


@pytest.mark.asyncio
async def test_code_async(dsf, mock_session):
    mock_session.post.return_value.__aenter__.return_value.text = AsyncMock(return_value='')
    response = await dsf.code('G28', async_exec=True)
    assert response == ''
    mock_session.post.assert_called_once_with(
        'http://10.42.0.2/machine/code',
        data='G28',
        params={'async': 'true'},
    )


@pytest.mark.asyncio
async def test_download(dsf, mock_session):

    async def chunks(size):
        data = [b'chunk1', b'chunk2']
        for item in data:
            yield item

    mock_session.get.return_value.__aenter__.return_value.content.iter_chunked = chunks
    chunks_received = []
    async for chunk in dsf.download('0:/gcodes/test.gcode'):
        chunks_received.append(chunk)

    assert chunks_received == [b'chunk1', b'chunk2']
    mock_session.get.assert_called_once_with('http://10.42.0.2/machine/file/0%3A%2Fgcodes%2Ftest.gcode')


@pytest.mark.asyncio
async def test_upload(dsf, mock_session):
    content = b'G28\nG1 X10'
    await dsf.upload('0:/gcodes/test.gcode', content)
    mock_session.put.assert_called_once_with(
        'http://10.42.0.2/machine/file/0%3A%2Fgcodes%2Ftest.gcode',
        data=content,
        headers={'Content-Length': '10'},
    )


@pytest.mark.asyncio
async def test_upload_string_content(dsf, mock_session):
    content = 'G28\nG1 X10'
    await dsf.upload('0:/gcodes/test.gcode', content)
    mock_session.put.assert_called_once_with(
        'http://10.42.0.2/machine/file/0%3A%2Fgcodes%2Ftest.gcode',
        data=b'G28\nG1 X10',
        headers={'Content-Length': '10'},
    )


@pytest.mark.asyncio
async def test_upload_stream(dsf, mock_session):
    file = MagicMock()
    file.tell.return_value = 12
    file.read.side_effect = [b'chunk1', b'chunk2', b'']
    mock_session.reset_mock()
    await dsf.upload_stream('0:/gcodes/test.gcode', file)
    mock_session.put.assert_called_once()
    assert mock_session.put.call_args[1]['url'] == 'http://10.42.0.2/machine/file/0%3A%2Fgcodes%2Ftest.gcode'


@pytest.mark.asyncio
async def test_delete(dsf, mock_session):
    await dsf.delete('0:/gcodes/test.gcode')
    mock_session.delete.assert_called_once_with(
        'http://10.42.0.2/machine/file/0%3A%2Fgcodes%2Ftest.gcode',
        params=None,
    )


@pytest.mark.asyncio
async def test_delete_recursive(dsf, mock_session):
    await dsf.delete('0:/gcodes/folder', recursive=True)
    mock_session.delete.assert_called_once_with(
        'http://10.42.0.2/machine/file/0%3A%2Fgcodes%2Ffolder',
        params={'recursive': 'true'},
    )


@pytest.mark.asyncio
async def test_fileinfo(dsf, mock_session):
    mock_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={
            'fileName': 'test.gcode',
            'size': 1234
        },
    )
    response = await dsf.fileinfo('0:/gcodes/test.gcode')
    assert response == {'fileName': 'test.gcode', 'size': 1234}
    mock_session.get.assert_called_once_with('http://10.42.0.2/machine/fileinfo/0%3A%2Fgcodes%2Ftest.gcode')


@pytest.mark.asyncio
async def test_move(dsf, mock_session):
    mock_session.post.return_value.__aenter__.return_value.status = 204
    await dsf.move('0:/gcodes/old.gcode', '0:/gcodes/new.gcode')
    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert call_args[0][0] == 'http://10.42.0.2/machine/file/move'
    # Check that FormData was used
    assert isinstance(call_args[1]['data'], aiohttp.FormData)


@pytest.mark.asyncio
async def test_filelist(dsf, mock_session):
    mock_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value=[{
            'name': 'test.gcode',
            'type': 'f'
        }],
    )
    response = await dsf.filelist('0:/gcodes')
    assert response == [{'name': 'test.gcode', 'type': 'f'}]
    mock_session.get.assert_called_once_with('http://10.42.0.2/machine/directory/0%3A%2Fgcodes')


@pytest.mark.asyncio
async def test_mkdir(dsf, mock_session):
    mock_session.put.return_value.__aenter__.return_value.status = 204
    await dsf.mkdir('0:/gcodes/newfolder')
    mock_session.put.assert_called_once_with('http://10.42.0.2/machine/directory/0%3A%2Fgcodes%2Fnewfolder')


@pytest.mark.asyncio
async def test_install_plugin(dsf, mock_session):
    content = b'PK\x03\x04...'  # Fake ZIP content (7 bytes)
    await dsf.install_plugin(content)
    mock_session.put.assert_called_once_with(
        'http://10.42.0.2/machine/plugin',
        data=content,
        headers={
            'Content-Type': 'application/zip',
            'Content-Length': '7'
        },
    )


@pytest.mark.asyncio
async def test_uninstall_plugin(dsf, mock_session):
    await dsf.uninstall_plugin('TestPlugin')
    mock_session.delete.assert_called_once_with(
        'http://10.42.0.2/machine/plugin',
        headers={'X-Plugin-Name': 'TestPlugin'},
    )


@pytest.mark.asyncio
async def test_set_plugin_data(dsf, mock_session):
    await dsf.set_plugin_data('TestPlugin', 'setting1', 'value1')
    mock_session.patch.assert_called_once_with(
        'http://10.42.0.2/machine/plugin',
        json={
            'plugin': 'TestPlugin',
            'key': 'setting1',
            'value': 'value1'
        },
    )


@pytest.mark.asyncio
async def test_start_plugin(dsf, mock_session):
    mock_session.post.return_value.__aenter__.return_value.status = 204
    await dsf.start_plugin('TestPlugin')
    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert call_args[0][0] == 'http://10.42.0.2/machine/startPlugin'
    assert isinstance(call_args[1]['data'], aiohttp.FormData)


@pytest.mark.asyncio
async def test_stop_plugin(dsf, mock_session):
    mock_session.post.return_value.__aenter__.return_value.status = 204
    await dsf.stop_plugin('TestPlugin')
    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert call_args[0][0] == 'http://10.42.0.2/machine/stopPlugin'
    assert isinstance(call_args[1]['data'], aiohttp.FormData)


@pytest.mark.asyncio
async def test_reconnect_with_session_key(dsf, mock_session):
    mock_session.get.return_value.__aenter__.return_value.json = AsyncMock(
        return_value={'sessionKey': 'new-session-key'},
    )
    response = await dsf.reconnect()
    assert response == {'sessionKey': 'new-session-key'}
    mock_session.get.assert_called_once_with(
        'http://10.42.0.2/machine/connect',
        params={'password': 'reprap'},
    )


@pytest.mark.asyncio
async def test_address_validation():
    with pytest.raises(ValueError, match='Address must start with http://'):
        DuetSoftwareFramework(address='invalid-address')


@pytest.mark.asyncio
async def test_close(dsf, mock_session):
    await dsf.close()
    mock_session.close.assert_called_once()
    assert dsf.session is None


def test_dsf_is_duet_api_base(dsf):
    assert isinstance(dsf, DuetAPIBase)


@pytest.mark.asyncio
async def test_send_gcode(dsf, mock_session):
    mock_session.post.return_value.__aenter__.return_value.text = AsyncMock(return_value='ok')
    result = await dsf.send_gcode('G28', no_reply=False)
    assert result == 'ok'
    mock_session.post.assert_called_once_with(
        'http://10.42.0.2/machine/code',
        data='G28',
        params=None,
    )


@pytest.mark.asyncio
async def test_send_gcode_no_reply(dsf, mock_session):
    mock_session.post.return_value.__aenter__.return_value.text = AsyncMock(return_value='')
    result = await dsf.send_gcode('G28', no_reply=True)
    assert result == ''
    mock_session.post.assert_called_once_with(
        'http://10.42.0.2/machine/code',
        data='G28',
        params={'async': 'true'},
    )
