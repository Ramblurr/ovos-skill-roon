# roon-skill
# Copyright (C) 2022, 2023 Casey Link
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
"""Constants for the Roon skill"""
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union, Tuple
from enum import Enum, unique

from .roon_types import BrowseItemHint

AUTHENTICATE_TIMEOUT = 5

DEFAULT_NAME = "Roon Labs Music Player"

ROON_APPINFO = {
    "extension_id": "mycroft_roon",
    "display_name": "Roon Skill for Mycroft",
    "display_version": "0.0.1",
    "publisher": "Casey Link",
    "email": "ramblurr@users.noreply.github.com",
    "website": "https://github.com/ramblurr/mycroft-roon-skill",
}

ROON_KEYWORDS = ["roon", "ruin", "rune"]

TYPE_STATION = "station"
TYPE_ALBUM = "album"
TYPE_TRACK = "track"
TYPE_ARTIST = "artist"
TYPE_PLAYLIST = "playlist"
TYPE_GENRE = "genre"
TYPE_TAG = "tag"

CONF_DEFAULT_ZONE_ID = "default_zone_id"
CONF_DEFAULT_ZONE_NAME = "default_zone_name"

# Return value definition indication nothing was found
# (confidence None, data None)
NOTHING_FOUND = (None, 0.0)


LIBRARY_CACHE_UPDATE_MIN_INTERVAL_MINUTES = 2
DIRECT_RESPONSE_CONFIDENCE = 0.8
MATCH_CONFIDENCE = 0.5

PAGE_SIZE = 100

DEFAULT_VOLUME_STEP = 10


@unique
class ItemType(Enum):
    TRACK = TYPE_TRACK
    ALBUM = TYPE_ALBUM
    ARTIST = TYPE_ARTIST
    PLAYLIST = TYPE_PLAYLIST
    GENRE = TYPE_GENRE
    STATION = TYPE_STATION
    TAG = TYPE_TAG

    @classmethod
    @property
    def filterable(self):
        return self.STATION, self.GENRE

    @property
    def is_filterable(self):
        return self in self.filterable

    @classmethod
    @property
    def searchable(self):
        return self.TRACK, self.ALBUM, self.ARTIST, self.PLAYLIST, self.TAG

    @property
    def is_searchable(self):
        return self in self.searchable


class PairingStatus(Enum):
    IN_PROGRESS = "in-progress"
    PAIRED = "paired"
    FAILED = "failed"
    NOT_STARTED = "not-started"
    WAITING_FOR_AUTHORIZATION = "waiting-for-auth"


class DiscoverStatus(Enum):
    IN_PROGRESS = "in-progress"
    DISCOVERED = "discovered"
    FAILED = "failed"
    NOT_STARTED = "not-started"


class MycroftMetadata(TypedDict):
    path: Optional[List[str]]
    session_key: Optional[str]
    type: Optional[ItemType]


class EnrichedBrowseItem(TypedDict):
    title: str
    subtitle: Optional[str]
    image_key: Optional[str]
    item_key: Optional[str]
    hint: Optional[BrowseItemHint]
    mycroft: MycroftMetadata
    confidence: float


DataAndConfidence = Tuple[Optional[EnrichedBrowseItem], float]
