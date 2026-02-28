"""Duet Printer model class."""

import asyncio
import csv
import io
import logging
import os
import time
from copy import deepcopy
from enum import auto
from typing import AsyncIterable, BinaryIO, Callable, Optional

import aiohttp
from attr import define, field
from pyee.asyncio import AsyncIOEventEmitter
from strenum import CamelCaseStrEnum, StrEnum

from .api import RepRapFirmware
from .base import DuetAPIBase
from .dsf import DuetSoftwareFramework
from .dsf_socket import DEFAULT_SOCKET_PATH, DuetControlSocket


def merge_dictionary(source, destination):
    """
    Deep-merge.

    - dicts: recursively merged
    - lists: merged by index, supports different lengths
    - scalars: destination wins if key exists, else source
    """
    if not isinstance(destination, dict):
        # Wenn destination kein dict ist, können wir es nicht sinnvoll mergen
        return deepcopy(source)

    result = deepcopy(destination)

    for key, s_val in (source or {}).items():
        if key not in destination:
            result[key] = deepcopy(s_val)
            continue

        d_val = destination.get(key)

        # dict + dict => rekursiv mergen
        if isinstance(s_val, dict) and isinstance(d_val, dict):
            result[key] = merge_dictionary(s_val, d_val)
            continue

        # list + list => index-basiert mergen (unterschiedliche Länge ok)
        if isinstance(s_val, list) and isinstance(d_val, list):
            merged_list = []

            max_len = max(len(s_val), len(d_val))
            for i in range(max_len):
                s_item = s_val[i] if i < len(s_val) else None
                d_item = d_val[i] if i < len(d_val) else None

                # Wenn eins fehlt, nimm das andere
                if s_item is None:
                    merged_list.append(deepcopy(d_item))
                    continue
                if d_item is None:
                    merged_list.append(deepcopy(s_item))
                    continue

                # dict-items rekursiv mergen
                if isinstance(s_item, dict) and isinstance(d_item, dict):
                    merged_list.append(merge_dictionary(s_item, d_item))
                else:
                    # "destination wins" für nicht-dict items
                    merged_list.append(deepcopy(d_item))

            result[key] = merged_list
            continue

        # Typkonflikt oder scalar => destination wins
        result[key] = deepcopy(d_val)

    return result


class DuetModelEvents(StrEnum):
    """Duet Model Events enum."""

    state = auto()
    objectmodel = auto()
    connect = auto()
    close = auto()


class DuetState(CamelCaseStrEnum):
    """Duet State enum."""

    disconnected = auto()
    starting = auto()
    updating = auto()
    off = auto()
    halted = auto()
    pausing = auto()
    paused = auto()
    resuming = auto()
    cancelling = auto()
    processing = auto()
    simulating = auto()
    busy = auto()
    changing_tool = auto()
    idle = auto()


@define
class DuetPrinter():
    """Duet Printer model class."""

    # Backoff schedule in seconds: 1 min, 5 min, 30 min (capped)
    WS_RETRY_DELAYS = [60, 300, 1800]

    api: DuetAPIBase = field(factory=RepRapFirmware)
    om = field(type=dict, default=None)
    seqs = field(type=dict, factory=dict)
    logger = field(type=logging.Logger, factory=logging.getLogger)
    events = field(type=AsyncIOEventEmitter, factory=AsyncIOEventEmitter)
    sbc = field(type=bool, default=False)
    socket_path = field(type=str, default=DEFAULT_SOCKET_PATH)
    _reply = field(type=str, default=None)
    _wait_for_reply = field(type=asyncio.Event, factory=asyncio.Event)
    _ws_task = field(type=Optional[asyncio.Task], default=None)
    _ws_enabled = field(type=bool, default=True)
    _ws_retry_at: Optional[float] = field(default=None)
    _ws_retry_count: int = field(default=0)
    _rrf_poll_interval: float = field(default=3.0)
    _last_poll_at: float = field(default=0.0)

    def __attrs_post_init__(self) -> None:
        """Post init."""
        self.api.callbacks[503] = self._http_503_callback
        self.events.on(DuetModelEvents.objectmodel, self._track_state)

    @property
    def state(self) -> DuetState:
        """Get the state of the printer."""
        try:
            return DuetState(self.om['state']['status'])
        except (KeyError, TypeError):
            return DuetState.disconnected

    async def _track_state(self, old_om: dict):
        """Track the state of the printer."""
        if old_om is None:
            return
        try:
            old_state = DuetState(old_om['state']['status'])
        except (KeyError, TypeError, ValueError):
            return
        if self.state != old_state:
            self.logger.debug(f"State change: {old_state} -> {self.state}")
            self.events.emit(DuetModelEvents.state, old_state)

    async def connect(self) -> None:
        """Connect the printer.

        Connection priority:
        1. DCS Unix socket (direct, when running on same SBC)
        2. DSF HTTP API (SBC mode via network, detected by isEmulated)
        3. RepRapFirmware HTTP API (standalone mode)
        """
        if await self._try_socket_connection():
            self.logger.info("Connected via DCS Unix socket")
        else:
            result = await self.api.connect()
            if 'isEmulated' in result:
                self.sbc = True
                # Switch to DSF HTTP API, reusing connection parameters
                dsf_api = DuetSoftwareFramework(
                    address=self.api.address,
                    password=self.api.password,
                    session=self.api.session,
                    logger=self.api.logger,
                )
                self.api.session = None  # Prevent session close
                self.api = dsf_api
                await self.api.connect()
                self.api.callbacks[503] = self._http_503_callback
        result = await self._fetch_full_status()
        self.om = result['result'] if 'result' in result else result
        self.events.emit(DuetModelEvents.connect)

    async def _try_socket_connection(self) -> bool:
        """Attempt to connect via the DCS Unix socket.

        :return: True if socket connection succeeded
        """
        if not os.path.exists(self.socket_path):
            return False

        self.logger.info(f"DCS socket found at {self.socket_path}, attempting direct connection")
        try:
            socket_api = DuetControlSocket(
                address='socket://' + self.socket_path,
                socket_path=self.socket_path,
                logger=self.api.logger,
            )
            await socket_api.connect()
            await self.api.close()
            self.api = socket_api
            self.sbc = True
            return True
        except OSError as e:
            self.logger.warning(f"DCS socket connection failed: {e}, falling back to HTTP")
            return False

    async def close(self) -> None:
        """Close the printer."""
        await self._stop_websocket_subscription()
        self._ws_retry_at = None
        self._ws_retry_count = 0
        await self.api.close()
        self.events.emit(DuetModelEvents.close)

    def connected(self) -> bool:
        """Check if the printer is connected."""
        if isinstance(self.api, DuetControlSocket):
            return self.api._cmd_writer is not None
        if self.api.session is None or self.api.session.closed:
            return False
        return True

    async def gcode(self, command: str, no_reply: bool = True) -> str:
        """Send a GCode command to the printer."""
        self.logger.debug(f"Sending GCode: {command}")
        self._wait_for_reply.clear()
        result = await self.api.send_gcode(command, no_reply)
        if self.sbc:
            if not no_reply:
                self._reply = result
                self._wait_for_reply.set()
            return result
        if no_reply:
            return ''
        return await self.reply()

    async def heightmap(self) -> dict:
        """Get the heightmap from the printer."""
        compensation = self.om['move']['compensation']
        heightmap = io.BytesIO()

        async for chunk in self.api.download(filepath=compensation['file']):
            heightmap.write(chunk)

        heightmap.seek(0)
        heightmap = heightmap.read().decode('utf-8')

        self.logger.debug('Mesh data: {!s}'.format(heightmap))

        mesh_data_csv = csv.reader(heightmap.splitlines()[3:], dialect='unix')

        mesh_data = []
        z_min, z_max = float('inf'), float('-inf')

        for row in mesh_data_csv:
            x_line = [float(x.strip()) for x in row]
            z_min = min(z_min, *x_line)
            z_max = max(z_max, *x_line)
            mesh_data.append(x_line)

        return {
            'type': 'rectangular' if compensation['liveGrid']['radius'] == -1 else 'circular',
            'x_min': compensation['liveGrid']['mins'][0],
            'x_max': compensation['liveGrid']['maxs'][0],
            'y_min': compensation['liveGrid']['mins'][1],
            'y_max': compensation['liveGrid']['maxs'][1],
            'z_min': z_min,
            'z_max': z_max,
            'mesh_data': mesh_data,
        }

    async def reply(self) -> str:
        """Get the last reply from the printer."""
        await self._wait_for_reply.wait()
        return self._reply

    async def download(
        self,
        filepath: str,
        chunk_size: int = 1024,
    ) -> AsyncIterable:
        """Download a file from the printer."""
        async for chunk in self.api.download(filepath=filepath, chunk_size=chunk_size):
            yield chunk

    async def upload_stream(
        self,
        filepath: str,
        file: BinaryIO,
        progress: Optional[Callable] = None,
    ) -> None:
        """Upload a file to the printer.

        :raises IOError: If the upload fails
        """
        await self.api.upload_stream(filepath=filepath, file=file, progress=progress)

    async def delete(self, filepath: str) -> None:
        """Delete a file on the printer."""
        await self.api.delete(filepath=filepath)

    async def fileinfo(self, filepath: str, **kwargs) -> dict:
        """Get file information from the printer."""
        return await self.api.fileinfo(filepath=filepath, **kwargs)

    async def _fetch_objectmodel_recursive(
        self,
        *args,
        key='',
        depth=1,
        frequently=False,
        include_null=True,
        verbose=True,
        array=None,
        **kwargs,
    ) -> dict:
        """
        Fetch the object model recursively.

        Duet2:
        The implementation is recursive to fetch the object model in chunks.
        This is required because the object model is too large to fetch in a single request.
        The implementation might be slow because of the recursive nature of the function, but
        this helps to reduce the load on the duet board.

        Duet3 or SBC mode (isEmulated):
        For DSF API, fetches the full model in a single request.
        """
        if self.sbc:
            # DSF: Fetch full model, extract key if needed
            result = await self.api.model()
            if key and key != "global":
                for part in key.split('.'):
                    if part:
                        result = result.get(part, {})
            return {'result': result, 'next': 0}

        response = await self.api.rr_model(
            *args,
            key=key,
            depth=depth,
            frequently=frequently,
            include_null=include_null,
            verbose=verbose,
            array=array,
            **kwargs,
        )

        if isinstance(response['result'], dict) and not (depth == 1 and key == "global"):
            for k, v in response['result'].items():
                if not isinstance(v, (list, dict)):
                    continue
                sub_key = f"{key}.{k}" if key else k
                sub_depth = depth + 1 if isinstance(v, dict) and depth < 2 else 99
                sub_response = await self._fetch_objectmodel_recursive(
                    *args,
                    key=sub_key,
                    depth=sub_depth,
                    frequently=frequently,
                    include_null=include_null,
                    verbose=verbose,
                    **kwargs,
                )
                response['result'][k] = sub_response['result']
        elif 'next' in response and response['next'] > 0:
            next_data = await self._fetch_objectmodel_recursive(
                *args,
                key=key,
                depth=depth,
                frequently=frequently,
                include_null=include_null,
                verbose=verbose,
                array=response['next'],
                **kwargs,
            )
            response['result'].extend(next_data['result'])
            response['next'] = 0

        return response

    async def _fetch_full_status(self) -> dict:
        try:
            response = await self._fetch_objectmodel_recursive(
                key='',
                depth=1,
                frequently=False,
                include_null=True,
                verbose=True,
            )
        except KeyError:
            response = {}

        return response

    async def _handle_om_changes(self, changes: dict) -> None:
        """Handle object model changes."""
        if 'reply' in changes:
            if not self.sbc:
                self._reply = await self.api.rr_reply()
            self._wait_for_reply.set()
            self.logger.debug(f"Reply: {self._reply}")
            changes.pop('reply')

        if 'volChanges' in changes:
            # TODO: handle volume changes
            changes.pop('volChanges')

        for key in changes:
            changed_obj = await self._fetch_objectmodel_recursive(
                key=key,
                depth=2,
                frequently=False,
                include_null=True,
                verbose=False,
            )
            self.om[key] = changed_obj['result']

    async def tick(self) -> None:
        """Tick the printer."""
        if not self.connected():
            await self.connect()

        if self.om is None:
            await self._initialize_object_model()
            # Start WebSocket after initial model fetch
            if self.sbc and self._ws_enabled:
                await self._start_websocket_subscription()
        elif self.sbc and self._ws_enabled:
            # In SBC mode with WebSocket, check if task is running
            if self._ws_task is None or self._ws_task.done():
                # WebSocket not running - check if we should retry or poll
                if self._should_retry_websocket():
                    self.logger.info("Attempting WebSocket reconnection...")
                    await self._start_websocket_subscription()
                else:
                    # Continue polling until retry time
                    await self._update_object_model()
            # else: WebSocket is handling updates, nothing to do
        else:
            now = time.monotonic()
            if now - self._last_poll_at >= self._rrf_poll_interval:
                self._last_poll_at = now
                await self._update_object_model()

    async def _initialize_object_model(self) -> None:
        """Initialize the object model by fetching the full status."""
        result = await self._fetch_full_status()
        if result is None or 'result' not in result:
            return
        self.om = result['result']
        self.events.emit(DuetModelEvents.objectmodel, None)

    async def _update_object_model(self) -> None:
        """Update the object model by fetching partial updates."""
        if self.sbc:
            result = await self.api.model()
            if result is None:
                return
            changes = self._detect_om_changes(result.get('seqs', {}))
        else:
            response = await self.api.rr_model(
                key='',
                depth=99,
                frequently=True,
                include_null=True,
                verbose=False,
            )
            if response is None or 'result' not in response:
                return
            result = response['result']
            changes = self._detect_om_changes(result['seqs'])

        old_om = deepcopy(self.om)
        try:
            self.om = merge_dictionary(self.om, result)
            if changes:
                await self._handle_om_changes(changes)
            self.events.emit(DuetModelEvents.objectmodel, old_om)
        except (TypeError, KeyError, ValueError):
            self.logger.exception("Failed to update object model - fetch full model")
            self.logger.debug(f"Old OM: {old_om} result {result}")
            self.om = None
            # TODO: send to sentry

    def _detect_om_changes(self, new_seqs) -> dict:
        """Detect changes between the current and new sequences."""
        changes = {}
        for key, value in new_seqs.items():
            if key not in self.seqs or self.seqs[key] != value:
                changes[key] = value
        self.seqs = new_seqs
        return changes

    async def _http_503_callback(self, error: aiohttp.ClientResponseError):
        """503 callback."""
        await asyncio.sleep(5)

    def _schedule_ws_retry(self) -> None:
        """Schedule WebSocket reconnection with exponential backoff."""
        delay_index = min(self._ws_retry_count, len(self.WS_RETRY_DELAYS) - 1)
        delay = self.WS_RETRY_DELAYS[delay_index]
        self._ws_retry_at = time.monotonic() + delay
        self._ws_retry_count += 1
        self.logger.info(f"WebSocket reconnect scheduled in {delay}s (attempt {self._ws_retry_count})")

    def _should_retry_websocket(self) -> bool:
        """Check if it's time to retry WebSocket connection."""
        if self._ws_retry_at is None:
            return True  # First attempt or explicit reset
        return time.monotonic() >= self._ws_retry_at

    async def _start_websocket_subscription(self) -> None:
        """Start WebSocket subscription for object model updates."""
        if not self.sbc or not self._ws_enabled:
            return

        if self._ws_task is not None and not self._ws_task.done():
            return  # Already running

        self._ws_task = asyncio.create_task(self._websocket_loop())

    async def _websocket_loop(self) -> None:
        """Process WebSocket messages to receive and handle object model updates."""
        first_message = True
        cancelled = False
        try:
            async for data in self.api.subscribe():
                if first_message:
                    # Reset retry counter on successful connection
                    self._ws_retry_count = 0
                    self._ws_retry_at = None
                    # First message is full object model
                    self.om = data
                    self.seqs = data.get('seqs', {})
                    self.events.emit(DuetModelEvents.objectmodel, None)
                    first_message = False
                else:
                    # Subsequent messages are patches
                    old_om = deepcopy(self.om)
                    try:
                        self.om = merge_dictionary(self.om, data)
                        self.events.emit(DuetModelEvents.objectmodel, old_om)
                    except (TypeError, KeyError, ValueError):
                        self.logger.exception("Failed to merge WebSocket update")
                        # Request full model on next iteration
                        first_message = True

        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as e:
            self.logger.error(f"WebSocket loop error: {e}")
        finally:
            self._ws_task = None
            # Schedule retry with backoff if not cancelled
            if not cancelled:
                self._schedule_ws_retry()

    async def _stop_websocket_subscription(self) -> None:
        """Stop WebSocket subscription."""
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        if self.sbc and hasattr(self.api, 'unsubscribe'):
            await self.api.unsubscribe()
