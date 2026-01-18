"""Duet Printer model class."""

import asyncio
import csv
import io
import logging
from enum import auto

import aiohttp

from attr import define, field

from pyee.asyncio import AsyncIOEventEmitter

from strenum import CamelCaseStrEnum, StrEnum

from .api import RepRapFirmware


def merge_dictionary(source, destination):
    """Merge multiple dictionaries."""
    result = {}
    try:
        destination_dict = dict(destination)
    except TypeError:
        return None

    for key, value in source.items():
        if isinstance(value, dict):
            result[key] = merge_dictionary(value, destination.get(key, {}))
        elif isinstance(value, list):
            result[key] = value
            dest_value = destination.get(key, [])
            if len(dest_value) == 0:
                continue
            if len(value) > len(dest_value):
                raise ValueError(f"List length mismatch in merge for key: {key} src: {value} dest: {dest_value}")
            for idx, item in enumerate(value):
                if dest_value[idx] is not None and isinstance(item, dict):
                    result[key][idx] = merge_dictionary(item, dest_value[idx])
        else:
            result[key] = destination.get(key, value)
        destination_dict.pop(key, None)
    result.update(destination_dict)
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

    api = field(type=RepRapFirmware, factory=RepRapFirmware)
    om = field(type=dict, default=None)
    seqs = field(type=dict, factory=dict)
    logger = field(type=logging.Logger, factory=logging.getLogger)
    events = field(type=AsyncIOEventEmitter, factory=AsyncIOEventEmitter)
    sbc = field(type=bool, default=False)
    _reply = field(type=str, default=None)
    _wait_for_reply = field(type=asyncio.Event, factory=asyncio.Event)

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
        old_state = DuetState(old_om['state']['status'])
        if self.state != old_state:
            self.logger.debug(f"State change: {old_state} -> {self.state}")
            self.events.emit(DuetModelEvents.state, old_state)

    async def connect(self) -> None:
        """Connect the printer."""
        result = await self.api.connect()
        if 'isEmulated' in result:
            self.sbc = True
        result = await self._fetch_full_status()
        self.om = result['result']
        self.events.emit(DuetModelEvents.connect)

    async def close(self) -> None:
        """Close the printer."""
        await self.api.close()
        self.events.emit(DuetModelEvents.close)

    def connected(self) -> bool:
        """Check if the printer is connected."""
        if self.api.session is None or self.api.session.closed:
            return False
        return True

    async def gcode(self, command: str, no_reply: bool = True) -> str:
        """Send a GCode command to the printer."""
        self.logger.debug(f"Sending GCode: {command}")
        self._wait_for_reply.clear()
        await self.api.rr_gcode(
            gcode=command,
            no_reply=True,
        )
        if no_reply:
            return ''
        return await self.reply()

    async def heightmap(self) -> dict:
        """Get the heightmap from the printer."""
        compensation = self.om['move']['compensation']
        heightmap = io.BytesIO()

        async for chunk in self.api.rr_download(filepath=compensation['file']):
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
        The implementation is not recursive and fetches the object model in a single request
        starting from the second level of the object model (d=2).
        """
        if self.sbc and depth == 2:
            depth = 99

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

        if (depth == 1 or not self.sbc) and isinstance(response['result'], dict) and key != "global":
            for k, v in response['result'].items():
                sub_key = f"{key}.{k}" if key else k
                sub_depth = depth + 1 if isinstance(v, dict) else 99
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
                verbose=True,
            )
            self.om[key] = changed_obj['result']

    async def tick(self) -> None:
        """Tick the printer."""
        if not self.connected():
            await self.connect()

        if self.om is None:
            await self._initialize_object_model()
        else:
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
        result = await self.api.rr_model(
            key='',
            depth=99,
            frequently=True,
            include_null=True,
            verbose=True,
        )
        if result is None or 'result' not in result:
            return
        changes = self._detect_om_changes(result['result']['seqs'])
        old_om = dict(self.om)
        try:
            self.om = merge_dictionary(self.om, result['result'])
            if changes:
                await self._handle_om_changes(changes)
            self.events.emit(DuetModelEvents.objectmodel, old_om)
        except (TypeError, KeyError, ValueError):
            self.logger.exception("Failed to update object model - fetch full model")
            self.logger.debug(f"Old OM: {old_om} result {result['result']}")
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
        if self.sbc:
            await asyncio.sleep(5)
            return

        # there are no more than 10 clients connected to the duet board
        for _ in range(10):
            reply = await self.api.rr_reply(nocache=True)
            if reply == '':
                break
            self._reply = reply
        self._wait_for_reply.set()
