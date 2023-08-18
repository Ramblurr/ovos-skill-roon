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
from random import randint
import sys
from server import Server
from schema import (
    Message,
    UnhandledApplicationError,
    Payload,
    EmptyPayload,
    is_empty_payload,
    encode,
    decode,
)
from app_msgs import (
    EchoRequest,
    EchoResponse,
    SumRequest,
    SumResponse,
)
from typing import Callable, Dict, Optional

app = Server()


@app.register_rpc
async def handle_echo(request):
    if randint(0, 3) == 0:
        print("Simulating a crash")
        sys.exit(1)
    return EchoResponse(echo=request.message)


@app.register_rpc
async def handle_sum(request):
    return SumResponse(result=request.a + request.b)


@app.register_rpc
async def handle_gogo():
    print("GO GO GO!")


if __name__ == "__main__":
    asyncio.run(app.run())
    # asyncio.run(main())
