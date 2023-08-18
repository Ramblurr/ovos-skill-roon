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
import logging
from typing import Callable, Dict

import zmq
import zmq.asyncio

from .schema import (
    EmptyPayload,
    Message,
    Payload,
    UnhandledApplicationError,
    decode,
    encode,
    is_deserialize_error,
    is_empty_payload,
)


class Server:
    def __init__(self):
        self._rpc_router: Dict[str, Callable] = {}

    def register_rpc(self, func: Callable) -> Callable:
        self._rpc_router[func.__name__] = func
        return func

    async def handle_message(self, func_name: str, payload: Payload):
        func = self._rpc_router[func_name]

        ret = None
        try:
            ret = await func() if is_empty_payload(payload) else await func(payload)
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception(exc)
            ret = UnhandledApplicationError(repr(exc))
        return ret

    async def handle_client(self, socket):
        while True:
            try:
                # Receive and decode a request
                request_data = await socket.recv()

                msg = decode(request_data)
                req_id = msg.msg_id
                func_name = msg.topic
                payload = msg.payload
                if is_deserialize_error(msg.payload):
                    resp = msg.payload
                    logging.exception("Deserialization error", resp)
                else:
                    logging.debug(f"{func_name}")
                    resp = await self.handle_message(func_name, payload)
                await socket.send(
                    encode(
                        Message(
                            func_name,
                            req_id,
                            resp if resp is not None else EmptyPayload(),
                        )
                    )
                )
            except EOFError:
                print("Connection closed")
                return

    async def run(self, address: str):
        ctx = zmq.asyncio.Context.instance()
        socket = ctx.socket(zmq.REP)
        socket.bind(address)

        await self.handle_client(socket)
