#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Duet Software Framework (SBC mode) HTTP API Module."""

import asyncio
import functools
import logging
from typing import AsyncIterable, BinaryIO, Callable, Optional
from urllib.parse import quote

import aiohttp

import attr


class DSFAuthenticationError(Exception):
    """DSF Authentication Error."""

    pass


def reauthenticate_dsf(retries: int = 3):
    """Reauthenticate DSF API requests.

    Decorator that wraps async API methods with retry logic for handling
    connection errors and authentication failures. Uses linear backoff
    capped at RETRY_DELAY_MAX seconds.

    DSF uses HTTP 403 for authentication errors instead of 401.
    """

    def decorator(f):

        @functools.wraps(f)
        async def inner(self, *args, **kwargs):
            remaining = retries
            while remaining > 0:
                try:
                    return await f(self, *args, **kwargs)
                except (TimeoutError, aiohttp.ClientPayloadError, aiohttp.ClientConnectionError) as e:
                    self.logger.error(f"{e} - retry")
                    remaining -= 1
                    delay = min(2 * (retries - remaining + 1), self.RETRY_DELAY_MAX)
                    await asyncio.sleep(delay)
                except aiohttp.ClientResponseError as e:
                    remaining -= 1
                    if e.status in self.callbacks:
                        await self.callbacks[e.status](e)
                    elif e.status == 403:
                        # DSF uses 403 for authentication errors
                        self.logger.error(
                            f'Forbidden while requesting {e.request_info!s} - retry',
                        )
                        delay = min(2 * (retries - remaining + 1), self.RETRY_DELAY_MAX)
                        await asyncio.sleep(delay)
                        await self.reconnect()
                        remaining = retries
                    else:
                        raise e
            raise TimeoutError(f'Retried {retries} times to reauthenticate.')

        return inner

    return decorator


@attr.s
class DuetSoftwareFramework():
    """Duet Software Framework (SBC mode) API Class."""

    # Class constants
    DEFAULT_SESSION_TIMEOUT = 8000
    DEFAULT_HTTP_TIMEOUT = 15
    DEFAULT_HTTP_RETRIES = 3
    RETRY_DELAY_MAX = 10
    UPLOAD_TIMEOUT = 60 * 30  # 30 minutes
    UPLOAD_CHUNK_SIZE = 8192

    address = attr.ib(type=str, default="http://10.42.0.2")
    password = attr.ib(type=str, default="reprap")
    session_timeout = attr.ib(type=int, default=DEFAULT_SESSION_TIMEOUT)
    http_timeout = attr.ib(type=int, default=DEFAULT_HTTP_TIMEOUT)
    http_retries = attr.ib(type=int, default=DEFAULT_HTTP_RETRIES)
    session = attr.ib(type=aiohttp.ClientSession, default=None)
    logger = attr.ib(type=logging.Logger, factory=logging.getLogger)
    callbacks = attr.ib(type=dict, factory=dict)
    _reconnect_lock = attr.ib(type=asyncio.Lock, factory=asyncio.Lock)

    def __attrs_post_init__(self):
        """Post init."""
        self.callbacks[502] = self._default_http_502_bad_gateway_callback
        self.callbacks[503] = self._default_http_503_unavailable_callback

    async def _default_http_502_bad_gateway_callback(self, e):
        # HTTP 502 may indicate incompatible DCS version
        self.logger.error(f'DSF bad gateway (incompatible DCS version?) {e.request_info!s} - retry')
        await asyncio.sleep(5)

    async def _default_http_503_unavailable_callback(self, e):
        # HTTP 503 indicates DCS is unavailable
        self.logger.error(f'DSF unavailable {e.request_info!s} - retry')
        await asyncio.sleep(5)

    @address.validator
    def _validate_address(self, attribute, value):
        if not value.startswith('http://') and not value.startswith('https://'):
            raise ValueError('Address must start with http:// or https://')

    async def connect(self) -> dict:
        """Connect to the Duet via DSF."""
        return await self.reconnect()

    async def reconnect(self) -> dict:
        """Reconnect to the Duet via DSF."""
        # Prevent multiple reconnects
        if self._reconnect_lock.locked():
            # Wait for reconnect to finish
            async with self._reconnect_lock:
                return {'sessionKey': self.session.headers.get('X-Session-Key', '')}

        async with self._reconnect_lock:
            url = f'{self.address}/machine/connect'

            params = {
                'password': self.password,
            }

            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.http_timeout),
                    raise_for_status=True,
                )
            else:
                self.session.headers.clear()

            json_response = {}
            async with self.session.get(url, params=params) as r:
                if r.status == 200:
                    json_response = await r.json()
                    if 'sessionKey' in json_response:
                        self.session.headers['X-Session-Key'] = str(json_response['sessionKey'])
                elif r.status == 403:
                    raise DSFAuthenticationError("Invalid password")

            return json_response

    async def close(self) -> None:
        """Close the Client Session."""
        if self.session is not None and not self.session.closed:
            await self.session.close()
            self.session = None

    async def disconnect(self) -> None:
        """Disconnect from the Duet via DSF."""
        await self._ensure_session()

        url = f'{self.address}/machine/disconnect'

        async with self.session.get(url) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during disconnect')

        await self.close()

    async def _ensure_session(self) -> None:
        """Ensure a valid session."""
        if self.session is None or self.session.closed:
            await self.reconnect()

    @reauthenticate_dsf()
    async def noop(self) -> None:
        """Send keep-alive request to DSF."""
        await self._ensure_session()

        url = f'{self.address}/machine/noop'

        async with self.session.get(url) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during noop')

    @reauthenticate_dsf()
    async def model(self) -> dict:
        """Get the full machine object model."""
        self.logger.debug("model: fetching full object model")

        await self._ensure_session()

        url = f'{self.address}/machine/model'

        response = {}
        async with self.session.get(url) as r:
            response = await r.json()
        return response

    @reauthenticate_dsf()
    async def status(self) -> dict:
        """Get machine object model (alias for model in DSF v3.4.6+)."""
        self.logger.debug("status: fetching machine status")

        await self._ensure_session()

        url = f'{self.address}/machine/status'

        response = {}
        async with self.session.get(url) as r:
            response = await r.json()
        return response

    @reauthenticate_dsf()
    async def code(self, gcode: str, async_exec: bool = False) -> str:
        """Execute G-code on the Duet via DSF.

        :param gcode: G-code command to execute
        :param async_exec: If True, don't wait for code to complete
        :return: Response from G-code execution
        """
        await self._ensure_session()

        url = f'{self.address}/machine/code'

        params = {}
        if async_exec:
            params['async'] = 'true'

        # DSF expects the G-code in the request body
        async with self.session.post(url, data=gcode, params=params if params else None) as r:
            response = await r.text()
        return response

    async def download(
        self,
        filename: str,
        chunk_size: Optional[int] = 1024,
    ) -> AsyncIterable:
        """Download a file from the Duet via DSF.

        :param filename: Path to the file on the Duet (e.g., '0:/gcodes/test.gcode')
        :param chunk_size: Size of chunks to yield
        """
        await self._ensure_session()

        # URL-encode the filename in the path
        encoded_filename = quote(filename, safe='')
        url = f'{self.address}/machine/file/{encoded_filename}'

        async with self.session.get(url) as r:
            async for chunk in r.content.iter_chunked(chunk_size):
                yield chunk

    @reauthenticate_dsf()
    async def upload(
        self,
        filename: str,
        content: bytes,
    ) -> None:
        """Upload a file to the Duet via DSF.

        :param filename: Destination path on the Duet (e.g., '0:/gcodes/test.gcode')
        :param content: File content as bytes
        """
        await self._ensure_session()

        # URL-encode the filename in the path
        encoded_filename = quote(filename, safe='')
        url = f'{self.address}/machine/file/{encoded_filename}'

        if isinstance(content, str):
            content = content.encode('utf-8')

        headers = {
            'Content-Length': str(len(content)),
        }

        async with self.session.put(url, data=content, headers=headers) as r:
            # DSF returns 201 Created on success
            if r.status not in (200, 201):
                self.logger.warning(f'Unexpected status {r.status} during upload')

    async def upload_stream(
        self,
        filename: str,
        file: BinaryIO,
        progress: Optional[Callable] = None,
    ) -> None:
        """Upload a file to the Duet via DSF using streaming.

        :param filename: Destination path on the Duet
        :param file: File-like object to upload
        :param progress: Optional callback for progress updates (0-100)
        """
        await self._ensure_session()

        # URL-encode the filename in the path
        encoded_filename = quote(filename, safe='')
        url = f'{self.address}/machine/file/{encoded_filename}'

        # Get file size
        file.seek(0, 2)  # Seek to end
        filesize = file.tell()
        file.seek(0)  # Seek back to start

        async def file_chunk():
            while chunk := file.read(self.UPLOAD_CHUNK_SIZE):
                if progress:
                    progress(
                        max(0.0, min(100.0,
                                     file.tell() / filesize * 100.0)),
                    )
                if not chunk:
                    break
                yield chunk

        timeout = aiohttp.ClientTimeout(
            total=self.UPLOAD_TIMEOUT,
        )

        headers = {
            'Content-Length': str(filesize),
        }

        async with self.session.put(
            url=url,
            data=file_chunk(),
            timeout=timeout,
            headers=headers,
        ) as r:
            # DSF returns 201 Created on success
            if r.status not in (200, 201):
                self.logger.warning(f'Unexpected status {r.status} during upload_stream')

    @reauthenticate_dsf()
    async def delete(self, filename: str, recursive: bool = False) -> None:
        """Delete a file or directory from the Duet via DSF.

        :param filename: Path to delete on the Duet
        :param recursive: If True, delete directories recursively
        """
        await self._ensure_session()

        # URL-encode the filename in the path
        encoded_filename = quote(filename, safe='')
        url = f'{self.address}/machine/file/{encoded_filename}'

        params = {}
        if recursive:
            params['recursive'] = 'true'

        async with self.session.delete(url, params=params if params else None) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during delete')

    @reauthenticate_dsf()
    async def fileinfo(self, filename: str) -> dict:
        """Get parsed file information from DSF.

        :param filename: Path to the file on the Duet
        :return: Parsed file information
        """
        await self._ensure_session()

        # URL-encode the filename in the path
        encoded_filename = quote(filename, safe='')
        url = f'{self.address}/machine/fileinfo/{encoded_filename}'

        response = {}
        async with self.session.get(url) as r:
            response = await r.json()
        return response

    @reauthenticate_dsf()
    async def move(
        self,
        from_path: str,
        to_path: str,
        force: bool = False,
    ) -> None:
        """Move a file on the Duet via DSF.

        :param from_path: Source path
        :param to_path: Destination path
        :param force: If True, overwrite existing file at destination
        """
        await self._ensure_session()

        url = f'{self.address}/machine/file/move'

        # DSF expects form-encoded body
        data = aiohttp.FormData()
        data.add_field('from', from_path)
        data.add_field('to', to_path)
        if force:
            data.add_field('force', 'true')

        async with self.session.post(url, data=data) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during move')

    @reauthenticate_dsf()
    async def filelist(self, directory: str) -> list:
        """List files in a directory on the Duet via DSF.

        :param directory: Directory path to list
        :return: List of files and directories
        """
        await self._ensure_session()

        # URL-encode the directory in the path
        encoded_directory = quote(directory, safe='')
        url = f'{self.address}/machine/directory/{encoded_directory}'

        response = []
        async with self.session.get(url) as r:
            response = await r.json()
        return response

    @reauthenticate_dsf()
    async def mkdir(self, directory: str) -> None:
        """Create a directory on the Duet via DSF.

        :param directory: Directory path to create
        """
        await self._ensure_session()

        # URL-encode the directory in the path
        encoded_directory = quote(directory, safe='')
        url = f'{self.address}/machine/directory/{encoded_directory}'

        async with self.session.put(url) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during mkdir')

    @reauthenticate_dsf()
    async def install_plugin(self, content: bytes) -> None:
        """Install a plugin on the Duet via DSF.

        :param content: Plugin ZIP file content
        """
        await self._ensure_session()

        url = f'{self.address}/machine/plugin'

        headers = {
            'Content-Type': 'application/zip',
            'Content-Length': str(len(content)),
        }

        async with self.session.put(url, data=content, headers=headers) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during install_plugin')

    @reauthenticate_dsf()
    async def uninstall_plugin(self, name: str) -> None:
        """Uninstall a plugin from the Duet via DSF.

        :param name: Plugin name to uninstall
        """
        await self._ensure_session()

        url = f'{self.address}/machine/plugin'

        # Plugin name is passed as a header
        headers = {
            'X-Plugin-Name': name,
        }

        async with self.session.delete(url, headers=headers) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during uninstall_plugin')

    @reauthenticate_dsf()
    async def set_plugin_data(self, plugin: str, key: str, value: str) -> None:
        """Set plugin data via DSF.

        :param plugin: Plugin name
        :param key: Data key
        :param value: Data value
        """
        await self._ensure_session()

        url = f'{self.address}/machine/plugin'

        data = {
            'plugin': plugin,
            'key': key,
            'value': value,
        }

        async with self.session.patch(url, json=data) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during set_plugin_data')

    @reauthenticate_dsf()
    async def start_plugin(self, name: str) -> None:
        """Start a plugin on the Duet via DSF.

        :param name: Plugin name to start
        """
        await self._ensure_session()

        url = f'{self.address}/machine/startPlugin'

        # Plugin name is passed as form data
        data = aiohttp.FormData()
        data.add_field('plugin', name)

        async with self.session.post(url, data=data) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during start_plugin')

    @reauthenticate_dsf()
    async def stop_plugin(self, name: str) -> None:
        """Stop a plugin on the Duet via DSF.

        :param name: Plugin name to stop
        """
        await self._ensure_session()

        url = f'{self.address}/machine/stopPlugin'

        # Plugin name is passed as form data
        data = aiohttp.FormData()
        data.add_field('plugin', name)

        async with self.session.post(url, data=data) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during stop_plugin')
