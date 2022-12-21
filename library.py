"""Search helpers."""

from typing import Any, Dict, List, Optional, Tuple, Literal
from .const import TYPE_STATION, TYPE_ALBUM, TYPE_ARTIST, TYPE_PLAYLIST, NOTHING_FOUND
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

    def list_radio_stations(self) -> List[Dict]:
        """List all radio stations."""
        return self.list_("internet_radio")

    def update_cache(self, roonapi)-> None:
        """Update the library cache."""
        self.roon = roonapi
        if not self.should_update():
            return

        self.log.info("Updating library cache")

        self.radio_stations = self.list_radio_stations()

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

    def _navigate_search(self, phrase: str , item_type: Literal[TYPE_ALBUM, TYPE_ARTIST, TYPE_PLAYLIST]) -> Tuple[Optional[Dict], int]:
        mapping = {
            TYPE_ALBUM: "Albums",
            TYPE_ARTIST: "Artists",
            TYPE_PLAYLIST: "Playlists",
        }
        mapping_path = {
            TYPE_ALBUM: ["Library", "Albums"],
            TYPE_ARTIST: ["Library", "Artists"],
            TYPE_PLAYLIST: ["Playlists"]
        }
        opts = {
            "hierarchy": "search",
            "count": 10,
            "input": phrase,
            "pop_all": True,
            "multi_session_key": "search"
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
        self.log.info(items)
        data, confidence = match_one(phrase, items, "title")
        self.log.info(data)
        path = mapping_path[item_type].copy()
        path.append(data["title"])
        return self.enrich(data, item_type, path), confidence

    def search_albums(self, phrase) -> Tuple[Optional[Dict], int]:
        return self._navigate_search(phrase, TYPE_ALBUM)

    def search_artists(self, phrase) -> Tuple[Optional[Dict], int]:
        return self._navigate_search(phrase, TYPE_ARTIST)

    def search_playlists(self, phrase) -> Tuple[Optional[Dict], int]:
        return self._navigate_search(phrase, TYPE_PLAYLIST)

    def search_stations(self, phrase) -> Tuple[Optional[Dict], int]:
        """Search for radio stations."""

        def match(stations):
            names = [station["title"] for station in stations]
            data, confidence = match_one(phrase, stations, "title")
            if data:
                return self.enrich(data, TYPE_STATION, ["My Live Radio", data["title"]]), confidence
            return data, confidence
        stations = self.radio_stations
        if len(stations) == 0:
            return NOTHING_FOUND
        d, c = match(stations)
        if  d:
            return d, c

        self.log.info("Not found in station cache, updating")
        stations = self.list_radio_stations()
        return match(stations)
