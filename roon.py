"""Search helpers."""

from logging import Logger
import datetime
import re
from typing import Any, Dict, List, Optional, Tuple, TypedDict, Union, cast

from dataclasses import asdict

from roonapi import RoonApi
from roonapi.constants import SERVICE_TRANSPORT

from .roon_types import (
    RoonApiBrowseOptions,
    RoonApiBrowseResponse,
    RoonApiBrowseLoadOptions,
    RoonApiBrowseLoadResponse,
    RoonApiErrorResponse,
    RoonAuthSettings,
    BrowseList,
    BrowseItem,
    BrowseItemHint,
    HierarchyTypes,
    ChangeVolumeMethod,
    RepeatOption,
)
from .const import (
    DIRECT_RESPONSE_CONFIDENCE,
    LIBRARY_CACHE_UPDATE_MIN_INTERVAL_MINUTES,
    NOTHING_FOUND,
    ROON_APPINFO,
    TYPE_ALBUM,
    TYPE_ARTIST,
    TYPE_GENRE,
    TYPE_PLAYLIST,
    TYPE_STATION,
    TYPE_TAG,
    FilterableItemTypes,
    ItemTypes,
    SearchableItemTypes,
)
from .util import best_match, match_one, match_one_item

EXCLUDE_ITEMS = {
    "No Results",
    "Play Album",
    "Play Artist",
    "Play Playlist",
    "Play Composer",
    "Play Now",
    "Play From Here",
    "Queue",
    "Start Radio",
    "Add Next",
    "Play Radio",
    "Play Work",
    "Settings",
    "Search",
    "Search Tidal",
    "Search Qobuz",
}

# source https://roonlabs.github.io/node-roon-api/RoonApiBrowse.html


class MycroftMetadata(TypedDict):
    path: Optional[List[str]]
    session_key: Optional[str]
    type: Optional[ItemTypes]


class EnrichedBrowseItem(TypedDict):
    title: str
    subtitle: Optional[str]
    image_key: Optional[str]
    item_key: Optional[str]
    hint: Optional[BrowseItemHint]
    mycroft: MycroftMetadata


ServiceTransportResponse = Optional[Union[str, Dict[str, Any]]]
DataAndConfidence = Tuple[Optional[EnrichedBrowseItem], float]


class RoonApiBrowse:
    def __init__(self, roonapi: RoonApi):
        self.roonapi = roonapi

    def browse(
        self, options: RoonApiBrowseOptions
    ) -> Union[RoonApiErrorResponse, RoonApiBrowseResponse]:
        resp = cast(
            Optional[Dict[str, Any]], self.roonapi.browse_browse(asdict(options))
        )
        if resp is None:
            return RoonApiErrorResponse(is_error=True, message="No response from Roon")
        if resp and resp.get("is_error"):
            return RoonApiErrorResponse(**resp)

        browse_list = BrowseList(**resp["list"])
        resp = RoonApiBrowseResponse(
            action=cast(str, resp.get("action")),
            list=browse_list,
            item=resp.get("item"),
        )
        if resp.list is None:
            resp.list = BrowseList(title="No Response from Roon", count=0, level=0)
        return resp

    def load(
        self, options: RoonApiBrowseLoadOptions
    ) -> Union[RoonApiErrorResponse, RoonApiBrowseLoadResponse]:
        resp = cast(Optional[Dict[str, Any]], self.roonapi.browse_load(asdict(options)))
        if resp is None:
            return RoonApiErrorResponse(is_error=True, message="No response from Roon")
        if resp and resp.get("is_error"):
            return RoonApiErrorResponse(**resp)
        browse_items = []
        for item in resp["items"]:
            browse_items.append(BrowseItem(**item))

        browse_list = BrowseList(**resp["list"])
        return RoonApiBrowseLoadResponse(
            items=browse_items, offset=resp["offset"], list=browse_list
        )


class RoonCore:
    """Wrapper for the Roon API."""

    roon: RoonApi
    log: Logger
    radio_stations: List[BrowseItem]
    genres: List[BrowseItem]
    playlists: List[BrowseItem]

    def __init__(self, log: Logger, auth: RoonAuthSettings):
        """init."""
        self.last_updated = None
        self.log = log
        self.radio_stations = []
        self.genres = []
        self.playlists = []
        self.zones = {}
        self.outputs = {}
        self.roon = RoonApi(
            ROON_APPINFO,
            auth["token"],
            auth["host"],
            auth["port"],
            blocking_init=True,
        )
        self.browse = RoonApiBrowse(self.roon)

    def list_(self, hierarchy: HierarchyTypes) -> List[BrowseItem]:
        """List all items in a hierarchy."""

        opts = RoonApiBrowseOptions(hierarchy=hierarchy, pop_all=True)

        resp = self.browse.browse(opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("Roon browse api returned error: %s", resp)
            return []
        self.log.debug(f"Browse response: {resp}")
        if resp.list.count == 0:
            return []
        load_opts = RoonApiBrowseLoadOptions(hierarchy=opts.hierarchy, count=100)
        data = self.browse.load(load_opts)
        if isinstance(data, RoonApiErrorResponse):
            self.log.info("Roon browse load api returned error: %s", data)
            return []
        if not data.items:
            return []
        return data.items

    def list_genres(self) -> List[BrowseItem]:
        """List all genres."""
        return self.list_("genres")

    def list_radio_stations(self) -> List[BrowseItem]:
        """List all radio stations."""
        return self.list_("internet_radio")

    def list_playlists(self) -> List[BrowseItem]:
        """List all playlists."""
        return self.list_("playlists")

    def update_cache(self) -> None:
        """Update the library cache."""
        if not self.should_update():
            return

        self.log.info("Updating library cache")

        self.zones = self.roon.zones
        self.outputs = self.roon.outputs
        self.radio_stations = self.list_radio_stations()
        self.genres = self.list_genres()
        self.playlists = self.list_playlists()
        self.last_updated = datetime.datetime.now()

    def should_update(self) -> bool:
        """Check if the cache should be updated."""
        if self.last_updated is None:
            return True
        now = datetime.datetime.now()
        delta = now - self.last_updated
        return delta.total_seconds() > LIBRARY_CACHE_UPDATE_MIN_INTERVAL_MINUTES * 60

    def enrich(
        self, item: BrowseItem, item_type: str, path: List[str]
    ) -> EnrichedBrowseItem:
        """Enrich a Roon item with additional metadata."""
        return cast(
            EnrichedBrowseItem,
            asdict(item)
            | {
                "mycroft": {
                    "type": item_type,
                    "path": path,
                }
            },
        )

    def search_type(self, phrase: str, item_type: ItemTypes) -> DataAndConfidence:
        if item_type == TYPE_GENRE:
            return self.search_genres(phrase)
        if item_type == TYPE_STATION:
            return self.search_stations(phrase)

        return self._navigate_type_search(phrase, item_type)

    def _navigate_type_search(
        self, phrase: str, item_type: SearchableItemTypes
    ) -> DataAndConfidence:
        # pylint: disable=too-many-return-statements
        """Search for a phrase in a specific type."""
        mapping = {
            TYPE_ALBUM: "Albums",
            TYPE_ARTIST: "Artists",
            TYPE_PLAYLIST: "Playlists",
            TYPE_TAG: "Tags",
        }
        mapping_path = {
            TYPE_ALBUM: ["Library", "Albums"],
            TYPE_ARTIST: ["Library", "Artists"],
            TYPE_PLAYLIST: ["Playlists"],
            TYPE_TAG: ["Library", "Tags"],
        }

        if not item_type in mapping:
            raise Exception(f"Unhandled item type {item_type} for search")

        opts = RoonApiBrowseOptions(
            hierarchy="search",
            pop_all=True,
            input=phrase,
            multi_session_key="navigate_search",
        )
        self.log.info("searching %s for %s", item_type, phrase)
        resp = self.browse.browse(opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("room browse api returned error %s", resp)
            return NOTHING_FOUND
        if resp.list.count == 0:
            self.log.info("room browse api returned null search results")
            return NOTHING_FOUND
        load_opts = RoonApiBrowseLoadOptions(
            count=10, hierarchy="search", multi_session_key="navigate_search"
        )

        resp = self.browse.load(load_opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("room load api returned error %s", resp)
            return NOTHING_FOUND
        category_key = None
        for item in resp.items:
            if item.title == mapping[item_type]:
                category_key = item.item_key
                break
        if not category_key:
            return NOTHING_FOUND
        opts = RoonApiBrowseOptions(
            hierarchy="search",
            multi_session_key="navigate_search",
            item_key=category_key,
        )

        resp = self.browse.browse(opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("room browse api returned error %s", resp)
            return NOTHING_FOUND
        if resp.list.count == 0:
            self.log.info("room browse api returned null search results")
            return NOTHING_FOUND

        load_opts = RoonApiBrowseLoadOptions(
            count=10, hierarchy="search", multi_session_key="navigate_search"
        )
        resp = self.browse.load(load_opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("room load api returned error %s", resp)
            return NOTHING_FOUND

        data, confidence = match_one_item(phrase, resp.items)
        if not data:
            return NOTHING_FOUND
        path = mapping_path[item_type].copy()
        path.append(data.title)
        return (
            cast(
                EnrichedBrowseItem,
                asdict(data) | {"mycroft": {"session_key": "navigate_search"}},
            ),
            confidence,
        )

    def search(self, session_key, phrase: str) -> DataAndConfidence:
        # pylint: disable=too-many-return-statements
        """Perform a generic search, returning the top result."""
        opts = RoonApiBrowseOptions(
            hierarchy="search",
            input=phrase,
            pop_all=True,
            multi_session_key=session_key,
        )
        self.log.info("searching generic for %s", phrase)
        resp = self.browse.browse(opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("room browse api returned error %s", resp)
            return NOTHING_FOUND
        if resp.list.count == 0:
            self.log.info("room browse api returned null search results")
            return NOTHING_FOUND
        load_opts = RoonApiBrowseLoadOptions(
            count=10, hierarchy="search", multi_session_key=session_key
        )
        resp = self.browse.load(load_opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("room load api returned error %s", resp)
            return NOTHING_FOUND
        if len(resp.items) == 0:
            self.log.info("room load api returned 0 search results")
            return NOTHING_FOUND
        first_item = resp.items[0]
        opts.pop_all = False
        opts.item_key = first_item.item_key
        resp = self.browse.browse(opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("room browse api returned error %s", resp)
            return NOTHING_FOUND
        if resp.list.count == 0:
            self.log.info("room browse api returned null search results")
            return NOTHING_FOUND
        if resp.action == "none":
            return NOTHING_FOUND
        data = cast(
            EnrichedBrowseItem,
            asdict(first_item) | {"mycroft": {"session_key": session_key}},
        )
        return data, DIRECT_RESPONSE_CONFIDENCE

    def play_path(self, zone_or_output_id, path, action=None, report_error=True):
        """
        Play the media specified.

        params:
            zone_or_output_id: where to play the media
            path: a list allowing roon to find the media
                  eg ["Library", "Artists", "Neil Young", "Harvest"] or ["My Live Radio", "BBC Radio 4"]
            action: the roon action to take to play the media - leave blank to choose the roon default
                    eg "Play Now", "Queue" or "Start Radio"
        """
        return self.roon.play_media(zone_or_output_id, path, action, report_error)

    def play_search_result(
        self, zone_or_output_id, item_key: str, session_key: str
    ) -> Union[RoonApiBrowseResponse, RoonApiErrorResponse]:
        # pylint disable=too-many-statements
        """Play the top result of a previous search."""
        self.log.info("playing item from session key %s", session_key)
        opts = RoonApiBrowseOptions(
            hierarchy="search",
            item_key=item_key,
            multi_session_key=session_key,
        )

        action_list_key = None

        levels = set()
        while action_list_key is None:
            resp = self.browse.browse(opts)
            if isinstance(resp, RoonApiErrorResponse):
                self.log.info("room browse api returned error %s", resp)
                break
            current_level = resp.list.level
            if current_level in levels:
                self.log.info("Aborting. Current level already visited")
                break
            levels.add(current_level)
            self.log.info("Browsing level %d", current_level)
            self.log.debug(resp)
            if resp.action != "list" or resp.list.count == 0:
                self.log.info("room api returned 0 search results")
                break
            load_opts = RoonApiBrowseLoadOptions(
                count=10, hierarchy="search", multi_session_key=session_key
            )
            resp = self.browse.load(load_opts)
            if isinstance(resp, RoonApiErrorResponse):
                self.log.info("room browse api returned error %s", resp)
                break
            if resp.list.count == 0:
                self.log.info("room browse api returned null search results")
                break
            self.log.debug(resp)
            for item in resp.items:
                if item.hint == "action_list":
                    action_list_key = item.item_key
                    break
            # couldn't find the action list, let's go a level deeper
            opts.item_key = resp.items[0].item_key

        if action_list_key is None:
            self.log.info("Could not find action list key!")
            return RoonApiErrorResponse(is_error=True, message="Nothing found")

        opts.item_key = action_list_key
        resp = self.browse.browse(opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("room browse api returned error %s", resp)
            return RoonApiErrorResponse(is_error=True, message="Nothing found")
        if resp.action != "list" or resp.list.count == 0:
            self.log.info("room api returned 0 search results")
            return RoonApiErrorResponse(is_error=True, message="Nothing found")

        load_opts = RoonApiBrowseLoadOptions(
            count=10, hierarchy="search", multi_session_key=session_key
        )
        resp = self.browse.load(load_opts)
        if isinstance(resp, RoonApiErrorResponse):
            self.log.info("room api returned null search results")
            return RoonApiErrorResponse(is_error=True, message="Nothing found")
        action_key = None
        for item in resp.items:
            self.log.debug(item)
            if item.hint == "action":
                action_key = item.item_key
                break
        if action_key is None:
            self.log.info("Could not find action key!")
            return RoonApiErrorResponse(is_error=True, message="Nothing found")

        opts.item_key = action_key
        opts.zone_or_output_id = zone_or_output_id
        resp = self.browse.browse(opts)
        self.log.info("Play result: %s", resp)
        return resp

    def match_and_enrich(
        self,
        phrase: str,
        item_type: FilterableItemTypes,
        path: List[str],
        items: List[BrowseItem],
    ) -> DataAndConfidence:
        """Match and enrich an item."""
        data, confidence = match_one_item(phrase, items)
        if data is None:
            return NOTHING_FOUND
        path = path.copy()
        path.append(data.title)
        if data:
            return self.enrich(data, item_type, path), confidence
        return data, confidence

    def filter_hierarchy_cache(
        self, phrase: str, item_type: ItemTypes
    ) -> DataAndConfidence:
        """Filter the cached hierarchy items for a match."""
        if item_type == TYPE_STATION:
            items = self.radio_stations
            path = ["My Live Radio"]
        elif item_type == TYPE_GENRE:
            items = self.genres
            path = ["Genres"]
        else:
            raise Exception("Unhandled item type for hierarchy cache filter")

        if len(items) == 0:
            return NOTHING_FOUND
        data, confidence = self.match_and_enrich(phrase, item_type, path, items)
        if data:
            return data, confidence

        self.log.info("Not found in %s cache, updating", item_type)
        if item_type == TYPE_STATION:
            items = self.list_radio_stations()
            path = ["My Live Radio"]
        elif item_type == TYPE_GENRE:
            items = self.list_genres()
            path = ["Genres"]
        return self.match_and_enrich(phrase, item_type, path, items)

    def search_genres(self, phrase: str) -> DataAndConfidence:
        """Search for genres."""
        return self.filter_hierarchy_cache(phrase, TYPE_GENRE)

    def search_stations(self, phrase: str) -> DataAndConfidence:
        """Search for radio stations."""
        opt1 = self.filter_hierarchy_cache(phrase, TYPE_STATION)
        pat = r".*(fm \d+).*"
        match = re.match(pat, phrase, re.IGNORECASE)
        if match:
            no_whitespace = match.group(1).replace(" ", "")
            phrase = re.sub(pat, no_whitespace, phrase, flags=re.IGNORECASE)
            opt2 = self.filter_hierarchy_cache(phrase, TYPE_STATION)
            return best_match(opt1, opt2)
        return opt1

    def match_user_playlist(self, phrase: str) -> DataAndConfidence:
        """Match a user playlist."""
        return self.match_and_enrich(
            phrase, TYPE_PLAYLIST, ["Playlists"], self.playlists
        )

    def playback_control(self, zone_id: str, control: str) -> ServiceTransportResponse:
        """Send a playback control command.

        Wrapper around roonapi.playback_control"""
        return self.roon.playback_control(zone_id, control)

    def mute(self, output_id: str, mute: bool) -> ServiceTransportResponse:
        """Mute/unmute an output.

        Wrapper around roonapi.mute.
        """
        return self.roon.mute(output_id, mute)

    def change_volume(
        self, output_id: str, step_or_value: int, method: ChangeVolumeMethod
    ) -> ServiceTransportResponse:
        """Change the volume of an output.

        Wrapper around roonapi.change_volume.
        """
        return self.roon.change_volume(output_id, step_or_value, method=method)

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
        data = {"zone_or_output_id": zone_or_output_id, "loop": repeat}
        # pylint: disable=protected-access
        return self.roon._request(SERVICE_TRANSPORT + "/change_settings", data)

    def disconnect(self) -> None:
        """Disconnect from Roon."""
        self.roon.stop()

    def update_zone(self, zone_id: str) -> None:
        """Update a zone."""
        self.zones[zone_id] = self.roon.zones.get(zone_id)
        # from pprint import pformat
        # self.log.info("zone: %s", pformat(self.zones[zone_id], indent=2))

    def update_output(self, output_id: str) -> None:
        """Update a output."""
        self.outputs[output_id] = self.roon.outputs.get(output_id)
