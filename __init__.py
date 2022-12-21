import asyncio
import re
from mycroft.skills.core import intent_handler
from adapt.intent import IntentBuilder
from mycroft.skills.common_play_skill import CommonPlaySkill, CPSMatchLevel

from roonapi import RoonApi

from .discovery import discover, authenticate, InvalidAuth
from .const import ROON_APPINFO, ROON_KEYWORDS
from .search import search_stations

# Return value definition indication nothing was found
# (confidence None, data None)
NOTHING_FOUND = (None, 0.0)

# Confidence levels for generic play handling
DIRECT_RESPONSE_CONFIDENCE = 0.8

MATCH_CONFIDENCE = 0.5



class RoonSkill(CommonPlaySkill):
    """Roon control"""

    def __init__(self):
        super(RoonSkill, self).__init__()
        # We cannot access any existing loop because each Skill runs in it's
        # own thread.
        # So  asyncio.get_event_loop() will not work.
        # Instead we can create a new loop for our Skill's dedicated thread.
        self.roon = None
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
        confidence, data = self.continue_playback(phrase, bonus)
        if not data:
            confidence, data = self.specific_query(phrase, bonus)
            if not data:
                confidence, data = self.generic_query(phrase, bonus)

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
        """Try to find a radio station
        Arguments:
          station (str): station to search for
        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        probs = search_stations(self.roon, station)
        self.log.info("probs: {}".format(probs))
        return NOTHING_FOUND


    def CPS_start(self, phrase, data):
        """Handler for common play framework start. Starts playback"""
        pass

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

    @intent_handler(IntentBuilder("Rune").require("Zone"))
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

    @intent_handler(IntentBuilder("Rune").require("Device"))
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

    def roon_not_connected(self):
        if not self.roon:
            self.speak_dialog("RoonNotConfigured")
            return True
        return False


def create_skill():
    return RoonSkill()
