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
from dataclasses import asdict
from typing import Any, Dict, Optional, Union, cast

from roonapi import RoonApi

from .roon_types import (
    BrowseItem,
    BrowseList,
    RoonApiBrowseLoadOptions,
    RoonApiBrowseLoadResponse,
    RoonApiBrowseOptions,
    RoonApiBrowseResponse,
    RoonApiErrorResponse,
)

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
    roon, zone_or_output_id, item_key: str, session_key: str
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
