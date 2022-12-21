from fuzzywuzzy import fuzz, process as fuzz_process
from typing import Any, Dict, List, Optional, Tuple

def match_one(input, choices, key) -> Tuple[Optional[Dict], int]:
    """Match a single item from a list of choice dicts."""
    options = [i.get(key) for i in choices]
    try:
        opt, confidence = fuzz_process.extractOne(
            input, options, scorer=fuzz.ratio
        )
        choice = [i for i in choices if i.get(key) == opt][0]
        return choice, confidence
    except:
        return None, 0
