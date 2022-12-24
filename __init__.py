# roon-skill
# Copyright (C) 2022 Casey Link
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
from mycroft.skills.common_play_skill import CommonPlaySkill, CPSMatchLevel
from mycroft.skills.core import intent_handler, resting_screen_handler
from mycroft.util.parse import extract_number

from .roon_types import (
    RoonSubscriptionEvent,
    RoonAuthSettings,
    RoonApiErrorResponse,
    RoonApiBrowseLoadResponse,
    RoonApiBrowseResponse,
    EVENT_ZONE_CHANGED,
    EVENT_ZONE_SEEK_CHANGED,
    EVENT_OUTPUT_CHANGED,
)
from .const import (
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
from .discovery import InvalidAuth, authenticate
from .roon import RoonCore
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
    default_zone_id: Optional[str] = None
    default_zone_name: Optional[str] = None


class RoonNotAuthorizedError(Exception):
    """Error for when Roon isn't authorized."""


class RoonSkill(CommonPlaySkill):
    """Control roon with your voice."""

    roon: Optional[RoonCore]
    loop: Optional[asyncio.AbstractEventLoop]
    regexes: List[Pattern]
    watched_zone_id: Optional[str]
    watched_artist_image_last_changed_at: Optional[datetime.datetime]
    watched_artist_image_keys: Optional[Set[str]]
    watched_artist_image_change_after_seconds: Optional[int]

    def __init__(self):
        """Init class."""
        super().__init__()
        # We cannot access any existing loop because each Skill runs in it's
        # own thread.
        # So  asyncio.get_event_loop() will not work.
        # Instead we can create a new loop for our Skill's dedicated thread.
        self.roon = None
        self.loop = None
        self.regexes = {}
        self.watched_zone_id = None
        self.watched_artist_image_keys = None
        self.watched_artist_image_last_changed_at = None
        self.watched_artist_image_current = None
        self.watched_artist_image_change_after_seconds = None

    def get_settings(self) -> RoonSkillSettings:
        return cast(RoonSkillSettings, self.settings)

    def get_auth(self) -> Optional[RoonAuthSettings]:
        return self.get_settings().get("auth")

    def set_auth(self, auth: RoonAuthSettings):
        self.settings["auth"] = auth

    def set_auth_waiting(self, waiting: bool) -> None:
        self.settings["auth_waiting"] = waiting

    def is_auth_waiting(self) -> bool:
        return self.settings["auth_waiting"] is True

    def get_default_zone_id(self) -> Optional[str]:
        return self.settings.get(CONF_DEFAULT_ZONE_ID)

    def set_default_zone_id(self, zone_id: str) -> None:
        self.settings[CONF_DEFAULT_ZONE_ID] = zone_id

    def get_default_zone_name(self) -> Optional[str]:
        return self.settings.get(CONF_DEFAULT_ZONE_NAME)

    def set_default_zone_name(self, zone_name: str) -> None:
        self.settings[CONF_DEFAULT_ZONE_NAME] = zone_name

    def initialize(self):
        """Init skill."""
        super().initialize()
        self.log.info("roon init")
        if not self.loop:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
        else:
            self.loop = asyncio.get_running_loop()
        self.cancel_scheduled_event("RoonSettingsUpdate")
        # Setup handlers for playback control messages
        self.add_event("mycroft.audio.service.next", self.handle_next)
        self.add_event("mycroft.audio.service.prev", self.handle_prev)
        self.add_event("mycroft.audio.service.pause", self.handle_pause)
        self.add_event("mycroft.audio.service.resume", self.handle_resume)
        self.add_event("mycroft.audio.service.stop", self.handle_stop)
        self.add_event("mycroft.stop", self.handle_stop)

        self.settings_change_callback = self.handle_settings_change
        if not self.get_auth():
            # if we aren't authorized yet, then update our settings every 3 seconds
            # this is so that if the user authorizes the extension we can react timely
            update_settings_interval = 3
            self.schedule_repeating_event(
                self.handle_settings_change,
                None,
                update_settings_interval,
                name="RoonSettingsUpdate",
            )

        self.handle_settings_change()

    def shutdown(self):
        """Shutdown skill."""
        if self.roon:
            self.roon.disconnect()
        self.cancel_scheduled_event("RoonSettingsUpdate")
        self.cancel_scheduled_event("RoonCoreCache")

    def handle_settings_change(self):
        """Handle websettings change."""
        auth = self.get_auth()
        if self.roon:
            # we are connected!
            self.cancel_scheduled_event("RoonSettingsUpdate")
            self.schedule_repeating_event(
                self.update_library_cache, None, 5 * 60, name="RoonCoreCache"
            )
        elif auth:
            # we have authorization but are not connected
            self.connect_to_roon(auth)
        elif self.is_auth_waiting():
            # we lack authorization
            self.configure_manually(headless=True)

    def connect_to_roon(self, auth: RoonAuthSettings) -> None:
        self.log.info("roon auth %s", auth)
        self.roon = RoonCore(self.log, auth)
        self.schedule_repeating_event(
            self.update_library_cache, None, 5 * 60, name="RoonCoreCache"
        )
        self.update_library_cache()
        self.update_entities()
        self.roon.roon.register_state_callback(self.handle_roon_state_change)
        self.register_resting_screen()

    @intent_handler("ConfigureRoon.intent")
    def handle_configure_roon(self, message: str):
        # pylint: disable=unused-argument
        """Handle configure command."""
        if self.roon or self.get_auth():
            self.speak_dialog("AlreadyConfigured")
            self.set_auth_waiting(False)
            return
        if self.is_auth_waiting():
            self.speak_dialog("AuthorizationWaiting")
            return

        if False:
            # TODO auto discover
            pass
        else:
            self.configure_manually(headless=False)

    def configure_manually(self, headless=True):
        self.log.info("configure_manually headless=%s", headless)
        host = self.settings.get("host").strip()
        port = self.settings.get("port")
        if not host or not port or host == "" or port == 0:
            if not headless:
                self.speak_dialog("InvalidRoonConfig")
            return
        try:
            if not headless:
                self.speak_dialog("AuthorizationManualStarting")
            r = authenticate(self.log, self.loop, host, port, None)
            self.set_auth(r)
            self.log.info("Roon token saved locally: %s", r.get("token"))
            self.speak_dialog("AuthorizationManualSuccess")
        except InvalidAuth:
            self.set_auth_waiting(True)
            if not headless:
                self.speak_dialog("AuthorizationWaiting")

    # @intent_handler("RoonStatus.intent")
    def handle_roon_status(self, message: str):
        # pylint: disable=unused-argument
        """Handle roon status command."""
        if self.is_auth_waiting():
            self.speak_dialog("AuthorizationWaiting")
        elif self.get_auth() and self.get_auth().get("roon_server_name"):
            auth = self.get_auth()
            self.speak_dialog(
                "RoonStatus",
                {
                    "name": auth.get("roon_server_name"),
                    "host": auth.get("host"),
                    "port": auth.get("port"),
                },
            )
        else:
            self.speak_dialog("RoonNotConfigured")

    def handle_roon_state_change(
        self, event: RoonSubscriptionEvent, output_or_zone_ids: List[str]
    ):
        # Warning this log line is very very noisy, only use in development
        # self.log.info("event %s in %s", event, output_or_zone_ids)
        should_update_entities = False
        if event in [EVENT_ZONE_CHANGED, EVENT_ZONE_SEEK_CHANGED]:
            for zone_id in output_or_zone_ids:
                if zone_id not in self.roon.zones:
                    should_update_entities = True
                self.roon.update_zone(zone_id)
                if self.watched_zone_id == zone_id:
                    self.update_watched_zone()
        elif event in [EVENT_OUTPUT_CHANGED]:
            for output_id in output_or_zone_ids:
                if output_id not in self.roon.outputs:
                    should_update_entities = True
                self.roon.update_output(output_id)

        if should_update_entities:
            self.update_entities()

    def update_library_cache(self):
        """Update library cache."""
        if self.roon:
            self.roon.update_cache()
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

        zone_names = [norm(z["display_name"]) for z in self.roon.zones.values()]
        self._write_entity_file("zone_name", zone_names)

        output_names = [norm(z["display_name"]) for z in self.roon.outputs.values()]
        self._write_entity_file("output_name", output_names)
        combined = sorted(set(zone_names + output_names))
        self._write_entity_file("zone_or_output", combined)

    def CPS_match_query_phrase(
        self, utterance
    ) -> Optional[Tuple[str, CPSMatchLevel, Optional[Dict]]]:
        # pylint: disable=arguments-renamed
        """Handle common play framework query.

        This method responds wether the skill can play the input phrase.

         The method is invoked by the PlayBackControlSkill.

         Returns: tuple (matched phrase(str),
                         match level(CPSMatchLevel),
                         optional data(dict))
                  or None if no match was found.
        """
        phrase = utterance
        self.log.info("CPS_match_query_phrase: %s", phrase)
        roon_specified = any(x in phrase for x in ROON_KEYWORDS)
        if not self.playback_prerequisites_ok():
            if roon_specified:
                return phrase, CPSMatchLevel.GENERIC
            return None

        bonus = 0.1 if roon_specified else 0.0
        phrase = re.sub(self.translate_regex("OnRoon"), "", phrase, re.IGNORECASE)
        res = re.search(self.translate_regex("AtZone"), phrase, re.IGNORECASE)
        zone_name = None
        zone_id = None
        if res:
            try:
                zone_name = res.group("zone_or_output")
                self.log.info("Extracted raw %s", zone_name)
                zone_id = self.get_target_zone_or_output(zone_name)
                self.log.info("matched to %s", zone_name)
                phrase = res.group("query")
            except IndexError:
                self.log.info("failed to extract zone")
        self.log.info("utterance: %s", utterance)
        self.log.info("phrase: %s", phrase)
        if zone_id:
            self.log.info("zone_name: %s", zone_name)
            self.log.info("zone_id: %s", self.zone_name(zone_id))
        self.log.info("bonus: %s", bonus)
        data, confidence = self.specific_query(phrase, bonus)
        if not data:
            data, confidence = self.generic_query(phrase, bonus)
        if data:
            self.log.info("Roon Confidence: %s", confidence)
            self.log.info("              data: %s", data)
            if roon_specified:
                level = CPSMatchLevel.EXACT
            else:
                if confidence > 0.9 and phrase in data["title"].lower():
                    level = CPSMatchLevel.MULTI_KEY
                if confidence > 0.9:
                    level = CPSMatchLevel.TITLE
                elif confidence < 0.5:
                    level = CPSMatchLevel.GENERIC
                else:
                    level = CPSMatchLevel.TITLE
                phrase += " on roon"
            self.log.info("Matched %s with level %s to %s", phrase, level, data)
            data["mycroft"]["zone_id"] = zone_id
            return phrase, level, data
        self.log.info("Couldn't find anything on Roon")
        return None

    def specific_query(self, phrase, bonus):
        """
        Check if the phrase can be matched against a specific roon request.

        This includes asking for radio, playlists, albums, artists, or tracks.
        Arguments:
            phrase (str): Text to match against
            bonus (float): Any existing match bonus
        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        for item_type in [
            TYPE_STATION,
            TYPE_PLAYLIST,
            TYPE_TAG,
            TYPE_ARTIST,
            TYPE_ALBUM,
        ]:
            match = re.match(self.translate_regex(item_type), phrase, re.IGNORECASE)
            if match:
                extracted = match.groupdict()[item_type]
                self.log.info("%s extracted: %s match: %s", item_type, extracted, match)
                return self.query_type(item_type, extracted, bonus)

        # Check genres
        match = re.match(self.translate_regex("genre1"), phrase, re.IGNORECASE)
        if not match:
            match = re.match(self.translate_regex("genre2"), phrase, re.IGNORECASE)
        self.log.info("genre match: %s", match)
        if match:
            genre = match.groupdict()["genre"]
            return self.query_type(TYPE_GENRE, genre, bonus)

        return NOTHING_FOUND

    def generic_query(self, phrase, bonus):
        """Search roon and return the top result

        Arguments:
            phrase (str): Text to match against
            bonus (float): Any existing match bonus
        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        self.log.info('Handling "%s" as a generic query...', phrase)

        d, c = self.roon.search("generic_search", phrase)
        return d, bonus + c

    def translate_regex(self, regex):
        """Translate the given regex."""
        if regex not in self.regexes:
            path = self.find_resource(regex + ".regex")
            if path:
                with open(path, "r", encoding="utf-8") as f:
                    string = f.read().strip()
                self.regexes[regex] = string
        return self.regexes[regex]

    def query_type(self, item_type, query, bonus) -> Tuple[dict, float]:
        """Try and find a specific item type."""
        bonus += 1
        data, confidence = self.roon.search_type(query, item_type)
        confidence = min(confidence + bonus, 1.0)
        return data, confidence

    def CPS_start(self, phrase, data):
        """Handle common play framework start.

        Starts playback of the given item.
        """
        self.log.info("CPS_start: %s %s", phrase, data)
        if self.roon_not_connected():
            raise RoonNotAuthorizedError()

        zone_id = data["mycroft"].get("zone_id")
        if not zone_id:
            self.speak_dialog("NoDefaultZone")
            return
        if "path" in data["mycroft"]:
            r = self.roon.play_path(zone_id, data["mycroft"]["path"])
        elif "session_key" in data["mycroft"]:
            r = self.roon.play_search_result(
                zone_id, data["item_key"], data["mycroft"]["session_key"]
            )
        else:
            r = None
        if not self.is_success(r):
            self.speak_playback_error(phrase, data, r)
            self.log.error("Could not play %s from %s. Response %s", phrase, data, r)
            return

        zone_name = self.zone_name(zone_id)
        data["zone_name"] = zone_name
        media_type = data["mycroft"].get("type")
        if media_type:
            if media_type == TYPE_STATION:
                self.speak_dialog("ListeningToStation", data)
            elif media_type == TYPE_ALBUM:
                self.speak_dialog("ListeningToAlbum", data)
            elif media_type == TYPE_ARTIST:
                self.speak_dialog("ListeningToAlbum", data)
        else:
            self.speak_dialog("ListeningTo", {"phrase": phrase, "zone_name": zone_name})
        self.log.info("Started playback of %s at zone %s", data["title"], zone_name)

    def speak_playback_error(
        self,
        phrase: str,
        data: Dict[str, Any],
        roon_response: Union[str | Dict[str, Any]],
    ) -> None:
        # pylint: disable=unused-argument
        if isinstance(roon_response, str):
            if "ZoneNotFound" in roon_response:
                zone_id = data["mycroft"].get("zone_id")
                if zone_id:
                    zone_name = self.zone_name(zone_id)
                    self.speak_dialog("ZoneNotFound-named", {"zone_name": zone_name})
                    return
                self.speak_dialog("ZoneNotFound")
                return
        elif isinstance(roon_response, dict):
            if "message" in roon_response:
                self.speak(roon_response["message"])
                return

        self.speak_dialog("PlaybackFailed", data)

    def playback_prerequisites_ok(self):
        """Check if the playback prereqs are met."""
        return not self.roon_not_connected()

    @intent_handler(
        IntentBuilder("GetDefaultZone")
        .optionally("Roon")
        .require("List")
        .require("Default")
        .require("Zone")
    )
    def handle_get_default_zone(self, message):
        # pylint: disable=unused-argument
        """Handle get default zone command."""
        zone_id = self.get_default_zone_id()
        if zone_id:
            zone = self.roon.zones[zone_id]
            self.speak_dialog("DefaultZone", zone)
        else:
            self.speak_dialog("NoDefaultZone")

    def converse(self, message: Optional[str] = None) -> bool:
        # pylint: disable=unused-argument
        return False

    @intent_handler(
        IntentBuilder("ListZones").optionally("Roon").require("List").require("Zone")
    )
    def list_zones(self, message):
        # pylint: disable=unused-argument
        """List available zones."""
        if self.roon_not_connected():
            return
        zones = self.roon.zones
        if len(zones) == 0:
            self.speak_dialog("NoZonesAvailable")
            return
        zone_names = [o["display_name"] for id, o in zones.items()]
        zone_names.sort()
        if len(zones) == 1:
            self.speak(zone_names[0])
        else:
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
    def list_outputs(self, message):
        # pylint: disable=unused-argument
        """List available devices."""
        if self.roon_not_connected():
            return
        outputs = self.roon.outputs
        if len(outputs) == 0:
            self.speak_dialog("NoOutputsAvailable")
            return
        output_names = [o["display_name"] for id, o in outputs.items()]
        output_names.sort()
        if len(outputs) == 1:
            self.speak(output_names[0])
        else:
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
    def handle_set_default_zone(self, message):
        """Handle set default zone command."""
        zone_name = message.data.get("SetZone")
        zone, conf = match_one(zone_name, self.roon.zones.values(), "display_name")
        self.log.info("zone %s conf %s", zone, conf)
        self.set_default_zone_id(zone["zone_id"])
        self.set_default_zone_name(zone["display_name"])
        self.speak_dialog("DefaultZoneConfirm", zone)
        self.gui.show_text(zone["display_name"], title="Default Zone")
        self.release_gui_after()

    def roon_not_connected(self):
        """Check if the skill is not connected to Roon."""
        if not self.roon:
            self.speak_dialog("RoonNotConfigured")
            return True
        return False

    def release_gui_after(self, seconds=10):
        """Release the gui after a number of seconds."""
        self.schedule_event(self.release_gui, seconds)

    def release_gui(self):
        """Release the gui now."""
        self.gui.release()

    @intent_handler("Stop.intent")
    def handle_stop(self, message):
        """Stop playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        self.roon.playback_control(zone_id, control="stop")

    @intent_handler("Pause.intent")
    def handle_pause(self, message):
        """Pause playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        self.roon.playback_control(zone_id, control="pause")

    @intent_handler("Resume.intent")
    def handle_resume(self, message):
        """Resume playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        self.roon.playback_control(zone_id, control="play")

    @intent_handler("Next.intent")
    def handle_next(self, message):
        """Next playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        self.roon.playback_control(zone_id, control="next")

    @intent_handler("Prev.intent")
    def handle_prev(self, message):
        """Prev playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        self.roon.playback_control(zone_id, control="previous")

    @intent_handler("Mute.intent")
    def handle_mute(self, message):
        """Mute playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        for output in self.outputs_for_zones(zone_id):
            r = self.roon.mute(output["output_id"], mute=True)
            self.log.info(
                "muting %s %s %s", output["display_name"], r, output["output_id"]
            )

    @intent_handler("Unmute.intent")
    def handle_unmute(self, message):
        """Unmute playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        for output in self.outputs_for_zones(zone_id):
            r = self.roon.mute(output["output_id"], mute=False)
            self.log.info(
                "unmuting %s %s %s", output["display_name"], r, output["output_id"]
            )

    @intent_handler("IncreaseVolume.intent")
    def handle_volume_increase(self, message):
        """Increase the volume a little bit."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        self._step_volume(zone_id, DEFAULT_VOLUME_STEP)
        self.acknowledge()

    @intent_handler("DecreaseVolume.intent")
    def handle_volume_decrease(self, message):
        """Decrease the volume a little bit."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        self._step_volume(zone_id, -DEFAULT_VOLUME_STEP)
        self.acknowledge()

    @intent_handler("SetVolumePercent.intent")
    def handle_set_volume_percent(self, message):
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
        if zone_or_output_id in self.roon.zones:
            for output in self.outputs_for_zones(zone_or_output_id):
                outputs.append(output)
        elif zone_or_output_id in self.roon.outputs:
            outputs.append(self.roon.outputs[zone_or_output_id])

        for output in outputs:
            r = self.roon.change_volume(
                output["output_id"], step, method="relative_step"
            )
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
        if zone_or_output_id in self.roon.zones:
            for output in self.outputs_for_zones(zone_or_output_id):
                outputs.append(output)
        elif zone_or_output_id in self.roon.outputs:
            outputs.append(self.roon.outputs[zone_or_output_id])

        for output in outputs:
            r = self.roon.change_volume(output["output_id"], percent, method="absolute")
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
        self.roon.shuffle(zone_id, True)

    @intent_handler("ShuffleOff.intent")
    def handle_shuffle_off(self, message):
        """Turn shuffle off."""
        zone_id = self.get_target_zone_or_output(message)
        self.roon.shuffle(zone_id, False)

    @intent_handler("RepeatTrackOn.intent")
    def handle_repeat_one_on(self, message):
        """Turn repeat one on."""
        zone_id = self.get_target_zone_or_output(message)
        r = self.roon.repeat(zone_id, "loop_one")
        if self.is_success(r):
            self.acknowledge()

    @intent_handler("RepeatOff.intent")
    def handle_repeat_off(self, message):
        """Turn repeat off."""
        zone_id = self.get_target_zone_or_output(message)
        r = self.roon.repeat(zone_id, "disabled")
        if self.is_success(r):
            self.acknowledge()

    @intent_handler("WhatIsPlaying.intent")
    def handle_what_is_playing(self, message):
        zone_id = self.get_target_zone_or_output(message)
        now_playing = self.roon.now_playing_for(zone_id)
        if not now_playing:
            self.log.info("no now playing for %s", zone_id)
            self.speak_dialog("ZoneUnavailable")
            return
        self.log.info("what is playing in %s? %s", self.zone_name(zone_id), now_playing)
        line1 = now_playing.get("two_line").get("line1")
        line2 = now_playing.get("two_line").get("line2")

        if len(line1) > 0 and len(line2) > 0:
            self.speak_dialog("WhatIsPlayingReply-2", {"line1": line1, "line2": line2})
        elif len(line1) > 0:
            self.speak_dialog("WhatIsPlayingReply", {"line1": line1})
        self.show_player(zone_id)

    def get_target_zone(self, message: Union[str, Dict[str, Any]]) -> Optional[str]:
        """Get the target zone id from a user's query."""
        if isinstance(message, str):
            zone_name = message
        else:
            zone_name = message.data.get("zone_or_output")
        zones = list(self.roon.zones.values())
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

    def get_target_output(self, message: Union[str, Dict[str, Any]]) -> Optional[str]:
        """Get the target output id from a user's query."""
        if isinstance(message, str):
            output_name = message
        else:
            output_name = message.data.get("zone_or_output")
        outputs = list(self.roon.outputs.values())
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

    def get_target_zone_or_output(
        self, message: Union[str, Dict[str, Any]]
    ) -> Optional[str]:
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
        return self.roon.zones[zone_id]["outputs"]

    def zone_name(self, zone_id):
        """Get the zone name."""
        zone = self.roon.zones.get(zone_id)
        if not zone:
            return None
        return zone.get("display_name")

    def is_success(
        self, roon_response: Union[RoonApiErrorResponse, Dict[str, Any], str]
    ):
        """Check if a roon response was successful."""
        if isinstance(roon_response, RoonApiErrorResponse):
            return False
        if isinstance(roon_response, RoonApiBrowseResponse):
            return True
        if isinstance(roon_response, RoonApiBrowseLoadResponse):
            return True
        if isinstance(roon_response, str):
            return "Success" in roon_response
        if isinstance(roon_response, dict):
            return "is_error" not in roon_response
        return True

    def format_duration(self, seconds: int) -> str:
        if seconds is None:
            return ""
        minutes = seconds // 60
        hours = minutes // 60
        if hours == 0:
            return "%02d:%02d" % (minutes, seconds % 60)
        else:
            return "%02d:%02d:%02d" % (hours, minutes % 60, seconds % 60)

    def show_player(self, zone_id: str) -> None:
        if not self.gui.connected:
            return

        self.gui.clear()
        self.clear_gui_info()
        self.set_watched_zone(zone_id)
        if self.update_watched_zone():
            self.gui.show_page("AudioPlayer.qml", override_idle=True)

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
        now_playing = self.roon.now_playing_for(self.watched_zone_id)
        # self.log.info("showing player %s", now_playing)
        if now_playing is None:
            return False
        self.gui["line1"] = now_playing["two_line"]["line1"]
        self.gui["line2"] = now_playing["two_line"]["line2"]
        current_pos_seconds = now_playing["seek_position"]
        duration_seconds = now_playing.get("length")
        if current_pos_seconds and duration_seconds:
            self.gui["currentPosition"] = self.format_duration(current_pos_seconds)
            self.gui["duration"] = self.format_duration(duration_seconds)
            self.gui["progressValue"] = current_pos_seconds / duration_seconds
            self.gui["hasProgress"] = True
        else:
            self.gui["hasProgress"] = False

        album_cover_image_key = now_playing.get("image_key")
        if album_cover_image_key:
            self.gui["albumCoverUrl"] = self.roon.get_image(album_cover_image_key)
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
                if (
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
                self.gui["artistImageUrl"] = self.roon.get_image(artist_image_key)
                self.gui["artistBlurredImageUrl"] = self.roon.get_image(
                    artist_image_key
                )
                self.gui["artistBlurredImageDim"] = False
                self.watched_artist_image_current = artist_image_key
                self.watched_artist_image_last_changed_at = datetime.datetime.now()
                self.watched_artist_image_change_after_seconds = random.randint(60, 120)
        else:
            self.gui["hasArtistImage"] = False
            if album_cover_image_key:
                self.gui["artistBlurredImageUrl"] = self.roon.get_image(
                    album_cover_image_key
                )
                self.gui["hasArtistBlurredImage"] = True
                self.gui["artistBlurredImageDim"] = True
            else:
                self.gui["hasArtistBlurredImage"] = False
        return True

    def get_display_url(self) -> Optional[str]:
        if self.roon_not_connected():
            self.log.info("not connected")
            return None
        auth = self.get_auth()
        if not auth:
            self.log.info("no auth")
            return None
        host = auth.get("host")
        return "http://{}:{}/display/".format(host, 9330)

    def clear_gui_info(self):
        """Clear the gui variable list."""
        for k in STATUS_KEYS:
            self.gui[k] = ""

    @resting_screen_handler("RoonNowPlaying")
    def handle_idle(self, message: str) -> None:
        # pylint: disable=unused-argument
        url = self.get_display_url()
        if url:
            self.gui.clear()
            self.gui.show_url(url)


def create_skill():
    """Create the Roon Skill."""
    return RoonSkill()
