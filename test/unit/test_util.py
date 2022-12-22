import pytest
import unittest
from fuzzywuzzy import fuzz, process as fuzz_process

import util

class TestMatchOne(unittest.TestCase):
    def test_match_one(self):
        playlists = [
            {"title": "Malika Sellami",},
            {"title": "The Balkan Gypsy Vinyl Boxes  (all editions)  presented by Dunkelbunt",},
            {"title": "Something about balkans but not gypsys"},
            {"title": "Balkan Gypsy"},
            {"title": "Balkan Gypsy (2023 remix edition)"},
        ]
        r, _ = util.match_one("balkan gypsy", playlists, "title")
        self.assertEqual(r["title"], "Balkan Gypsy")
        r, _ = util.match_one("balkan gypsy remix edition", playlists, "title")
        self.assertEqual(r["title"], "Balkan Gypsy (2023 remix edition)")

        r, _ = util.match_one("booker t and the mgs", playlists, "title")
        print(r, _)
