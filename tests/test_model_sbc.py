"""Tests for DuetPrinter SBC mode integration."""
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
