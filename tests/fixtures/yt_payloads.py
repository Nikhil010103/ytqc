"""Hand-authored canned in-page-JS payloads.

Each constant is the *already-JSON-decoded* dict that ``KimiClient.js(...)``
would return for the matching ``ytqc.browser.youtube_js`` constant. Field names
and shapes mirror youtube_js.py exactly so they drive the real parsing code in
video_page.py / channel_page.py / transcript.py / frames.py with no browser.

Import freely and ``copy.deepcopy`` before mutating — these are shared module
globals, so a test that edits one in place would corrupt every other test.
"""
from __future__ import annotations

# ── PLAYER_RESPONSE (J.PLAYER_RESPONSE) ───────────────────────────────────
# A valid, playable video. Mirrors the dict shape returned by the
# PLAYER_RESPONSE JS: ok/status/reason/hasVideoDetails/title/author/channelId/
# lengthSeconds/viewCount/keywords/shortDescription/isLiveContent/publishDate/
# category/isFamilySafe/tracks.
PLAYER_RESPONSE_OK: dict = {
    "ok": True,
    "status": "OK",
    "reason": "",
    "hasVideoDetails": True,
    "title": "Kawasaki Ninja ZX-4R Track Day Review",
    "author": "MotoGarage",
    "channelId": "UC1234567890abcdefABCD12",
    "lengthSeconds": "814",
    "viewCount": "100711",
    "keywords": ["kawasaki", "ninja zx-4r", "track day", "motorcycle review"],
    "shortDescription": (
        "Full track-day review of the Kawasaki Ninja ZX-4R. We cover the "
        "engine, suspension setup and lap times at the circuit."
    ),
    "isLiveContent": False,
    "publishDate": "2026-05-12T20:50:47-07:00",
    "category": "Autos & Vehicles",
    "isFamilySafe": True,
    "tracks": [{"lang": "en", "kind": "asr"}],
}

# Unavailable / deleted / private — status != OK and hasVideoDetails False.
# Drives the video_page.py F2 "video unavailable" early-return path.
PLAYER_RESPONSE_UNAVAILABLE: dict = {
    "ok": True,
    "status": "ERROR",
    "reason": "Video unavailable",
    "hasVideoDetails": False,
    "title": "",
    "author": "",
    "channelId": "",
    "lengthSeconds": "0",
    "viewCount": "0",
    "keywords": [],
    "shortDescription": "",
    "isLiveContent": False,
    "publishDate": "",
    "category": "",
    "isFamilySafe": None,
    "tracks": [],
}

# The JS itself failed to read a player response at all (ok:false). Drives the
# video_page.py "player response unavailable (page failed to load?)" path.
PLAYER_RESPONSE_NO_PR: dict = {"ok": False}

# Valid + playable, but an empty/non-ISO publishDate so the
# datetime.fromisoformat parse fails and days_since_publish keeps its 365.0
# default (views_per_day == viewCount / 365).
PLAYER_RESPONSE_BAD_DATE: dict = {
    "ok": True,
    "status": "OK",
    "reason": "",
    "hasVideoDetails": True,
    "title": "Mystery Upload",
    "author": "Anon Channel",
    "channelId": "UCabcdefABCDEF0987654321",
    "lengthSeconds": "300",
    "viewCount": "36500",
    "keywords": ["mystery"],
    "shortDescription": "No reliable publish date on this one.",
    "isLiveContent": False,
    "publishDate": "",            # empty → fromisoformat never runs → 365.0
    "category": "Entertainment",
    "isFamilySafe": True,
    "tracks": [],
}

# Same as BAD_DATE but with a non-ISO string (e.g. a human-readable date) — the
# fromisoformat call raises ValueError and is swallowed, again leaving 365.0.
PLAYER_RESPONSE_BAD_DATE_NONISO: dict = {
    **PLAYER_RESPONSE_BAD_DATE,
    "publishDate": "May 12, 2026",
}

# A live stream — isLiveContent True (frame capture must not seek).
PLAYER_RESPONSE_LIVE: dict = {
    **PLAYER_RESPONSE_OK,
    "title": "24/7 Lofi Beats Live Stream",
    "isLiveContent": True,
    "category": "Music",
    "tracks": [],
}

# ── LIKES (J.LIKES) ───────────────────────────────────────────────────────
LIKES_OK: dict = {"likes_text": "12K"}
LIKES_EMPTY: dict = {"likes_text": ""}

# ── COMMENTS (J.COMMENTS) ─────────────────────────────────────────────────
# Note: the live JS substitutes __TOP_N__; the FakeKimiClient routes the
# substituted code back to the "comments" label, so this is the post-scrape
# shape: {count_text, comments:[{author,text,likes}]}.
COMMENTS_OK: dict = {
    "count_text": "1,204 Comments",
    "comments": [
        {"author": "@riderdan", "text": "That lap time is insane!", "likes": "320"},
        {"author": "@trackjunkie", "text": "Best 400cc on the market.", "likes": "97"},
        {"author": "@newbie22", "text": "Saving up for this bike.", "likes": "12"},
    ],
}
COMMENTS_EMPTY: dict = {"count_text": "", "comments": []}

# ── TRANSCRIPT_OPEN (J.TRANSCRIPT_OPEN) ───────────────────────────────────
TRANSCRIPT_OPEN_CLICKED: dict = {"state": "clicked"}
TRANSCRIPT_OPEN_ALREADY: dict = {"state": "open"}
TRANSCRIPT_OPEN_NO_BUTTON: dict = {"state": "no-button"}

# ── TRANSCRIPT_SCRAPE (J.TRANSCRIPT_SCRAPE) ───────────────────────────────
# Shape: {n, segs:[{t:"0:03", text:"..."}]}.
TRANSCRIPT_SCRAPE_OK: dict = {
    "n": 5,
    "segs": [
        {"t": "0:03", "text": "Welcome back to the channel, today we ride the ZX-4R."},
        {"t": "0:42", "text": "First thing you notice is the inline-four scream."},
        {"t": "2:15", "text": "On the back straight we hit a solid top speed."},
        {"t": "5:30", "text": "Suspension soaks up the kerbs surprisingly well."},
        {"t": "10:48", "text": "Final verdict: a brilliant track-day weapon."},
    ],
}
TRANSCRIPT_SCRAPE_EMPTY: dict = {"n": 0, "segs": []}

# ── FRAME capture JS (frames.py) ──────────────────────────────────────────
# frames.py calls several JS constants. The defaults below let capture_frames
# run without a browser. FRAME_GRAB returns a data: URL string (not a dict);
# the b64 body here is the same tiny JPEG as TINY_JPEG_B64 so it survives the
# size guard but is treated as "blank" only if PIL flags it — keep it small but
# > 5000 decoded bytes if you want it kept. By default we route FRAME_GRAB to an
# ERR string so capture falls back to screenshot in tests that care; tests that
# want a real frame can override the "frame_grab" label.
FRAME_SEEK_OK: dict = {"ok": True}
FRAME_READY_OK: dict = {"seeking": False, "ready": True, "ad": False}
AD_SKIP_NONE: dict = {"ad": False, "skippable": False, "skipped": False}
# ad present + skippable button clicked this poll (gate sees ad clear on next poll)
AD_SKIP_SKIPPABLE: dict = {"ad": True, "skippable": True, "skipped": True}
# ad present, countdown / non-skippable — no clickable skip yet
AD_SKIP_COUNTDOWN: dict = {"ad": True, "skippable": False, "skipped": False}

# ── CHANNEL_ABOUT (J.CHANNEL_ABOUT) ───────────────────────────────────────
# Shape mirrors the CHANNEL_ABOUT JS return: ok/title/externalId/description/
# subscriberCountText/viewCountText/videoCountText/country/joinedDateText/
# links/channelKeywords/isFamilySafe.
CHANNEL_ABOUT_OK: dict = {
    "ok": True,
    "title": "MotoGarage",
    "externalId": "UC1234567890abcdefABCD12",
    "description": (
        "Motorcycle reviews, track days and maintenance guides. New video "
        "every Friday."
    ),
    "subscriberCountText": "1.2M subscribers",
    "viewCountText": "458,000,000 views",
    "videoCountText": "642 videos",
    "country": "United States",
    "joinedDateText": "Joined Mar 3, 2015",
    "links": [
        {"title": "Instagram", "url": "https://instagram.com/motogarage"},
        {"title": "Merch", "url": "https://motogarage.shop"},
    ],
    "channelKeywords": "motorcycles reviews track days",
    "isFamilySafe": True,
}

# about JS could not find aboutChannelViewModel (ok:false).
CHANNEL_ABOUT_MISSING: dict = {"ok": False}

# ── CHANNEL_VIDEOS (J.CHANNEL_VIDEOS) ─────────────────────────────────────
# Shape: {n, vids:[{id,title,views,age}]}.
CHANNEL_VIDEOS_OK: dict = {
    "n": 6,
    "vids": [
        {"id": "vid0001aaaa", "title": "ZX-4R Track Day Review", "views": "100K views", "age": "2 days ago"},
        {"id": "vid0002bbbb", "title": "CBR650R vs ZX-6R", "views": "250K views", "age": "1 week ago"},
        {"id": "vid0003cccc", "title": "Cheap Mods That Work", "views": "80K views", "age": "3 weeks ago"},
        {"id": "vid0004dddd", "title": "Chain Maintenance 101", "views": "1.1M views", "age": "2 months ago"},
        {"id": "vid0005eeee", "title": "Trackside Vlog: Round 3", "views": "45K views", "age": "5 months ago"},
        {"id": "vid0006ffff", "title": "Best Beginner Bikes 2026", "views": "600K views", "age": "8 months ago"},
    ],
}

# No video grid extracted (empty).
CHANNEL_VIDEOS_EMPTY: dict = {"n": 0, "vids": []}

# ── readiness / gate probes (webbridge internals) ─────────────────────────
# webbridge.navigate() polls a readiness JS that returns {"r": True} and runs a
# consent/captcha gate. FakeKimiClient is browser-free and overrides navigate so
# these aren't strictly needed, but exported for completeness / direct use.
READY_TRUE: dict = {"r": True}
CONSENT_NOT_CLICKED: dict = {"clicked": False}
CAPTCHA_CLEAR: dict = {"sorry": False, "captcha": False}

# ── tiny valid base64 JPEG ────────────────────────────────────────────────
# A 1x1 JPEG. Use as a stand-in thumbnail/frame/screenshot. Decoded length is
# tiny (< 5000 bytes) so frames.py would treat it as "blank" — that is fine for
# vision-LLM routing tests where you just need a non-empty base64 string.
TINY_JPEG_B64: str = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAAR"
    "CAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAA"
    "AgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
    "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYk"
    "NOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigD//2Q=="
)
