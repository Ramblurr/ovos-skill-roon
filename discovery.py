import asyncio
from roonapi import RoonApi, RoonDiscovery

from .const import AUTHENTICATE_TIMEOUT, ROON_APPINFO


DISCOVER_TIMEOUT = 120


async def sleep(s):
    await asyncio.sleep(s)


class RoonHub:
    def __init__(self, loop, log):
        # our async context
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


def authenticate(log, loop, host, port, servers):
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
