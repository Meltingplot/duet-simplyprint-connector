#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Duet Web Control HTTP Api Module."""

import asyncio
import datetime
from typing import AsyncIterable, BinaryIO, Callable, Optional
from zlib import crc32

import aiohttp
import attr

from .base import DuetAPIBase, reauthenticate


@attr.s
class RepRapFirmware(DuetAPIBase):
    """RepRapFirmware API Class."""

    REPLY_CACHE_TTL = 10

    password = attr.ib(type=str, default="meltingplot")
    _last_reply = attr.ib(type=str, default='')
    _last_reply_timeout = attr.ib(type=datetime.datetime, factory=datetime.datetime.now)

    def __attrs_post_init__(self):
        """Post init."""
        self.callbacks[502] = self._default_http_502_bad_gateway_callback
        self.callbacks[503] = self._default_http_503_busy_callback

    async def _default_http_502_bad_gateway_callback(self, e):
        # a reverse proxy may return HTTP status code 502 if the Duet is not available
        # due to open socket limit. In this case, we retry the request.
        self.logger.error(f'Duet bad gateway {e.request_info!s} - retry')
        await asyncio.sleep(5)

    async def _default_http_503_busy_callback(self, e):
        # Besides, RepRapFirmware may run short on memory and
        # may not be able to respond properly. In this case,
        # HTTP status code 503 is returned.
        self.logger.error(f'Duet busy {e.request_info!s} - retry')
        await asyncio.sleep(5)

    async def reconnect(self) -> dict:
        """Reconnect to the Duet."""
        # Prevent multiple reconnects
        if self._reconnect_lock.locked():
            # Wait for reconnect to finish
            async with self._reconnect_lock:
                return {'err': 0}

        async with self._reconnect_lock:
            url = f'{self.address}/rr_connect'

            params = {
                'password': self.password,
                'sessionKey': 'yes',
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
                json_response = await r.json()

            if json_response.get('err', 0) != 0:
                raise aiohttp.ClientResponseError(
                    request_info=aiohttp.RequestInfo(
                        url=url,
                        method='GET',
                        headers={},
                        real_url=url,
                    ),
                    history=(),
                    status=401,
                    message=f'Authentication failed: {json_response}',
                )

            if 'sessionKey' in json_response:
                self.session.headers['X-Session-Key'] = str(json_response['sessionKey'])
            if 'sessionTimeout' in json_response:
                self.session_timeout = json_response['sessionTimeout']

            return json_response

    async def disconnect(self) -> dict:
        """Disconnect from the Duet."""
        await self._ensure_session()

        url = f'{self.address}/rr_disconnect'

        response = {}
        async with self.session.get(url) as r:
            response = await r.json()
        await self.close()
        return response

    @reauthenticate()
    async def rr_model(
        self,
        key: Optional[str] = None,
        frequently: Optional[bool] = False,
        verbose: Optional[bool] = False,
        include_null: Optional[bool] = False,
        include_obsolete: Optional[bool] = False,
        depth: Optional[int] = 99,
        array: Optional[int] = 0,
    ) -> dict:
        """rr_model Get Machine Model."""
        self.logger.debug(
            f"rr_model: key={key}, frequently={frequently},"
            f" verbose={verbose}, include_null={include_null},"
            f" include_obsolete={include_obsolete}, depth={depth}, array={array}",
        )

        await self._ensure_session()

        url = f"{self.address}/rr_model"

        flags = []

        if frequently:
            flags.append('f')

        if verbose:
            flags.append('v')

        if include_null:
            flags.append('n')

        if include_obsolete:
            flags.append('o')

        flags.append(f"d{depth}")

        if array is not None and array > 0:
            flags.append(f"a{array}")

        params = {
            'key': key if key is not None else '',
            'flags': ''.join(flags),
        }

        response = {}
        async with self.session.get(url, params=params) as r:
            response = await r.json()
        return response

    @reauthenticate()
    async def rr_gcode(self, gcode: str, no_reply: bool = False) -> str:
        """rr_gcode Send GCode to Duet."""
        await self._ensure_session()

        url = f'{self.address}/rr_gcode'

        params = {
            'gcode': gcode,
        }

        async with self.session.get(url, params=params):
            pass

        if no_reply:
            return ''
        else:
            return await self.rr_reply()

    @reauthenticate()
    async def rr_reply(self, nocache: bool = False) -> str:
        """rr_reply Get Reply from Duet.

        Returns the cached reply if caching is enabled, response is empty,
        and the cache hasn't expired. Otherwise fetches and caches new response.
        """
        await self._ensure_session()

        url = f'{self.address}/rr_reply'

        response = ''
        async with self.session.get(url) as r:
            response = await r.text()

        now = datetime.datetime.now()

        # Return cached reply if: caching enabled, empty response, cache still valid
        if not nocache and response == '' and now < self._last_reply_timeout:
            return self._last_reply

        # Update cache with new response (only if caching enabled or response non-empty)
        if not nocache or response != '':
            self._last_reply = response
            self._last_reply_timeout = now + datetime.timedelta(seconds=self.REPLY_CACHE_TTL)

        return response

    async def rr_download(
        self,
        filepath: str,
        chunk_size: Optional[int] = 1024,
    ) -> AsyncIterable:
        """rr_download Download File from Duet."""
        await self._ensure_session()

        url = f'{self.address}/rr_download'

        params = {
            'name': filepath,
        }

        async with self.session.get(url, params=params) as r:
            async for chunk in r.content.iter_chunked(chunk_size):
                yield chunk

    @reauthenticate()
    async def rr_upload(
        self,
        filepath: str,
        content: bytes,
        last_modified: Optional[datetime.datetime] = None,
    ) -> object:
        """rr_upload Upload File to Duet."""
        await self._ensure_session()

        url = f'{self.address}/rr_upload'

        params = {
            'name': filepath,
        }

        if last_modified is not None:
            params['time'] = last_modified.isoformat(timespec='seconds')

        try:
            checksum = crc32(content) & 0xffffffff
        except TypeError:
            content = content.encode('utf-8')
            checksum = crc32(content) & 0xffffffff

        params['crc32'] = f'{checksum:08x}'

        headers = {
            'Content-Length': str(len(content)),
        }

        response = b''
        async with self.session.post(url, data=content, params=params, headers=headers) as r:
            response = await r.json()
        return response

    async def rr_upload_stream(
        self,
        filepath: str,
        file: BinaryIO,
        last_modified: Optional[datetime.datetime] = None,
        progress: Optional[Callable] = None,
    ) -> object:
        """rr_upload_stream Upload File to Duet."""
        await self._ensure_session()

        url = f'{self.address}/rr_upload'

        params = {
            'name': filepath,
        }

        if last_modified is not None:
            params['time'] = last_modified.isoformat(timespec='seconds')

        # Calculate CRC32 checksum by reading entire file
        checksum = 0
        while chunk := file.read(self.UPLOAD_CHUNK_SIZE):
            checksum = crc32(chunk, checksum) & 0xffffffff

        filesize = file.tell()
        file.seek(0)

        params['crc32'] = f'{checksum:08x}'

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

        response = b''
        async with self.session.post(
            url=url,
            data=file_chunk(),
            params=params,
            timeout=timeout,
            headers=headers,
        ) as r:
            response = await r.json()

        return response

    @reauthenticate()
    async def rr_filelist(self, directory: str, **kwargs: any) -> object:
        """
        rr_filelist List Files in Directory.

        List Files in a Directory on the Duet.

        :param directory: Directory Path
        :type directory: str
        :param kwargs: Additional Parameters
        :type kwargs: any
        :return: File List
        :rtype: object
        """
        await self._ensure_session()

        url = f'{self.address}/rr_filelist'

        params = {
            'dir': directory,
        }

        response = {}
        async with self.session.get(url, params=params, **kwargs) as r:
            response = await r.json()

        return response

    @reauthenticate()
    async def rr_fileinfo(self, name: Optional[str] = None, **kwargs: any) -> object:
        """
        rr_fileinfo Get File Information.

        Get Information about a File on the Duet.

        :param name: Filepath
        :type name: str
        :param kwargs: Additional Parameters
        :type kwargs: any
        :return: File Information
        :rtype: object
        """
        await self._ensure_session()

        url = f'{self.address}/rr_fileinfo'

        params = {}

        if name is not None:
            params['name'] = name

        response = {}
        async with self.session.get(url, params=params, **kwargs) as r:
            response = await r.json()

        return response

    @reauthenticate()
    async def rr_mkdir(self, directory: str) -> object:
        """rr_mkdir Create a Folder.

        Create a Folder on the Duet.

        :param directory: Folder Path
        :type directory: str
        :return: Error Object
        :rtype: object
        """
        await self._ensure_session()

        url = f'{self.address}/rr_mkdir'

        params = {
            'dir': directory,
        }

        response = {}
        async with self.session.get(url, params=params) as r:
            response = await r.json()

        return response

    @reauthenticate()
    async def rr_move(
        self,
        old_filepath: str,
        new_filepath: str,
        overwrite: Optional[bool] = False,
    ) -> object:
        """rr_move Move File.

        Move a File on Filesystem.

        :param old_filepath: Source Filepath
        :type old_filepath: str
        :param new_filepath: Destination Filepath
        :type new_filepath: str
        :param overwrite: Override existing Destination, defaults to False
        :type overwrite: bool, optional
        :return: Error Object
        :rtype: object
        """
        await self._ensure_session()

        url = f'{self.address}/rr_move'

        params = {
            'old': old_filepath,
            'new': new_filepath,
            'deleteexisting': 'yes' if overwrite is True else 'no',
        }

        response = {}
        async with self.session.get(url, params=params) as r:
            response = await r.json()

        return response

    @reauthenticate()
    async def rr_delete(self, filepath: str) -> object:
        """rr_delete delete remote file.

        Delete File on Duet.

        :param filepath: Filepath
        :type filepath: str
        :return: Error Object
        :rtype: object
        """
        await self._ensure_session()

        url = f'{self.address}/rr_delete'

        params = {
            'name': filepath,
        }

        response = {}
        async with self.session.get(url, params=params) as r:
            response = await r.json()

        return response

    # --- Unified interface methods delegating to rr_* ---

    async def send_gcode(self, command: str, no_reply: bool = True) -> str:
        """Send G-code via the unified interface."""
        await self.rr_gcode(gcode=command, no_reply=True)
        return '' if no_reply else await self.rr_reply()

    async def download(
        self,
        filepath: str,
        chunk_size: Optional[int] = 1024,
    ) -> AsyncIterable:
        """Download a file via the unified interface."""
        async for chunk in self.rr_download(filepath=filepath, chunk_size=chunk_size):
            yield chunk

    async def upload_stream(
        self,
        filepath: str,
        file: BinaryIO,
        progress: Optional[Callable] = None,
    ) -> None:
        """Upload a file via the unified interface.

        :raises IOError: If the upload fails
        """
        response = await self.rr_upload_stream(filepath=filepath, file=file, progress=progress)
        if response.get('err', 0) != 0:
            raise IOError(f"Upload failed: {response}")

    async def delete(self, filepath: str) -> None:
        """Delete a file via the unified interface."""
        await self.rr_delete(filepath=filepath)

    async def fileinfo(self, filepath: str, **kwargs) -> dict:
        """Get file info via the unified interface."""
        response = await self.rr_fileinfo(name=filepath, **kwargs)
        if response.get('err', 0) != 0:
            raise FileNotFoundError(f"File not found: {filepath}")
        return response

    async def filelist(self, directory: str) -> list:
        """List files via the unified interface."""
        return await self.rr_filelist(directory=directory)

    async def mkdir(self, directory: str) -> None:
        """Create directory via the unified interface."""
        await self.rr_mkdir(directory=directory)

    async def move(
        self,
        old_filepath: str,
        new_filepath: str,
        overwrite: bool = False,
    ) -> None:
        """Move file via the unified interface."""
        await self.rr_move(old_filepath, new_filepath, overwrite)
