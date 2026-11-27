"""School level from Malawi naming conventions.

The schools dataset has no level field, so the level is read from the name:
F.P. full primary, J.P. junior primary, CDSS community day secondary, "Open"
open secondary, and so on. Names with no level cue (for example "Bembeke RC")
stay unclassified and count only when all schools are analysed.
static/js/ui/format.js schoolLevel() applies the same rules in the browser.
"""

from __future__ import annotations

import re

SCHOOL_LEVELS = ("all", "primary", "secondary")

_SECONDARY = re.compile(r"\b(c?dss|secondary|sec\.?|high school|acad[ae]my|open|seminary)\b")
_JUNIOR_PRIMARY = re.compile(r"\bj\.? ?p\b")
_PRIMARY = re.compile(r"\bf\.? ?p\b|\bprimary\b|\bl\. ?e\. ?a\b")


def school_level(name: object) -> str:
    """"Secondary", "Junior primary", "Primary" or "School" (unclassified)."""
    text = re.sub(r"\s+", " ", str(name or "").lower())
    if _SECONDARY.search(text):
        return "Secondary"
    if _JUNIOR_PRIMARY.search(text):
        return "Junior primary"
    if _PRIMARY.search(text):
        return "Primary"
    return "School"


def level_group(name: object) -> str | None:
    """"primary" (full or junior), "secondary", or None when the name gives no level."""
    level = school_level(name)
    if level == "Secondary":
        return "secondary"
    if level in ("Primary", "Junior primary"):
        return "primary"
    return None


def service_kind(facility_type: str, school_level: str = "all") -> str:
    """Key for the walking-distance caches: "health", "school", "school:primary" or "school:secondary"."""
    if facility_type != "school":
        return "health"
    return "school" if school_level == "all" else f"school:{school_level}"
