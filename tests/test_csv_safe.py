"""The shared spreadsheet formula-injection guard (ytqc/utils/csv_safe.py).

Both CSV sinks route through this, so the security property and the one
deliberate exception (an @handle in an ID column) are pinned here once.
"""
from __future__ import annotations

import pytest

from ytqc.utils.csv_safe import csv_safe, is_youtube_handle


@pytest.mark.parametrize("value", [
    "=1+1",
    "=HYPERLINK(\"http://evil\",\"click\")",
    "+1+1",
    "-1+1",
    "@SUM(1+1)",
    "=cmd|' /C calc'!A0",
    "@SUM(1+1)*cmd|' /C calc'!A0",     # the classic @-prefixed DDE payload
])
def test_formula_starters_are_escaped(value):
    assert csv_safe(value) == "'" + value
    # still escaped in an ID column — the exception is ONLY for plain handles
    assert csv_safe(value, id_column=True).startswith("'")


@pytest.mark.parametrize("value", ["mrbeast", "UC1234567890abcdefABCD12", "Safe", "", "en"])
def test_ordinary_values_pass_through(value):
    assert csv_safe(value) == value
    assert csv_safe(value, id_column=True) == value


@pytest.mark.parametrize("handle", ["@mrbeast", "@Mr.Beast", "@a_b-c.1", "@x"])
def test_plain_handles_are_exempt_only_in_id_columns(handle):
    assert csv_safe(handle, id_column=True) == handle
    assert csv_safe(handle) == "'" + handle       # anywhere else: still guarded


@pytest.mark.parametrize("value", ["@handle with spaces", "@han(dle)", "@han!dle",
                                   "@", "@handle,x", "@handle'"])
def test_handle_lookalikes_are_not_exempt(value):
    assert csv_safe(value, id_column=True) == "'" + value
    assert is_youtube_handle(value) is False


def test_non_strings_pass_through_untouched():
    for value in (0, 1200, None, True, 3.5):
        assert csv_safe(value) is value
        assert csv_safe(value, id_column=True) is value


def test_escaping_is_not_applied_twice():
    """An already-quoted value doesn't start with a risky char, so a second
    pass (e.g. a resumed run re-reading its own file) leaves it alone."""
    once = csv_safe("=1+1")
    assert csv_safe(once) == once
