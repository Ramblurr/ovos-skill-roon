from typing import List, Optional, TypedDict

from ovos_plugin_common_play.ocp import MediaType, PlaybackType

from roon_proxy.roon_types import RoonAuthSettings


class RoonSkillSettings(TypedDict):
    __mycroft_skill_firstrun: bool
    auth_waiting: Optional[bool]
    auth: Optional[RoonAuthSettings]
    host: Optional[str]
    port: Optional[int]
    default_zone_id: Optional[str]
    default_zone_name: Optional[str]


class RoonNotAuthorizedError(Exception):
    """Error for when Roon isn't authorized."""


class RoonPlayData(TypedDict):
    zone_or_output_id: Optional[str]
    path: Optional[List[str]]
    session_key: Optional[str]
    item_key: Optional[str]


class OVOSAudioTrack(TypedDict):
    uri: str  # URL/URI of media, OCP will handle formatting and file handling
    title: str
    media_type: MediaType
    playback: PlaybackType
    match_confidence: int  # 0-100
    album: Optional[str]
    artist: Optional[str]
    length: Optional[int]  # milliseconds
    image: Optional[str]
    bg_image: Optional[str]
    skill_icon: Optional[str]  # Optional filename for skill icon
    skill_id: Optional[
        str
    ]  # Optional ID of skill to distinguish where results came from
