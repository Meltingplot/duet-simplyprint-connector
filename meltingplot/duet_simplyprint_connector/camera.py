"""HTTP camera protocol for the Duet SimplyPrint connector."""

import time
from typing import ClassVar, Iterator, Optional

import requests
from simplyprint_ws_client.shared.camera.base import (
    BaseCameraProtocol,
    CameraProtocolConnectionError,
    CameraProtocolPollingMode,
    FrameT,
)
from yarl import URL

JPEG_SOI = b'\xff\xd8'
JPEG_EOI = b'\xff\xd9'
MAX_BUFFER_SIZE = 5 * 1024 * 1024  # 5 MB


class HttpCameraProtocol(BaseCameraProtocol):
    """Camera protocol for HTTP JPEG and multipart MJPEG streams."""

    polling_mode: ClassVar[CameraProtocolPollingMode] = CameraProtocolPollingMode.CONTINUOUS
    is_async: ClassVar[bool] = False

    _session: Optional[requests.Session] = None

    @staticmethod
    def test(uri: URL) -> bool:
        """Check if the URI is an HTTP(S) URL."""
        return uri.scheme in ('http', 'https')

    def _get_session(self) -> requests.Session:
        """Get or create a reusable HTTP session."""
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def read(self) -> Iterator[FrameT]:
        """Read frames continuously from an HTTP JPEG or multipart MJPEG stream."""
        session = self._get_session()
        while True:
            response = None
            try:
                response = session.get(
                    str(self.uri),
                    stream=True,
                    timeout=(30, 60),
                )
                response.raise_for_status()

                content_type = response.headers.get('Content-Type', '').lower()

                if 'multipart' in content_type:
                    yield from self._read_multipart(response)
                else:
                    yield response.content
                    time.sleep(1 / 4)
            except requests.ConnectionError as e:
                raise CameraProtocolConnectionError(str(e)) from e
            except requests.RequestException as e:
                raise CameraProtocolConnectionError(str(e)) from e
            finally:
                if response is not None:
                    response.close()

    def _read_multipart(self, response: requests.Response) -> Iterator[FrameT]:
        """Parse multipart MJPEG stream and yield JPEG frames.

        Uses boundary-based parsing when a boundary is present in the
        Content-Type header. Falls back to JPEG SOI/EOI marker scanning
        when no boundary can be extracted.
        """
        content_type = response.headers.get('Content-Type', '')
        boundary = self._extract_boundary(content_type)

        if boundary is not None:
            yield from self._parse_boundary(response, boundary)
        else:
            yield from self._parse_jpeg_markers(response)

    def _parse_boundary(self, response: requests.Response, boundary: str) -> Iterator[FrameT]:
        """Parse multipart stream using boundary delimiters."""
        boundary_bytes = b'\r\n--' + boundary.encode('ascii', 'ignore')
        buffer = bytearray()
        header_seen = False

        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            buffer.extend(chunk)

            if len(buffer) > MAX_BUFFER_SIZE:
                buffer.clear()
                header_seen = False
                continue

            while True:
                if not header_seen:
                    header_end = buffer.find(b'\r\n\r\n')
                    if header_end == -1:
                        break
                    del buffer[:header_end + 4]
                    header_seen = True

                boundary_pos = buffer.find(boundary_bytes)
                if boundary_pos == -1:
                    break

                frame_data = bytes(buffer[:boundary_pos])
                del buffer[:boundary_pos + len(boundary_bytes)]
                header_seen = False

                if frame_data:
                    yield frame_data
                    time.sleep(1 / 4)

    def _parse_jpeg_markers(self, response: requests.Response) -> Iterator[FrameT]:
        """Parse stream by scanning for JPEG SOI/EOI markers."""
        buffer = bytearray()

        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            buffer.extend(chunk)

            if len(buffer) > MAX_BUFFER_SIZE:
                buffer.clear()
                continue

            while True:
                soi_pos = buffer.find(JPEG_SOI)
                if soi_pos == -1:
                    buffer.clear()
                    break

                # Discard data before the SOI marker
                if soi_pos > 0:
                    del buffer[:soi_pos]
                    soi_pos = 0

                eoi_pos = buffer.find(JPEG_EOI, 2)
                if eoi_pos == -1:
                    break

                frame_data = bytes(buffer[:eoi_pos + 2])
                del buffer[:eoi_pos + 2]

                yield frame_data
                time.sleep(1 / 4)

    @staticmethod
    def _extract_boundary(content_type: str) -> str:
        """Extract the boundary string from a multipart Content-Type header."""
        for part in content_type.split(';'):
            part = part.strip()
            if part.lower().startswith('boundary='):
                boundary = part.split('=', 1)[1].strip().strip('"')
                return boundary
        return None
