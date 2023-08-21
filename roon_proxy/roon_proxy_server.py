# roon-skill
# Copyright (C) 2023 Casey Link
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import asyncio
import logging
from functools import wraps
from typing import Optional, Union

from roonapi import RoonApi

from rpc.server import Server

from .const import (
    DiscoverStatus,
    PairingStatus,
)
from .roon_core import RoonCore
from .roon_discovery import Discovery, Pairing
from .schema import (
    MuteRequest,
    PlaybackControl,
    PlayPath,
    PlaySearch,
    Repeat,
    RoonAuthSettings,
    RoonCacheData,
    RoonDiscoverStatus,
    RoonManualPairSettings,
    RoonPairStatus,
    Shuffle,
    VolumeAbsoluteChange,
    VolumeRelativeChange,
    SearchType,
    SearchTypeResult,
)

log = logging.getLogger(__name__)
app = Server()

discovery: Optional[Discovery] = Discovery()
pairing: Pairing = Pairing()
roon: Optional[RoonCore] = None


def ensure_roon(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        if roon is None:
            raise Exception("Not connected to Roon")
        return await func(roon, *args, **kwargs)

    return wrapper


def handle_pair_result(
    api: Optional[RoonApi] = None, auth: Optional[RoonAuthSettings] = None
) -> None:
    global roon, discovery, pairing
    if api and auth:
        roon = RoonCore(api)
        discovery = None
        log.info(f"Paired successfully to roon core {auth.core_name} id={auth.core_id}")
    else:
        roon = None
        discovery = Discovery()
        pairing = Pairing()
        log.info(f"Failed to pair to a roon core")


def handle_discover_result(
    host: Optional[str] = None, port: Optional[int] = None
) -> None:
    global discovery
    if host and port:
        discovery = None
        log.info(f"Discovered roon core at {host} on port {port}")
    else:
        discovery = Discovery()
        log.info(f"Failed to pair to a roon core")


@app.register_rpc
async def connect_rpc_server() -> None:
    return


@app.register_rpc
async def discover() -> None:
    global discovery
    if not discovery:
        discovery = Discovery()
    await discovery.discover()


@app.register_rpc
async def discover_status() -> RoonDiscoverStatus:
    if discovery and discovery.has_started():
        if discovery.is_finished():
            host, port = await discovery.result()
            if host and port:
                handle_discover_result(host, port)
                return RoonDiscoverStatus(DiscoverStatus.DISCOVERED, host, port)
            else:
                handle_discover_result()
                return RoonDiscoverStatus(DiscoverStatus.FAILED)
        else:
            return RoonDiscoverStatus(DiscoverStatus.IN_PROGRESS)
    else:
        return RoonDiscoverStatus(DiscoverStatus.NOT_STARTED)


@app.register_rpc
async def pair(discover_settings: RoonManualPairSettings) -> None:
    pairing.pair(discover_settings)


@app.register_rpc
async def pair_status() -> RoonPairStatus:
    if roon:
        auth = pairing.auth_settings()
        return RoonPairStatus(PairingStatus.PAIRED, auth)
    if pairing and pairing.has_started():
        if pairing.is_waiting_for_approval():
            return RoonPairStatus(PairingStatus.WAITING_FOR_AUTHORIZATION)
        if pairing.is_approved():
            auth = pairing.auth_settings()
            handle_pair_result(pairing.api, auth)
            return RoonPairStatus(PairingStatus.PAIRED, auth)
        if pairing.is_failed():
            handle_pair_result()
            return RoonPairStatus(PairingStatus.FAILED)
        return RoonPairStatus(PairingStatus.IN_PROGRESS)

    else:
        return RoonPairStatus(PairingStatus.NOT_STARTED)


@app.register_rpc
@ensure_roon
async def disconnect_roon(roon: RoonCore) -> None:
    roon.disconnect()


@app.register_rpc
@ensure_roon
async def update_cache(roon: RoonCore) -> RoonCacheData:
    return roon.update_cache()


@app.register_rpc
@ensure_roon
async def get_cache(roon: RoonCore) -> RoonCacheData:
    return roon.get_cache()


@app.register_rpc
@ensure_roon
async def mute(roon: RoonCore, mute_req: MuteRequest) -> None:
    roon.mute(mute_req.output_id, mute_req.mute)


@app.register_rpc
@ensure_roon
async def change_volume_percent(roon: RoonCore, change: VolumeRelativeChange) -> None:
    roon.change_volume_percent(change.output_id, change.relative_value)


@app.register_rpc
@ensure_roon
async def set_volume_percent(roon: RoonCore, change: VolumeAbsoluteChange) -> None:
    roon.set_volume_percent(change.output_id, change.absolute_value)


@app.register_rpc
@ensure_roon
async def shuffle(roon: RoonCore, shuffle: Shuffle) -> None:
    roon.shuffle(shuffle.zone_or_output_id, shuffle.shuffle)


@app.register_rpc
@ensure_roon
async def repeat(roon: RoonCore, repeat: Repeat) -> None:
    roon.repeat(repeat.zone_or_output_id, repeat.repeat)


@app.register_rpc
@ensure_roon
async def playback_control(roon: RoonCore, cmd: PlaybackControl) -> None:
    roon.playback_control(cmd.zone_or_output_id, cmd.playback_control)


@app.register_rpc
@ensure_roon
async def play(roon: RoonCore, cmd: Union[PlayPath, PlaySearch]) -> None:
    roon.play(cmd)


@app.register_rpc
@ensure_roon
async def search_type(roon: RoonCore, cmd: SearchType) -> SearchTypeResult:
    r = roon.search_type(cmd.item_type, cmd.query)
    log.info("%s", r)
    return r


def main():
    import os

    sock_addr = os.environ["ROON_PROXY_SOCK"]
    log.info(f"Starting roon proxy server at {sock_addr}")
    asyncio.run(app.run(sock_addr))


if __name__ == "__main__":
    main()
