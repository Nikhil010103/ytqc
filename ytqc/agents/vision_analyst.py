"""Vision Analyst — one vision call per video. Separate from the Content
Analyst for failure isolation: when it dies, the flow degrades to text-only
with a confidence penalty instead of losing the whole item."""
from __future__ import annotations

import json
import logging

from ytqc.llm.client import LLMClient
from ytqc.llm.prompts import VISION_ANALYST_SYSTEM
from ytqc.models import FrameSet, VisionEvidence

log = logging.getLogger("ytqc.vision")


def analyze_frames(
    llm: LLMClient,
    frames: FrameSet,
    title: str,
    author: str,
    frame_labels: list[str] | None = None,
) -> VisionEvidence:
    images = []
    roles = []
    if frames.thumbnail_b64:
        images.append(frames.thumbnail_b64)
        roles.append("image 1 = thumbnail")
    for i, b64 in enumerate(frames.frames_b64):
        images.append(b64)
        label = (frame_labels or [])[i] if frame_labels and i < len(frame_labels) else f"t={frames.frame_timestamps[i]:.0f}s" if i < len(frames.frame_timestamps) else "frame"
        roles.append(f"image {len(images)} = frame ({label})")

    if not images:
        return VisionEvidence(ok=False, error="no frames captured")

    user = (
        f"Video: {title!r} by {author!r}\n"
        f"Images: {'; '.join(roles)}\n"
        "Analyze per the schema."
    )
    try:
        raw = llm.chat_json(VISION_ANALYST_SYSTEM, user, images_b64=images, temperature=0.1)
        return VisionEvidence.model_validate({**raw, "ok": True})
    except Exception as exc:
        log.warning("vision analyst failed: %s", exc)
        return VisionEvidence(ok=False, error=str(exc)[:200])


def vision_digest(ev: VisionEvidence) -> str:
    """Compact JSON digest injected into the Content Analyst prompt."""
    if not ev.ok:
        return ""
    d = ev.model_dump(exclude={"ok", "error"})
    return json.dumps(d, ensure_ascii=False)
