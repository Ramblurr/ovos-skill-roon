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
from datetime import datetime
from typing import Any, Dict, List, Optional

from rpc.schema import Payload, register_message_type

from .const import DiscoverStatus, EnrichedBrowseItem, ItemType, PairingStatus
from .roon_types import PlaybackControlOption, RepeatOption


@register_message_type
class RoonAuthSettings(Payload):
    host: str
    port: int
    token: str
    core_id: str
    core_name: str


@register_message_type
class RoonCacheData(Payload):
    last_updated: Optional[datetime]
    radio_stations: List[Any]
    genres: List[Any]
    playlists: List[Any]
    zones: Dict[str, Any]
    outputs: Dict[str, Any]


@register_message_type
class RoonManualPairSettings(Payload):
    host: str
    port: int
    token: Optional[str] = None
    core_id: Optional[str] = None
    core_name: Optional[str] = None


@register_message_type
class RoonDiscoverStatus(Payload):
    status: DiscoverStatus
    host: Optional[str] = None
    port: Optional[int] = None


@register_message_type
class RoonPairStatus(Payload):
    status: PairingStatus
    auth: Optional[RoonAuthSettings] = None


@register_message_type
class MuteRequest(Payload):
    output_id: str
    mute: bool


@register_message_type
class VolumeRelativeChange(Payload):
    output_id: str
    relative_value: int


@register_message_type
class VolumeAbsoluteChange(Payload):
    output_id: str
    absolute_value: int


@register_message_type
class Shuffle(Payload):
    zone_or_output_id: str
    shuffle: bool


@register_message_type
class Repeat(Payload):
    zone_or_output_id: str
    repeat: RepeatOption


@register_message_type
class PlaybackControl(Payload):
    zone_or_output_id: str
    playback_control: PlaybackControlOption


@register_message_type
class PlaySearch(Payload):
    zone_or_output_id: str
    item_key: Optional[str]
    session_key: str


@register_message_type
class PlayPath(Payload):
    zone_or_output_id: str
    path: List[str]
    report_error: bool = True
    action: Optional[str] = None


@register_message_type
class SearchType(Payload):
    item_type: ItemType
    query: str


@register_message_type
class SearchGeneric(Payload):
    query: str
    session_key: str


@register_message_type
class SearchTypeResult(Payload):
    results: List[EnrichedBrowseItem]


@register_message_type
class SubscribeCommand(Payload):
    address: str


@register_message_type
class NowPlayingCommand(Payload):
    zone_id: str


@register_message_type
class NowPlayingReply(Payload):
    np: Dict[str, Any]


@register_message_type
class GetImageCommand(Payload):
    image_key: str


@register_message_type
class GetImageReply(Payload):
    url: Optional[str]
