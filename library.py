"""Search helpers."""

import datetime
from typing import Any, Dict, List, Optional, Tuple, Literal
from .const import TYPE_TRACK, TYPE_STATION, TYPE_TAG, TYPE_ALBUM, TYPE_ARTIST, TYPE_PLAYLIST, TYPE_GENRE, NOTHING_FOUND, LIBRARY_CACHE_UPDATE_MIN_INTERVAL_MINUTES, PAGE_SIZE, DIRECT_RESPONSE_CONFIDENCE
from .util import match_one

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
HierarchyTypes = Literal["browse", "playlists", "settings", "internet_radio", "albums", "artists", "genres", "composers", "search"]
ItemTypes = Literal[TYPE_TRACK, TYPE_ALBUM, TYPE_ARTIST, TYPE_PLAYLIST, TYPE_GENRE, TYPE_STATION, TYPE_TAG]
SearchableItemTypes = Literal[TYPE_TRACK, TYPE_ALBUM, TYPE_ARTIST, TYPE_PLAYLIST, TYPE_TAG]
FilterableItemTypes = Literal[TYPE_STATION, TYPE_GENRE]
def item_payload(roonapi, item, list_image_id):
    """Return a payload for a search result item."""
    title = item["title"]
    if (subtitle := item.get("subtitle")) is None:
        display_title = title
    else:
        display_title = f"{title} ({subtitle})"

    image_id = item.get("image_key") or list_image_id
    image = None
    if image_id:
        image = roonapi.get_image(image_id)

    hint = item["hint"]
    media_content_id = item["item_key"]
    media_content_type = "library"
    payload = {
        "title": display_title,
        "media_class": hint,
        "media_content_id": media_content_id,
        "media_content_type": media_content_type,
        "can_play": True,
        "thumbnail": image,
    }
    return payload


class RoonLibrary():
    """Wrapper for the Roon API."""

    def __init__(self, roonapi, log):
        """init."""
        self.last_updated = None
        self.log = log
        self.roon = roonapi;
        self.radio_stations = []
        self.genres = []
        self.playlists = []
        self.zones = {}
        self.outputs = {}


    def list_(self, hierarchy: HierarchyTypes) -> List[Dict]:
        """List all items in a hierarchy."""
        opts = {
            "hierarchy": hierarchy,
            "count": 100,
            "pop_all": True,
        }

        r = self.roon.browse_browse(opts)
        if r is None:
            return []
        self.log.info(r)
        if r["list"]["count"] == 0:
            return []
        data = self.roon.browse_load(opts)
        if not data or "items" not in data:
            return []
        return data["items"]

    def list_genres(self) -> List[Dict]:
        """List all genres."""
        results = []
        for item in self.list_("genres"):
            results.append(self.enrich(item, TYPE_GENRE, ["Genres"]))
        return results

    def list_radio_stations(self) -> List[Dict]:
        """List all radio stations."""
        results = []
        for item in self.list_("internet_radio"):
            results.append(self.enrich(item, TYPE_STATION, ["My Live Radio"]))
        return results

    def list_playlists(self) -> List[Dict]:
        """List all playlists."""
        results = []
        for item in self.list_("playlists"):
            results.append(self.enrich(item, TYPE_PLAYLIST, ["Playlists"]))
        return results

    def update_cache(self, roonapi)-> None:
        """Update the library cache."""
        self.roon = roonapi
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

    def enrich(self, item: Dict, type: str, path: List[str]) -> Dict:
        """Enrich a Roon item with additional metadata."""
        return item | {"mycroft": {
            "type": type,
            "path": path,
        }}

    def _navigate_search(self, phrase: str , item_type: SearchableItemTypes) -> Tuple[Optional[Dict], int]:
        mapping = {
            TYPE_ALBUM: "Albums",
            TYPE_ARTIST: "Artists",
            TYPE_PLAYLIST: "Playlists",
            TYPE_TAG: "Tags"
        }
        mapping_path = {
            TYPE_ALBUM: ["Library", "Albums"],
            TYPE_ARTIST: ["Library", "Artists"],
            TYPE_PLAYLIST: ["Playlists"],
            TYPE_TAG: ["Library", "Tags"]
        }

        if not item_type in mapping:
            raise Exception(f"Unhandled item type {item_type} for search")

        opts = {
            "hierarchy": "search",
            "count": 10,
            "input": phrase,
            "pop_all": True,
            "multi_session_key": "navigate_search"
        }
        self.log.info(f"searching {item_type} for {phrase}")
        r = self.roon.browse_browse(opts)
        if not r:
            self.log.info("room api returned null search results")
            return NOTHING_FOUND
        if r["list"]["count"] == 0:
            return NOTHING_FOUND
        items = self.roon.browse_load(opts)["items"]
        category_key = None
        for item in items:
            if item["title"] == mapping[item_type]:
                category_key = item["item_key"]
                break
        if not category_key:
            return NOTHING_FOUND
        del opts["pop_all"]
        del opts["input"]
        opts["item_key"] = category_key
        r = self.roon.browse_browse(opts)
        if r["list"]["count"] == 0:
            return NOTHING_FOUND
        r = self.roon.browse_load(opts)
        if not r:
            return NOTHING_FOUND
        items = r["items"]
        data, confidence = match_one(phrase, items, "title")
        path = mapping_path[item_type].copy()
        path.append(data["title"])
        return data |{"mycroft": {
            "session_key": "navigate_search"
        }}, confidence

    def search(self, session_key, phrase: str) -> Tuple[Optional[Dict], int]:
        """Perform a generic search, returning the top result."""
        opts = {
            "hierarchy": "search",
            "count": 10,
            "input": phrase,
            "pop_all": True,
            "multi_session_key": session_key
        }
        self.log.info(f"searching generic for {phrase}")
        r = self.roon.browse_browse(opts)
        if not r:
            self.log.info("room api returned null search results")
            return NOTHING_FOUND
        if r["list"]["count"] == 0:
            return NOTHING_FOUND
        first_item = self.roon.browse_load(opts)["items"][0]
        del opts["pop_all"]
        opts["item_key"] = first_item["item_key"]
        r = self.roon.browse_browse(opts)
        if r.get("action") == "none":
            return NOTHING_FOUND
        data = first_item | {"mycroft": {"session_key": session_key}}
        return data, DIRECT_RESPONSE_CONFIDENCE


    def play_path(self, zone_or_output_id, path, action=None, report_error=True):
        # pylint: disable=too-many-locals,too-many-branches
        """
        Play the media specified.

        params:
            zone_or_output_id: where to play the media
            path: a list allowing roon to find the media
                  eg ["Library", "Artists", "Neil Young", "Harvest"] or ["My Live Radio", "BBC Radio 4"]
            action: the roon action to take to play the media - leave blank to choose the roon default
                    eg "Play Now", "Queue" or "Start Radio"
        """
        opts = {
            "zone_or_output_id": zone_or_output_id,
            "hierarchy": "browse",
            "count": PAGE_SIZE,
            "pop_all": True,
        }

        total_count = self.roon.browse_browse(opts)["list"]["count"]
        del opts["pop_all"]

        load_opts = {
            "zone_or_output_id": zone_or_output_id,
            "hierarchy": "browse",
            "count": PAGE_SIZE,
            "offset": 0,
        }
        items = []
        for element in path:
            load_opts["offset"] = 0
            found = None
            searched = 0

            self.log.debug("Looking for %s", element)
            while searched < total_count and found is None:
                items = self.roon.browse_load(load_opts)["items"]

                for item in items:
                    searched += 1
                    if item["title"] == element:
                        found = item
                        break

                load_opts["offset"] += PAGE_SIZE
            if searched >= total_count and found is None:
                if report_error:
                    self.log.error(
                        "Could not find media path element '%s' in %s",
                        element,
                        [item["title"] for item in items],
                    )
                return None

            opts["item_key"] = found["item_key"]
            load_opts["item_key"] = found["item_key"]

            total_count = self.roon.browse_browse(opts)["list"]["count"]

            load_opts["offset"] = 0
            items = self.roon.browse_load(load_opts)["items"]

            if found["hint"] == "action":
                # Loading item we found already started playing
                return True

        # First item shoule be the action/action_list for playing this item (eg Play Genre, Play Artist, Play Album)
        if items[0].get("hint") not in ["action_list", "action"]:
            self.log.error(
                "Found media does not have playable action_list hint='%s' '%s'",
                items[0].get("hint"),
                [item["title"] for item in items],
            )
            return False

        play_header = items[0]["title"]
        if items[0].get("hint") == "action_list":
            opts["item_key"] = items[0]["item_key"]
            load_opts["item_key"] = items[0]["item_key"]
            self.roon.browse_browse(opts)
            items = self.roon.browse_load(load_opts)["items"]

        # We should now have play actions (eg Play Now, Add Next, Queue action, Start Radio)
        # So pick the one to use - the default is the first one
        if action is None:
            take_action = items[0]
        else:
            found_actions = [item for item in items if item["title"] == action]
            if len(found_actions) == 0:
                self.log.error(
                    "Could not find play action '%s' in %s",
                    action,
                    [item["title"] for item in items],
                )
                return False
            take_action = found_actions[0]

        if take_action["hint"] != "action":
            self.log.error(
                "Found media does not have playable action %s - %s",
                take_action["title"],
                take_action["hint"],
            )
            return False

        opts["item_key"] = take_action["item_key"]
        load_opts["item_key"] = take_action["item_key"]
        self.log.info("Play action was '%s' / '%s'", play_header, take_action["title"])
        r = self.roon.browse_browse(opts)
        self.log.info(f"Play result: {r}")
        return r

    def play_search_result(self, zone_or_output_id, item_key: str, session_key: str) -> Dict[str, Any]:
        """Play the top result of a previous search."""
        self.log.info(f"playing item from session key {session_key}")
        opts = {
            "hierarchy": "search",
            "count": 10,
            "multi_session_key": session_key,
            "item_key": item_key
        }
        action_list_key = None

        levels = set()
        while action_list_key is None:
            r = self.roon.browse_browse(opts)
            current_level = r["list"]["level"]
            if current_level in levels:
                self.log.info("Aborting. Current level already visited")
                break
            levels.add(current_level)
            self.log.info(f'Browsing level {r["list"]["level"]}')
            self.log.debug(r)
            if not r:
                self.log.info("room api returned null search results")
                break
            if r["action"] != "list" or r["list"]["count"] == 0:
                self.log.info("room api returned 0 search results")
                break
            r = self.roon.browse_load(opts)
            self.log.debug(r)
            items = r["items"]
            for item in items:
                if item.get("hint") == "action_list":
                    action_list_key = item["item_key"]
                    break
            # couldn't find the action list, let's go a level deeper
            opts["item_key"] = items[0]["item_key"]


        if action_list_key is None:
            self.log.info(f"Could not find action list key!")
            return {"is_error": True, "message": "Nothing found" }

        opts["item_key"] = action_list_key
        r = self.roon.browse_browse(opts)

        action_key = None
        for item in self.roon.browse_load(opts)["items"]:
            self.log.info(item)
            if item.get("hint") == "action":
                action_key = item["item_key"]
                break
        if action_key is None:
            self.log.info(f"Could not find action key!")
            return {"is_error": True, "message": "Nothing found" }

        opts["item_key"] = action_key
        opts["zone_or_output_id"] = zone_or_output_id
        r = self.roon.browse_browse(opts)
        self.log.info(f"Play result: {r}")
        return r

    def search_tags(self, phrase: str) -> Tuple[Optional[Dict], int]:
        """Search for an tag."""
        return self._navigate_search(phrase, TYPE_TAG)

    def search_albums(self, phrase: str) -> Tuple[Optional[Dict], int]:
        """Search for an album."""
        return self._navigate_search(phrase, TYPE_ALBUM)

    def search_tracks(self, phrase: str) -> Tuple[Optional[Dict], int]:
        """Search for an track."""
        return self._navigate_search(phrase, TYPE_TRACK)

    def search_artists(self, phrase: str) -> Tuple[Optional[Dict], int]:
        """Search for an artist."""
        return self._navigate_search(phrase, TYPE_ARTIST)

    def search_playlists(self, phrase: str) -> Tuple[Optional[Dict], int]:
        """Search playlists."""
        return self._navigate_search(phrase, TYPE_PLAYLIST)

    def match_and_enrich(self, phrase: str, item_type: FilterableItemTypes, path: List[str], items: List[Dict]) -> Tuple[Optional[Dict], int]:
        """Match and enrich an item."""
        names = [item["title"] for item in items]
        data, confidence = match_one(phrase, items, "title")
        path = path.copy()
        path.append(data["title"])
        if data:
            return self.enrich(data, item_type, path), confidence
        return data, confidence

    def filter_hierarchy_cache(self, phrase: str, item_type: ItemTypes) -> Tuple[Optional[Dict], int]:
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
        d, c = self.match_and_enrich(phrase, item_type, path, items)
        if  d:
            return d, c

        self.log.info(f"Not found in {item_type} cache, updating")
        if item_type == TYPE_STATION:
            items = self.list_radio_stations()
            path = ["My Live Radio"]
        elif item_type == TYPE_GENRE:
            items = self.list_genres()
            path = ["Genres"]
        return  self.match_and_enrich(phrase, item_type, path, items)

    def search_genres(self, phrase: str) -> Tuple[Optional[Dict], int]:
        """Search for genres."""
        return self.filter_hierarchy_cache(phrase, TYPE_GENRE)

    def search_stations(self, phrase: str) -> Tuple[Optional[Dict], int]:
        """Search for radio stations."""
        return self.filter_hierarchy_cache(phrase, TYPE_STATION)

    def match_user_playlist(self, phrase: str) -> Tuple[Optional[Dict], int]:
        """Match a user playlist."""
        return self.match_and_enrich(phrase, TYPE_PLAYLIST, ["Playlists"], self.playlists)

        #import pprint
        #self.log.info(pprint.pformat(self.playlists, indent=4))
