"""Utils."""

import re
from mycroft.util.parse import fuzzy_match
from fuzzywuzzy import fuzz, process as fuzz_process
from fuzzywuzzy import utils
from typing import Any, Dict, List, Optional, Tuple
from fuzzysearch import find_near_matches


default_processor = utils.full_process

def expand_choices(choices, key, key2):
    """Expand choices to include a variant with content in parens removed."""
    new_choices = choices.copy()
    for choice in choices:
        orig = choice.get(key, "")
        choice_stripped = re.sub('(\(.+\)|-.+)$', '', orig).strip()
        if orig != choice_stripped:
            choice2 = choice.copy()
            choice2[key2] = choice_stripped
            new_choices.append(choice2)
    return new_choices



def key_processor(key, key2):
    """Process, for fuzzywuzzy, that uses the key to extract the string."""
    def processor(s):
        if isinstance(s, dict):
            if key2 in s:
                return utils.full_process(s[key2])
            return utils.full_process(s.get(key, ""))
        return utils.full_process(s)
    return processor


def match_one(query, choices, key) -> Tuple[Optional[Dict], int]:
    """Match a single item from a list of choice dicts."""
    choices = expand_choices(choices, key, "_expanded")
    try:
        best_list = fuzz_process.extract(query, choices, scorer=fuzz.WRatio, processor=key_processor(key, "_expanded"), limit=999)
        chosen, confidence = max(best_list, key=lambda i: i[1])
        if "_expanded" in chosen:
            del chosen["_expanded"]
        return chosen, confidence/100
    except Exception as e:
        #raise e
        return None, 0
