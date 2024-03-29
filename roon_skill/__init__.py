# roon-skill
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
# pylint: disable=invalid-name
"""Roon Skill."""
import datetime
import hashlib
import os
import random
import re
from functools import wraps
from typing import Dict, List, Match, Optional, Pattern, Union, cast

from adapt.intent import IntentBuilder
from lingua_franca.parse import extract_number
from ovos_bus_client.message import Message
from ovos_plugin_common_play.ocp import MediaType, PlaybackType

# from mycroft.skills.common_play_skill import CPSMatchLevel
from ovos_workshop.decorators import intent_handler, resting_screen_handler
from ovos_workshop.skills.common_play import (
    OVOSCommonPlaybackSkill,
    ocp_play,
    ocp_search,
)

import rpc.error
from roon_proxy.const import (
    CONF_DEFAULT_ZONE_ID,
    CONF_DEFAULT_ZONE_NAME,
    DEFAULT_VOLUME_STEP,
    DiscoverStatus,
    EnrichedBrowseItem,
    ItemType,
    PairingStatus,
)
from roon_proxy.roon_cache import empty_roon_cache
from roon_proxy.roon_proxy_client import RoonProxyClient
from roon_proxy.roon_types import (
    EVENT_OUTPUT_CHANGED,
    EVENT_ZONE_CHANGED,
    EVENT_ZONE_SEEK_CHANGED,
    RoonAuthSettings,
    RoonStateChange,
)
from roon_proxy.schema import RoonCacheData, RoonManualPairSettings
from roon_proxy.util import match_one

from .types import (
    OVOSAudioTrack,
    RoonNotAuthorizedError,
    RoonPlayData,
    RoonSkillSettings,
)
from .util import (
    format_duration,
    from_roon_uri,
    remove_nulls,
    to_roon_uri,
    write_contents_if_changed,
)

STATUS_KEYS = [
    "line1",
    "line2",
    "currentPosition",
    "progressValue",
    "albumCoverUrl",
    "duration",
    "artistImageUrl",
    "hasProgress"
    "hasAlbumCover"
    "hasArtistBlurredImage"
    "artistBlurredImageDim"
    "hasArtistImage"
    "artistBlurredImageUrl",
]


def auth_settings_valid(
    auth: Optional[RoonAuthSettings],
) -> Optional[RoonManualPairSettings]:
    if auth is not None and all(value is not None for value in auth.values()):
        return RoonManualPairSettings(
            host=auth["host"],
            port=auth["port"],
            token=auth["token"],
            core_id=auth["core_id"],
            core_name=auth["core_name"],
        )
    return None


def ensure_proxy_connected(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        if not self.proxy_connected:
            # raise Exception("Not connected to Roon Proxy")
            return
        return method(self, *args, **kwargs)

    return wrapper


def ensure_paired(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        if not self.paired:
            # raise Exception("Not connected to Roon Proxy")
            return
        return method(self, *args, **kwargs)

    return wrapper


class RoonSkill(OVOSCommonPlaybackSkill):
    """Control roon with your voice."""

    def __init__(self, *args, **kwargs):
        """Init class."""
        self.paired = False
        self.proxy_connected = False
        self.waiting_for_authorization = False
        self.pairing_status: PairingStatus = PairingStatus.NOT_STARTED
        self.cache: RoonCacheData = empty_roon_cache()
        self.regexes: Dict[str, Pattern] = {}
        self.watched_zone_id = None
        self.watched_artist_image_keys = None
        self.watched_artist_image_last_changed_at = None
        self.watched_artist_image_current = None
        self.watched_artist_image_change_after_seconds = None
        super().__init__(*args, **kwargs)
        self.supported_media = [
            MediaType.GENERIC,
            MediaType.AUDIO,
            MediaType.MUSIC,
            MediaType.RADIO,
        ]
        # note: self.initialize is called in super.__init__

    def get_settings(self) -> RoonSkillSettings:
        return cast(RoonSkillSettings, self.settings)

    def get_auth(self) -> Optional[RoonAuthSettings]:
        return self.get_settings().get("auth")

    def set_auth(self, auth: RoonAuthSettings):
        if self.get_auth() != auth:
            self.settings["auth"] = auth

    def connect_roon_proxy(self):
        self.proxy_connected = False
        try:
            self.log.info("attempting to connect to roon proxy")
            self.roon_proxy.connect()
            self.proxy_connected = True
            self.log.info("connected to roon proxy")
            self.post_proxy_connect_init()
        except rpc.error.TimeoutException:
            self.log.info("failed to connect to roon proxy")
            self.schedule_event(self.connect_roon_proxy, 2, name="RoonProxyConnect")

    def post_proxy_connect_init(self):
        self.settings_change_callback = self.handle_settings_change
        self.start_pairing()

    def initialize(self):
        """Init skill."""
        super().initialize()
        settings = self.get_settings()
        proxy_addr = (
            settings.get("proxy_addr")
            or os.environ.get("ROON_PROXY_SOCK")
            or "ipc://server.sock"
        )
        self.roon_proxy = RoonProxyClient(self.log, proxy_addr)
        self.log.info("roon init")
        # Setup handlers for playback control messages
        self.add_event("mycroft.audio.service.next", self.handle_next)
        self.add_event("mycroft.audio.service.prev", self.handle_prev)
        self.add_event("mycroft.audio.service.pause", self.handle_pause)
        self.add_event("mycroft.audio.service.resume", self.handle_resume)
        self.add_event("mycroft.audio.service.stop", self.handle_stop)
        self.add_event("mycroft.stop", self.handle_stop)
        self.schedule_event(self.connect_roon_proxy, 1, name="RoonProxyConnect")

    def shutdown(self):
        """Shutdown skill."""
        self.cancel_scheduled_event("RoonCoreCache")
        self.cancel_scheduled_event("RoonPairing")
        self.roon_proxy.disconnect()

    def start_pairing(self) -> bool:
        """
        Returns true if pairing was started
        """
        auth = self.get_auth()
        settings = self.get_settings()
        settings_host = settings.get("host")
        settings_port = settings.get("port")
        auth_opts = auth_settings_valid(auth)
        pairing_started = False

        if auth_opts:
            # we have valid auth settings
            # so start the pairing process
            self.log.info("Starting roon pairing with saved settings")
            self.roon_proxy.pair(auth_opts)
            pairing_started = True
        elif os.environ.get("ROON_HOST") and os.environ.get("ROON_TOKEN"):
            # settings from env indicate a dev environment where we don't want to reload
            self.reload_skill = False
            pairing_started = True
            self.log.info(
                f"Starting roon pairing host={settings_host} and port={settings_port}"
            )
            self.roon_proxy.pair(
                RoonManualPairSettings(
                    host=os.environ["ROON_HOST"],
                    port=int(os.environ["ROON_PORT"]),
                    token=os.environ["ROON_TOKEN"],
                    core_id=os.environ["ROON_CORE_ID"],
                    core_name=os.environ["ROON_CORE_NAME"],
                )
            )
        elif settings_host and settings_port:
            # user has manually entered the host and port, so we can attempt to pair
            # but this will require authorization from the user in the Roon app
            pairing_started = True
            self.log.info(
                f"Starting roon pairing host={settings_host} and port={settings_port}"
            )
            self.roon_proxy.pair(
                RoonManualPairSettings(host=settings_host, port=settings_port)
            )
        if pairing_started:
            # start checking the pair status in the background
            self.cancel_scheduled_event("RoonPairing")
            self.schedule_repeating_event(
                self.update_pair_status,
                # see https://github.com/OpenVoiceOS/OVOS-workshop/issues/128
                None,  # type: ignore
                3,
                name="RoonPairing",
            )
            self.log.info("Roon pairing started")
            self.update_pair_status()
            return True
        return False

    def handle_paired(self):
        self.log.info(f"handle_paired {self.paired} {self.pairing_status}")
        if self.paired:
            # we want to regularly check to see if we paired
            # but not so often now that we paired once
            self.cancel_scheduled_event("RoonPairing")
            self.schedule_repeating_event(
                self.update_pair_status,
                # see https://github.com/OpenVoiceOS/OVOS-workshop/issues/128
                None,  # type: ignore
                60,
                name="RoonPairing",
            )
            self.schedule_cache_update()

            self.roon_proxy.subscribe(
                os.environ.get("ROON_PUBSUB_SOCK") or "ipc://pubsub.sock",
                self.handle_roon_state_change,
            )
            self.update_library_cache()
            self.register_resting_screen()

    def update_pair_status(self):
        prev_status = self.pairing_status
        status = self.roon_proxy.pair_status()
        self.pairing_status = status.status
        if status.status == PairingStatus.PAIRED:
            self.paired = True
            self.handle_paired()
            if status.auth:
                self.set_auth(
                    {
                        "host": status.auth.host,
                        "port": status.auth.port,
                        "token": status.auth.token,
                        "core_id": status.auth.core_id,
                        "core_name": status.auth.core_name,
                    }
                )
        elif status.status == PairingStatus.FAILED:
            self.paired = False
        elif status.status == PairingStatus.IN_PROGRESS:
            self.paired = False
        elif status.status == PairingStatus.NOT_STARTED:
            self.paired = False
        elif status.status == PairingStatus.WAITING_FOR_AUTHORIZATION:
            self.paired = False
            if prev_status == PairingStatus.IN_PROGRESS:
                self.speak_dialog("AuthorizationWaiting")
        else:
            self.paired = False
            raise Exception(f"Unhandled pairing status {status.status}")

    def update_discover_status(self):
        status = self.roon_proxy.discover_status()
        if status.status == DiscoverStatus.DISCOVERED:
            pass
        elif status.status == DiscoverStatus.FAILED:
            pass
        elif status.status == DiscoverStatus.IN_PROGRESS:
            pass
        elif status.status == DiscoverStatus.NOT_STARTED:
            pass
        else:
            raise Exception(f"Unhandled discover status {status.status}")

    def schedule_cache_update(self):
        self.cancel_scheduled_event("RoonCoreCache")
        self.schedule_repeating_event(
            self.update_library_cache,
            # see https://github.com/OpenVoiceOS/OVOS-workshop/issues/128
            None,  # type: ignore
            5 * 60,
            name="RoonCoreCache",
        )

    def handle_settings_change(self):
        """Handle websettings change."""
        if self.paired:
            # TODO handle changes to the auth settings
            pass
        if not self.paired:
            # try to start pairing with new auth settings
            self.start_pairing()

    def update_library_cache(self):
        """Update library cache."""
        if self.paired:
            self.cache = self.roon_proxy.update_cache()
            self.update_entities()

    def _write_entity_file(self, name: str, data: List[str]) -> None:
        """Write the entity file to the appropriate location on disk
        Only writes the file if it has changed (to prevent init loops)"""
        file_name = f"{name}.entity"
        file_path = os.path.join(self.root_dir, "locale", self.lang, file_name)
        was_changed = write_contents_if_changed(file_path, "\n".join(data))
        if was_changed:
            self.register_entity_file(f"{name}.entity")

    def update_entities(self):
        """Update locale entity files."""

        def norm(s):
            return s.lower().replace("’", "'")

        zone_names = [
            norm(z["display_name"])
            for z in self.cache.zones.values()
            if "display_name" in z
        ]
        self._write_entity_file("zone_name", zone_names)

        output_names = [
            norm(z["display_name"])
            for z in self.cache.outputs.values()
            if "display_name" in z
        ]
        self._write_entity_file("output_name", output_names)
        combined = sorted(set(zone_names + output_names))
        self._write_entity_file("zone_or_output", combined)

    def debug_message(self, message, label):
        self.log.info(
            "%s: data=%s, ctx=%s, type=%s",
            label,
            message.data,
            message.context,
            message.msg_type,
        )

    def get_default_zone_id(self) -> Optional[str]:
        return self.settings.get(CONF_DEFAULT_ZONE_ID)

    def set_default_zone_id(self, zone_id: str) -> None:
        self.settings[CONF_DEFAULT_ZONE_ID] = zone_id

    def get_default_zone_name(self) -> Optional[str]:
        return self.settings.get(CONF_DEFAULT_ZONE_NAME)

    def set_default_zone_name(self, zone_name: str) -> None:
        self.settings[CONF_DEFAULT_ZONE_NAME] = zone_name

    def get_target_zone(self, message: Union[str, Message]) -> Optional[str]:
        """Get the target zone id from a user's query."""
        if isinstance(message, str):
            zone_name = message
            self.log.debug(f"get_target_zone str {message}")
        else:
            zone_name = message.data.get("zone_or_output")
            # self.log.debug(
            #    "get_target_zone message data=%s, ctx=%s, type=%s",
            #    message.data,
            #    message.context,
            #    message.msg_type,
            # )
        zones = list(self.cache.zones.values())
        zone, confidence = match_one(zone_name, zones, "display_name")
        if confidence < 0.6:
            return None
        if zone:
            self.log.info(
                "extracting target zone from %s. Found %s",
                zone_name,
                zone["display_name"],
            )
            return zone["zone_id"]
        self.log.info(f"no zone found from '{zone_name}'")
        return None

    def get_target_output(self, message: Union[str, Message]) -> Optional[str]:
        """Get the target output id from a user's query."""
        if isinstance(message, str):
            output_name = message
            self.log.info(f"get_target_output str {message}")
        else:
            output_name = message.data.get("zone_or_output")
            # self.log.info(
            #    "get_target_output message data=%s, ctx=%s, type=%s",
            #    message.data,
            #    message.context,
            #    message.msg_type,
            # )
        outputs = list(self.cache.outputs.values())
        # self.log.info("outputs %s", outputs)
        output, confidence = match_one(output_name, outputs, "display_name")
        if confidence < 0.6:
            return None
        if output:
            self.log.info(
                "extracting target output from %s. Found %s",
                output_name,
                output["display_name"],
            )
            return output["output_id"]
        self.log.info(f"no output found from '{output_name}'")
        return None

    def get_target_zone_or_output(self, message: Union[str, Message]) -> Optional[str]:
        """Get the target zone or output id from a user's query."""
        zone_id = self.get_target_zone(message)
        if zone_id:
            return zone_id
        output_id = self.get_target_output(message)
        if output_id:
            return output_id

        return self.get_default_zone_id()

    def outputs_for_zones(self, zone_id):
        """Get the outputs for a zone."""
        return self.cache.zones[zone_id]["outputs"]

    def zone_or_output_name(self, zone_or_output_id: str) -> Optional[str]:
        """Get the zone or output name."""
        zone = self.cache.zones.get(zone_or_output_id)
        if zone:
            return zone.get("display_name")
        output = self.cache.outputs.get(zone_or_output_id)
        self.log.info(f"OUTPUT {output}")
        if output:
            return output.get("display_name")
        return None

    def roon_not_connected(self, speak_error=False):
        """Check if the skill is not connected to Roon and speak an error if so"""
        if not self.paired:
            if speak_error:
                self.speak_dialog("RoonNotConfigured")
            return True
        return False

    @intent_handler("ConfigureRoon.intent")
    @ensure_proxy_connected
    def handle_configure_roon(self, message: Message):
        auth = self.get_auth()
        if self.paired and auth:
            self.handle_roon_status(message)
        elif self.pairing_status == PairingStatus.WAITING_FOR_AUTHORIZATION:
            self.speak_dialog("AuthorizationWaiting")
        elif self.pairing_status == PairingStatus.NOT_STARTED:
            if self.start_pairing():
                self.speak_dialog("PairingInProgress")
            else:
                self.speak_dialog("InvalidRoonConfig")

    @intent_handler("RoonStatus.intent")
    def handle_roon_status(self, message: Message):
        # pylint: disable=unused-argument
        """Handle roon status command."""
        self.log.info(f"handle_roon_status {self.pairing_status}")
        if self.paired:
            auth = self.get_auth()
            if auth:
                self.speak_dialog(
                    "RoonStatus",
                    {
                        "name": auth.get("core_name"),
                        "host": auth.get("host"),
                        "port": auth.get("port"),
                    },
                )
        elif self.pairing_status == PairingStatus.WAITING_FOR_AUTHORIZATION:
            self.speak_dialog("AuthorizationWaiting")
        elif self.pairing_status == PairingStatus.IN_PROGRESS:
            self.speak_dialog("PairingInProgress")
        else:
            self.speak_dialog("RoonNotConfigured")

    @intent_handler("GetDefaultZone.intent")
    @ensure_paired
    def handle_get_default_zone(self, message: Message):
        # pylint: disable=unused-argument
        """Handle get default zone command."""
        zone_or_output_id = self.get_default_zone_id()
        if not zone_or_output_id:
            self.speak_dialog("NoDefaultZone")
            return
        zone = self.cache.zones.get(zone_or_output_id)
        if zone:
            self.speak_dialog("DefaultZone", zone)
            return
        output = self.cache.outputs.get(zone_or_output_id)
        if output:
            self.speak_dialog("DefaultZone", output)
            return
        default_zone_name = self.get_default_zone_name()
        self.speak_dialog("DefaultZoneNotFound", default_zone_name)

    def converse(self, message: Optional[str] = None) -> bool:
        # pylint: disable=unused-argument
        return False

    @intent_handler("GetZones.intent")
    @ensure_paired
    def list_zones(self, message: Message):
        # pylint: disable=unused-argument
        """List available zones."""
        self.log.info("list zones")
        if self.roon_not_connected():
            return
        zones = self.cache.zones
        outputs = self.cache.outputs
        if len(zones) == 0 and len(outputs) == 0:
            self.speak_dialog("NoZonesAvailable")
            return
        zone_names = [o["display_name"] for o in zones.values()]
        output_names = [o["display_name"] for o in outputs.values()]
        zone_and_output_names = zone_names + output_names
        zone_and_output_names.sort()

        if len(zone_and_output_names) == 1:
            self.speak(zone_and_output_names[0])
        else:
            if self.gui:
                self.gui.show_text(", ".join(zone_and_output_names))
            self.speak_dialog(
                "AvailableZones",
                {
                    "zones": " ".join(zone_and_output_names[:-1])
                    + " "
                    + self.translate("And")
                    + " "
                    + zone_and_output_names[-1]
                },
            )

    @intent_handler(
        IntentBuilder("SetDefaultZone")
        .optionally("Roon")
        .require("Set")
        .require("SetZone")
    )
    @ensure_paired
    def handle_set_default_zone(self, message: Message):
        """Handle set default zone command."""
        zone_name = message.data.get("SetZone")
        if not zone_name:
            self.log.info("Failed to get SetZone from message")
            return

        zone_or_output, conf = match_one(
            zone_name,
            list(self.cache.outputs.values()) + list(self.cache.zones.values()),
            "display_name",
        )
        if not zone_or_output:
            self.log.info(f"failed to match a zone for {zone_name}")
            return
        self.log.info("zone %s conf %s", zone_or_output, conf)
        zone_or_output_id = zone_or_output.get("zone_id", None)
        if zone_or_output_id is None:
            zone_or_output_id = zone_or_output.get("output_id", None)
        if not zone_or_output_id:
            self.log.info(f"failed to match a zone for {zone_name}")
            return
        self.set_default_zone_id(zone_or_output_id)
        self.set_default_zone_name(zone_or_output["display_name"])
        self.speak_dialog("DefaultZoneConfirm", zone_or_output)
        if self.gui:
            self.gui.show_text(zone_or_output["display_name"], title="Default Zone")
            self.release_gui_after()

    def release_gui_after(self, seconds=10):
        """Release the gui after a number of seconds."""
        self.schedule_event(self.release_gui, seconds)

    def release_gui(self):
        """Release the gui now."""
        if self.gui:
            self.gui.release()

    @intent_handler("Stop.intent")
    @ensure_paired
    def handle_stop(self, message: Message):
        """Stop playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="stop")

    @intent_handler("Pause.intent")
    @ensure_paired
    def handle_pause(self, message: Message):
        """Pause playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="pause")

    @intent_handler("Resume.intent")
    @ensure_paired
    def handle_resume(self, message: Message):
        """Resume playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="play")

    @intent_handler("Next.intent")
    @ensure_paired
    def handle_next(self, message: Message):
        """Next playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="next")

    @intent_handler("Prev.intent")
    @ensure_paired
    def handle_prev(self, message: Message):
        """Prev playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="previous")

    @intent_handler("Mute.intent")
    @ensure_paired
    def handle_mute(self, message: Message):
        """Mute playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            for output in self.outputs_for_zones(zone_id):
                r = self.roon_proxy.mute(output["output_id"], mute=True)
                self.log.info(
                    "muting %s %s %s", output["display_name"], r, output["output_id"]
                )

    @intent_handler("Unmute.intent")
    @ensure_paired
    def handle_unmute(self, message: Message):
        """Unmute playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        for output in self.outputs_for_zones(zone_id):
            r = self.roon_proxy.mute(output["output_id"], mute=False)
            self.log.info(
                "unmuting %s %s %s", output["display_name"], r, output["output_id"]
            )

    @intent_handler("IncreaseVolume.intent")
    @ensure_paired
    def handle_volume_increase(self, message: Message):
        """Increase the volume a little bit."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self._step_volume(zone_id, DEFAULT_VOLUME_STEP)
            self.acknowledge()

    @intent_handler("DecreaseVolume.intent")
    @ensure_paired
    def handle_volume_decrease(self, message: Message):
        """Decrease the volume a little bit."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self._step_volume(zone_id, -DEFAULT_VOLUME_STEP)
            self.acknowledge()

    @intent_handler("SetVolumePercent.intent")
    @ensure_paired
    def handle_set_volume_percent(self, message: Message):
        """Set volume to a percentage."""
        if self.roon_not_connected():
            return
        percent = extract_number(message.data["utterance"].replace("%", ""))
        percent = int(percent)
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.log.info(f"set_vol_percent {percent} {zone_id}")
            self._set_volume(zone_id, percent)
            self.acknowledge()

    def _step_volume(self, zone_or_output_id, step):
        """Change the volume by a relative step."""
        outputs = []
        if zone_or_output_id in self.cache.zones:
            for output in self.outputs_for_zones(zone_or_output_id):
                outputs.append(output)
        elif zone_or_output_id in self.cache.outputs:
            outputs.append(self.cache.outputs[zone_or_output_id])

        for output in outputs:
            r = self.roon_proxy.change_volume_percent(output["output_id"], step)
            self.log.info(
                "changing step=%d output=%s output_id=%s r=%s",
                step,
                output["display_name"],
                output["output_id"],
                r,
            )

    def _set_volume(self, zone_or_output_id, percent):
        """Set volume to a percentage."""
        outputs = []
        if zone_or_output_id in self.cache.zones:
            for output in self.outputs_for_zones(zone_or_output_id):
                outputs.append(output)
        elif zone_or_output_id in self.cache.outputs:
            outputs.append(self.cache.outputs[zone_or_output_id])

        for output in outputs:
            r = self.roon_proxy.set_volume_percent(output["output_id"], percent)
            self.log.info(
                "changing percent=%d output=%s output_id=%s r=%s",
                percent,
                output["display_name"],
                output["output_id"],
                r,
            )

    @intent_handler("ShuffleOn.intent")
    def handle_shuffle_on(self, message):
        """Turn shuffle on."""
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.shuffle(zone_id, True)
            self.acknowledge()

    @intent_handler("ShuffleOff.intent")
    def handle_shuffle_off(self, message):
        """Turn shuffle off."""
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.shuffle(zone_id, False)
            self.acknowledge()

    @intent_handler("RepeatTrackOn.intent")
    def handle_repeat_one_on(self, message):
        """Turn repeat one on."""
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.repeat(zone_id, "loop_one")
            self.acknowledge()

    @intent_handler("RepeatOff.intent")
    def handle_repeat_off(self, message):
        """Turn repeat off."""
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.repeat(zone_id, "disabled")
            self.acknowledge()

    @intent_handler("WhatIsPlaying.intent")
    def handle_what_is_playing(self, message):
        zone_id = self.get_target_zone_or_output(message)
        if not zone_id:
            return
        now_playing = self.roon_proxy.now_playing_for(zone_id)
        zone_name = self.zone_or_output_name(zone_id)
        if not now_playing or now_playing == {}:
            self.log.info("no now playing for %s", zone_id)
            self.speak_dialog("ZoneNotPlaying", {"zone_name": zone_name})
            return
        self.log.info("what is playing in %s? %s", zone_name, now_playing)
        line1 = now_playing.get("two_line").get("line1")
        line2 = now_playing.get("two_line").get("line2")

        if len(line1) > 0 and len(line2) > 0:
            self.speak_dialog("WhatIsPlayingReply-2", {"line1": line1, "line2": line2})
        elif len(line1) > 0:
            self.speak_dialog("WhatIsPlayingReply", {"line1": line1})
        self.show_player(zone_id)

    def regex_translate(self, regex: str) -> Pattern:
        """Translate the given regex."""
        if regex not in self.regexes:
            path = self.find_resource(regex + ".regex")
            if path:
                with open(path, "r", encoding="utf-8") as f:
                    string = f.read().strip()
                self.regexes[regex] = re.compile(string, re.IGNORECASE)
            else:
                self.log.error(f"unknown regex {regex}")
        return self.regexes[regex]

    def list_capture_groups(self, match: Match) -> List[str]:
        """List the capture groups in a match."""
        return [g for g in match.groups() if g]

    def regex_remove(self, phrase: str, regex_name: str) -> str:
        regex = self.regex_translate(regex_name)
        if regex:
            self.log.debug(
                "regex_remove %s: re='%s' phrase='%s'", regex_name, regex, phrase
            )
            return re.sub(regex, "", phrase, re.IGNORECASE)
        self.log.error(f"unknown regex {regex_name}")
        return phrase

    def regex_match(self, phrase: str, regex_name: str) -> Optional[Match]:
        regex = self.regex_translate(regex_name)
        if regex:
            return re.match(regex, phrase)
        self.log.error(f"unknown regex {regex_name}")

    def regex_search(self, phrase: str, regex_name: str) -> Optional[Match]:
        regex = self.regex_translate(regex_name)
        if regex:
            return re.search(regex, phrase)
        self.log.error(f"unknown regex {regex_name}")

    def _specific_query(self, phrase: str, zone_id: str) -> List[OVOSAudioTrack]:
        self.log.debug("_specific_query %s", phrase)
        for item_type in ItemType.searchable:
            type_name = item_type.name.lower()
            match = self.regex_search(phrase, type_name)
            if match:
                phrase = " ".join(match.groupdict().values()).strip()
                self.log.debug(
                    "_specific_query matched phrase to type phrase='%s' item_type='%s'",
                    phrase,
                    item_type.name,
                )
                return self._query_type(item_type, phrase, zone_id)
            else:
                self.log.debug("_specific_query nomatch %s", type_name)

        match = self.regex_match(phrase, "genre1")
        if not match:
            match = self.regex_match(phrase, "genre2")
        self.log.debug("genre match: %s", match)
        if match:
            genre = match.groupdict()["genre"]
            return self._query_type(ItemType.GENRE, genre, zone_id)
        return []

    def _browse_item_to_ovos_audio_track(
        self, zone_id: str, item: EnrichedBrowseItem
    ) -> Optional[OVOSAudioTrack]:
        """Convert a browse item to an ovos audio track."""
        media_type = MediaType.AUDIO

        uri = to_roon_uri(zone_id, item)
        if not uri:
            self.log.info(f"Failed to parse roon uri {uri}")
            return None
        return cast(
            OVOSAudioTrack,
            {
                "match_confidence": int(item["confidence"] * 100),
                "media_type": media_type,
                # "length":
                "uri": uri,
                "playback": PlaybackType.SKILL,
                # "image": r["image"],
                # "bg_image": r["bg_image"],
                # "skill_icon": self.skill_icon,
                "title": item["title"],
                # "artist": r["artist"],
                # "album": r["album"],
                "skill_id": self.skill_id,
            },
        )

    def _query_type(
        self, item_type: ItemType, query: str, zone_id: str
    ) -> List[OVOSAudioTrack]:
        """Try and find a specific item type."""
        self.log.info("_query_type: %s", query)
        data: List[EnrichedBrowseItem] = self.roon_proxy.search_type(item_type, query)
        self.log.info("data: %s", data)
        return remove_nulls(
            [self._browse_item_to_ovos_audio_track(zone_id, item) for item in data]
        )

    def _generic_query(self, query: str, zone_id: str) -> List[OVOSAudioTrack]:
        session_key = hashlib.md5(query.encode()).hexdigest()
        found_items = self.roon_proxy.search_generic(query, session_key)
        return remove_nulls(
            [
                self._browse_item_to_ovos_audio_track(zone_id, item)
                for item in found_items
            ]
        )

    @ocp_search()
    @ensure_paired
    def search(self, utterance: str, media_type: MediaType) -> List[OVOSAudioTrack]:
        """Search for media."""
        self.log.info("ocp_search: %s %s", media_type, utterance)
        self.extend_timeout(timeout=3)
        phrase = utterance
        base_score = 0
        if self.regex_match(phrase, "OnRoon"):
            phrase = self.regex_remove(phrase, "OnRoon")
            base_score = 40

        zone_match = self.regex_search(phrase, "AtZone")
        self.log.info("ZONE MATCH %s", zone_match)
        # self.log.info(self.cache.zones.values())
        # self.log.info(self.cache.outputs.values())
        zone_id = None
        zone_name = None
        if zone_match:
            try:
                zone_name = zone_match.group("zone_or_output")
                self.log.info("Extracted raw %s", zone_name)
                zone_id = self.get_target_zone_or_output(zone_name)
                self.log.info("matched to %s", zone_id)
                phrase = zone_match.group("query")
            except IndexError:
                self.log.info("failed to extract zone")

        self.log.info("utterance: %s", utterance)
        self.log.info("phrase: %s", phrase)
        self.log.info("base_score: %s", base_score)
        if zone_id:
            self.log.info("zone_name: %s", self.zone_or_output_name(zone_id))
            self.log.info("zone_id: %s", zone_id)
        else:
            self.log.error("Cannot complete search request, no zone/output found")
            return []

        results = self._specific_query(phrase, zone_id)
        if not results:
            self.log.info("performing generic search")
            results = self._generic_query(phrase, zone_id)
        self.log.info("RETURNING OCP SEARCH RESULTS")
        self.log.info("%s", results)
        return results

    @ocp_play()
    @ensure_paired
    def play(self, message: Message) -> None:
        if self.gui:
            self.gui.release()
        self.log.info("ocp_play: %s", message)
        uri = message.data["uri"]
        self.log.debug(f"from_roon_uri({uri})")
        play_data = from_roon_uri(uri)
        zone_or_output_id = play_data.get("zone_or_output_id")
        if not zone_or_output_id:
            self.log.error(f"No zone or output id in uri {uri}")
            return
        if play_data["path"]:
            self.roon_proxy.play_path(zone_or_output_id, play_data["path"])
        elif play_data["session_key"]:
            self.roon_proxy.play_session(
                zone_or_output_id, play_data["session_key"], play_data["item_key"]
            )

        self.show_player(zone_or_output_id)

    def handle_roon_state_change(self, msg: RoonStateChange):
        # self.log.debug("handle_roon_state_change %s", msg)
        updated_zones = msg.get("updated_zones")
        updated_outputs = msg.get("updated_outputs")
        new_zones_found = msg.get("new_zones_found")
        for z in updated_zones:
            zone_id = z["zone_id"]
            self.cache.zones[zone_id] = z
            if self.watched_zone_id == zone_id:
                self.update_watched_zone()
        for o in updated_outputs:
            self.cache.outputs[o["output_id"]] = o

        if new_zones_found:
            self.update_entities()

    def get_display_url(self) -> Optional[str]:
        if self.roon_not_connected():
            return None
        auth = self.get_auth()
        if not auth:
            return None
        host = auth.get("host")
        return "http://{}:{}/display/".format(host, 9330)

    @resting_screen_handler("RoonNowPlaying")
    def handle_idle(self, message: str) -> None:
        # pylint: disable=unused-argument
        if self.gui:
            self.log.debug("screen handler msg %s", message)
            url = self.get_display_url()
            self.log.info("showing idle screen %s", url)
            if url:
                self.gui.clear()
                self.gui.show_url(url)

    def clear_gui_info(self):
        """Clear the gui variable list."""
        if self.gui:
            for k in STATUS_KEYS:
                self.gui[k] = ""

    def show_player(self, zone_id: str) -> None:
        if not self.gui or not self.gui.connected:
            return

        self.gui.clear()
        self.clear_gui_info()
        self.set_watched_zone(zone_id)
        if self.update_watched_zone():
            self.gui.show_page("AudioPlayer", override_idle=True)

    def set_watched_zone(self, zone_id: str) -> None:
        self.clear_watched_zone()
        self.watched_zone_id = zone_id

    def clear_watched_zone(self) -> None:
        self.watched_zone_id = None
        self.watched_artist_image_keys = None
        self.watched_artist_image_last_changed_at = None
        self.watched_artist_image_current = None
        self.watched_artist_image_change_after_seconds = None

    def update_watched_zone(self) -> bool:
        if not self.gui or not self.watched_zone_id:
            return False
        now_playing = self.roon_proxy.now_playing_for(self.watched_zone_id)
        # self.log.info("showing player %s", now_playing)
        if now_playing is None:
            return False
        self.gui["line1"] = now_playing["two_line"]["line1"]
        self.gui["line2"] = now_playing["two_line"]["line2"]
        current_pos_seconds = now_playing["seek_position"]
        duration_seconds = now_playing.get("length")
        if current_pos_seconds and duration_seconds:
            self.gui["currentPosition"] = format_duration(current_pos_seconds)
            self.gui["duration"] = format_duration(duration_seconds)
            self.gui["progressValue"] = current_pos_seconds / duration_seconds
            self.gui["hasProgress"] = True
        else:
            self.gui["hasProgress"] = False

        album_cover_image_key = now_playing.get("image_key")
        if album_cover_image_key:
            self.gui["albumCoverUrl"] = self.roon_proxy.get_image(album_cover_image_key)
            self.gui["hasAlbumCover"] = True
        else:
            self.gui["hasAlbumCover"] = False
        if len(now_playing.get("artist_image_keys", [])) > 0:
            artist_image_key = None
            if (
                self.watched_artist_image_keys is None
                or self.watched_artist_image_keys
                != set(now_playing["artist_image_keys"])
            ):
                self.watched_artist_image_keys = set(now_playing["artist_image_keys"])
                artist_image_key = random.choice(list(self.watched_artist_image_keys))
            elif (
                self.watched_artist_image_keys
                and self.watched_artist_image_last_changed_at
            ):
                delta = (
                    datetime.datetime.now() - self.watched_artist_image_last_changed_at
                )
                if self.watched_artist_image_change_after_seconds is not None and (
                    delta.total_seconds()
                    > self.watched_artist_image_change_after_seconds
                ):
                    if (
                        self.watched_artist_image_current
                        in self.watched_artist_image_keys
                        and len(self.watched_artist_image_keys) > 1
                    ):
                        copy = self.watched_artist_image_keys.copy()
                        copy.remove(self.watched_artist_image_current)
                        artist_image_key = random.choice(list(copy))

            if artist_image_key:
                self.gui["hasArtistImage"] = True
                self.gui["hasArtistBlurredImage"] = True
                self.gui["artistImageUrl"] = self.roon_proxy.get_image(artist_image_key)
                self.gui["artistBlurredImageUrl"] = self.roon_proxy.get_image(
                    artist_image_key
                )
                self.gui["artistBlurredImageDim"] = False
                self.watched_artist_image_current = artist_image_key
                self.watched_artist_image_last_changed_at = datetime.datetime.now()
                self.watched_artist_image_change_after_seconds = random.randint(60, 120)
        else:
            self.gui["hasArtistImage"] = False
            if album_cover_image_key:
                self.gui["artistBlurredImageUrl"] = self.roon_proxy.get_image(
                    album_cover_image_key
                )
                self.gui["hasArtistBlurredImage"] = True
                self.gui["artistBlurredImageDim"] = True
            else:
                self.gui["hasArtistBlurredImage"] = False
        return True
