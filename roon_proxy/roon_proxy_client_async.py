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
from typing import Union

from rpc.client import Client

from .const import PairingStatus
from .roon_types import PlaybackControlOption, RepeatOption
from .schema import (
    MuteRequest,
    PlaybackControl,
    PlayPath,
    PlaySearch,
    Repeat,
    RoonCacheData,
    RoonDiscoverStatus,
    RoonManualDiscoverSettings,
    RoonPairStatus,
    Shuffle,
    VolumeAbsoluteChange,
    VolumeRelativeChange,
)


class RoonProxyClient:
    def __init__(self, address: str):
        self.ipc = Client(address)

    def connect(self) -> None:
        self.ipc.connect()

    def disconnect(self) -> None:
        self.ipc.disconnect()

    async def discover(self) -> None:
        await self.ipc.dispatch("discover")

    async def discover_status(self) -> RoonDiscoverStatus:
        return await self.ipc.dispatch("discover_status")

    async def pair(self, discover_settings: RoonManualDiscoverSettings) -> None:
        await self.ipc.dispatch("pair", discover_settings)

    async def pair_status(self) -> RoonPairStatus:
        return await self.ipc.dispatch("pair_status")

    async def disconnect_roon(self) -> None:
        """Ask the proxy server to disconect from roon"""
        await self.ipc.dispatch("disconnect_roon")

    async def update_cache(self) -> RoonCacheData:
        """Update the library cache."""
        return await self.ipc.dispatch("update_cache", timeout=10000)

    async def get_cache(self) -> RoonCacheData:
        """Fetch the current library cache without triggering an update"""
        return await self.ipc.dispatch("get_cache")

    async def mute(self, output_id: str, mute: bool) -> None:
        await self.ipc.dispatch("mute", MuteRequest(output_id, mute))

    async def change_volume_percent(self, output_id: str, relative_value: int) -> None:
        await self.ipc.dispatch(
            "change_volume_percent", VolumeRelativeChange(output_id, relative_value)
        )

    async def set_volume_percent(self, output_id: str, absolute_value: int) -> None:
        await self.ipc.dispatch(
            "set_volume_percent", VolumeAbsoluteChange(output_id, absolute_value)
        )

    async def shuffle(self, zone_or_output_id: str, shuffle: bool) -> None:
        await self.ipc.dispatch("shuffle", Shuffle(zone_or_output_id, shuffle))

    async def repeat(self, zone_or_output_id: str, repeat: RepeatOption) -> None:
        await self.ipc.dispatch("repeat", Repeat(zone_or_output_id, repeat))

    async def playback_control(
        self, zone_or_output_id: str, control: PlaybackControlOption
    ) -> None:
        await self.ipc.dispatch(
            "playback_control", PlaybackControl(zone_or_output_id, control)
        )

    async def play(self, play: Union[PlayPath, PlaySearch]) -> None:
        await self.ipc.dispatch("play", play)


async def main():
    import os

    auth = RoonManualDiscoverSettings(
        host=os.environ["ROON_HOST"],
        port=int(os.environ["ROON_PORT"]),
        token=os.environ["ROON_TOKEN"],
        core_id=os.environ["ROON_CORE_ID"],
        core_name=os.environ["ROON_CORE_NAME"],
    )
    assert auth.host and auth.port and auth.token
    client = RoonProxyClient("ipc://server.sock")
    client.connect()
    await client.pair(
        # RoonManualDiscoverSettings(host="roon.int.socozy.casa", port=9330)
        auth
    )
    print("pairing requested")
    while True:
        status = await client.pair_status()
        await asyncio.sleep(1)
        if status.status == PairingStatus.PAIRED:
            break

    print(await client.pair_status())

    # data = await client.get_cache()
    # print()
    # print("=====CACHE=====")
    # print(data)
    # data = await client.update_cache()
    # print()
    # print("=====CACHE=====")
    # print(data)
    await client.mute(output_id="1701f94071089b345ba9b605dfef71d76721", mute=False)
    # await client.connect_roon(
    #    RoonAuthSettings(
    #        **{
    #            "roon_server_id": "d6f83c6a-e3a3-4efd-b0f8-4f2996aa87f5",
    #            "roon_server_name": "Roon Optimized Core Kit",
    #            "host": "roon.int.socozy.casa",
    #            "port": 9330,
    #            "token": "32041f6e-ce93-4cab-9369-4adff8248019",
    #        }
    #    )
    # )
    # await asyncio.sleep(3)
    # await client.disconnect_roon()
    # data = await client.get_cache()
    # data = await client.update_cache()
    # data = await client.get_cache()


if __name__ == "__main__":
    asyncio.run(main())
