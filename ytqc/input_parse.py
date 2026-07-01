"""Deterministic normalization of pasted / free-text channel & video input.

The chat agent's `ids` argument can be anything a user pastes: a clean CSV-ish
block (id,type,label), a list of URLs, bare ids, @handles, or a noisy mix. This
module turns that into a deduped list[InputItem] plus a ParseReport describing
what was recognized and what was ignored — so the agent can confirm BEFORE a
long run ("found 5 unique channels, ignored 2 noise lines").

Design rules:
- Canonical id shapes drive type inference (UC...=channel, 11-char=video,
  @handle=channel); an explicit per-line type column or a default_type override
  refine but never invent an id.
- Order matters: a 24-char UC id must be consumed before the 11-char video
  matcher can nibble a substring of it.
- The literal word "channel"/"video"/"US"/a bare name must NEVER become an id.
- Dedupe by canonical id, first-seen wins for label & type.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ytqc.models import InputItem

# ── canonical id shapes ──────────────────────────────────────────────────────
# Channel id: literal "UC" + 22 url-safe chars = 24 total.
_CHANNEL_ID = r"UC[0-9A-Za-z_-]{22}"
# Video id: exactly 11 url-safe chars (only trusted in a URL or as a bare token).
_VIDEO_ID = r"[0-9A-Za-z_-]{11}"
# Handle: @ + 3..30 of YouTube's allowed handle chars.
_HANDLE = r"@[A-Za-z0-9_.\-]{3,30}"

_RE_CHANNEL_ID = re.compile(_CHANNEL_ID)
_RE_CHANNEL_TOKEN = re.compile(rf"^{_CHANNEL_ID}$")
_RE_VIDEO_TOKEN = re.compile(rf"^{_VIDEO_ID}$")
_RE_HANDLE_TOKEN = re.compile(rf"^{_HANDLE}$")

# URL extractors (host-anchored so "channel"/"watch" as bare words don't hit).
_RE_URL_CHANNEL = re.compile(rf"youtube\.com/channel/({_CHANNEL_ID})", re.I)
_RE_URL_HANDLE = re.compile(rf"youtube\.com/({_HANDLE})", re.I)
_RE_URL_WATCH = re.compile(rf"[?&]v=({_VIDEO_ID})")
_RE_URL_SHORT = re.compile(rf"youtu\.be/({_VIDEO_ID})", re.I)
_RE_URL_SHORTS = re.compile(rf"youtube\.com/shorts/({_VIDEO_ID})", re.I)

_TYPES = ("channel", "video")
_HEADER_TOKENS = {"id", "type", "label", "name", "channelid", "videoid", "url"}


@dataclass
class ParseReport:
    channels: int = 0
    videos: int = 0
    n_deduped: int = 0                       # how many duplicate ids were dropped
    unrecognized: list[str] = field(default_factory=list)  # lines/tokens with no id

    def as_dict(self) -> dict:
        return {
            "channels": self.channels,
            "videos": self.videos,
            "deduped": self.n_deduped,
            "unrecognized": self.unrecognized,
        }


def _norm_type(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    t = raw.strip().lower()
    if t in ("channels", "chan", "ch"):
        t = "channel"
    elif t in ("videos", "vid"):
        t = "video"
    return t if t in _TYPES else None


def _shape_type(canonical_id: str) -> Optional[str]:
    """Authoritative type implied by an unambiguous id shape, else None.

    UC-id and @handle are authoritative channels. A bare 11-char id is
    shape-ambiguous, so it returns None and defers to an explicit/default type.
    """
    if canonical_id.startswith("@") or _RE_CHANNEL_TOKEN.match(canonical_id):
        return "channel"
    return None


def _resolve_type(canonical_id: str, forced: Optional[str],
                  line_type: Optional[str], default_type: Optional[str]) -> str:
    """Strongest signal first: a forced type (UC/handle/URL-derived) wins, then
    the id's own authoritative shape, then an explicit per-line type column, then
    the caller's default_type, then fall back to 'video' (the only remaining
    shape for a bare 11-char token)."""
    return (forced or _shape_type(canonical_id) or line_type
            or _norm_type(default_type) or "video")


def _token_id(tok: str) -> Optional[tuple[str, Optional[str]]]:
    """Return (canonical_id, forced_type|None) for a single bare token, or None.

    URL forms first (a full URL token), then bare UC-id, @handle, and finally a
    standalone 11-char video id. The 11-char branch is last and only fires on an
    EXACT-length token so it can't swallow a substring of a 24-char channel id.
    forced_type is set when the shape/URL is authoritative."""
    tok = tok.strip().strip(",")
    if not tok:
        return None
    m = _RE_URL_CHANNEL.search(tok)
    if m:
        return m.group(1), "channel"
    for rx in (_RE_URL_WATCH, _RE_URL_SHORT, _RE_URL_SHORTS):
        m = rx.search(tok)
        if m:
            return m.group(1), "video"
    m = _RE_URL_HANDLE.search(tok)
    if m:
        return m.group(1), "channel"
    if _RE_CHANNEL_TOKEN.match(tok):
        return tok, "channel"
    if _RE_HANDLE_TOKEN.match(tok):
        return tok, "channel"
    if _RE_VIDEO_TOKEN.match(tok):
        return tok, None                     # bare 11-char → defer to explicit/default
    return None


def _split_csv_line(line: str):
    """If `line` is the canonical `id,type,label` CSV shape (field 1 is a
    YouTube id and field 2 is a type word or empty), return
    (canonical_id, forced_type, line_type, label). The label is the REST after
    the 2nd comma, so it may contain commas/spaces/hyphens ("US - Noodah05").

    Returns None if field 1 isn't an id, OR if field 2 is non-empty but isn't a
    type word — that means the line is a comma-separated LIST of ids
    ("UC1, UC2, UC3"), not a single CSV row, so the freeform extractor should
    handle it and pull every id."""
    parts = line.split(",", 2)
    hit = _token_id(parts[0])
    if hit is None:
        return None
    field2 = parts[1].strip() if len(parts) >= 2 else ""
    if field2 and _norm_type(field2) is None:
        return None                          # not `id,type,…` → a list; defer to freeform
    cid, forced = hit
    line_type = _norm_type(field2) if field2 else None
    label = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
    return cid, forced, line_type, label


def _extract_freeform(line: str) -> list[tuple[str, Optional[str]]]:
    """Pull every canonical id (with forced type) out of an arbitrary line.

    UC-ids are consumed and blanked FIRST so the 11-char video matcher can never
    nibble a substring of a 24-char channel id; then video URLs, then handles,
    then standalone 11-char tokens."""
    found: list[tuple[str, Optional[str]]] = []
    work = line
    for m in _RE_CHANNEL_ID.finditer(work):
        found.append((m.group(0), "channel"))
    work = _RE_CHANNEL_ID.sub(" ", work)
    for rx in (_RE_URL_WATCH, _RE_URL_SHORT, _RE_URL_SHORTS):
        for m in rx.finditer(work):
            found.append((m.group(1), "video"))
        work = rx.sub(" ", work)
    for m in re.finditer(_HANDLE, work):
        found.append((m.group(0), "channel"))
    work = re.sub(_HANDLE, " ", work)
    for tok in work.split():
        if _RE_VIDEO_TOKEN.match(tok):
            found.append((tok, None))        # bare 11-char → defer to default
    return found


def parse_items(text: str, default_type: Optional[str] = None
                ) -> tuple[list[InputItem], ParseReport]:
    """Normalize pasted text into a deduped list[InputItem] + a ParseReport.

    Per (CRLF-normalized) line: skip blanks and a header row, try the canonical
    CSV shape (id in field 1) → id + per-line type + label, otherwise extract
    every YouTube id in the line (freeform / URLs). A line that yields no id is
    recorded in report.unrecognized. Dedupe by canonical id, first-seen wins,
    order preserved."""
    report = ParseReport()
    seen: dict[str, InputItem] = {}

    raw_lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        # header row: every comma-field is a known header word, no id in field 1
        fields = [f.strip().lower() for f in line.split(",")]
        if fields and all(f in _HEADER_TOKENS for f in fields) \
                and _token_id(fields[0]) is None:
            continue

        csv_hit = _split_csv_line(line)
        if csv_hit is not None:
            cid, forced, line_type, label = csv_hit
            _add(seen, report, cid,
                 _resolve_type(cid, forced, line_type, default_type), label)
            continue

        ids = _extract_freeform(line)
        if not ids:
            report.unrecognized.append(line)
            continue
        for cid, forced in ids:
            _add(seen, report, cid,
                 _resolve_type(cid, forced, None, default_type), None)

    items = list(seen.values())
    report.channels = sum(1 for i in items if i.type == "channel")
    report.videos = sum(1 for i in items if i.type == "video")
    return items, report


def _add(seen: dict, report: ParseReport, cid: str, type_: str,
         label: Optional[str]) -> None:
    if cid in seen:
        report.n_deduped += 1
        if label and not seen[cid].label:    # back-fill a label seen later
            seen[cid].label = label
        return
    seen[cid] = InputItem(id=cid, type=type_, label=label)
