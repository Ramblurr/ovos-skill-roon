# roon-skill
# Copyright (C) 2022 Casey Link
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
"""Module for discovering and authenticating with a Roon Core"""
import asyncio
from logging import Logger
from roonapi import RoonApi, RoonDiscovery

from .const import AUTHENTICATE_TIMEOUT, ROON_APPINFO
from .roon_types import RoonAuthSettings


DISCOVER_TIMEOUT = 120


async def sleep(seconds: int):
    await asyncio.sleep(seconds)


class RoonHub:
    loop: asyncio.AbstractEventLoop
    log: Logger

    def __init__(self, loop: asyncio.AbstractEventLoop, log: Logger):
        self.loop = loop
        self.log = log

    async def discover(self):
        """Try and discover the roon core"""

        def get_discovered_cores(discovery):
            cores = discovery.all()
            discovery.stop()
            return cores

        discovery = RoonDiscovery(None)
        servers = await self.loop.run_in_executor(None, get_discovered_cores, discovery)
        self.log.debug("Discovered %s cores", servers)
        return servers

    async def authenticate(self, host, port, servers):
        """Authenticate with the roon core"""

        def stop_apis(apis):
            for api in apis:
                api.stop()

        token = None
        core_id = None
        core_name = None
        secs = 0

        if host is None:
            apis = [
                RoonApi(ROON_APPINFO, None, server[0], server[1], blocking_init=False)
                for server in servers
            ]
        else:
            apis = [RoonApi(ROON_APPINFO, None, host, port, blocking_init=False)]

        while secs <= DISCOVER_TIMEOUT:
            # Roon can discover multiple devices - not all of which are proper servers, so try and authenticate with them all.
            # The user will only enable one - so look for a valid token
            auth_api = [api for api in apis if api.token is not None]

            secs += AUTHENTICATE_TIMEOUT
            if auth_api:
                core_id = auth_api[0].core_id
                core_name = auth_api[0].core_name
                token = auth_api[0].token
                break

            await sleep(AUTHENTICATE_TIMEOUT)

        await self.loop.run_in_executor(None, stop_apis, apis)
        return (token, core_id, core_name)


def discover(log, loop):
    """Discover roon cores"""
    roon = RoonHub(loop, log)
    task = loop.create_task(roon.discover())
    loop.run_until_complete(asyncio.wait([task]))
    loop.close()
    return task.result()


def authenticate(log, loop, host, port, servers) -> RoonAuthSettings:
    """Connect and authenticate mycroft to the roon core"""
    roon = RoonHub(loop, log)
    task = loop.create_task(roon.authenticate(host, port, servers))
    loop.run_until_complete(asyncio.wait([task]))
    loop.close()
    (token, core_id, core_name) = task.result()
    if token is None:
        raise InvalidAuth

    return {
        "roon_server_id": core_id,
        "roon_server_name": core_name,
        "host": host,
        "port": port,
        "token": token,
    }


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
