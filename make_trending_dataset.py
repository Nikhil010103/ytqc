#!/usr/bin/env python3
"""Build ytqc test datasets from the mirrors trending source
(youtube-data16.p.rapidapi.com/popularVideos — RapidAPI-proxied YouTube Data
API mostPopular). One call returns ~50 trending videos per region, each
carrying its channelId. We gather US + IN, then derive the channel set from
the video set.

Outputs (id,type[,label] — the ytqc input contract):
  trending_videos.csv    ~100 rows, type=video
  trending_channels.csv  unique channels from those videos, type=channel
"""
from __future__ import annotations

import csv
import os
import sys

import httpx

HOST = "youtube-data16.p.rapidapi.com"
# Read the RapidAPI key from the environment — never hard-code secrets.
#   export YT_DATA16_RAPIDAPI_KEY="…"      (or put it in a gitignored .env)
KEY = os.environ.get("YT_DATA16_RAPIDAPI_KEY", "")
REGIONS = ["US", "IN"]
OUT_DIR = os.environ.get("YTQC_DATASET_OUT", ".")


def fetch_region(region: str) -> list[dict]:
    headers = {"x-rapidapi-key": KEY, "x-rapidapi-host": HOST}
    params = {"regionCode": region, "maxResults": 50, "hl": "en"}
    r = httpx.get(f"https://{HOST}/popularVideos", params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", []) or []
    print(f"  {region}: {len(data)} videos "
          f"(quota left: {r.headers.get('x-ratelimit-requests-remaining', '?')})")
    return [{**it, "_region": region} for it in data if isinstance(it, dict) and it.get("id")]


def main() -> None:
    if not KEY:
        print("error: set YT_DATA16_RAPIDAPI_KEY first — a free RapidAPI key for the trending "
              "test-data source.\n  export YT_DATA16_RAPIDAPI_KEY=…", file=sys.stderr)
        sys.exit(2)

    videos: dict[str, dict] = {}          # video_id -> row (dedup across regions)
    channels: dict[str, dict] = {}        # channel_id -> row

    for region in REGIONS:
        print(f"fetching trending for {region} …")
        for it in fetch_region(region):
            vid = it["id"]
            cid = it.get("channelId", "")
            ctitle = it.get("channelTitle", "")
            vtitle = it.get("title", "")
            if vid not in videos:
                videos[vid] = {
                    "id": vid, "type": "video",
                    "label": f"{it['_region']} - {vtitle[:60]}",
                }
            if cid and cid not in channels:
                channels[cid] = {
                    "id": cid, "type": "channel",
                    "label": f"{it['_region']} - {ctitle[:60]}",
                }

    vpath = f"{OUT_DIR}/trending_videos.csv"
    cpath = f"{OUT_DIR}/trending_channels.csv"
    for path, rows in ((vpath, list(videos.values())), (cpath, list(channels.values()))):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "type", "label"])
            w.writeheader()
            w.writerows(rows)

    print(f"\nwrote {len(videos)} videos  -> {vpath}")
    print(f"wrote {len(channels)} channels -> {cpath}")
    if not videos:
        print("WARNING: zero videos returned — check the API key / quota.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
