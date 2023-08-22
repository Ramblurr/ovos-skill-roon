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
import logging
import re
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Union, cast

from roonapi import RoonApi

from .const import DIRECT_RESPONSE_CONFIDENCE, EnrichedBrowseItem, ItemType
from .roon_types import (
    BrowseItem,
    BrowseList,
    RoonApiBrowseLoadOptions,
    RoonApiBrowseLoadResponse,
    RoonApiBrowseOptions,
    RoonApiBrowseResponse,
    RoonApiErrorResponse,
)
from .schema import RoonCacheData
from .util import best_match, match_one_item

log = logging.getLogger(__name__)


def roon_browse(
    roonapi: RoonApi, options: RoonApiBrowseOptions
) -> Union[RoonApiErrorResponse, RoonApiBrowseResponse]:
    resp = cast(Optional[Dict[str, Any]], roonapi.browse_browse(asdict(options)))
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


def roon_browse_load(
    roonapi: RoonApi, options: RoonApiBrowseLoadOptions
) -> Union[RoonApiErrorResponse, RoonApiBrowseLoadResponse]:
    resp = cast(Optional[Dict[str, Any]], roonapi.browse_load(asdict(options)))
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


def roon_play_search_result(
    roon, zone_or_output_id, item_key: Optional[str], session_key: str
) -> Union[RoonApiBrowseResponse, RoonApiErrorResponse]:
    # pylint disable=too-many-statements
    """Play the top result of a previous search."""
    log.info("playing item from session key %s", session_key)
    opts = RoonApiBrowseOptions(
        hierarchy="search",
        item_key=item_key,
        multi_session_key=session_key,
    )

    action_list_key = None

    levels = set()
    while action_list_key is None:
        resp = roon_browse(roon, opts)
        if isinstance(resp, RoonApiErrorResponse):
            log.info("room browse api returned error %s", resp)
            break
        current_level = resp.list.level
        if current_level in levels:
            log.info("Aborting. Current level already visited")
            break
        levels.add(current_level)
        log.info("Browsing level %d", current_level)
        log.debug(resp)
        if resp.action != "list" or resp.list.count == 0:
            log.info("room api returned 0 search results")
            break
        load_opts = RoonApiBrowseLoadOptions(
            count=10, hierarchy="search", multi_session_key=session_key
        )
        resp = roon_browse_load(roon, load_opts)
        if isinstance(resp, RoonApiErrorResponse):
            log.info("room browse api returned error %s", resp)
            break
        if resp.list.count == 0:
            log.info("room browse api returned null search results")
            break
        log.debug(resp)
        for item in resp.items:
            if item.hint == "action_list":
                action_list_key = item.item_key
                break
        # couldn't find the action list, let's go a level deeper
        opts.item_key = resp.items[0].item_key

    if action_list_key is None:
        log.info("Could not find action list key!")
        return RoonApiErrorResponse(is_error=True, message="Nothing found")

    opts.item_key = action_list_key
    resp = roon_browse(roon, opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("room browse api returned error %s", resp)
        return RoonApiErrorResponse(is_error=True, message="Nothing found")
    if resp.action != "list" or resp.list.count == 0:
        log.info("room api returned 0 search results")
        return RoonApiErrorResponse(is_error=True, message="Nothing found")

    load_opts = RoonApiBrowseLoadOptions(
        count=10, hierarchy="search", multi_session_key=session_key
    )
    resp = roon_browse_load(roon, load_opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("room api returned null search results")
        return RoonApiErrorResponse(is_error=True, message="Nothing found")
    action_key = None
    for item in resp.items:
        log.debug(item)
        if item.hint == "action":
            action_key = item.item_key
            break
    if action_key is None:
        log.info("Could not find action key!")
        return RoonApiErrorResponse(is_error=True, message="Nothing found")

    opts.item_key = action_key
    opts.zone_or_output_id = zone_or_output_id
    resp = roon_browse(roon, opts)
    log.info("Play result: %s", resp)
    return resp


def enrich(
    item: BrowseItem, item_type: ItemType, path: List[str], confidence: float
) -> EnrichedBrowseItem:
    """Enrich a Roon item with additional metadata."""
    return cast(
        EnrichedBrowseItem,
        asdict(item)
        | {
            "mycroft": {"type": item_type, "path": path, "session_key": None},
            "confidence": confidence,
        },
    )


NOTHING_FOUND: List[EnrichedBrowseItem] = []


def roon_search_type(
    roon: RoonApi,
    cache: RoonCacheData,
    item_type: ItemType,
    phrase: str,
) -> List[EnrichedBrowseItem]:
    if item_type == ItemType.GENRE:
        return search_genres(cache, phrase)
    if item_type == ItemType.STATION:
        return search_stations(cache, phrase)

    return navigate_type_search(roon, item_type, phrase)


def search_genres(cache: RoonCacheData, phrase: str) -> List[EnrichedBrowseItem]:
    """Search for genres."""
    return filter_hierarchy_cache(cache, phrase, ItemType.GENRE)


def search_stations(cache: RoonCacheData, phrase: str) -> List[EnrichedBrowseItem]:
    """Search for radio stations."""
    opt1 = filter_hierarchy_cache(cache, phrase, ItemType.STATION)
    pat = r".*(fm \d+).*"
    match = re.match(pat, phrase, re.IGNORECASE)
    if match:
        no_whitespace = match.group(1).replace(" ", "")
        phrase = re.sub(pat, no_whitespace, phrase, flags=re.IGNORECASE)
        opt2 = filter_hierarchy_cache(cache, phrase, ItemType.STATION)
        return opt1 + opt2
    return opt1


def match_and_enrich(
    phrase: str,
    item_type: ItemType,
    path: List[str],
    items: List[BrowseItem],
) -> List[EnrichedBrowseItem]:
    """Match and enrich an item."""
    data, confidence = match_one_item(phrase, items)
    if data is None:
        return NOTHING_FOUND
    path = path.copy()
    path.append(data.title)
    if data:
        return [enrich(data, item_type, path, confidence)]
    data["confidence"] = confidence
    return [data]


def filter_hierarchy_cache(
    cache: RoonCacheData, phrase: str, item_type: ItemType
) -> List[EnrichedBrowseItem]:
    """Filter the cached hierarchy items for a match."""
    if item_type == ItemType.STATION:
        items = cache.radio_stations
        path = ["My Live Radio"]
    elif item_type == ItemType.GENRE:
        items = cache.genres
        path = ["Genres"]
    else:
        raise Exception("Unhandled item type for hierarchy cache filter")

    if len(items) == 0:
        return NOTHING_FOUND
    result = match_and_enrich(phrase, item_type, path, items)

    if result:
        return result

    return NOTHING_FOUND
    # TODO update cache if not found in cache
    # log.info("Not found in %s cache, updating", item_type)
    # if item_type == ItemType.STATION:
    #     items = cache.list_radio_stations()
    #     path = ["My Live Radio"]
    # elif item_type == ItemType.GENRE:
    #     items = self.list_genres()
    #     path = ["Genres"]
    # return self.match_and_enrich(phrase, item_type, path, items)


def navigate_type_search(
    roon: RoonApi, item_type: ItemType, phrase: str
) -> List[EnrichedBrowseItem]:
    # pylint: disable=too-many-return-statements
    """Search for a phrase in a specific type."""
    assert item_type.is_searchable
    mapping = {
        ItemType.ALBUM: "Albums",
        ItemType.ARTIST: "Artists",
        ItemType.PLAYLIST: "Playlists",
        ItemType.TAG: "Tags",
    }
    mapping_path = {
        ItemType.ALBUM: ["Library", "Albums"],
        ItemType.ARTIST: ["Library", "Artists"],
        ItemType.PLAYLIST: ["Playlists"],
        ItemType.TAG: ["Library", "Tags"],
    }

    if not item_type in mapping:
        raise Exception(f"Unhandled item type {item_type} for search")

    opts = RoonApiBrowseOptions(
        hierarchy="search",
        pop_all=True,
        input=phrase,
        multi_session_key="navigate_search",
    )
    log.info("searching %s for %s", item_type, phrase)
    resp = roon_browse(roon, opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("room browse api returned error %s", resp)
        return NOTHING_FOUND
    if resp.list.count == 0:
        log.info("room browse api returned null search results")
        return NOTHING_FOUND
    load_opts = RoonApiBrowseLoadOptions(
        count=10, hierarchy="search", multi_session_key="navigate_search"
    )

    resp = roon_browse_load(roon, load_opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("room load api returned error %s", resp)
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

    resp = roon_browse(roon, opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("room browse api returned error %s", resp)
        return NOTHING_FOUND
    if resp.list.count == 0:
        log.info("room browse api returned null search results")
        return NOTHING_FOUND

    load_opts = RoonApiBrowseLoadOptions(
        count=10, hierarchy="search", multi_session_key="navigate_search"
    )
    resp = roon_browse_load(roon, load_opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("room load api returned error %s", resp)
        return NOTHING_FOUND

    data, confidence = match_one_item(phrase, resp.items)
    if not data:
        return NOTHING_FOUND
    path = mapping_path[item_type].copy()
    path.append(data.title)
    return [
        cast(
            EnrichedBrowseItem,
            asdict(data)
            | {
                "mycroft": {
                    "path": None,
                    "type": None,
                    "session_key": "navigate_search",
                },
                "confidence": confidence,
            },
        ),
    ]


def roon_search_generic(
    roon: RoonApi, cache: RoonCacheData, session_key: str, phrase: str
) -> List[EnrichedBrowseItem]:
    # pylint: disable=too-many-return-statements
    """Perform a generic search, returning the top result."""
    opts = RoonApiBrowseOptions(
        hierarchy="search",
        input=phrase,
        pop_all=True,
        multi_session_key=session_key,
    )
    log.info("searching generic for %s", phrase)
    resp = roon_browse(roon, opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("room browse api returned error %s", resp)
        return NOTHING_FOUND
    if resp.list.count == 0:
        log.info("room browse api returned null search results")
        return NOTHING_FOUND
    load_opts = RoonApiBrowseLoadOptions(
        count=10, hierarchy="search", multi_session_key=session_key
    )
    resp = roon_browse_load(roon, load_opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("room load api returned error %s", resp)
        return NOTHING_FOUND
    if len(resp.items) == 0:
        log.info("room load api returned 0 search results")
        return NOTHING_FOUND
    first_item = resp.items[0]
    opts.pop_all = False
    opts.item_key = first_item.item_key
    resp = roon_browse(roon, opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("room browse api returned error %s", resp)
        return NOTHING_FOUND
    if resp.list.count == 0:
        log.info("room browse api returned null search results")
        return NOTHING_FOUND
    if resp.action == "none":
        return NOTHING_FOUND
    data = cast(
        EnrichedBrowseItem,
        asdict(first_item)
        | {
            "mycroft": {"path": None, "type": None, "session_key": session_key},
            "confidence": DIRECT_RESPONSE_CONFIDENCE,
        },
    )
    return [data]
