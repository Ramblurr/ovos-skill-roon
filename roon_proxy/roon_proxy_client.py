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
from typing import Union

from rpc.client_sync import Client

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
    RoonManualPairSettings,
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

    def discover(self) -> None:
        self.ipc.dispatch("discover")

    def discover_status(self) -> RoonDiscoverStatus:
        return self.ipc.dispatch("discover_status")

    def pair(self, pair_settings: RoonManualPairSettings) -> None:
        self.ipc.dispatch("pair", pair_settings)

    def pair_status(self) -> RoonPairStatus:
        return self.ipc.dispatch("pair_status")

    def disconnect_roon(self) -> None:
        """Ask the proxy server to disconect from roon"""
        self.ipc.dispatch("disconnect_roon")

    def update_cache(self) -> RoonCacheData:
        """Update the library cache."""
        return self.ipc.dispatch("update_cache", timeout=10000)

    def get_cache(self) -> RoonCacheData:
        """Fetch the current library cache without triggering an update"""
        return self.ipc.dispatch("get_cache")

    def mute(self, output_id: str, mute: bool) -> None:
        self.ipc.dispatch("mute", MuteRequest(output_id, mute))

    def change_volume_percent(self, output_id: str, relative_value: int) -> None:
        self.ipc.dispatch(
            "change_volume_percent", VolumeRelativeChange(output_id, relative_value)
        )

    def set_volume_percent(self, output_id: str, absolute_value: int) -> None:
        self.ipc.dispatch(
            "set_volume_percent", VolumeAbsoluteChange(output_id, absolute_value)
        )

    def shuffle(self, zone_or_output_id: str, shuffle: bool) -> None:
        self.ipc.dispatch("shuffle", Shuffle(zone_or_output_id, shuffle))

    def repeat(self, zone_or_output_id: str, repeat: RepeatOption) -> None:
        self.ipc.dispatch("repeat", Repeat(zone_or_output_id, repeat))

    def playback_control(
        self, zone_or_output_id: str, control: PlaybackControlOption
    ) -> None:
        self.ipc.dispatch(
            "playback_control", PlaybackControl(zone_or_output_id, control)
        )

    def play(self, play: Union[PlayPath, PlaySearch]) -> None:
        self.ipc.dispatch("play", play)


def main():
    import os, time

    auth = RoonManualPairSettings(
        host=os.environ["ROON_HOST"],
        port=int(os.environ["ROON_PORT"]),
        token=os.environ["ROON_TOKEN"],
        core_id=os.environ["ROON_CORE_ID"],
        core_name=os.environ["ROON_CORE_NAME"],
    )
    assert auth.host and auth.port and auth.token
    client = RoonProxyClient("ipc://server.sock")
    client.connect()
    client.pair(
        # RoonManualDiscoverSettings(host="roon.int.socozy.casa", port=9330)
        auth
    )
    print("pairing requested")
    while True:
        status = client.pair_status()
        time.sleep(1)
        if status.status == PairingStatus.PAIRED:
            break

    print(client.pair_status())

    # data = await client.get_cache()
    # print()
    # print("=====CACHE=====")
    # print(data)
    # data = await client.update_cache()
    # print()
    # print("=====CACHE=====")
    # print(data)
    client.mute(output_id="1701f94071089b345ba9b605dfef71d76721", mute=False)
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
    main()
