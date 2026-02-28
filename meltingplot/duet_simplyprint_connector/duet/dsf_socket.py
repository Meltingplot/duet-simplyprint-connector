#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Duet Software Framework Socket API Module.

Connects directly to the DCS Unix domain socket at /var/run/dsf/dcs.sock
for command execution and object model subscriptions, and uses direct
filesystem I/O for file uploads and downloads on the SBC.
"""

import asyncio
import json
import os
import shutil
from typing import AsyncIterable, AsyncIterator, BinaryIO, Callable, Optional

import attr

from .base import DuetAPIBase

# DSF protocol version we support
DSF_PROTOCOL_VERSION = 12

# Default paths
DEFAULT_SOCKET_PATH = '/var/run/dsf/dcs.sock'
DEFAULT_SD_BASE_DIR = '/opt/dsf/sd'

# Buffer size for socket reads
SOCKET_BUFFER_SIZE = 65536

# Chunk size for file I/O streaming
FILE_IO_CHUNK_SIZE = 65536


def _resolve_dsf_path(filepath: str, base_dir: str) -> str:
    """Resolve a RepRapFirmware virtual path to a real filesystem path.

    Maps paths like '0:/gcodes/test.gcode' or '/gcodes/test.gcode'
    to the local SBC filesystem under the DSF base directory.

    :param filepath: RRF virtual path (e.g. '0:/gcodes/test.gcode')
    :param base_dir: DSF SD card base directory (e.g. '/opt/dsf/sd')
    :return: Resolved absolute filesystem path
    """
    # Strip the volume prefix '0:/' or '0:' if present
    if filepath.startswith('0:'):
        filepath = filepath[2:]
    # Ensure leading slash for consistent joining
    if filepath.startswith('/'):
        filepath = filepath[1:]
    resolved = os.path.join(base_dir, filepath)
    # Prevent directory traversal
    resolved = os.path.realpath(resolved)
    base_real = os.path.realpath(base_dir)
    if not resolved.startswith(base_real):
        raise ValueError(f'Path traversal detected: {filepath}')
    return resolved


class _SocketReceiver:
    """Buffered JSON message receiver for Unix domain sockets.

    Reads from an asyncio StreamReader and yields complete JSON objects
    by tracking brace nesting depth.
    """

    def __init__(self, reader: asyncio.StreamReader):
        self._reader = reader
        self._buffer = ''

    async def receive_json(self) -> dict:
        """Receive a single complete JSON object from the socket.

        :return: Parsed JSON dict
        :raises ConnectionError: If the connection is closed
        """
        while True:
            # Try to find a complete JSON object in the buffer
            end = self._find_json_end(self._buffer)
            if end >= 0:
                json_str = self._buffer[:end + 1]
                self._buffer = self._buffer[end + 1:]
                return json.loads(json_str)

            # Need more data
            data = await self._reader.read(SOCKET_BUFFER_SIZE)
            if not data:
                raise ConnectionError('Socket connection closed')
            self._buffer += data.decode('utf-8')

    @staticmethod
    def _find_json_end(s: str) -> int:
        """Find the end index of the first complete JSON object in a string.

        :param s: String buffer to search
        :return: End index of JSON object, or -1 if incomplete
        """
        depth = 0
        in_string = False
        escape = False

        for i, ch in enumerate(s):
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return i
        return -1


@attr.s
class DuetControlSocket(DuetAPIBase):
    """DCS Unix domain socket API implementation.

    Communicates with DuetControlServer via the Unix socket for
    G-code execution and object model retrieval. Uses direct
    filesystem I/O for file operations on the SBC.
    """

    socket_path = attr.ib(type=str, default=DEFAULT_SOCKET_PATH)
    sd_base_dir = attr.ib(type=str, default=DEFAULT_SD_BASE_DIR)

    # Internal state for command connection
    _cmd_reader = attr.ib(type=Optional[asyncio.StreamReader], default=None)
    _cmd_writer = attr.ib(type=Optional[asyncio.StreamWriter], default=None)
    _cmd_receiver = attr.ib(type=Optional[_SocketReceiver], default=None)
    _cmd_lock = attr.ib(type=asyncio.Lock, factory=asyncio.Lock)

    # Internal state for subscribe connection
    _sub_reader = attr.ib(type=Optional[asyncio.StreamReader], default=None)
    _sub_writer = attr.ib(type=Optional[asyncio.StreamWriter], default=None)
    _sub_receiver = attr.ib(type=Optional[_SocketReceiver], default=None)

    async def _open_connection(self) -> tuple:
        """Open a Unix socket connection and perform the handshake.

        :return: (reader, writer, receiver) tuple
        :raises ConnectionError: If connection or handshake fails
        """
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        receiver = _SocketReceiver(reader)

        # Receive server init message: {"id": N, "version": N}
        server_init = await receiver.receive_json()
        self.logger.debug(f"DCS server init: {server_init}")

        server_version = server_init.get('version', 0)
        if server_version < 8:
            writer.close()
            raise ConnectionError(
                f'Incompatible DCS API version: {server_version} (need >= 8)',
            )

        return reader, writer, receiver

    async def _init_command_connection(self) -> None:
        """Initialize a command mode connection to DCS."""
        reader, writer, receiver = await self._open_connection()

        # Send command init message
        init_msg = {
            'mode': 'Command',
            'version': DSF_PROTOCOL_VERSION,
        }
        writer.write(json.dumps(init_msg, separators=(',', ':')).encode('utf-8'))
        await writer.drain()

        # Wait for success response
        response = await receiver.receive_json()
        if not response.get('success', False):
            writer.close()
            raise ConnectionError(
                f'DCS command connection rejected: {response}',
            )

        self._cmd_reader = reader
        self._cmd_writer = writer
        self._cmd_receiver = receiver
        self.logger.info("DCS command connection established")

    async def _init_subscribe_connection(self) -> tuple:
        """Initialize a subscribe mode connection to DCS.

        :return: (reader, writer, receiver) tuple
        """
        reader, writer, receiver = await self._open_connection()

        # Send subscribe init message with Patch mode
        init_msg = {
            'mode': 'Subscribe',
            'version': DSF_PROTOCOL_VERSION,
            'SubscriptionMode': 'Patch',
        }
        writer.write(json.dumps(init_msg, separators=(',', ':')).encode('utf-8'))
        await writer.drain()

        # Wait for success response
        response = await receiver.receive_json()
        if not response.get('success', False):
            writer.close()
            raise ConnectionError(
                f'DCS subscribe connection rejected: {response}',
            )

        self.logger.info("DCS subscribe connection established")
        return reader, writer, receiver

    async def _send_command(self, command: dict) -> dict:
        """Send a command over the command connection and return the response.

        :param command: Command dict to send
        :return: Response dict from DCS
        """
        async with self._cmd_lock:
            if self._cmd_writer is None:
                await self._init_command_connection()

            data = json.dumps(command, separators=(',', ':')).encode('utf-8')
            self._cmd_writer.write(data)
            await self._cmd_writer.drain()

            response = await self._cmd_receiver.receive_json()

            if not response.get('success', True):
                error_type = response.get('errorType', 'Unknown')
                error_msg = response.get('errorMessage', str(response))
                raise ConnectionError(f'DCS command failed ({error_type}): {error_msg}')

            return response

    async def reconnect(self) -> dict:
        """Reconnect to DCS via Unix socket."""
        if self._reconnect_lock.locked():
            async with self._reconnect_lock:
                return {'isEmulated': True}

        async with self._reconnect_lock:
            await self._close_command_connection()
            await self._init_command_connection()
            return {'isEmulated': True}

    async def connect(self) -> dict:
        """Connect to DCS via Unix socket.

        Returns a dict with 'isEmulated': True to indicate SBC mode.
        """
        await self._init_command_connection()
        return {'isEmulated': True}

    async def close(self) -> None:
        """Close all socket connections."""
        await self._close_command_connection()
        await self._close_subscribe_connection()

    async def _close_command_connection(self) -> None:
        """Close the command connection."""
        if self._cmd_writer is not None:
            try:
                self._cmd_writer.close()
            except Exception:
                pass
            self._cmd_writer = None
            self._cmd_reader = None
            self._cmd_receiver = None

    async def _close_subscribe_connection(self) -> None:
        """Close the subscribe connection."""
        if self._sub_writer is not None:
            try:
                self._sub_writer.close()
            except Exception:
                pass
            self._sub_writer = None
            self._sub_reader = None
            self._sub_receiver = None

    # --- Unified interface: G-code ---

    async def send_gcode(self, command: str, no_reply: bool = True) -> str:
        """Send a G-code command via DCS command connection.

        :param command: G-code command string
        :param no_reply: If True, execute asynchronously
        :return: Reply string
        """
        cmd = {
            'command': 'SimpleCode',
            'code': command,
        }
        if no_reply:
            cmd['async'] = True

        response = await self._send_command(cmd)
        return response.get('result', '')

    # --- Unified interface: Object model ---

    async def model(self) -> dict:
        """Get the full machine object model via command connection."""
        response = await self._send_command({
            'command': 'GetObjectModel',
        })
        return response.get('result', {})

    # --- Subscribe interface ---

    async def subscribe(self) -> AsyncIterator[dict]:
        """Subscribe to object model updates via a dedicated socket connection.

        Yields object model updates (first full model, then patches).
        """
        reader, writer, receiver = await self._init_subscribe_connection()
        self._sub_reader = reader
        self._sub_writer = writer
        self._sub_receiver = receiver

        try:
            while True:
                # Receive object model update
                data = await receiver.receive_json()
                yield data

                # Send acknowledgment
                ack = json.dumps(
                    {
                        'command': 'Acknowledge',
                    },
                    separators=(',', ':'),
                ).encode('utf-8')
                writer.write(ack)
                await writer.drain()

        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception as e:
            self.logger.error(f"Subscribe connection error: {e}")
            raise
        finally:
            await self._close_subscribe_connection()

    @property
    def ws_connected(self) -> bool:
        """Check if subscribe connection is active."""
        return self._sub_writer is not None

    async def unsubscribe(self) -> None:
        """Close the subscribe connection."""
        await self._close_subscribe_connection()

    # --- Unified interface: File operations via direct filesystem I/O ---

    async def download(
        self,
        filepath: str,
        chunk_size: Optional[int] = 1024,
    ) -> AsyncIterable:
        """Download a file from the printer using direct filesystem read.

        :param filepath: Virtual path (e.g. '0:/gcodes/test.gcode')
        :param chunk_size: Size of chunks to yield
        """
        real_path = _resolve_dsf_path(filepath, self.sd_base_dir)
        loop = asyncio.get_running_loop()

        def _read_chunks():
            chunks = []
            with open(real_path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    chunks.append(chunk)
            return chunks

        chunks = await loop.run_in_executor(None, _read_chunks)
        for chunk in chunks:
            yield chunk

    async def upload_stream(
        self,
        filepath: str,
        file: BinaryIO,
        progress: Optional[Callable] = None,
    ) -> None:
        """Upload a file to the printer using direct filesystem write.

        :param filepath: Virtual destination path (e.g. '0:/gcodes/test.gcode')
        :param file: File-like object to upload
        :param progress: Optional progress callback (0-100)
        :raises IOError: If the write fails
        """
        real_path = _resolve_dsf_path(filepath, self.sd_base_dir)
        loop = asyncio.get_running_loop()

        # Ensure parent directory exists
        parent_dir = os.path.dirname(real_path)

        def _write_file():
            os.makedirs(parent_dir, exist_ok=True)

            file.seek(0, 2)
            filesize = file.tell()
            file.seek(0)

            with open(real_path, 'wb') as out:
                written = 0
                while True:
                    chunk = file.read(FILE_IO_CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    if progress and filesize > 0:
                        progress(
                            max(0.0, min(100.0, written / filesize * 100.0)),
                        )

        try:
            await loop.run_in_executor(None, _write_file)
        except OSError as e:
            raise IOError(f"Upload failed: {e}") from e

    async def delete(self, filepath: str) -> None:
        """Delete a file on the printer using direct filesystem delete.

        :param filepath: Virtual path to file
        """
        real_path = _resolve_dsf_path(filepath, self.sd_base_dir)
        loop = asyncio.get_running_loop()

        def _delete():
            if os.path.isdir(real_path):
                shutil.rmtree(real_path)
            elif os.path.exists(real_path):
                os.remove(real_path)

        await loop.run_in_executor(None, _delete)

    async def fileinfo(self, filepath: str, **kwargs) -> dict:
        """Get file information.

        Uses DCS command connection to parse the G-code file
        and return metadata, since direct stat() won't give
        G-code-specific info like layer height.

        :param filepath: Virtual path to file
        :return: File information dict
        """
        cmd = {
            'command': 'GetFileInfo',
            'fileName': filepath,
        }
        response = await self._send_command(cmd)
        result = response.get('result', {})
        if not result:
            raise FileNotFoundError(f"File not found: {filepath}")
        return result

    async def filelist(self, directory: str) -> list:
        """List files in a directory using direct filesystem listing.

        :param directory: Virtual directory path
        :return: List of file info dicts
        """
        real_path = _resolve_dsf_path(directory, self.sd_base_dir)
        loop = asyncio.get_running_loop()

        def _list_dir():
            result = []
            if not os.path.isdir(real_path):
                return result
            for entry in os.scandir(real_path):
                stat = entry.stat()
                result.append({
                    'type': 'd' if entry.is_dir() else 'f',
                    'name': entry.name,
                    'size': stat.st_size if entry.is_file() else 0,
                    'date': stat.st_mtime,
                })
            return result

        return await loop.run_in_executor(None, _list_dir)

    async def mkdir(self, directory: str) -> None:
        """Create a directory using direct filesystem mkdir.

        :param directory: Virtual directory path
        """
        real_path = _resolve_dsf_path(directory, self.sd_base_dir)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.makedirs, real_path, 0o755, True)

    async def move(
        self,
        old_filepath: str,
        new_filepath: str,
        overwrite: bool = False,
    ) -> None:
        """Move/rename a file using direct filesystem operations.

        :param old_filepath: Virtual source path
        :param new_filepath: Virtual destination path
        :param overwrite: Overwrite existing file
        """
        old_real = _resolve_dsf_path(old_filepath, self.sd_base_dir)
        new_real = _resolve_dsf_path(new_filepath, self.sd_base_dir)
        loop = asyncio.get_running_loop()

        def _move():
            if not overwrite and os.path.exists(new_real):
                raise IOError(f"Destination already exists: {new_filepath}")
            os.makedirs(os.path.dirname(new_real), exist_ok=True)
            shutil.move(old_real, new_real)

        await loop.run_in_executor(None, _move)
