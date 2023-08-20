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
import datetime
import logging
from typing import Optional, Union, List

from roonapi import RoonApi

from .const import (
    LIBRARY_CACHE_UPDATE_MIN_INTERVAL_MINUTES,
    ItemType,
    EnrichedBrowseItem,
)
from .roon_api_browse import roon_play_search_result, roon_search_type
from .roon_cache import roon_cache_update, empty_roon_cache
from .roon_types import (
    PlaybackControlOption,
    RepeatOption,
    RoonAuthSettings,
    ServiceTransportResponse,
)
from .schema import PlayPath, PlaySearch, RoonCacheData, SearchAndPlay, SearchTypeResult

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


class RoonCore:
    def __init__(self, api: RoonApi):
        self.auth: RoonAuthSettings
        self.connected = True
        self.roon: RoonApi = api
        self.cache: RoonCacheData = empty_roon_cache()

    def disconnect(self) -> None:
        """Disconnect from Roon."""
        self.roon.stop()
        self.connected = False
        log.info("disconnected from roon")

    def update_cache(self) -> RoonCacheData:
        """Update the library cache."""
        if not self.should_update():
            return self.cache

        log.info("Updating library cache")
        self.cache = roon_cache_update(self.roon)
        return self.cache

    def get_cache(self) -> RoonCacheData:
        return self.cache

    def should_update(self) -> bool:
        """Check if the cache should be updated."""
        if self.cache.last_updated is None:
            return True
        now = datetime.datetime.now()
        delta = now - self.cache.last_updated
        return delta.total_seconds() > LIBRARY_CACHE_UPDATE_MIN_INTERVAL_MINUTES * 60

    def mute(self, output_id: str, mute: bool) -> ServiceTransportResponse:
        """Mute/unmute an output.

        Wrapper around roonapi.mute.
        """
        return self.roon.mute(output_id, mute)

    def change_volume_percent(self, output_id: str, relative_value: int):
        return self.roon.change_volume_percent(output_id, relative_value)

    def set_volume_percent(self, output_id: str, absolute_value: int):
        return self.roon.set_volume_percent(output_id, absolute_value)

    def shuffle(
        self, zone_or_output_id: str, shuffle: bool
    ) -> ServiceTransportResponse:
        """Enable or disable shuffle.

        Wrapper around roonapi.shuffle
        """
        return self.roon.shuffle(zone_or_output_id, shuffle)

    def repeat(
        self, zone_or_output_id: str, repeat: RepeatOption
    ) -> ServiceTransportResponse:
        """Enable or disable repeat.

        :param zone_or_output_id: The zone or output id
        :param repeat: The repeat option
        :return: The response from the Roon API
        """
        return self.roon.repeat(zone_or_output_id, repeat)

    def now_playing_for(self, zone_id: str):
        zone = self.roon.zones.get(zone_id)
        if zone is None:
            return None
        np = zone.get("now_playing")
        np["seek_position"] = zone["seek_position"]
        return np

    def get_image(self, image_key: str) -> Optional[str]:
        return self.roon.get_image(image_key)

    def playback_control(
        self, zone_or_output_id: str, control: PlaybackControlOption
    ) -> ServiceTransportResponse:
        """Send a playback control command.

        Wrapper around roonapi.playback_control"""
        return self.roon.playback_control(zone_or_output_id, control)

    def play(self, play: Union[PlayPath, PlaySearch]) -> None:
        if isinstance(play, PlayPath):
            self.roon.play_media(
                play.zone_or_output_id, play.path, play.action, play.report_error
            )
        elif isinstance(play, PlaySearch):
            roon_play_search_result(
                self.roon, play.zone_or_output_id, play.item_key, play.session_key
            )

    def play_type(self, cmd: SearchAndPlay) -> None:
        pass

    def search_type(self, item_type: ItemType, query: str) -> SearchTypeResult:
        results: List[EnrichedBrowseItem] = roon_search_type(
            self.roon, self.cache, item_type, query
        )
        return SearchTypeResult(results=results)
