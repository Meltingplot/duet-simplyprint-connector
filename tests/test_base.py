"""Tests for the reauthenticate decorator and DuetAPIBase."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from meltingplot.duet_simplyprint_connector.duet.base import DuetAPIBase, reauthenticate


class ConcreteAPI(DuetAPIBase):
    """Minimal concrete subclass for testing."""

    async def reconnect(self):
        return {}

    async def send_gcode(self, command, no_reply=True):
        return ''

    async def download(self, filepath, chunk_size=1024):
        yield b''

    async def upload_stream(self, filepath, file, progress=None):
        pass

    async def delete(self, filepath):
        pass

    async def fileinfo(self, filepath, **kwargs):
        return {}

    async def filelist(self, directory):
        return []

    async def mkdir(self, directory):
        pass

    async def move(self, old_filepath, new_filepath, overwrite=False):
        pass


@pytest.fixture
def api():
    """Create a ConcreteAPI instance."""
    session = MagicMock(spec=aiohttp.ClientSession)
    session.closed = False
    return ConcreteAPI(session=session)


@pytest.mark.asyncio
async def test_reauthenticate_success(api):
    """Test that a successful call returns immediately."""
    @reauthenticate()
    async def method(self):
        return 'ok'

    result = await method(api)
    assert result == 'ok'


@pytest.mark.asyncio
async def test_reauthenticate_retries_on_timeout(api):
    """Test retry on TimeoutError."""
    call_count = 0

    @reauthenticate(retries=3)
    async def method(self):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TimeoutError("timeout")
        return 'ok'

    with patch('asyncio.sleep', new_callable=AsyncMock):
        result = await method(api)

    assert result == 'ok'
    assert call_count == 3


@pytest.mark.asyncio
async def test_reauthenticate_retries_on_asyncio_timeout(api):
    """Test retry on asyncio.TimeoutError."""
    call_count = 0

    @reauthenticate(retries=3)
    async def method(self):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise asyncio.TimeoutError()
        return 'ok'

    with patch('asyncio.sleep', new_callable=AsyncMock):
        result = await method(api)

    assert result == 'ok'
    assert call_count == 2


@pytest.mark.asyncio
async def test_reauthenticate_retries_on_connection_error(api):
    """Test retry on ClientConnectionError."""
    call_count = 0

    @reauthenticate(retries=3)
    async def method(self):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise aiohttp.ClientConnectionError("conn error")
        return 'ok'

    with patch('asyncio.sleep', new_callable=AsyncMock):
        result = await method(api)

    assert result == 'ok'


@pytest.mark.asyncio
async def test_reauthenticate_exhausted_retries_raises(api):
    """Test that exhausted retries raise TimeoutError."""
    @reauthenticate(retries=2)
    async def method(self):
        raise TimeoutError("always timeout")

    with patch('asyncio.sleep', new_callable=AsyncMock):
        with pytest.raises(TimeoutError, match='Retried 2 times'):
            await method(api)


@pytest.mark.asyncio
async def test_reauthenticate_auth_error_reconnects(api):
    """Test that auth error triggers reconnect."""
    call_count = 0
    api.reconnect = AsyncMock()

    @reauthenticate(retries=3, auth_error_status=[401])
    async def method(self):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=401,
                message='Unauthorized',
            )
        return 'ok'

    with patch('asyncio.sleep', new_callable=AsyncMock):
        result = await method(api)

    assert result == 'ok'
    api.reconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_reauthenticate_callback_status(api):
    """Test that a registered callback status invokes the callback."""
    callback = AsyncMock()
    api.callbacks[503] = callback

    call_count = 0

    @reauthenticate(retries=3)
    async def method(self):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=503,
                message='Service Unavailable',
            )
        return 'ok'

    result = await method(api)
    assert result == 'ok'
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_reauthenticate_unknown_status_reraises(api):
    """Test that an unknown HTTP status re-raises the error."""
    @reauthenticate(retries=3)
    async def method(self):
        raise aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=404,
            message='Not Found',
        )

    with pytest.raises(aiohttp.ClientResponseError) as exc_info:
        await method(api)
    assert exc_info.value.status == 404


@pytest.mark.asyncio
async def test_reauthenticate_payload_error_retries(api):
    """Test retry on ClientPayloadError."""
    call_count = 0

    @reauthenticate(retries=3)
    async def method(self):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise aiohttp.ClientPayloadError("payload error")
        return 'ok'

    with patch('asyncio.sleep', new_callable=AsyncMock):
        result = await method(api)

    assert result == 'ok'


@pytest.mark.asyncio
async def test_ensure_session_calls_reconnect_when_none(api):
    """Test that _ensure_session calls reconnect when session is None."""
    api.session = None
    api.reconnect = AsyncMock()

    await api._ensure_session()

    api.reconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_session_calls_reconnect_when_closed(api):
    """Test that _ensure_session calls reconnect when session is closed."""
    api.session = MagicMock()
    api.session.closed = True
    api.reconnect = AsyncMock()

    await api._ensure_session()

    api.reconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_session_noop_when_open(api):
    """Test that _ensure_session does nothing when session is open."""
    api.session = MagicMock()
    api.session.closed = False
    api.reconnect = AsyncMock()

    await api._ensure_session()

    api.reconnect.assert_not_awaited()
