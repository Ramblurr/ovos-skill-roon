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
from typing import Any, Callable, Dict, List, Optional, Union

from roonapi import RoonApi

from .const import (
    LIBRARY_CACHE_UPDATE_MIN_INTERVAL_MINUTES,
    EnrichedBrowseItem,
    ItemType,
)
from .roon_api_browse import (
    roon_play_search_result,
    roon_search_generic,
    roon_search_type,
)
from .roon_cache import empty_roon_cache, roon_cache_update
from .roon_types import (
    EVENT_OUTPUT_CHANGED,
    EVENT_ZONE_CHANGED,
    EVENT_ZONE_SEEK_CHANGED,
    PlaybackControlOption,
    RepeatOption,
    RoonAuthSettings,
    RoonStateChange,
    RoonSubscriptionEvent,
    ServiceTransportResponse,
)
from .schema import PlayPath, PlaySearch, RoonCacheData, SearchTypeResult

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


class RoonCore:
    def __init__(self, api: RoonApi):
        self.auth: RoonAuthSettings
        self.connected = True
        self.roon: RoonApi = api
        self.cache: RoonCacheData = empty_roon_cache()
        self._state_callbacks: List[Callable] = []
        self.roon.register_state_callback(self.handle_state_change)

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

    def search_type(self, item_type: ItemType, query: str) -> SearchTypeResult:
        results: List[EnrichedBrowseItem] = roon_search_type(
            self.roon, self.cache, item_type, query
        )
        return SearchTypeResult(results=results)

    def search_generic(self, query: str, session_key: str) -> SearchTypeResult:
        results: List[EnrichedBrowseItem] = roon_search_generic(
            self.roon, self.cache, session_key, query
        )
        return SearchTypeResult(results=results)

    def register_state_callback(self, callback):
        self._state_callbacks.append(callback)

    def unregister_state_callback(self, callback):
        self._state_callbacks.remove(callback)

    def handle_state_change(
        self, event: RoonSubscriptionEvent, zone_or_output_ids: List[str]
    ):
        # log.debug("event %s in %s", event, zone_or_output_ids)
        should_update_entities = False
        updated_zones = []
        updated_outputs = []
        if event in [EVENT_ZONE_CHANGED, EVENT_ZONE_SEEK_CHANGED]:
            for zone_id in zone_or_output_ids:
                if zone_id not in self.roon.zones:
                    should_update_entities = True
                updated_zones.append(self.update_zone(zone_id))
        elif event in [EVENT_OUTPUT_CHANGED]:
            for output_id in zone_or_output_ids:
                if output_id not in self.roon.outputs:
                    should_update_entities = True
                updated_outputs.append(self.update_output(output_id))
        for cb in self._state_callbacks:
            cb(
                RoonStateChange(
                    event=event,
                    updated_zones=updated_zones,
                    updated_outputs=updated_outputs,
                    new_zones_found=should_update_entities,
                )
            )

    def update_zone(self, zone_id: str):
        """Update a zone."""
        self.cache.zones[zone_id] = self.roon.zones.get(zone_id)
        return self.cache.zones[zone_id]

    def update_output(self, output_id: str):
        """Update a output."""
        self.cache.outputs[output_id] = self.roon.outputs.get(output_id)
        return self.cache.outputs[output_id]

    def now_playing_for(self, zone_id: str) -> Dict[str, Any]:
        zone = self.roon.zones.get(zone_id)
        if zone is None:
            return {}
        np = zone.get("now_playing")
        if np:
            np["seek_position"] = zone.get("seek_position")
            return np
        return {}

    def get_image(self, image_key: str) -> Optional[str]:
        return self.roon.get_image(image_key)
