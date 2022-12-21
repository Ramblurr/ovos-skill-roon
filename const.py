"""Constants for the Roon skill"""

AUTHENTICATE_TIMEOUT = 5

DEFAULT_NAME = "Roon Labs Music Player"

ROON_APPINFO = {
    "extension_id": "mycroft_roon",
    "display_name": "Roon Skill for Mycroft",
    "display_version": "0.0.1",
    "publisher": "ramblurr",
    "email": "ramblurr@users.noreply.github.com",
    "website": "https://github.com/ramblurr/mycroft-roon-skill",
}

ROON_KEYWORDS = ["roon", "ruin", "rune"]

TYPE_STATION = "station"

CONF_DEFAULT_ZONE_ID = "default_zone_id"
CONF_DEFAULT_ZONE_NAME = "default_zone_name"

# Return value definition indication nothing was found
# (confidence None, data None)
NOTHING_FOUND = (None, 0.0)
