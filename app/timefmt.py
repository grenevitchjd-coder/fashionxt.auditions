"""Tiny helpers for the designers' walk-through (practice walk) start times."""
from datetime import time


def walk_label(t: time | None) -> str | None:
    """9:30 AM style label, or None when no time has been set."""
    if t is None:
        return None
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def walk_text(label: str | None) -> str:
    """What gets printed/shown: the time, or TBD."""
    return label or "TBD"