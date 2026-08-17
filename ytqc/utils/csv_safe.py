"""Spreadsheet formula-injection guard, shared by every CSV/Excel sink.

A cell whose first character is `=`, `+`, `-` or `@` can be interpreted by
Excel / Calc as a formula rather than text, so we prefix it with a single quote.

The one deliberate exception is a YouTube @handle in an ID column. Ids are join
keys: writing `'@mrbeast` breaks every VLOOKUP against the QC team's other
sheets, and @handles are how a large share of any channel list is written. A
handle is `@` followed only by YouTube's allowed charset (letters, digits, dot,
dash, underscore) — that can't express a function call or a DDE payload, which
is what makes a leading `@` dangerous in the first place. Anything else keeps
the guard, including `@` outside an ID column and `@SUM(...)`-shaped values.
"""
from __future__ import annotations

import re

RISKY_LEADING = "=+@-"

# '@' plus exactly the character set YouTube permits in a handle.
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]+$")


def is_youtube_handle(value: str) -> bool:
    return bool(_HANDLE_RE.match(value))


def csv_safe(value, *, id_column: bool = False):
    """Return `value` with a leading formula character quoted.

    Non-strings and empty strings pass through untouched. `id_column=True`
    exempts a plain @handle (see the module docstring)."""
    if not isinstance(value, str) or not value or value[0] not in RISKY_LEADING:
        return value
    if id_column and is_youtube_handle(value):
        return value
    return "'" + value
