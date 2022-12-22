"""Roon Skill."""
import asyncio
from typing import Optional, Literal, Tuple, Dict, Any, Union
import re
import os
import datetime
from mycroft.skills.core import intent_handler
from mycroft.util.parse import extract_number
from adapt.intent import IntentBuilder
from mycroft.skills.common_play_skill import CommonPlaySkill, CPSMatchLevel

from roonapi import RoonApi
from roonapi.constants import SERVICE_TRANSPORT

from .discovery import discover, authenticate, InvalidAuth
from .const import (
    DEFAULT_VOLUME_STEP,
    ROON_APPINFO,
    ROON_KEYWORDS,
    TYPE_GENRE,
    TYPE_TAG,
    TYPE_PLAYLIST,
    TYPE_ARTIST,
    TYPE_ALBUM,
    TYPE_STATION,
    CONF_DEFAULT_ZONE_NAME,
    CONF_DEFAULT_ZONE_ID,
    NOTHING_FOUND,
    DIRECT_RESPONSE_CONFIDENCE,
    MATCH_CONFIDENCE,
)
from .library import RoonLibrary
from .util import match_one


class RoonNotAuthorizedError(Exception):
    """Error for when Roon isn't authorized."""

    pass


class RoonSkill(CommonPlaySkill):
    """Control roon with your voice."""

    library: Optional[RoonLibrary]
    roon: Optional[RoonApi]
    loop: Optional[asyncio.AbstractEventLoop]

    def __init__(self):
        """Init class."""
        super(RoonSkill, self).__init__()
        # We cannot access any existing loop because each Skill runs in it's
        # own thread.
        # So  asyncio.get_event_loop() will not work.
        # Instead we can create a new loop for our Skill's dedicated thread.
        self.roon = None
        self.library = None
        self.loop = None
        self.regexes = {}

    def initialize(self):
        """Init skill."""
        super().initialize()
        self.log.info("roon init")
        if not self.loop:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
        else:
            self.loop = asyncio.get_running_loop()
        self.cancel_scheduled_event("RoonLogin")
        # Setup handlers for playback control messages
        self.add_event("mycroft.audio.service.next", self.handle_next)
        self.add_event("mycroft.audio.service.prev", self.handle_prev)
        self.add_event("mycroft.audio.service.pause", self.handle_pause)
        self.add_event("mycroft.audio.service.resume", self.handle_resume)
        self.add_event("mycroft.audio.service.stop", self.handle_stop)
        self.add_event("mycroft.stop", self.handle_stop)

        self.settings_change_callback = self.on_websettings_changed
        # Retry in 5 minutes
        self.schedule_repeating_event(
            self.on_websettings_changed, None, 5 * 60, name="RoonLogin"
        )
        self.on_websettings_changed()

    def shutdown(self):
        """Shutdown skill."""
        self.cancel_scheduled_event("RoonLogin")
        if self.roon:
            self.roon.stop()

    def on_websettings_changed(self):
        """Handle websettings change."""
        auth = self.settings.get("auth")
        if self.roon:
            self.cancel_scheduled_event("RoonLogin")
            self.schedule_repeating_event(
                self.update_library_cache, None, 5 * 60, name="RoonLibraryCache"
            )

            # Refresh saved tracks
            # We can't get this list when the user asks because it takes
            # too long and causes
            # mycroft-playback-control.mycroftai:PlayQueryTimeout
            # self.refresh_saved_tracks()

        elif auth:
            self.roon = RoonApi(
                ROON_APPINFO,
                auth["token"],
                auth["host"],
                auth["port"],
                blocking_init=True,
            )
            self.schedule_repeating_event(
                self.update_library_cache, None, 5 * 60, name="RoonLibraryCache"
            )
            self.library = RoonLibrary(self.roon, self.log)
            self.update_library_cache()
            self.update_entities()

    def update_library_cache(self):
        """Update library cache."""
        if self.library:
            self.library.update_cache(self.roon)
            self.update_entities()

    def _write_entity_file(self, name, data):
        with open(
            os.path.join(self.root_dir, "locale", self.lang, f"{name}.entity"), "w+"
        ) as f:
            f.write("\n".join(data))
        self.register_entity_file(f"{name}.entity")

    def update_entities(self):
        """Update locale entity files."""

        def norm(s):
            return s.lower().replace("’", "'")

        zone_names = [norm(z["display_name"]) for z in self.library.zones.values()]
        self._write_entity_file("zone_name", zone_names)

        output_names = [norm(z["display_name"]) for z in self.library.outputs.values()]
        self._write_entity_file("output_name", output_names)
        combined = sorted(set(zone_names + output_names))
        self._write_entity_file("zone_or_output", combined)

    def CPS_match_query_phrase(
        self, utterance
    ) -> Optional[Tuple[str, CPSMatchLevel, Optional[Dict]]]:
        """Handle common play framework query.

        This method responds wether the skill can play the input phrase.

         The method is invoked by the PlayBackControlSkill.

         Returns: tuple (matched phrase(str),
                         match level(CPSMatchLevel),
                         optional data(dict))
                  or None if no match was found.
        """
        phrase = utterance
        self.log.info("CPS_match_query_phrase: {}".format(phrase))
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
                self.log.info(f"Extracted raw {zone_name}")
                zone_id = self.get_target_zone_or_output(zone_name)
                self.log.info(f"matched to  {zone_name}")
                phrase = res.group("query")
            except IndexError:
                self.log.info("failed to extract zone")
        self.log.info(f"utterance: {utterance}")
        self.log.info(f"phrase: {phrase}")
        if zone_id:
            self.log.info(f"zone_name: {zone_name}".format(zone_name))
            self.log.info(f"zone_id: {self.zone_name(zone_id)}")
        self.log.info("bonus: {}".format(bonus))
        data, confidence = self.specific_query(phrase, bonus)
        if not data:
            data, confidence = self.generic_query(phrase, bonus)
        if data:
            self.log.info(f"Roon Confidence: {confidence}")
            self.log.info(f"              data: {data}")
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
            self.log.info(f"Matched {phrase} with level {level} to {data}")
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
            self.log.info(f"{item_type} match: {match}")
            if match:
                return self.query_type(item_type, match.groupdict()[item_type], bonus)

        # Check genres
        match = re.match(self.translate_regex("genre1"), phrase, re.IGNORECASE)
        if not match:
            match = re.match(self.translate_regex("genre2"), phrase, re.IGNORECASE)
        self.log.info("genre match: {}".format(match))
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
        self.log.info('Handling "{}" as a generic query...'.format(phrase))

        d, c = self.library.search("generic_search", phrase)
        return d, bonus + c

    def translate_regex(self, regex):
        """Translate the given regex."""
        if regex not in self.regexes:
            path = self.find_resource(regex + ".regex")
            if path:
                with open(path) as f:
                    string = f.read().strip()
                self.regexes[regex] = string
        return self.regexes[regex]

    def query_type(self, item_type, query, bonus) -> Tuple[dict, float]:
        """Try and find a specific item type."""
        bonus += 1
        data, confidence = self.library.search_type(query, item_type)
        confidence = min(confidence + bonus, 1.0)
        return data, confidence

    def CPS_start(self, phrase, data):
        """Handle common play framework start.

        Starts playback of the given item.
        """
        self.log.info("CPS_start: {} {}".format(phrase, data))
        if self.roon_not_connected():
            raise RoonNotAuthorizedError()

        zone_id = data["mycroft"].get("zone_id")
        if not zone_id:
            self.speak_dialog("NoDefaultZone")
            return
        if "path" in data["mycroft"]:
            r = self.library.play_path(zone_id, data["mycroft"]["path"])
        elif "session_key" in data["mycroft"]:
            r = self.library.play_search_result(
                zone_id, data["item_key"], data["mycroft"]["session_key"]
            )
        else:
            r = None
        if not self.is_success(r):
            self.speak_playback_error(phrase, data, r)
            self.log.error(f"Could not play {phrase} from {data}. Got response {r}")
            return

        self.acknowledge()
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
        self.log.info(f"Started playback of {data['title']} at zone {zone_name}")

    def speak_playback_error(
        self,
        phrase: str,
        data: Dict[str, Any],
        roon_response: Union[str | Dict[str, Any]],
    ) -> None:
        if isinstance(roon_response, str):
            if "ZoneNotFound" in roon_response:
                zone_id = data["mycroft"].get("zone_id")
                if zone_id:
                    zone_name = self.zone_name(zone_id)
                    self.speak_dialog("ZoneNotFound-named", {"zone_name": zone_name})
                    return
                else:
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

    @intent_handler("ConfigureRoon.intent")
    def handle_configure_roon(self, message):
        """Handle configure command."""
        if self.roon or self.settings.get("auth"):
            self.speak_dialog("AlreadyConfigured")
            self.settings["auth_waiting"] = False
            return
        if self.settings.get("auth_waiting"):
            self.speak_dialog("AuthorizationWaiting")
            self.speak_dialog("AuthorizationRequired")
            return
        host = self.settings.get("host")
        port = self.settings.get("port")
        if not host and not port:
            self.speak_dialog("InvalidRoonConfig")
            return
        try:
            self.speak_dialog("AuthorizationRequired")
            self.settings["auth_waiting"] = True
            r = authenticate(self.log, self.loop, host, port, None)
            self.settings["auth_waiting"] = False
            self.settings["auth"] = r
            self.log.info("Roon token saved locally: {}".format(r.get("token")))
        except InvalidAuth:
            self.speak_dialog("AuthorizationFailed")
        finally:
            self.settings["auth_waiting"] = True

    @intent_handler("RoonStatus.intent")
    def handle_roon_status(self, message):
        """Handle roon status command."""
        if self.settings.get("auth_waiting"):
            self.speak_dialog("AuthorizationWaiting")
            self.speak_dialog("AuthorizationRequired")
        elif self.settings.get("auth").get("roon_server_name"):
            auth = self.settings.get("auth")
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

    @intent_handler(
        IntentBuilder("GetDefaultZone")
        .optionally("Roon")
        .require("List")
        .require("Default")
        .require("Zone")
    )
    def handle_get_default_zone(self, message):
        """Handle get default zone command."""
        zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        if zone_id:
            zone = self.roon.zones[zone_id]
            self.speak_dialog("DefaultZone", zone)
        else:
            self.speak_dialog("NoDefaultZone")

    def converse(self, message: str):
        return False

    @intent_handler(
        IntentBuilder("ListZones").optionally("Roon").require("List").require("Zone")
    )
    def list_zones(self, message):
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
        self.log.info("zone {} conf {}".format(zone, conf))
        self.settings[CONF_DEFAULT_ZONE_ID] = zone["zone_id"]
        self.settings[CONF_DEFAULT_ZONE_NAME] = zone["display_name"]
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
            self.log.info(f"muting {output['display_name']} {r} {output['output_id']}")

    @intent_handler("Unmute.intent")
    def handle_unmute(self, message):
        """Unmute playback."""
        if self.roon_not_connected():
            return
        zone_id = self.get_target_zone_or_output(message)
        for output in self.outputs_for_zones(zone_id):
            r = self.roon.mute(output["output_id"], mute=False)
            self.log.info(
                f"unmuting {output['display_name']} {r} {output['output_id']}"
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
        if zone_or_output_id in self.library.zones:
            for output in self.outputs_for_zones(zone_or_output_id):
                outputs.append(output)
        elif zone_or_output_id in self.library.outputs:
            outputs.append(self.library.outputs[zone_or_output_id])

        for output in outputs:
            r = self.roon.change_volume(
                output["output_id"], step, method="relative_step"
            )
            self.log.info(
                f"changing vol={step} {output['display_name']} {r} {output['output_id']}"
            )

    def _set_volume(self, zone_or_output_id, percent):
        """Set volume to a percentage."""
        outputs = []
        if zone_or_output_id in self.library.zones:
            for output in self.outputs_for_zones(zone_or_output_id):
                outputs.append(output)
        elif zone_or_output_id in self.library.outputs:
            outputs.append(self.library.outputs[zone_or_output_id])

        for output in outputs:
            r = self.roon.change_volume(output["output_id"], percent)
            self.log.info(
                f"changing vol={percent} {output['display_name']} {r} {output['output_id']}"
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
        r = self._set_repeat(zone_id, "loop_one")
        if self.is_success(r):
            self.acknowledge()

    @intent_handler("RepeatOff.intent")
    def handle_repeat_off(self):
        """Turn repeat off."""
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        r = self._set_repeat(default_zone_id, "disabled")
        if self.is_success(r):
            self.acknowledge()

    def _set_repeat(
        self, zone_or_output_id, loop: Literal["loop", "loop_one", "disabled"]
    ):
        data = {"zone_or_output_id": zone_or_output_id, "loop": loop}
        return self.roon._request(SERVICE_TRANSPORT + "/change_settings", data)

    def get_target_zone(self, message: Union[str, Dict[str, Any]]) -> Optional[str]:
        """Get the target zone id from a user's query."""
        if isinstance(message, str):
            zone_name = message
        else:
            zone_name = message.data.get("zone_or_output")
        zones = list(self.library.zones.values())
        zone, confidence = match_one(zone_name, zones, "display_name")
        if confidence < 0.6:
            return None
        if zone:
            self.log.info(
                f"extracting target zone from {zone_name}. Found {zone['display_name']}"
            )
            return zone["zone_id"]

    def get_target_output(self, message: Union[str, Dict[str, Any]]) -> Optional[str]:
        """Get the target output id from a user's query."""
        if isinstance(message, str):
            output_name = message
        else:
            output_name = message.data.get("zone_or_output")
        outputs = list(self.library.outputs.values())
        output, confidence = match_one(output_name, outputs, "display_name")
        if confidence < 0.6:
            return None
        if output:
            self.log.info(
                f"extracting target output from {output_name}. Found {output['display_name']}"
            )
            return output["output_id"]

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

        return self.settings.get(CONF_DEFAULT_ZONE_ID)

    def outputs_for_zones(self, zone_id):
        """Get the outputs for a zone."""
        return self.library.zones[zone_id]["outputs"]

    def zone_name(self, zone_id):
        """Get the zone name."""
        zone = self.library.zones.get(zone_id)
        if not zone:
            return None
        return zone.get("display_name")

    def is_success(self, roon_response):
        """Check if a roon response was successful."""
        if isinstance(roon_response, str):
            return "Success" in roon_response
        if isinstance(roon_response, dict):
            return "is_error" not in roon_response
        return False


def create_skill():
    """Create the Roon Skill."""
    return RoonSkill()
