"""Type definitions for the roon api"""
from dataclasses import dataclass
from typing import List, Literal, Optional, TypedDict

HierarchyTypes = Literal[
    "browse",
    "playlists",
    "settings",
    "internet_radio",
    "albums",
    "artists",
    "genres",
    "composers",
    "search",
]

EVENT_ZONE_CHANGED = "zones_changed"
EVENT_ZONE_SEEK_CHANGED = "zones_seek_changed"
EVENT_OUTPUT_CHANGED = "outputs_changed"

RoonSubscriptionEvent = Literal[
    "zones_changed", "zones_seek_changed", "outputs_changed"
]

BrowseItemHint = Literal["action", "action_list", "list", "header"]
BrowseListHint = Literal["action_list"]

ChangeVolumeMethod = Literal["absolute", "relative", "relative_step"]
RepeatOption = Literal["loop", "loop_one", "disabled"]


class RoonAuthSettings(TypedDict):
    host: str
    port: str
    token: str
    roon_server_id: str
    roon_server_name: str


@dataclass
class BrowseItemInputPrompt:
    prompt: str
    action: str
    value: Optional[str] = None
    is_password: Optional[bool] = None


@dataclass
class BrowseItem:
    title: str
    subtitle: Optional[str] = None
    image_key: Optional[str] = None
    item_key: Optional[str] = None
    hint: Optional[BrowseItemHint] = None
    # input_prompt: Optional[BrowseItemInputPrompt] = None


@dataclass
class BrowseList:
    title: str
    count: int
    level: int
    subtitle: Optional[str] = None
    image_key: Optional[str] = None
    display_offset: Optional[int] = None
    hint: Optional[BrowseListHint] = None


@dataclass
class RoonApiBrowseOptions:
    hierarchy: HierarchyTypes
    multi_session_key: Optional[str] = None
    item_key: Optional[str] = None
    input: Optional[str] = None
    zone_or_output_id: Optional[str] = None
    pop_levels: Optional[bool] = None
    pop_all: Optional[bool] = None
    refresh_list: Optional[bool] = None
    set_display_offset: Optional[str] = None


@dataclass
class RoonApiErrorResponse:
    message: str
    is_error: bool


@dataclass
class RoonApiBrowseResponse:
    action: str
    list: BrowseList
    item: Optional[BrowseItem] = None


@dataclass
class RoonApiBrowseLoadOptions:
    hierarchy: HierarchyTypes
    multi_session_key: Optional[str] = None
    set_display_offset: Optional[str] = None
    level: Optional[int] = None
    offset: Optional[int] = None
    count: Optional[int] = None


@dataclass
class RoonApiBrowseLoadResponse:
    items: List[BrowseItem]
    offset: int
    list: BrowseList
