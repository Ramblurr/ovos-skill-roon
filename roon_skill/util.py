# Copyright (C) 2022, 2023 Casey Link
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
import os
from typing import List, Optional, TypeVar
from urllib.parse import parse_qs, quote, unquote, urlparse

from roon_proxy.const import EnrichedBrowseItem

from .types import RoonPlayData

T = TypeVar("T")


def remove_nulls(items: List[Optional[T]]) -> List[T]:
    return [item for item in items if item is not None]


def to_roon_uri(zone_id: str, item: EnrichedBrowseItem) -> Optional[str]:
    path = None
    if "path" in item["mycroft"] and item["mycroft"]["path"]:
        encoded_parts = [quote(part) for part in item["mycroft"]["path"]]
        path = "/path/" + "/".join(encoded_parts)
    elif (
        "session_key" in item["mycroft"]
        and item["mycroft"]["session_key"]
        and item["item_key"]
    ):
        path = "/session/" + item["mycroft"]["session_key"] + "/" + item["item_key"]
    if not path:
        return None
    if zone_id:
        path += f"?zone_or_output={quote(zone_id)}"
    return f"roon://{path}"


def from_roon_uri(uri: str) -> RoonPlayData:
    if uri.startswith("roon:/"):
        url = urlparse(uri)
        parts = url.path.split("/")
        parts.pop(0)  # first /
        play_type = parts.pop(0)  # /path or /session
        zone_or_output = parse_qs(url.query).get("zone_or_output", [None])[0]
        if play_type == "path":
            decoded_parts = [unquote(part) for part in parts if part]
            return RoonPlayData(
                path=decoded_parts,
                zone_or_output_id=zone_or_output,
                session_key=None,
                item_key=None,
            )
        elif play_type == "session":
            session_key = parts.pop(0)
            item_key = parts.pop(0)
            return RoonPlayData(
                path=None,
                zone_or_output_id=zone_or_output,
                session_key=session_key,
                item_key=item_key,
            )
    raise Exception(f"Unknown type of uri: {uri}")


def write_contents_if_changed(file_path: str, contents: str) -> bool:
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            existing_content = f.read()
    else:
        existing_content = None

    new_content = "\n".join(contents)

    if existing_content != new_content:
        with open(file_path, "w+", encoding="utf-8") as f:
            f.write(new_content)
        return True
    return False


def format_duration(seconds: int) -> str:
    if seconds is None:
        return ""
    minutes = seconds // 60
    hours = minutes // 60
    if hours == 0:
        return "%02d:%02d" % (minutes, seconds % 60)
    else:
        return "%02d:%02d:%02d" % (hours, minutes % 60, seconds % 60)
