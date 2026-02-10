#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Duet Software Framework (SBC mode) HTTP API Module."""

import asyncio
import json
from typing import AsyncIterable, AsyncIterator, BinaryIO, Callable, Optional
from urllib.parse import quote

import aiohttp
import attr

from .base import DuetAPIBase, reauthenticate


class DSFAuthenticationError(Exception):
    """DSF Authentication Error."""

    pass


@attr.s
class DuetSoftwareFramework(DuetAPIBase):
    """Duet Software Framework (SBC mode) API Class."""

    _ws = attr.ib(type=Optional[aiohttp.ClientWebSocketResponse], default=None)
    _ws_connected = attr.ib(type=bool, default=False)

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

    async def disconnect(self) -> None:
        """Disconnect from the Duet via DSF."""
        await self._ensure_session()

        url = f'{self.address}/machine/disconnect'

        async with self.session.get(url) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during disconnect')

        await self.close()

    def _build_ws_url(self) -> str:
        """Build WebSocket URL with session key."""
        ws_url = self.address.replace('http://', 'ws://').replace('https://', 'wss://')
        ws_url = f'{ws_url}/machine'

        session_key = self.session.headers.get('X-Session-Key', '')
        if session_key:
            ws_url = f'{ws_url}?sessionKey={session_key}'
        return ws_url

    async def _handle_ws_text_message(self, msg) -> Optional[dict]:
        """Handle a TEXT WebSocket message, returns parsed data or None."""
        if msg.data == 'PONG\n':
            return None  # Ignore PONG responses

        try:
            data = json.loads(msg.data)
            await self._ws.send_str('OK\n')
            return data
        except json.JSONDecodeError:
            self.logger.warning(f"Invalid JSON from WebSocket: {msg.data[:100]}")
            return None

    async def subscribe(self) -> AsyncIterator[dict]:
        """Subscribe to object model updates via WebSocket.

        Yields object model updates (first full model, then patches).
        Handles PING/PONG keepalive internally.
        """
        await self._ensure_session()
        ws_url = self._build_ws_url()

        try:
            self._ws = await self.session.ws_connect(ws_url, heartbeat=30.0)
            self._ws_connected = True
            self.logger.info(f"WebSocket connected to {ws_url}")

            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = await self._handle_ws_text_message(msg)
                    if data is not None:
                        yield data
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.logger.error(f"WebSocket error: {self._ws.exception()}")
                    break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                    self.logger.info("WebSocket closed by server")
                    break

        except aiohttp.ClientError as e:
            self.logger.error(f"WebSocket connection error: {e}")
            raise
        finally:
            self._ws_connected = False
            if self._ws and not self._ws.closed:
                await self._ws.close()
            self._ws = None

    async def send_ping(self) -> bool:
        """Send PING to WebSocket, returns True if connected."""
        if self._ws and not self._ws.closed:
            try:
                await self._ws.send_str('PING\n')
                return True
            except Exception:
                return False
        return False

    async def unsubscribe(self) -> None:
        """Close WebSocket subscription."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._ws_connected = False

    @property
    def ws_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._ws_connected and self._ws is not None and not self._ws.closed

    @reauthenticate(auth_error_status=403)
    async def noop(self) -> None:
        """Send keep-alive request to DSF."""
        await self._ensure_session()

        url = f'{self.address}/machine/noop'

        async with self.session.get(url) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during noop')

    @reauthenticate(auth_error_status=403)
    async def model(self) -> dict:
        """Get the full machine object model."""
        self.logger.debug("model: fetching full object model")

        await self._ensure_session()

        url = f'{self.address}/machine/model'

        response = {}
        async with self.session.get(url) as r:
            response = await r.json()
        return response

    @reauthenticate(auth_error_status=403)
    async def status(self) -> dict:
        """Get machine object model (alias for model in DSF v3.4.6+)."""
        self.logger.debug("status: fetching machine status")

        await self._ensure_session()

        url = f'{self.address}/machine/status'

        response = {}
        async with self.session.get(url) as r:
            response = await r.json()
        return response

    @reauthenticate(auth_error_status=403)
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

    # --- Unified interface methods ---

    async def send_gcode(self, command: str, no_reply: bool = True) -> str:
        """Send G-code via the unified interface."""
        return await self.code(command, async_exec=no_reply)

    async def download(
        self,
        filepath: str,
        chunk_size: Optional[int] = 1024,
    ) -> AsyncIterable:
        """Download a file from the Duet via DSF.

        :param filepath: Path to the file on the Duet (e.g., '0:/gcodes/test.gcode')
        :param chunk_size: Size of chunks to yield
        """
        await self._ensure_session()

        # URL-encode the filepath in the path
        encoded_filepath = quote(filepath, safe='')
        url = f'{self.address}/machine/file/{encoded_filepath}'

        async with self.session.get(url) as r:
            async for chunk in r.content.iter_chunked(chunk_size):
                yield chunk

    @reauthenticate(auth_error_status=403)
    async def upload(
        self,
        filepath: str,
        content: bytes,
    ) -> None:
        """Upload a file to the Duet via DSF.

        :param filepath: Destination path on the Duet (e.g., '0:/gcodes/test.gcode')
        :param content: File content as bytes
        """
        await self._ensure_session()

        # URL-encode the filepath in the path
        encoded_filepath = quote(filepath, safe='')
        url = f'{self.address}/machine/file/{encoded_filepath}'

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
        filepath: str,
        file: BinaryIO,
        progress: Optional[Callable] = None,
    ) -> None:
        """Upload a file to the Duet via DSF using streaming.

        :param filepath: Destination path on the Duet
        :param file: File-like object to upload
        :param progress: Optional callback for progress updates (0-100)
        """
        await self._ensure_session()

        # URL-encode the filepath in the path
        encoded_filepath = quote(filepath, safe='')
        url = f'{self.address}/machine/file/{encoded_filepath}'

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

    @reauthenticate(auth_error_status=403)
    async def delete(self, filepath: str, recursive: bool = False) -> None:
        """Delete a file or directory from the Duet via DSF.

        :param filepath: Path to delete on the Duet
        :param recursive: If True, delete directories recursively
        """
        await self._ensure_session()

        # URL-encode the filepath in the path
        encoded_filepath = quote(filepath, safe='')
        url = f'{self.address}/machine/file/{encoded_filepath}'

        params = {}
        if recursive:
            params['recursive'] = 'true'

        async with self.session.delete(url, params=params if params else None) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during delete')

    @reauthenticate(auth_error_status=403)
    async def fileinfo(self, filepath: str, **kwargs) -> dict:
        """Get parsed file information from DSF.

        :param filepath: Path to the file on the Duet
        :return: Parsed file information
        """
        await self._ensure_session()

        # URL-encode the filepath in the path
        encoded_filepath = quote(filepath, safe='')
        url = f'{self.address}/machine/fileinfo/{encoded_filepath}'

        response = {}
        async with self.session.get(url) as r:
            response = await r.json()
        return response

    @reauthenticate(auth_error_status=403)
    async def move(
        self,
        old_filepath: str,
        new_filepath: str,
        overwrite: bool = False,
    ) -> None:
        """Move a file on the Duet via DSF.

        :param old_filepath: Source path
        :param new_filepath: Destination path
        :param overwrite: If True, overwrite existing file at destination
        """
        await self._ensure_session()

        url = f'{self.address}/machine/file/move'

        # DSF expects form-encoded body
        data = aiohttp.FormData()
        data.add_field('from', old_filepath)
        data.add_field('to', new_filepath)
        if overwrite:
            data.add_field('force', 'true')

        async with self.session.post(url, data=data) as r:
            # DSF returns 204 No Content on success
            if r.status != 204:
                self.logger.warning(f'Unexpected status {r.status} during move')

    @reauthenticate(auth_error_status=403)
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

    @reauthenticate(auth_error_status=403)
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

    @reauthenticate(auth_error_status=403)
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

    @reauthenticate(auth_error_status=403)
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

    @reauthenticate(auth_error_status=403)
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

    @reauthenticate(auth_error_status=403)
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

    @reauthenticate(auth_error_status=403)
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
