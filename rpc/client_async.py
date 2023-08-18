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
from typing import Any, Dict, Optional, TypeVar

import zmq
import zmq.asyncio

from .util import current_time_us, unique_id, ErrorHandlerFn
from .error import TimeoutException
from .schema import (
    EmptyPayload,
    Message,
    Payload,
    T,
    UnhandledApplicationError,
    decode,
    encode,
)


T = TypeVar("T", bound=Payload)


class Client:
    def __init__(
        self, address: str, default_timeout: int = 2000, default_retries: int = 3
    ):
        self.address: str = address
        self.ctx = zmq.asyncio.Context.instance()
        self.socket: Optional[zmq.asyncio.Socket] = self.ctx.socket(zmq.REQ)
        self._responses: Dict[str, Any] = {}
        self.default_timeout: int = default_timeout
        self.error_handler: Optional[ErrorHandlerFn] = None
        self.default_retries: int = default_retries

    def connect(self) -> None:
        assert self.socket
        self._init()

    def disconnect(self) -> None:
        assert self.socket
        self.socket.disconnect(self.address)

    def set_error_handler(self, error_handler: ErrorHandlerFn) -> None:
        self.error_handler = error_handler

    def close(self) -> None:
        if self.socket is not None:
            self.socket.setsockopt(zmq.LINGER, 0)
            self.socket.close()
            self.socket = None
            self._responses = {}

    def _init(self) -> None:
        self._responses: Dict[str, Any] = {}
        self.socket = self.ctx.socket(zmq.REQ)
        self.socket.connect(self.address)

    async def dispatch(
        self,
        func_name,
        payload: Optional[Payload] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
        error_handler: Optional[ErrorHandlerFn] = None,
    ) -> T:
        await self._ensure_connected()
        _timeout = self.default_timeout if timeout is None else timeout
        _retries = self.default_retries if retries is None else retries
        expire_at = current_time_us() + (_timeout * 1000)

        async def _poll_data(data: Any) -> None:
            """Implements lazy-pirate poll (see zmq guide chapter 4)"""
            assert self.socket
            await self.socket.send(data)
            retries_left = _retries
            while retries_left > 0:
                if await self.socket.poll(_timeout):
                    break
                retries_left -= 1
                logging.info(
                    f"response from server timed out retries_left={retries_left}"
                )
                self.close()
                await self._ensure_connected()
                await self.socket.send(data)

            if retries_left == 0:
                self.close()
                logging.info(
                    f"response from server timed out. retries exhausted. giving up."
                )
                raise TimeoutException(f"Timeout while sending message")
            else:
                data = await self.socket.recv()
                rsp = decode(data)
                self._responses[rsp.msg_id] = rsp

        req_id = unique_id()
        msg = Message(
            func_name, req_id, payload if payload is not None else EmptyPayload()
        )
        await _poll_data(encode(msg))

        while req_id not in self._responses and current_time_us() <= expire_at:
            await asyncio.sleep(1e-6)

        # better late than never?
        # if current_time_us() > expire_at:
        #    raise TimeoutException(
        #        f"Timeout while waiting for response at {self._address}"
        #    )

        rsp = self._responses.pop(req_id)
        if (
            hasattr(rsp.payload, "__class__")
            and rsp.payload.__class__.__name__ == UnhandledApplicationError.__name__
        ):
            await self.handle_error(error_handler, msg, rsp)
            return rsp.payload
        return rsp.payload

    async def _ensure_connected(self) -> None:
        if self.socket is None:
            self._init()

    async def handle_error(
        self,
        endpoint_error_handler: Optional[ErrorHandlerFn],
        request: Message,
        error: Message,
    ) -> None:
        async def default_error_handler(request: Message, error: Message) -> None:
            print(f"got error for {request}")
            print(f"error: {error.payload}")

        error_handlers = [
            endpoint_error_handler,
            self.error_handler,
            default_error_handler,
        ]

        handler = next(filter(lambda x: x is not None, error_handlers), None)
        assert handler
        await handler(request, error)
