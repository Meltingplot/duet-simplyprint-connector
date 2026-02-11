"""Tests for the HttpCameraProtocol."""

from unittest.mock import Mock, patch

import pytest
import requests
from yarl import URL

from meltingplot.duet_simplyprint_connector.camera import HttpCameraProtocol
from simplyprint_ws_client.shared.camera.base import (
    CameraProtocolConnectionError,
    CameraProtocolPollingMode,
)


def test_http_camera_protocol_test_http():
    """Test that HTTP URIs are accepted."""
    assert HttpCameraProtocol.test(URL("http://192.168.1.1/webcam")) is True


def test_http_camera_protocol_test_https():
    """Test that HTTPS URIs are accepted."""
    assert HttpCameraProtocol.test(URL("https://example.com/cam")) is True


def test_http_camera_protocol_test_rtsp_rejected():
    """Test that RTSP URIs are rejected."""
    assert HttpCameraProtocol.test(URL("rtsp://192.168.1.1/stream")) is False


def test_http_camera_protocol_test_empty_scheme():
    """Test that URIs without scheme are rejected."""
    assert HttpCameraProtocol.test(URL("/local/path")) is False


def test_http_camera_protocol_polling_mode():
    """Test that the polling mode is CONTINUOUS."""
    assert HttpCameraProtocol.polling_mode == CameraProtocolPollingMode.CONTINUOUS


def test_http_camera_protocol_is_not_async():
    """Test that the protocol is synchronous."""
    assert HttpCameraProtocol.is_async is False


def test_http_camera_protocol_read_jpeg():
    """Test reading a single JPEG frame from continuous loop."""
    mock_response = Mock()
    mock_response.headers = {"Content-Type": "image/jpeg"}
    mock_response.content = b"\xff\xd8\xff\xe0jpeg-data"
    mock_response.raise_for_status = Mock()

    with patch("requests.Session.get", return_value=mock_response):
        protocol = HttpCameraProtocol(uri=URL("http://example.com/cam"))
        frame = next(protocol.read())
        assert frame == b"\xff\xd8\xff\xe0jpeg-data"


def test_http_camera_protocol_read_connection_error():
    """Test that connection errors raise CameraProtocolConnectionError."""
    with patch(
        "requests.Session.get",
        side_effect=requests.ConnectionError("Connection refused"),
    ):
        protocol = HttpCameraProtocol(uri=URL("http://example.com/cam"))
        with pytest.raises(CameraProtocolConnectionError):
            next(protocol.read())


def test_http_camera_protocol_read_multipart():
    """Test reading frames from a multipart MJPEG stream."""
    jpeg1 = b'\xff\xd8frame-one\xff\xd9'
    jpeg2 = b'\xff\xd8frame-two\xff\xd9'
    body = (
        b'--BoundaryString\r\n'
        b'Content-Type: image/jpeg\r\n'
        b'\r\n' + jpeg1 + b'\r\n--BoundaryString\r\n'
        b'Content-Type: image/jpeg\r\n'
        b'\r\n' + jpeg2 + b'\r\n--BoundaryString--\r\n'
    )

    mock_response = Mock()
    mock_response.headers = {
        "Content-Type": "multipart/x-mixed-replace; boundary=BoundaryString",
    }
    mock_response.raise_for_status = Mock()
    mock_response.iter_content = Mock(return_value=iter([body]))
    mock_response.close = Mock()

    with patch(
        "requests.Session.get",
        side_effect=[mock_response, requests.ConnectionError("stream ended")],
    ):
        protocol = HttpCameraProtocol(uri=URL("http://example.com/webcam"))
        frames = []
        try:
            for frame in protocol.read():
                frames.append(frame)
        except CameraProtocolConnectionError:
            pass
        assert len(frames) == 2
        assert frames[0] == jpeg1
        assert frames[1] == jpeg2


def test_http_camera_protocol_read_multipart_jpeg_fallback():
    """Test JPEG marker fallback when no boundary is in Content-Type."""
    jpeg1 = b'\xff\xd8frame-one\xff\xd9'
    jpeg2 = b'\xff\xd8frame-two\xff\xd9'
    # Stream with JPEG data but no multipart boundaries
    body = b'garbage' + jpeg1 + b'more-garbage' + jpeg2 + b'trailing'

    mock_response = Mock()
    mock_response.headers = {
        "Content-Type": "multipart/x-mixed-replace",
    }
    mock_response.raise_for_status = Mock()
    mock_response.iter_content = Mock(return_value=iter([body]))
    mock_response.close = Mock()

    with patch(
        "requests.Session.get",
        side_effect=[mock_response, requests.ConnectionError("stream ended")],
    ):
        protocol = HttpCameraProtocol(uri=URL("http://example.com/webcam"))
        frames = []
        try:
            for frame in protocol.read():
                frames.append(frame)
        except CameraProtocolConnectionError:
            pass
        assert len(frames) == 2
        assert frames[0] == jpeg1
        assert frames[1] == jpeg2


def test_parse_jpeg_markers_across_chunks():
    """Test JPEG marker parser handles frames split across chunks."""
    jpeg1 = b'\xff\xd8chunk-one\xff\xd9'
    jpeg2 = b'\xff\xd8chunk-two\xff\xd9'
    chunk1 = jpeg1 + b'\xff\xd8chunk'
    chunk2 = b'-two\xff\xd9'

    mock_response = Mock()
    mock_response.headers = {"Content-Type": ""}
    mock_response.iter_content = Mock(return_value=iter([chunk1, chunk2]))

    protocol = HttpCameraProtocol(uri=URL("http://example.com/webcam"))
    frames = list(protocol._parse_jpeg_markers(mock_response))
    assert len(frames) == 2
    assert frames[0] == jpeg1
    assert frames[1] == jpeg2


def test_extract_boundary():
    """Test boundary extraction from Content-Type header."""
    boundary = HttpCameraProtocol._extract_boundary(
        'multipart/x-mixed-replace; boundary=myboundary',
    )
    assert boundary == "myboundary"


def test_extract_boundary_quoted():
    """Test boundary extraction with quoted boundary."""
    boundary = HttpCameraProtocol._extract_boundary(
        'multipart/x-mixed-replace; boundary="frame-boundary"',
    )
    assert boundary == "frame-boundary"


def test_extract_boundary_missing():
    """Test boundary extraction when no boundary is present."""
    boundary = HttpCameraProtocol._extract_boundary('multipart/x-mixed-replace')
    assert boundary is None
