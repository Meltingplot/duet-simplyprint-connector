#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Duet API Base Module.

Provides the abstract base class for Duet API backends and a unified
retry/reauthenticate decorator.
"""

import abc
import asyncio
import functools
import logging
from typing import AsyncIterable, BinaryIO, Callable, Optional

import aiohttp
import attr


def reauthenticate(retries: int = 3, auth_error_status: int = 401):
    """Reauthenticate API requests.

    Decorator that wraps async API methods with retry logic for handling
    connection errors and authentication failures. Uses linear backoff
    capped at RETRY_DELAY_MAX seconds.

    :param retries: Number of retries before giving up
    :param auth_error_status: HTTP status code indicating auth failure
                              (401 for RRF, 403 for DSF)
    """

    def decorator(f):

        @functools.wraps(f)
        async def inner(self, *args, **kwargs):
            remaining = retries
            while remaining > 0:
                try:
                    return await f(self, *args, **kwargs)
                except (
                    TimeoutError, asyncio.TimeoutError, aiohttp.ClientPayloadError, aiohttp.ClientConnectionError,
                ) as e:
                    self.logger.error(f"{e} - retry")
                    remaining -= 1
                    delay = min(2 * (retries - remaining + 1), self.RETRY_DELAY_MAX)
                    await asyncio.sleep(delay)
                except aiohttp.ClientResponseError as e:
                    remaining -= 1
                    if e.status in self.callbacks:
                        await self.callbacks[e.status](e)
                    elif e.status == auth_error_status:
                        self.logger.error(
                            f'Auth error ({e.status}) while requesting {e.request_info!s} - retry',
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
class DuetAPIBase(abc.ABC):
    """Abstract base class for Duet API backends."""

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

    @address.validator
    def _validate_address(self, attribute, value):
        if not value.startswith('http://') and not value.startswith('https://'):
            raise ValueError('Address must start with http:// or https://')

    async def connect(self) -> dict:
        """Connect to the Duet."""
        return await self.reconnect()

    @abc.abstractmethod
    async def reconnect(self) -> dict:
        """Reconnect to the Duet."""

    async def close(self) -> None:
        """Close the Client Session."""
        if self.session is not None and not self.session.closed:
            await self.session.close()
            self.session = None

    async def _ensure_session(self) -> None:
        """Ensure a valid session."""
        if self.session is None or self.session.closed:
            await self.reconnect()

    @abc.abstractmethod
    async def send_gcode(self, command: str, no_reply: bool = True) -> str:
        """Send a G-code command.

        :param command: G-code command string
        :param no_reply: If True, don't wait for a reply
        :return: Reply string (empty if no_reply=True)
        """

    @abc.abstractmethod
    async def download(
        self,
        filepath: str,
        chunk_size: Optional[int] = 1024,
    ) -> AsyncIterable:
        """Download a file from the printer.

        :param filepath: Path to file on printer
        :param chunk_size: Size of chunks to yield
        """

    @abc.abstractmethod
    async def upload_stream(
        self,
        filepath: str,
        file: BinaryIO,
        progress: Optional[Callable] = None,
    ) -> None:
        """Upload a file to the printer using streaming.

        :param filepath: Destination path on printer
        :param file: File-like object to upload
        :param progress: Optional progress callback (0-100)
        :raises IOError: If the upload fails
        """

    @abc.abstractmethod
    async def delete(self, filepath: str) -> None:
        """Delete a file on the printer.

        :param filepath: Path to file on printer
        """

    @abc.abstractmethod
    async def fileinfo(self, filepath: str, **kwargs) -> dict:
        """Get file information.

        :param filepath: Path to file on printer
        :return: File information dict
        """

    @abc.abstractmethod
    async def filelist(self, directory: str) -> list:
        """List files in a directory.

        :param directory: Directory path
        :return: List of files
        """

    @abc.abstractmethod
    async def mkdir(self, directory: str) -> None:
        """Create a directory.

        :param directory: Directory path to create
        """

    @abc.abstractmethod
    async def move(
        self,
        old_filepath: str,
        new_filepath: str,
        overwrite: bool = False,
    ) -> None:
        """Move/rename a file.

        :param old_filepath: Source path
        :param new_filepath: Destination path
        :param overwrite: Overwrite existing file
        """
