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

from dataclasses import asdict
import asyncio
import os
import re
import random
import datetime
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Pattern,
    Tuple,
    Union,
    TypedDict,
    cast,
    Set,
)


from adapt.intent import IntentBuilder
from ovos_bus_client.message import Message

# from mycroft.skills.common_play_skill import CPSMatchLevel
from ovos_workshop.decorators import intent_handler, resting_screen_handler
from lingua_franca.parse import extract_number

# from ovos_workshop.skills.common_play import (
#    OVOSCommonPlaybackSkill,
#    MediaType,
#    PlaybackType,
#    ocp_search,
#    ocp_play,
# )
from ovos_workshop.skills import OVOSSkill
from roon_proxy.const import DiscoverStatus, PairingStatus
from roon_proxy.roon_cache import empty_roon_cache

from roon_proxy.schema import RoonCacheData, RoonManualPairSettings

from roon_proxy.roon_types import (
    RoonSubscriptionEvent,
    RoonAuthSettings,
    RoonApiErrorResponse,
    RoonApiBrowseLoadResponse,
    RoonApiBrowseResponse,
    EVENT_ZONE_CHANGED,
    EVENT_ZONE_SEEK_CHANGED,
    EVENT_OUTPUT_CHANGED,
)
from roon_proxy.const import (
    CONF_DEFAULT_ZONE_ID,
    CONF_DEFAULT_ZONE_NAME,
    DEFAULT_VOLUME_STEP,
    NOTHING_FOUND,
    ROON_KEYWORDS,
    TYPE_ALBUM,
    TYPE_ARTIST,
    TYPE_GENRE,
    TYPE_PLAYLIST,
    TYPE_STATION,
    TYPE_TAG,
)

from roon_proxy.roon_proxy_client import RoonProxyClient
from .util import match_one

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


class RoonSkill(OVOSSkill):
    """Control roon with your voice."""

    def __init__(self, *args, **kwargs):
        """Init class."""
        self.paired = False
        self.waiting_for_authorization = False
        self.pairing_status: PairingStatus = PairingStatus.NOT_STARTED
        self.roon_proxy = RoonProxyClient(
            os.environ.get("ROON_PROXY_SOCK") or "ipc://server.sock"
        )
        self.roon_proxy.connect()
        self.cache: RoonCacheData = empty_roon_cache()
        super().__init__(*args, **kwargs)
        # note: self.initialize is called in super.__init__

    def get_settings(self) -> RoonSkillSettings:
        return cast(RoonSkillSettings, self.settings)

    def get_auth(self) -> Optional[RoonAuthSettings]:
        return self.get_settings().get("auth")

    def set_auth(self, auth: RoonAuthSettings):
        self.log.info("set_auth {auth}")
        self.settings["auth"] = auth

    def initialize(self):
        """Init skill."""
        super().initialize()
        self.log.info("roon init")
        # Setup handlers for playback control messages
        self.add_event("mycroft.audio.service.next", self.handle_next)
        self.add_event("mycroft.audio.service.prev", self.handle_prev)
        self.add_event("mycroft.audio.service.pause", self.handle_pause)
        self.add_event("mycroft.audio.service.resume", self.handle_resume)
        self.add_event("mycroft.audio.service.stop", self.handle_stop)
        self.add_event("mycroft.stop", self.handle_stop)

        self.cancel_scheduled_event("RoonSettingsUpdate")
        self.settings_change_callback = self.handle_settings_change
        if not self.start_pairing():
            # if we aren't authorized yet, then update our settings every 3 seconds
            # this is so that if the user changes the settings in the webui (or config file)
            # we can react timely
            update_settings_interval = 3
            self.schedule_repeating_event(
                self.handle_settings_change,
                # see https://github.com/OpenVoiceOS/OVOS-workshop/issues/128
                None,  # type: ignore
                update_settings_interval,
                name="RoonSettingsUpdate",
            )

    def shutdown(self):
        """Shutdown skill."""
        self.cancel_scheduled_event("RoonSettingsUpdate")
        self.cancel_scheduled_event("RoonCoreCache")
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
            # we want to reguraly check to see if we paired
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
            self.cancel_scheduled_event("RoonSettingsUpdate")
            self.start_pairing()

    def update_library_cache(self):
        """Update library cache."""
        if self.paired:
            self.cache = self.roon_proxy.update_cache()
            self.update_entities()

    def _write_entity_file(self, name, data):
        with open(
            os.path.join(self.root_dir, "locale", self.lang, f"{name}.entity"),
            "w+",
            encoding="utf-8",
        ) as f:
            f.write("\n".join(data))
        self.register_entity_file(f"{name}.entity")

    def update_entities(self):
        """Update locale entity files."""

        def norm(s):
            return s.lower().replace("’", "'")

        zone_names = [norm(z["display_name"]) for z in self.cache.zones.values()]
        self._write_entity_file("zone_name", zone_names)

        output_names = [norm(z["display_name"]) for z in self.cache.outputs.values()]
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
        else:
            zone_name = message.data.get("zone_or_output")
            self.log.info(
                "get_target_zone message data=%s, ctx=%s, type=%s",
                message.data,
                message.context,
                message.msg_type,
            )
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
        return None

    def get_target_output(self, message: Union[str, Message]) -> Optional[str]:
        """Get the target output id from a user's query."""
        if isinstance(message, str):
            output_name = message
        else:
            output_name = message.data.get("zone_or_output")
        outputs = list(self.cache.outputs.values())
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

    def zone_name(self, zone_id):
        """Get the zone name."""
        zone = self.cache.zones.get(zone_id)
        if not zone:
            return None
        return zone.get("display_name")

    def roon_not_connected(self, speak_error=False):
        """Check if the skill is not connected to Roon and speak an error if so"""
        if not self.paired:
            if speak_error:
                self.speak_dialog("RoonNotConfigured")
            return True
        return False

    @intent_handler("ConfigureRoon.intent")
    def handle_configure_roon(self, message: Message):
        settings = self.get_settings()
        auth = self.get_auth()
        if self.paired and auth:
            self.handle_roon_status(message)
        if self.pairing_status == PairingStatus.NOT_STARTED:
            if self.start_pairing():
                self.speak_dialog("PairingInProgress")

    @intent_handler("RoonStatus.intent")
    def handle_roon_status(self, message: Message):
        # pylint: disable=unused-argument
        """Handle roon status command."""
        auth = self.get_auth()
        self.log.info(f"handle_roon_status {self.pairing_status}")
        if self.paired and auth:
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

    @intent_handler(
        IntentBuilder("GetDefaultZone")
        .optionally("Roon")
        .require("List")
        .require("Default")
        .require("Zone")
    )
    def handle_get_default_zone(self, message: Message):
        # pylint: disable=unused-argument
        """Handle get default zone command."""
        zone_id = self.get_default_zone_id()
        if zone_id:
            zone = self.cache.zones[zone_id]
            self.speak_dialog("DefaultZone", zone)
        else:
            self.speak_dialog("NoDefaultZone")

    def converse(self, message: Optional[str] = None) -> bool:
        # pylint: disable=unused-argument
        return False

    @intent_handler(
        IntentBuilder("ListZones").optionally("Roon").require("List").require("Zone")
    )
    def list_zones(self, message: Message):
        # pylint: disable=unused-argument
        """List available zones."""
        self.log.info("list zones")
        if self.roon_not_connected():
            return
        zones = self.cache.zones
        if len(zones) == 0:
            self.speak_dialog("NoZonesAvailable")
            return
        zone_names = [o["display_name"] for id, o in zones.items()]
        zone_names.sort()
        if len(zones) == 1:
            self.speak(zone_names[0])
        else:
            if self.gui:
                self.gui.show_text(", ".join(zone_names))
            self.speak_dialog(
                "AvailableZones",
                {
                    "zones": " ".join(zone_names[:-1])
                    + " "
                    + self.translate("And")
                    + " "
                    + zone_names[-1]
                },
            )

    @intent_handler(
        IntentBuilder("ListOutputs")
        .optionally("Roon")
        .require("List")
        .require("Device")
    )
    def list_outputs(self, message: Message):
        # pylint: disable=unused-argument
        """List available devices."""
        if self.roon_not_connected():
            return
        outputs = self.cache.outputs
        if len(outputs) == 0:
            self.speak_dialog("NoOutputsAvailable")
            return
        output_names = [o["display_name"] for id, o in outputs.items()]
        output_names.sort()
        if len(outputs) == 1:
            self.speak(output_names[0])
        else:
            if self.gui:
                self.gui.show_text(", ".join(output_names))
            self.speak_dialog(
                "AvailableOutputs",
                {
                    "outputs": " ".join(output_names[:-1])
                    + " "
                    + self.translate("And")
                    + " "
                    + output_names[-1]
                },
            )

    @intent_handler(
        IntentBuilder("SetDefaultZone")
        .optionally("Roon")
        .require("Set")
        .require("SetZone")
    )
    def handle_set_default_zone(self, message: Message):
        """Handle set default zone command."""
        zone_name = message.data.get("SetZone")
        if not zone_name:
            self.log.info("Failed to get SetZone from message")
            return
        zone, conf = match_one(
            zone_name, list(self.cache.zones.values()), "display_name"
        )
        if not zone:
            self.log.info(f"failed to match a zone for {zone_name}")
            return
        self.log.info("zone %s conf %s", zone, conf)
        self.set_default_zone_id(zone["zone_id"])
        self.set_default_zone_name(zone["display_name"])
        self.speak_dialog("DefaultZoneConfirm", zone)
        if self.gui:
            self.gui.show_text(zone["display_name"], title="Default Zone")
            self.release_gui_after()

    def release_gui_after(self, seconds=10):
        """Release the gui after a number of seconds."""
        self.schedule_event(self.release_gui, seconds)

    def release_gui(self):
        """Release the gui now."""
        if self.gui:
            self.gui.release()

    @intent_handler("Stop.intent")
    def handle_stop(self, message: Message):
        """Stop playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="stop")

    @intent_handler("Pause.intent")
    def handle_pause(self, message: Message):
        """Pause playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="pause")

    # @intent_handler("Resume.intent")
    def handle_resume(self, message: Message):
        """Resume playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="play")

    @intent_handler("Next.intent")
    def handle_next(self, message: Message):
        """Next playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="next")

    @intent_handler("Prev.intent")
    def handle_prev(self, message: Message):
        """Prev playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self.roon_proxy.playback_control(zone_id, control="previous")

    @intent_handler("Mute.intent")
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
    def handle_volume_increase(self, message: Message):
        """Increase the volume a little bit."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self._step_volume(zone_id, DEFAULT_VOLUME_STEP)
            self.acknowledge()

    @intent_handler("DecreaseVolume.intent")
    def handle_volume_decrease(self, message: Message):
        """Decrease the volume a little bit."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        if zone_id:
            self._step_volume(zone_id, -DEFAULT_VOLUME_STEP)
            self.acknowledge()

    @intent_handler("SetVolumePercent.intent")
    def handle_set_volume_percent(self, message: Message):
        """Set volume to a percentage."""
        if self.roon_not_connected():
            return
        percent = extract_number(message.data["utterance"].replace("%", ""))
        percent = int(percent)
        zone_id = self.get_target_zone_or_output(message)
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
