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
"""Utils."""
import re
from dataclasses import asdict
from typing import (
    Any,
    Dict,
    List,
    Tuple,
    Union,
    Optional,
    TypeVar,
    cast,
)
from fuzzywuzzy import fuzz, process as fuzz_process
from fuzzywuzzy import utils
from roon_proxy.roon_types import BrowseItem


T = TypeVar("T")

ConfidenceInt = Tuple[T, int]
ConfidenceFloat = Tuple[T, float]
ConfidenceDictFloat = Tuple[Optional[Dict[str, Any]], float]
ConfidenceItemFloat = Tuple[Optional[BrowseItem], float]

default_processor = utils.full_process


def expand_choices(choices, key, key2):
    """Expand choices to include a variant with content in parens removed."""
    new_choices = choices.copy()
    for choice in choices:
        orig = choice.get(key, "")
        choice_stripped = re.sub(r"(\(.+\)|-.+)$", "", orig).strip()
        if orig != choice_stripped:
            choice2 = choice.copy()
            choice2[key2] = choice_stripped
            new_choices.append(choice2)
    return new_choices


def key_processor(key: str, key2: str) -> Any:
    """Process, for fuzzywuzzy, that uses the key to extract the string."""

    def processor(query: Union[Dict[str, Any], str]) -> str:
        if isinstance(query, dict):
            if key2 in query:
                return utils.full_process(query[key2])
            return utils.full_process(query.get(key, ""))
        return utils.full_process(query)

    return processor


def match_one(
    query: str, choices: List[Dict[str, Any]], key: str
) -> ConfidenceDictFloat:
    """Match a single item from a list of choice dicts."""
    choices = expand_choices(choices, key, "_expanded")
    try:
        best_list = cast(
            List[ConfidenceInt],
            fuzz_process.extract(
                query,
                choices,
                scorer=fuzz.WRatio,
                processor=key_processor(key, "_expanded"),
                limit=999,
            ),
        )
        # for b in best_list:
        # print(f"{b[0]['title']} {b[1]}")
        chosen, confidence = max(best_list, key=lambda i: i[1])
        if "_expanded" in chosen:
            del chosen["_expanded"]
        return chosen, confidence / 100
    # pylint: disable=broad-except, unused-variable, invalid-name
    except Exception as e:
        # raise e # for debugging
        return None, 0


def match_one_item(query: str, items: List[BrowseItem]) -> ConfidenceItemFloat:
    choices = [asdict(item) for item in items]
    item_dict, confidence = match_one(query, choices, "title")
    if item_dict is None:
        return None, 0
    return BrowseItem(**item_dict), confidence


def best_match(opt1, opt2):
    if opt1[1] > opt2[1]:
        return opt1
    return opt2
