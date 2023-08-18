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
from typing import List

from roonapi import RoonApi

from .roon_api_browse import roon_browse, roon_browse_load
from .roon_types import (
    BrowseItem,
    HierarchyTypes,
    RoonApiBrowseLoadOptions,
    RoonApiBrowseOptions,
    RoonApiErrorResponse,
)
from .schema import RoonCacheData

log = logging.getLogger(__name__)


def list_(roon: RoonApi, hierarchy: HierarchyTypes) -> List[BrowseItem]:
    """List all items in a hierarchy."""

    opts = RoonApiBrowseOptions(hierarchy=hierarchy, pop_all=True)

    resp = roon_browse(roon, opts)
    if isinstance(resp, RoonApiErrorResponse):
        log.info("Roon browse api returned error: %s", resp)
        return []
    log.debug(f"Browse response: {resp}")
    if resp.list.count == 0:
        return []
    load_opts = RoonApiBrowseLoadOptions(hierarchy=opts.hierarchy, count=100)
    data = roon_browse_load(roon, load_opts)
    if isinstance(data, RoonApiErrorResponse):
        log.info("Roon browse load api returned error: %s", data)
        return []
    if not data.items:
        return []
    return data.items


def list_genres(roon: RoonApi) -> List[BrowseItem]:
    """List all genres."""
    return list_(roon, "genres")


def list_radio_stations(roon: RoonApi) -> List[BrowseItem]:
    """List all radio stations."""
    return list_(roon, "internet_radio")


def list_playlists(roon: RoonApi) -> List[BrowseItem]:
    """List all playlists."""
    return list_(roon, "playlists")


def roon_cache_update(roon: RoonApi) -> RoonCacheData:
    return RoonCacheData(
        zones=roon.zones,
        outputs=roon.outputs,
        radio_stations=list_radio_stations(roon),
        genres=list_genres(roon),
        playlists=list_playlists(roon),
        last_updated=datetime.datetime.now(),
    )


def empty_roon_cache() -> RoonCacheData:
    return RoonCacheData(
        zones={},
        outputs={},
        radio_stations=[],
        genres=[],
        playlists=[],
        last_updated=None,
    )
