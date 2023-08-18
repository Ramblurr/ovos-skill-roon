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
from roonapi import RoonApi, RoonDiscovery

from typing import Optional, Tuple
from .schema import RoonManualPairSettings, RoonAuthSettings

from .const import ROON_APPINFO

log = logging.getLogger(__name__)

HostPort = Tuple[str, int]
MaybeHostPort = Tuple[Optional[str], Optional[int]]

MaybeApiAndAuth = Tuple[Optional[RoonApi], Optional[RoonAuthSettings]]


def get_roon_host(core_id: Optional[str] = None) -> MaybeHostPort:
    """Try and discover the roon core"""

    discovery = RoonDiscovery(core_id)
    host, port = discovery.first()
    discovery.stop()
    return host, port


def get_api(token: Optional[str], host: str, port: int) -> MaybeApiAndAuth:
    api = RoonApi(ROON_APPINFO, token, host, port, blocking_init=False)
    if api:
        return api, RoonAuthSettings(
            **{
                "core_id": api.core_id,
                "core_name": api.core_name,
                "host": api.host,
                "port": port,
                "token": api.token,
            }
        )
    return None, None


class DiscoveryFailedException(Exception):
    pass


async def discover() -> HostPort:
    def _get_host():
        (host, port) = get_roon_host()
        if host and port:
            return host, port
        raise DiscoveryFailedException()

    return await asyncio.to_thread(_get_host)


async def discover_manual(
    discover_settings: RoonManualPairSettings,
) -> MaybeApiAndAuth:
    def _get_api():
        # TODO handle when a token expires
        return get_api(
            discover_settings.token,
            discover_settings.host,
            discover_settings.port,
        )

    return await asyncio.to_thread(_get_api)


class Discovery:
    def __init__(self):
        self.discover_task: Optional[asyncio.Task] = None

    async def discover(self) -> None:
        if self.discover_task:
            return
        log.info("Starting discovery of roon cores")
        self.discover_task = asyncio.create_task(discover())

    def has_started(self) -> bool:
        return self.discover_task is not None

    def is_finished(self) -> bool:
        if self.discover_task:
            return self.discover_task.done()
        return False

    async def result(self) -> MaybeHostPort:
        if not self.discover_task:
            raise Exception("Discovery has not started")
        try:
            host, port = await self.discover_task
            self.discoverd_host = host
            self.discoverd_port = port
            return host, port
        except DiscoveryFailedException:
            return None, None


class Pairing:
    def __init__(self):
        self.api = None
        self.discover_settings: Optional[RoonManualPairSettings] = None

    def pair(self, discover_settings: RoonManualPairSettings):
        if self.api:
            return
        log.info(
            f"Starting pairing to roon core {discover_settings.host} {discover_settings.port}"
        )

        self.discover_settings = discover_settings
        self.api = RoonApi(
            ROON_APPINFO,
            discover_settings.token,
            discover_settings.host,
            discover_settings.port,
            blocking_init=False,
        )

    def has_started(self) -> bool:
        return self.api is not None

    def is_waiting_for_approval(self) -> bool:
        return self.api is not None and not self.api.ready and not self.is_failed()

    def is_approved(self) -> bool:
        return (
            self.api is not None
            and self.api.ready is not None
            and self.api.token is not None
            and not self.is_failed()
        )

    def is_failed(self) -> bool:
        return (
            self.api is not None
            and self.api._roonsocket is not None
            and self.api._roonsocket.failed_state
        )

    def auth_settings(self) -> RoonAuthSettings:
        return RoonAuthSettings(
            **{
                "core_id": self.api.core_id,  # type: ignore
                "core_name": self.api.core_name,  # type: ignore
                "host": self.api.host,  # type: ignore
                "port": self.discover_settings.port,  # type: ignore
                "token": self.api.token,  # type: ignore
            }
        )
