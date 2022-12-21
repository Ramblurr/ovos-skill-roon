from mycroft.skills.core import intent_handler
from adapt.intent import IntentBuilder
from mycroft.skills.common_play_skill import CommonPlaySkill, CPSMatchLevel

from roonapi import RoonApi, RoonDiscovery

from .discovery import discover, authenticate, InvalidAuth


def get_roon_api(token):
    (host, port) = get_roon_host()
    return RoonApi(ROON_APPINFO, token, host, port, blocking_init=True)


class RoonSkill(CommonPlaySkill):
    """Roon control"""

    def __init__(self):
        super(RoonSkill, self).__init__()
        # We cannot access any existing loop because each Skill runs in it's
        # own thread.
        # So  asyncio.get_event_loop() will not work.
        # Instead we can create a new loop for our Skill's dedicated thread.
        self.roon = None
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    def initialize(self):
        super().initialize()
        self.cancel_scheduled_event("RoonLogin")
        # Setup handlers for playback control messages
        self.add_event("mycroft.audio.service.next", self.next_track)
        self.add_event("mycroft.audio.service.prev", self.prev_track)
        self.add_event("mycroft.audio.service.pause", self.pause)
        self.add_event("mycroft.audio.service.resume", self.resume)
        self.settings_change_callback = self.on_websettings_changed
        # Retry in 5 minutes
        self.schedule_repeating_event(
            self.on_websettings_changed, None, 5 * 60, name="RoonLogin"
        )
        self.on_websettings_changed()

    def on_websettings_changed(self):
        if not self.roon:
            try:
                self.load_credentials()
            except Exception as e:
                self.log.debug(
                    "Credentials could not be fetched. " "({})".format(repr(e))
                )

        if self.roon:
            self.cancel_scheduled_event("RoonLogin")

            # Refresh saved tracks
            # We can't get this list when the user asks because it takes
            # too long and causes
            # mycroft-playback-control.mycroftai:PlayQueryTimeout
            self.refresh_saved_tracks()

    def load_local_creds(self):
        pass

    def load_remote_creds(self):
        pass

    def load_credentials(self):
        self.roon = self.load_local_creds() or self.load_remote_creds()
        if self.roon:
            # we are connected to roon
            pass

    def CPS_match_query_phrase(self, phrase):
        """Handler for common play framework query. Checks if the skill can play the utterance"""
        pass

    def CPS_start(self, phrase, data):
        """Handler for common play framework start. Starts playback"""
        pass

    def handle_stop(self, message):
        self.bus.emit(message.reply("mycroft.stop"))

    @intent_handler("ConfigureRoon.intent")
    def configure_roon(self, message):
        servers = discover(self.log, self.loop)
        self.log(info, "Found servers: {}".format(servers))
        self.speak_dialog("ConfigurationComplete")

    @intent_handler(IntentBuilder("Roon").require("Device"))
    def list_devices(self, message):
        """List available devices"""
        self.log.info(self.roon)
        if self.roon:
            self.speak_dialog("NoDevicesAvailable")
        else:
            self.failed_auth()


def create_skill():
    return RoonSkill()
