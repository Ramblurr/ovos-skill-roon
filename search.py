from fuzzywuzzy import fuzz, process as fuzz_process

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

def list_radio_stations(roonapi):
    opts = {
        "hierarchy": "internet_radio",
        "count": 10,
        "pop_all": True,
    }
    if roonapi.browse_browse(opts)["list"]["count"] == 0:
        return None
    data = roonapi.browse_load(opts)
    if not data or "items" not in data:
        return None
    return data["items"]

def search_stations(roonapi, phrase):
    stations = list_radio_stations(roonapi)
    names = [station["title"] for station in stations]
    return fuzz_process.extractOne(
        phrase, names, scorer=fuzz.ratio
    )
