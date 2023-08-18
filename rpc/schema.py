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
from typing import Generic, Literal, Type, TypedDict, TypeVar

import msgspec


class Payload(msgspec.Struct):
    pass


# Every payload type needs a single definition. As currently written the clas
# name needs to match the corresponding `type` value, but that can be changed
# if needed.


class EmptyPayload(Payload):
    pass


class UnhandledApplicationError(Payload):
    """Represents an error that occured (probably on the server side) that was not handled."""

    _exception: str


class DeserializationError(Payload):
    """Represents an error thar occured when deserializing a payload"""

    _exception: str
    message: str
    payload_type: str
    msg_id: str
    topic: str


T = TypeVar("T", bound=Payload)


class Message(Generic[T]):
    """A generic Message wrapper used for all payload types"""

    topic: str
    msg_id: str
    payload: T

    def __init__(self, topic: str, msg_id: str, payload: T) -> None:
        self.topic = topic
        self.msg_id = msg_id
        self.payload = payload

    def __eq__(self, other):
        return (
            type(other) is Message
            and self.topic == other.topic
            and self.msg_id == other.msg_id
            and self.payload == other.payload
        )

    def __repr__(self):
        return f"Message(topic={self.topic!r}, msg_id={self.msg_id}, payload={self.payload!r})"


# A lookup table of all possible Payload types. This uses `__subclasses__` to
# automatically find all `Payload` subclasses. If this is too magical, you
# could explicitly write out this mapping instead.
_payload_class_lookup = {cls.__name__: cls for cls in Payload.__subclasses__()}


def register_message_type(cls: Type[T]) -> Type[T]:
    """Decorator to register the message payload types"""
    _payload_class_lookup[cls.__name__] = cls
    return cls


def is_empty_payload(payload: Payload) -> bool:
    return payload.__class__.__name__ == EmptyPayload.__name__


def is_deserialize_error(payload: Payload) -> bool:
    return payload.__class__.__name__ == DeserializationError.__name__


class _MessageSchema(TypedDict):
    """A schema used for validating Message objects in `dec_hook` below"""

    type: Literal[tuple(_payload_class_lookup)]  # type: ignore
    topic: str
    payload: str


# `enc_hook` and `dec_hook` implementations for handling encoding/decoding of
# the `Message` type.
def enc_hook(x):
    if isinstance(x, Message):
        try:
            return {
                "type": type(x.payload).__name__,
                "topic": x.topic,
                "msg_id": x.msg_id,
                "payload": msgspec.msgpack.encode(x.payload),
            }
        except TypeError as e:
            print("TYPE ERROR ENCOUNTERED")
            print(x)
    raise TypeError(f"{type(x).__name__} is not supported")


def dec_hook(type, data):
    if type is Message:
        # Use `from_builtins` to validate `data` matches the expected
        # MessageSchema. This isn't strictly necessary, but does make
        # it easier to raise a nicer error on an invalid `Message`
        # print(f"dec_hook: _payload_class_lookup {data['type']} {payload_cls}")
        # msg = msgspec.from_builtins(data, _MessageSchema)
        # payload_cls = _payload_class_lookup[msg["type"]]
        try:
            payload_cls = _payload_class_lookup[data["type"]]
            payload = msgspec.msgpack.decode(data["payload"], type=payload_cls)
            return Message(data["topic"], data["msg_id"], payload)
        except KeyError as e:
            return Message(
                data["topic"],
                data["msg_id"],
                DeserializationError(
                    _exception=repr(e),
                    message=f"Unknown payload type: {data['type']}",
                    payload_type=data["type"],
                    topic=data["topic"],
                    msg_id=data["msg_id"],
                ),
            )
        except msgspec.ValidationError as e:
            return Message(
                data["topic"],
                data["msg_id"],
                DeserializationError(
                    _exception=repr(e),
                    message=f"Payload type failed to deserialize {data['type']}",
                    payload_type=data["type"],
                    topic=data["topic"],
                    msg_id=data["msg_id"],
                ),
            )
    raise TypeError(f"{type} is not supported")


_encoder = msgspec.msgpack.Encoder(enc_hook=enc_hook)
_decoder = msgspec.msgpack.Decoder(Message, dec_hook=dec_hook)


# Functions for MSGPACK encoding & decoding a Message
def encode(x: Message) -> bytes:
    return _encoder.encode(x)


def decode(msg: bytes) -> Message:
    return _decoder.decode(msg)
