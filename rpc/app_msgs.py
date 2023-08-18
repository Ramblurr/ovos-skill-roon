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
from schema import Payload, register_message_type

# Every payload type needs a single definition. As currently written the clas
# name needs to match the corresponding `type` value, but that can be changed
# if needed.


@register_message_type
class EchoRequest(Payload):
    message: str


@register_message_type
class EchoResponse(Payload):
    echo: str


@register_message_type
class SumRequest(Payload):
    a: int
    b: int


@register_message_type
class SumResponse(Payload):
    result: int
