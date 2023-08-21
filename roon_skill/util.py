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
