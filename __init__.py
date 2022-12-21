import asyncio
from typing import Optional, Literal
import re
import datetime
from mycroft.skills.core import intent_handler
from mycroft.util.parse import extract_number
from adapt.intent import IntentBuilder
from mycroft.skills.common_play_skill import CommonPlaySkill, CPSMatchLevel

from roonapi import RoonApi
from roonapi.constants import SERVICE_TRANSPORT

from .discovery import discover, authenticate, InvalidAuth
from .const import ROON_APPINFO, ROON_KEYWORDS, TYPE_STATION, CONF_DEFAULT_ZONE_NAME, CONF_DEFAULT_ZONE_ID, NOTHING_FOUND
from .library import RoonLibrary
from .util import match_one

# Confidence levels for generic play handling
DIRECT_RESPONSE_CONFIDENCE = 0.8

MATCH_CONFIDENCE = 0.5

LIBRARY_CACHE_UPDATE_MIN_INTERVAL_MINUTES = 2

class RoonSkill(CommonPlaySkill):
    """Roon control"""
    library: Optional[RoonLibrary]

    def __init__(self):
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
        super().initialize()
        self.log.info("roon init")
        if not self.loop:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
        else:
            self.loop = asyncio.get_running_loop()
        self.cancel_scheduled_event("RoonLogin")
        # Setup handlers for playback control messages
        # self.add_event("mycroft.audio.service.next", self.next_track)
        # self.add_event("mycroft.audio.service.prev", self.prev_track)
        # self.add_event("mycroft.audio.service.pause", self.pause)
        # self.add_event("mycroft.audio.service.resume", self.resume)
        self.settings_change_callback = self.on_websettings_changed
        # Retry in 5 minutes
        self.schedule_repeating_event(
            self.on_websettings_changed, None, 5 * 60, name="RoonLogin"
        )
        self.on_websettings_changed()

    def shutdown(self):
        self.cancel_scheduled_event("RoonLogin")
        if self.roon:
            self.roon.stop()

    def on_websettings_changed(self):
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

    def update_library_cache(self):
        if self.library:
            self.library.update_cache(self.roon)

    def CPS_match_query_phrase(self, phrase):
        """Handler for common play framework query. Checks if the skill can play the utterance"""
        self.log.info("CPS_match_query_phrase: {}".format(phrase))
        roon_specified = any(x in phrase for x in ROON_KEYWORDS)
        if not self.playback_prerequisites_ok():
            if roon_specified:
                return phrase, CPSMatchLevel.GENERIC
            else:
                return None

        bonus = 0.1 if roon_specified else 0.0
        phrase = re.sub(self.translate_regex("on_roon"), "", phrase, re.IGNORECASE)
        self.log.info("phrase: {}".format(phrase))
        self.log.info("bonus: {}".format(bonus))
        data, confidence = self.continue_playback(phrase, bonus)
        if not data:
            data, confidence = self.specific_query(phrase, bonus)
            if not data:
                data, confidence = self.generic_query(phrase, bonus)
        if data:
            self.log.info("Roon Confidence: {}".format(confidence))
            self.log.info('              data: {}'.format(data))
            if roon_specified:
                level = CPSMatchLevel.EXACT
            else:
                if confidence > 0.9:
                    level = CPSMatchLevel.TITLE
                elif confidence < 0.5:
                    level = CPSMatchLevel.GENERIC
                else:
                    level = CPSMatchLevel.TITLE
                phrase += " on roon"
            self.log.info("Matched {} with level {} to {}".format(phrase, level, data))
            return phrase, level, data
        else:
            self.log.info("Couldn't find anything on Roon")

    def continue_playback(self, phrase, bonus):
        if phrase.strip() == 'roon':
            return (1.0,
                    {
                        'data': None,
                        'name': None,
                        'type': 'continue'
                    })
        else:
            return NOTHING_FOUND

    def specific_query(self, phrase, bonus):
        """
        Check if the phrase can be matched against a specific roon request.
        This includes asking for radio, playlists, albums, artists, or tracks.
        Arguments:
            phrase (str): Text to match against
            bonus (float): Any existing match bonus
        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        match = re.match(self.translate_regex("radio"), phrase,
                         re.IGNORECASE)
        self.log.info("match: {}".format(match))
        if match:
            return self.query_radio(match.groupdict()["station"])
        return NOTHING_FOUND

    def generic_query(self, phrase, bonus):
        """Check for a generic query, not asking for any special feature.
        This will try to parse the entire phrase in the following order
        - As a user playlist
        - As an album
        - As a track
        - As a public playlist
        Arguments:
            phrase (str): Text to match against
            bonus (float): Any existing match bonus
        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        self.log.info('Handling "{}" as a generic query...'.format(phrase))
        return NOTHING_FOUND

    def translate_regex(self, regex):
        if regex not in self.regexes:
            path = self.find_resource(regex + '.regex')
            if path:
                with open(path) as f:
                    string = f.read().strip()
                self.regexes[regex] = string
        return self.regexes[regex]

    def query_radio(self, station):
        """Try to find a radio station.
        Arguments:
          station (str): station to search for
        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        probs = self.library.search_stations(station)
        self.log.info("probs: {}".format(probs))
        return probs


    def CPS_start(self, phrase, data):
        """Handler for common play framework start. Starts playback"""
        self.log.info("CPS_start: {} {}".format(phrase, data))
        if self.roon_not_connected():
            raise RoonNotAuthorizedError()
        type = data["mycroft"]["type"]
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        if not default_zone_id:
            self.speak_dialog("NoDefaultZone")
            return
        r = self.roon.play_media(default_zone_id, data["mycroft"]["path"])
        self.log.info("zone_id: {} result: {}".format(default_zone_id, r))
        if r == True:
            self.acknowledge()

        zone_name = {"zone_name": self.settings.get(CONF_DEFAULT_ZONE_NAME)}
        if data["mycroft"]["type"] == TYPE_STATION:
            self.speak_dialog("ListeningToStation", data|zone_name)

    def playback_prerequisites_ok(self):
        return not self.roon_not_connected()

    def handle_stop(self, message):
        self.bus.emit(message.reply("mycroft.stop"))

    @intent_handler("ConfigureRoon.intent")
    def configure_roon(self, message):
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
    def roon_status(self, message):
        if self.settings.get("auth_waiting"):
            self.speak_dialog("AuthorizationWaiting")
            self.speak_dialog("AuthorizationRequired")
        elif self.settings.get("auth").get("roon_server_name"):
            auth = self.settings.get("auth")
            self.log.info(auth)
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

    @intent_handler(IntentBuilder("GetDefaultZone").optionally("Roon").require("List").require("Default").require("Zone"))
    def get_default_zone(self, message):
        zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        if zone_id:
            zone = self.roon.zones[zone_id]
            self.speak_dialog("DefaultZone", zone)
        else:
            self.speak_dialog("NoDefaultZone")

    @intent_handler(IntentBuilder("ListZones").optionally("Roon").require("List").require("Zone"))
    def list_zones(self, message):
        """List available zones"""
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

    @intent_handler(IntentBuilder("ListOutputs").optionally("Roon").require("List").require("Device"))
    def list_outputs(self, message):
        """List available devices"""
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

    @intent_handler(IntentBuilder("SetDefaultZone").optionally("Roon").require("Set").require("SetZone"))
    def set_default_zone(self, message):
        zone_name = message.data.get("SetZone")
        zone, conf = match_one(zone_name, self.roon.zones.values(), "display_name")
        self.log.info("zone {} conf {}".format(zone, conf))
        self.settings[CONF_DEFAULT_ZONE_ID] = zone["zone_id"]
        self.settings[CONF_DEFAULT_ZONE_NAME] = zone["display_name"]
        self.speak_dialog("DefaultZoneConfirm", zone)
        self.gui.show_text(zone["display_name"], title="Default Zone")
        self.release_gui_after()

    def roon_not_connected(self):
        if not self.roon:
            self.speak_dialog("RoonNotConfigured")
            return True
        return False

    def release_gui_after(self, seconds=10):
        self.schedule_event(
            self.release_gui, seconds
        )

    def release_gui(self):
        self.gui.release()

    @intent_handler(IntentBuilder("Stop").require("Stop").optionally("Roon"))
    def handle_stop(self):
        """Stop playback."""
        if self.roon_not_connected():
            return
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.roon.playback_control(default_zone_id, control="stop")

    @intent_handler(IntentBuilder("Pause").require("Pause").optionally("Roon"))
    def handle_pause(self):
        """Pause playback."""
        if self.roon_not_connected():
            return
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.roon.playback_control(default_zone_id, control="pause")

    @intent_handler(IntentBuilder("Resume").one_of("PlayResume", "Resume").optionally("Roon"))
    def handle_resume(self):
        """Resume playback."""
        if self.roon_not_connected():
            return
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.roon.playback_control(default_zone_id, control="play")


    @intent_handler(IntentBuilder("Next").require("Next").optionally("Roon"))
    def handle_next(self):
        """Next playback."""
        if self.roon_not_connected():
            return
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.roon.playback_control(default_zone_id, control="next")

    @intent_handler(IntentBuilder("Prev").require("Prev").optionally("Roon"))
    def handle_prev(self):
        """Prev playback."""
        if self.roon_not_connected():
            return
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.roon.playback_control(default_zone_id, control="previous")

    @intent_handler(IntentBuilder("Mute").require("Mute").optionally("Roon"))
    def handle_mute(self):
        """Mute playback."""
        if self.roon_not_connected():
            return
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.log.info("default_zone_id {}".format(default_zone_id))
        for output in self.outputs_for_zones(default_zone_id):
            r = self.roon.mute(output["output_id"], mute=True)
            self.log.info(f"muting {output['display_name']} {r} {output['output_id']}")

    @intent_handler(IntentBuilder("Unmute").require("Unmute").optionally("Roon"))
    def handle_unmute(self):
        """Unmute playback."""
        if self.roon_not_connected():
            return
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.log.info("default_zone_id {}".format(default_zone_id))
        for output in self.outputs_for_zones(default_zone_id):
            r = self.roon.mute(output["output_id"], mute=False)
            self.log.info(f"unmuting {output['display_name']} {r} {output['output_id']}")

    @intent_handler(IntentBuilder("SetVolumePercent").require("Volume")
                    .optionally("Increase").optionally("Decrease")
                    .optionally("To").require("Percent").optionally("Roon"))
    def handle_set_volume_percent(self, message):
        """Set volume to a percentage."""
        if self.roon_not_connected():
            return
        percent = extract_number(message.data['utterance'].replace('%', ''))
        percent = int(percent)
        self._set_volume(percent)
        self.acknowledge()

    def _set_volume(self, percent):
        """Set volume to a percentage."""
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.log.info("default_zone_id {}".format(default_zone_id))
        for output in self.outputs_for_zones(default_zone_id):
            r = self.roon.change_volume(output["output_id"], percent)
            self.log.info(f"changing vol={percent} {output['display_name']} {r} {output['output_id']}")

    @intent_handler("ShuffleOn.intent")
    def handle_shuffle_on(self):
        """Turn shuffle on."""
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.roon.shuffle(default_zone_id, True)

    @intent_handler("ShuffleOff.intent")
    def handle_shuffle_off(self):
        """Turn shuffle off."""
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        self.roon.shuffle(default_zone_id, False)

    @intent_handler("RepeatTrackOn.intent")
    def handle_repeat_one_on(self):
        """Turn repeat one on."""
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        r = self._set_repeat(default_zone_id, "loop_one")
        if self.is_success(r):
            self.acknowledge()

    @intent_handler("RepeatOff.intent")
    def handle_repeat_off(self):
        """Turn repeat off."""
        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        r = self._set_repeat(default_zone_id, "disabled")
        if self.is_success(r):
            self.acknowledge()

    def _set_repeat(self, zone_or_output_id, loop: Literal["loop", "loop_one", "disabled"]):
        data = {"zone_or_output_id": zone_or_output_id, "loop": loop}
        return self.roon._request(SERVICE_TRANSPORT + "/change_settings", data)


    def outputs_for_zones(self, zone_id):
        return self.roon.zones[zone_id]["outputs"]

    def is_success(self, roon_response):
        r = "Success" in roon_response
        if r:
            self.log.info(f"roon error: {r}")
        return r

def create_skill():
    return RoonSkill()

class RoonNotAuthorizedError(Exception):
    pass
