"""Browser-bridge error classification + clean setup-failure handling."""
import threading
import time

import httpx
import pytest

from ytqc.browser.webbridge import BridgeNotConnected, KimiClient
from ytqc.config import BrowserConfig


def _fake_502(body: dict):
    """A KimiClient whose POST returns a 502 carrying the given tool-error body."""
    req = httpx.Request("POST", "http://x/command")
    resp = httpx.Response(502, json=body, request=req)

    class _HTTP:
        def post(self, *a, **k):
            return resp
    k = KimiClient(BrowserConfig())
    k._http = _HTTP()
    return k


def test_no_extension_raises_bridge_not_connected():
    k = _fake_502({"ok": False, "error": {"code": "tool_error",
                                          "message": "no extension connected"}})
    with pytest.raises(BridgeNotConnected):
        k._kimi("navigate", {"url": "x"})


def test_other_tool_error_raises_plain_runtimeerror():
    k = _fake_502({"ok": False, "error": {"message": "some other failure"}})
    with pytest.raises(RuntimeError) as ei:
        k._kimi("navigate", {"url": "x"})
    assert not isinstance(ei.value, BridgeNotConnected)


def test_transport_retry_backoff_bails_immediately_on_halt():
    """A lane mid-retry must wake the instant Ctrl-C sets the halt Event, not
    sleep out the full backoff — otherwise it logs retry spam after the run was
    cancelled (and corrupts the TUI in chat mode)."""
    halt = threading.Event()
    halt.set()                                   # halt already requested

    class _HTTP:
        def post(self, *a, **k):
            raise httpx.TimeoutException("forced")

    k = KimiClient(BrowserConfig(), halt=halt)
    k._http = _HTTP()

    t0 = time.monotonic()
    with pytest.raises(httpx.TimeoutException):   # stops retrying, re-raises
        k._kimi("navigate", {"url": "x"})
    assert time.monotonic() - t0 < 1.0           # did NOT sleep the 2s backoff


def test_transport_retry_sleeps_when_no_halt(monkeypatch):
    """Without a halt Event the backoff still happens (normal retry behaviour),
    using a plain sleep — assert the sleep path is taken, not skipped."""
    slept = []
    monkeypatch.setattr("ytqc.browser.webbridge.time.sleep", lambda s: slept.append(s))

    class _HTTP:
        def post(self, *a, **k):
            raise httpx.ConnectError("forced")

    k = KimiClient(BrowserConfig())              # halt=None
    k._http = _HTTP()
    with pytest.raises(httpx.ConnectError):
        k._kimi("navigate", {"url": "x"})
    assert slept == [2.0, 4.0]                    # two backoffs before exhausting 3 attempts
