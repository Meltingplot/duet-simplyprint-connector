"""Webcam module for the Duet SimplyPrint connector."""

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Union

import aiohttp
import imageio.v3 as iio
from attr import define, field
from simplyprint_ws_client.core.client import Client
from simplyprint_ws_client.core.ws_protocol.messages import StreamMsg
from yarl import URL

from .task import async_task


@dataclass
class WebcamSnapshotRequest():
    """Webcam snapshot request."""

    snapshot_id: str = None
    endpoint: Union[str, URL, None] = None


@define
class Webcam:
    """Webcam class for the Duet SimplyPrint connector."""

    client = field(type=Client)
    uri = field(type=str)
    _timeout = field(type=int, default=0)
    _snapshot_requests = field(type=asyncio.Queue, factory=asyncio.Queue)
    _distribution_task_handle = field(type=asyncio.Task, default=None)
    _frames = field(type=asyncio.Queue, default=None)

    def __attrs_post_init__(self) -> None:
        """Post init."""
        if self._frames is None:
            self._frames = asyncio.Queue(maxsize=3)

    @property
    def event_loop(self) -> asyncio.AbstractEventLoop:
        """Get the event loop."""
        return self.client.event_loop

    @property
    def _background_task(self) -> set:
        """Get the background task set."""
        return self.client._background_task

    async def reset_timeout(self) -> None:
        """Reset the timeout timer."""
        self._timeout = time.time() + 10

    async def _ensure_distribution_task(self) -> None:
        """Ensure the webcam distribution task is running."""
        if self._distribution_task_handle is None:
            self._distribution_task_handle = await self._webcam_distribution_task()

    async def request_snapshot(self, snapshot_id: str = None, endpoint: Union[str, URL, None] = None) -> None:
        """Request a webcam snapshot."""
        if not self.uri:
            return

        await self.reset_timeout()
        await self._ensure_distribution_task()
        await self._snapshot_requests.put(WebcamSnapshotRequest(snapshot_id=snapshot_id, endpoint=endpoint))

    async def _send_snapshot(self, image: bytes) -> None:
        jpg_encoded = image
        base64_encoded = base64.b64encode(jpg_encoded).decode()
        # TODO: remove when fixed in simplyprint-ws-client
        while self.client.printer.intervals.use('webcam') is False:
            await self.client.printer.intervals.wait_for('webcam')

        await self.client.send(
            StreamMsg(base64jpg=base64_encoded),
            skip_dispatch=True,
        )

    async def _send_snapshot_to_endpoint(self, image: bytes, request: WebcamSnapshotRequest) -> None:
        import simplyprint_ws_client.shared.sp.simplyprint_api as sp_api

        self.client.logger.info(
            f'Sending webcam snapshot id: {request.snapshot_id} endpoint: {request.endpoint or "Simplyprint"}',
        )
        try:
            await sp_api.SimplyPrintApi.post_snapshot(
                snapshot_id=request.snapshot_id,
                image_data=image,
                endpoint=request.endpoint,
            )
        except Exception as e:
            self.client.logger.error(
                f'Failed to send webcam snapshot id: {request.snapshot_id}'
                f' endpoint: {request.endpoint or "Simplyprint"} - {e}',
            )

    async def _get_image(self) -> bytes:
        try:
            raw_data = await asyncio.wait_for(self._frames.get(), timeout=60)
        except asyncio.TimeoutError:
            self.client.logger.debug("Timeout while fetching webcam image")
            return None

        img = iio.imread(
            uri=raw_data,
            extension='.jpeg',
            index=None,
        )

        jpg_encoded = iio.imwrite("<bytes>", img, extension=".jpeg")
        # rotated_img = PIL.Image.open(io.BytesIO(jpg_encoded))
        # rotated_img.rotate(270)
        # rotated_img.thumbnail((720, 720), resample=PIL.Image.Resampling.LANCZOS)
        # bytes_array = io.BytesIO()
        # rotated_img.save(bytes_array, format='JPEG')
        # jpg_encoded = bytes_array.getvalue()

        return jpg_encoded

    async def _handle_multipart_content(self, response: aiohttp.ClientResponse) -> None:
        reader = aiohttp.MultipartReader.from_response(response)
        async for part in reader:
            if part.headers[aiohttp.hdrs.CONTENT_TYPE].lower() != 'image/jpeg':
                continue
            content = await part.read()
            if self._frames.full():
                await self._frames.get()
            await self._frames.put(memoryview(content))

            if self.client._is_stopped or self._distribution_task_handle is None:
                break
            # max framerate of SP is 2fps
            await asyncio.sleep(1 / 4)

    async def _handle_image_content(self, response: aiohttp.ClientResponse) -> None:
        content = await response.read()
        if self._frames.full():
            await self._frames.get()
        await self._frames.put(memoryview(content))

    @async_task
    async def _receive_task(self) -> None:
        self.client.logger.debug('Webcam receive task started')

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=0,  # Disable total timeout
                connect=30,
                sock_read=0,
                sock_connect=30,
            ),
        ) as session:
            while not self.client._is_stopped and self._distribution_task_handle is not None:
                await self._fetch_frame(session)

    async def _fetch_frame(self, session: aiohttp.ClientSession) -> None:
        try:
            async with session.get(self.uri) as response:
                content_type = response.headers['Content-Type'].lower()
                if content_type == 'image/jpeg':
                    await self._handle_image_content(response)
                elif 'multipart' in content_type:
                    await self._handle_multipart_content(response)
                else:
                    self.client.logger.debug('Unsupported content type: {!s}'.format(response.headers['Content-Type']))
        except (aiohttp.ClientError, asyncio.TimeoutError):
            self.client.logger.debug('Failed to fetch webcam image')
            await asyncio.sleep(10)

    @async_task
    async def _webcam_distribution_task(self) -> None:
        self.client.logger.debug('Webcam distribution task started')

        # Start the webcam receive task
        await self._receive_task()

        while not self.client._is_stopped and time.time() < self._timeout:
            try:
                image = await self._get_image()
                if image is not None and self._snapshot_requests.qsize() > 0:
                    request = await self._snapshot_requests.get()
                    if request.snapshot_id is not None:
                        await self._send_snapshot_to_endpoint(image=image, request=request)
                        continue
                    if self.client.printer.intervals.is_ready('webcam'):
                        await self._send_snapshot(image=image)
                    else:
                        await self._snapshot_requests.put(request)
                        await self.client.printer.intervals.wait_for('webcam')
                else:
                    await asyncio.sleep(0.1)
                # else drop the frame and grab the next one
            except Exception:
                self.client.logger.exception("Failed to distribute webcam image")
                await asyncio.sleep(10)
        if time.time() >= self._timeout:
            # if we reach the timeout, we send a final snapshot
            # this is a workaround for the fact that SP can get stuck if no snapshot is sent
            # TODO: remove this when fixed in SP backend or simplyprint-ws-client
            self.client.logger.debug('Sending final additional snapshot before stopping distribution task')
            image = await self._get_image()
            await self._send_snapshot(image=image)
        self._distribution_task_handle = None
