"""Roon Skill"""
import asyncio
from typing import Optional, Literal, Tuple, Dict
import re
import datetime
from mycroft.skills.core import intent_handler
from mycroft.util.parse import extract_number
from adapt.intent import IntentBuilder
from mycroft.skills.common_play_skill import CommonPlaySkill, CPSMatchLevel

from roonapi import RoonApi
from roonapi.constants import SERVICE_TRANSPORT

from .discovery import discover, authenticate, InvalidAuth
from .const import ROON_APPINFO, ROON_KEYWORDS, TYPE_TAG, TYPE_PLAYLIST, TYPE_ARTIST, TYPE_ALBUM, TYPE_STATION, CONF_DEFAULT_ZONE_NAME, CONF_DEFAULT_ZONE_ID, NOTHING_FOUND, DIRECT_RESPONSE_CONFIDENCE, MATCH_CONFIDENCE
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

    def update_library_cache(self):
        """Update library cache."""
        if self.library:
            self.library.update_cache(self.roon)

    def CPS_match_query_phrase(self, phrase):
        """Handle common play framework query.

        Checks if the skill can play the utterance.
        """
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
        data, confidence = self.specific_query(phrase, bonus)
        if not data:
            data, confidence = self.generic_query(phrase, bonus)
        if data:
            self.log.info("Roon Confidence: {}".format(confidence))
            self.log.info('              data: {}'.format(data))
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
            self.log.info("Matched {} with level {} to {}".format(phrase, level, data))
            return phrase, level, data
        else:
            self.log.info("Couldn't find anything on Roon")

    def specific_query(self, phrase, bonus):
        """
        Check if the phrase can be matched against a specific roon request.

        This includes asking for radio, playlists, albums, artists, or tracks.
        Arguments:
            phrase (str): Text to match against
            bonus (float): Any existing match bonus
        Returns: Tuple with confidence and data or NOTHING_FOUND
        """
        # Check radio stations
        match = re.match(self.translate_regex("radio"), phrase,
                         re.IGNORECASE)
        self.log.info("station match: {}".format(match))
        if match:
            return self.query_radio(match.groupdict()["station"])

        # Check playlist
        match = re.match(self.translate_regex("playlist"), phrase,
                         re.IGNORECASE)
        if match:
            playlist = match.groupdict()["playlist"]
            return self.query_playlist(playlist, bonus)

        # Check tags
        match = re.match(self.translate_regex("tag"), phrase,
                         re.IGNORECASE)
        self.log.info("tag match: {}".format(match))
        if match:
            tag = match.groupdict()["tag"]
            return self.query_tag(tag, bonus)

        # Check artist
        match = re.match(self.translate_regex("artist"), phrase,
                         re.IGNORECASE)
        self.log.info("artist match: {}".format(match))
        if match:
            artist = match.groupdict()["artist"]
            return self.query_artist(artist, bonus)

        # Check albums
        match = re.match(self.translate_regex("album"), phrase,
                         re.IGNORECASE)
        self.log.info("album match: {}".format(match))
        if match:
            album = match.groupdict()["album"]
            return self.query_album(album, bonus)

        # Check genres
        match = re.match(self.translate_regex("genre1"), phrase,
                         re.IGNORECASE)
        if not match:
            match = re.match(self.translate_regex("genre2"), phrase,
                         re.IGNORECASE)
        self.log.info("genre match: {}".format(match))
        if match:
            genre = match.groupdict()["genre"]
            return self.query_genre(genre, bonus)

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
        return d, bonus+c

    def translate_regex(self, regex):
        """Translate the given regex."""
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

    def query_genre(self, genre, bonus)-> Tuple[dict, float]:
        """Try and find an genre."""
        bonus += 1
        data, confidence = self.library.search_genres(genre)
        confidence = min(confidence+bonus, 1.0)
        return data, confidence

    def query_album(self, album, bonus)-> Tuple[dict, float]:
        """Try and find an album."""
        bonus += 1
        data, confidence = self.library.search_albums(album)
        confidence = min(confidence+bonus, 1.0)
        return data, confidence

    def query_tag(self, tag, bonus)-> Tuple[dict, float]:
        """Try and find an tag."""
        bonus += 1
        data, confidence = self.library.search_tags(tag)
        confidence = min(confidence+bonus, 1.0)
        return data, confidence

    def query_artist(self, artist, bonus)-> Tuple[dict, float]:
        """Try and find an artist."""
        bonus += 1
        data, confidence = self.library.search_artists(artist)
        confidence = min(confidence+bonus, 1.0)
        return data, confidence

    def query_playlist(self, playlist, bonus)-> Tuple[dict, float]:
        """Try and find an playlist."""
        bonus += 1
        data, confidence = self.library.search_playlists(playlist)
        confidence = min(confidence+bonus, 1.0)
        return data, confidence

    def CPS_start(self, phrase, data):
        """Handle common play framework start.

        Starts playback of the given item.
        """
        self.log.info("CPS_start: {} {}".format(phrase, data))
        if self.roon_not_connected():
            raise RoonNotAuthorizedError()

        default_zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        if not default_zone_id:
            self.speak_dialog("NoDefaultZone")
            return
        if "path" in data["mycroft"]:
            r = self.library.play_path(default_zone_id, data["mycroft"]["path"])
        elif "session_key" in data["mycroft"]:
            r = self.library.play_search_result(default_zone_id, data["item_key"], data["mycroft"]["session_key"])
        else:
            r = None
        if not self.is_success(r):
            self.log.error(f"Could not play {phrase} from {data}. Got response {r}")
            return

        self.acknowledge()
        zone_name = self.zone_name(default_zone_id)
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


    def playback_prerequisites_ok(self):
        """Check if the playback prereqs are met."""
        return not self.roon_not_connected()

    def handle_stop(self, message):
        """Handle stop command."""
        self.bus.emit(message.reply("mycroft.stop"))

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
    def handle_get_default_zone(self, message):
        """Handle get default zone command."""
        zone_id = self.settings.get(CONF_DEFAULT_ZONE_ID)
        if zone_id:
            zone = self.roon.zones[zone_id]
            self.speak_dialog("DefaultZone", zone)
        else:
            self.speak_dialog("NoDefaultZone")

    @intent_handler(IntentBuilder("ListZones").optionally("Roon").require("List").require("Zone"))
    def list_zones(self, message):
        """List available zones."""
        if self.roon_not_connected():
            return
        zones = self.roon.zones
        self.log.info(zones)
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

    @intent_handler(IntentBuilder("SetDefaultZone").optionally("Roon").require("Set").require("SetZone"))
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
        self.schedule_event(
            self.release_gui, seconds
        )

    def release_gui(self):
        """Release the gui now."""
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
        """Get the outputs for a zone."""
        return self.roon.zones[zone_id]["outputs"]

    def zone_name(self, zone_id):
       zone = self.roon.zones.get(zone_id)
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
