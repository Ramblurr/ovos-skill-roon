"""Search helpers."""

from typing import Any, Dict, List, Optional, Tuple
from .const import TYPE_STATION
from .util import match_one

EXCLUDE_ITEMS = {
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

class RoonSearch():
    """Utility class for wrapping searching and browsing Roon."""

    def __init__(self, roonapi, log):
        """Initialize the RoonSearch class."""
        self.roonapi = roonapi
        self.log = log

    def list_radio_stations(self) -> List[Dict]:
        """List all radio stations."""
        opts = {
            "hierarchy": "internet_radio",
            "count": 10,
            "pop_all": True,
        }

        r = self.roonapi.browse_browse(opts)
        if r is None:
            return []
        self.log.info(r)
        if r["list"]["count"] == 0:
            return []
        data = self.roonapi.browse_load(opts)
        if not data or "items" not in data:
            return []
        return data["items"]

    def enrich(self, item: Dict, type: str, path: List[str]) -> Dict:
        """Enrich a Roon item with additional metadata."""
        return item | {"mycroft": {
            "type": type,
            "path": path,
        }}

    def search_stations(self, phrase) -> Tuple[Optional[Dict], int]:
        """Search for radio stations."""
        stations = self.list_radio_stations()
        names = [station["title"] for station in stations]
        data, confidence = match_one(phrase, stations, "title")
        if data:
            return self.enrich(data, TYPE_STATION, ["My Live Radio", data["title"]]), confidence
        return data, confidence
