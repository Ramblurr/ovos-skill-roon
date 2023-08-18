#!/usr/bin/env python3
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
# In this version there's a different class per payload, but only a single
# top-level (generic) class for wrapping the payload.
import asyncio
from client import Client
from app_msgs import (
    EchoRequest,
    EchoResponse,
    SumRequest,
    SumResponse,
)
from schema import (
    encode,
    decode,
    Message,
    Payload,
    EmptyPayload,
    UnhandledApplicationError,
)


class TestClient:
    def __init__(self):
        self.ipc = Client("ipc://server.sock")
        self.ipc.set_error_handler(self.handle_error)

    def connect(self):
        self.ipc.connect()

    # async def dispatch(self, func_name, msg):
    # return await self.ipc.dispatch(msg)
    #
    async def handle_error(self, request: Message, error: Message) -> None:
        print("CUSTOM ERROR HANDLER")
        print(f"got error for {request}")
        print(f"error: {error.payload}")

    async def echo(self, m):
        return (await self.ipc.dispatch("handle_echo", EchoRequest(m))).echo

    async def add(self, a: int, b: int) -> int:
        return (
            await self.ipc.dispatch(
                "handle_sum", SumRequest(a, b)  # , error_handler=special_error_handler
            )
        ).result

    async def gogo(self) -> None:
        return await self.ipc.dispatch("handle_gogo")


async def main():
    client = TestClient()
    client.connect()
    r = await client.echo("ping")
    assert r == "ping"
    print(f"Received echo: {r}")
    r = await client.add(1, 5)
    assert r == 6
    print(f"Received sum: {r}")
    await client.gogo()
    print("Went")


if __name__ == "__main__":
    asyncio.run(main())
