"""All in-page JavaScript as named constants — single audit point.
Every snippet here was validated live against YouTube's 2026 UI during the
planning probes (see plan: lockupViewModel, transcript-segment-view-model)."""

# Re-injected after EVERY navigation — page reloads wipe window globals.
DEEP_FIND = r"""
window.__ytqcFind = function(obj, key, out, depth) {
  out = out || []; depth = depth || 0;
  if (!obj || typeof obj !== 'object' || depth > 28) return out;
  if (obj[key] !== undefined) out.push(obj[key]);
  for (const k in obj) window.__ytqcFind(obj[k], key, out, depth + 1);
  return out; };
"""

WATCH_READY = r"""JSON.stringify((() => {
  const p = document.getElementById('movie_player');
  return {r: !!(p && p.getPlayerResponse && p.getPlayerResponse())};
})())"""

CHANNEL_READY = r"""JSON.stringify({r: !!window.ytInitialData})"""

PLAYER_RESPONSE = r"""JSON.stringify((() => {
  const p = document.getElementById('movie_player');
  const pr = (p && p.getPlayerResponse && p.getPlayerResponse())
          || window.ytInitialPlayerResponse || null;
  if (!pr) return {ok: false};
  const vd = pr.videoDetails || {};
  const mf = (pr.microformat || {}).playerMicroformatRenderer || {};
  const tracks = ((pr.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks || [];
  return {
    ok: true,
    status: (pr.playabilityStatus || {}).status || '',
    reason: ((pr.playabilityStatus || {}).reason)
      || (((pr.playabilityStatus || {}).errorScreen || {}).playerErrorMessageRenderer || {}).reason || '',
    hasVideoDetails: !!pr.videoDetails,
    title: vd.title || '',
    author: vd.author || '',
    channelId: vd.channelId || '',
    lengthSeconds: vd.lengthSeconds || '0',
    viewCount: vd.viewCount || '0',
    keywords: vd.keywords || [],
    shortDescription: (vd.shortDescription || '').substring(0, 2500),
    isLiveContent: !!vd.isLiveContent,
    publishDate: mf.publishDate || '',
    category: mf.category || '',
    isFamilySafe: mf.isFamilySafe,
    tracks: tracks.map(t => ({lang: t.languageCode, kind: t.kind || 'manual'})),
  };
})())"""

LIKES = r"""JSON.stringify((() => {
  const b = document.querySelector(
    'like-button-view-model button, segmented-like-dislike-button-view-model button, #segmented-like-button button');
  const al = (b && b.getAttribute('aria-label')) || '';
  const m = al.match(/[\d,.]+[KMB]?/i);
  return {likes_text: m ? m[0] : ''};
})())"""

COMMENTS = r"""JSON.stringify((() => {
  const count = (document.querySelector('ytd-comments-header-renderer h2, ytd-comments-header-renderer #count')
      || {}).textContent || '';
  const threads = Array.from(document.querySelectorAll('ytd-comment-thread-renderer'))
    .slice(0, __TOP_N__).map(c => ({
      author: ((c.querySelector('#author-text') || {}).textContent || '').trim().slice(0, 60),
      text: ((c.querySelector('#content-text') || {}).textContent || '').trim().slice(0, 300),
      likes: ((c.querySelector('#vote-count-middle') || {}).textContent || '0').trim(),
    }));
  return {count_text: count.replace(/\s+/g, ' ').trim().slice(0, 40), comments: threads};
})())"""

# Transcript: open via the description section's button (validated; the panel
# target-id is PAmodern_transcript_view in 2026, segments render as
# transcript-segment-view-model custom elements).
TRANSCRIPT_OPEN = r"""JSON.stringify((() => {
  if (document.querySelector('transcript-segment-view-model')) return {state: 'open'};
  const exp = document.querySelector('tp-yt-paper-button#expand, #expand');
  if (exp) exp.click();
  const section = document.querySelector('ytd-video-description-transcript-section-renderer');
  const btn = (section && section.querySelector('button'))
    || Array.from(document.querySelectorAll('button'))
        .find(b => /transcript/i.test(b.getAttribute('aria-label') || ''));
  if (btn) { btn.click(); return {state: 'clicked'}; }
  return {state: 'no-button'};
})())"""

TRANSCRIPT_SCRAPE = r"""JSON.stringify((() => {
  const els = Array.from(document.querySelectorAll('transcript-segment-view-model'));
  const legacy = els.length ? els
    : Array.from(document.querySelectorAll('ytd-transcript-segment-renderer'));
  const segs = legacy.map(e => {
    const ts = e.querySelector('.segment-timestamp, [class*="timestamp"]');
    const tx = e.querySelector('.segment-text, [class*="segment-text"], yt-formatted-string');
    let t = ts ? ts.textContent.trim() : '';
    let text = tx ? tx.textContent.trim() : '';
    if (!t || !text) {
      // 2026 view-model: derive from innerText, strip a11y duration prefix
      const raw = (e.innerText || '').trim();
      const m = raw.match(/^(\d{1,2}(?::\d{2}){1,2})\s*\n?([\s\S]*)$/);
      if (m) {
        t = t || m[1];
        text = text || m[2].replace(/^\s*\d+\s+(?:seconds?|minutes?,?\s*(?:\d+\s+seconds?)?)\s*/i, '').trim();
      }
    }
    return {t: t, text: text.replace(/\s+/g, ' ')};
  }).filter(s => s.t && s.text);
  return {n: segs.length, segs: segs};
})())"""

# Frame capture — canvas grab (validated: MSE video does not taint canvas).
FRAME_SEEK = r"""JSON.stringify((() => {
  const v = document.querySelector('#movie_player video');
  if (!v) return {ok: false, why: 'no-video'};
  v.muted = true;
  if (v.paused) { try { v.play(); } catch (e) {} }
  v.currentTime = __T__;
  return {ok: true};
})())"""

FRAME_READY = r"""JSON.stringify((() => {
  const p = document.getElementById('movie_player');
  const v = document.querySelector('#movie_player video');
  if (!v) return {seeking: false, ready: false, ad: false};
  // ad detection: CSS class OR player API ad-state (>=0 means an ad is active).
  // getAdState is the more reliable signal (probe: returns -1 when no ad).
  let adState = -1;
  try { if (p && p.getAdState) adState = p.getAdState(); } catch (e) {}
  const ad = !!(p && p.classList.contains('ad-showing')) || adState >= 0;
  return {seeking: v.seeking, ready: v.readyState >= 2, ad: ad};
})())"""

FRAME_GRAB = r"""(() => { try {
  const v = document.querySelector('#movie_player video');
  if (!v) return 'ERR:no-video';
  const W = Math.min(v.videoWidth || 640, 768);
  const H = Math.round(W * (v.videoHeight || 360) / (v.videoWidth || 640));
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  c.getContext('2d').drawImage(v, 0, 0, W, H);
  return c.toDataURL('image/jpeg', 0.7);
} catch (e) { return 'ERR:' + e.name; } })()"""

# Ad skip — selector-agnostic + self-healing. The exact 2026 skip-button class
# could not be live-confirmed (the probe session was served zero ad placements),
# so this tries the known classes AND falls back to scanning the ad overlay for
# ANY visible/enabled button whose aria-label/text/class says "skip". Also
# asserts muted playback each call — a paused pre-roll never reveals the button.
# Returns {ad, skippable, skipped}. skippable=false during the ~0-5s countdown
# (button present but not yet shown) or for non-skippable bumpers.
AD_SKIP = r"""JSON.stringify((() => {
  const p = document.getElementById('movie_player');
  const v = document.querySelector('#movie_player video');
  if (v) { v.muted = true; if (v.paused) { try { v.play(); } catch (e) {} } }
  let adState = -1;
  try { if (p && p.getAdState) adState = p.getAdState(); } catch (e) {}
  const ad = !!(p && p.classList.contains('ad-showing')) || adState >= 0;
  if (!ad) return {ad: false, skippable: false, skipped: false};

  const clickable = (el) => el && el.offsetParent !== null && !el.disabled;
  // 1) known skip-button classes (live-verify and reorder when an ad is available)
  const SEL = ['.ytp-skip-ad-button', '.ytp-ad-skip-button-modern',
               '.ytp-ad-skip-button', '.ytp-skip-ad-button__text',
               '.ytp-ad-skip-button-container button', 'button.ytp-ad-skip-button-modern'];
  let btn = null;
  for (const s of SEL) {
    const el = document.querySelector(s);
    if (el) { const b = el.closest('button,[role="button"]') || el; if (clickable(b)) { btn = b; break; } }
  }
  // 2) fallback: scan ad-overlay buttons for a /skip/ label/text/class
  if (!btn) {
    const scope = document.querySelector(
      '.ytp-ad-module, .ytp-ad-player-overlay, .video-ads, #movie_player') || document;
    for (const b of scope.querySelectorAll('button,[role="button"]')) {
      const sig = ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '')
                   + ' ' + (b.className || '')).toLowerCase();
      if (/\bskip\b/.test(sig) && clickable(b)) { btn = b; break; }
    }
  }
  if (btn) { try { btn.click(); } catch (e) {} return {ad: true, skippable: true, skipped: true}; }
  return {ad: true, skippable: false, skipped: false};   // countdown or non-skippable bumper
})())"""

PLAYER_QUALITY = r"""(() => {
  const p = document.getElementById('movie_player');
  if (p && p.setPlaybackQualityRange) { try { p.setPlaybackQualityRange('hd720'); } catch (e) {} }
  return 1; })()"""

PLAYER_PAUSE = r"""(() => { const v = document.querySelector('#movie_player video');
  if (v) v.pause(); return 1; })()"""

# Channel pages (validated: aboutChannelViewModel on /about, lockupViewModel on /videos)
CHANNEL_ABOUT = DEEP_FIND + r"""
JSON.stringify((() => {
  const about = window.__ytqcFind(window.ytInitialData, 'aboutChannelViewModel')[0] || null;
  const meta = ((window.ytInitialData || {}).metadata || {}).channelMetadataRenderer || {};
  if (!about && !meta.title) return {ok: false};
  const links = ((about || {}).links || []).map(l => {
    const lv = l.channelExternalLinkViewModel || {};
    return {title: ((lv.title || {}).content) || '', url: ((lv.link || {}).content) || ''};
  });
  return {
    ok: true,
    title: meta.title || '',
    externalId: meta.externalId || '',
    description: ((about || {}).description || meta.description || '').substring(0, 2500),
    subscriberCountText: (about || {}).subscriberCountText || '',
    viewCountText: (about || {}).viewCountText || '',
    videoCountText: (about || {}).videoCountText || '',
    country: (about || {}).country || '',
    joinedDateText: (((about || {}).joinedDateText) || {}).content || '',
    links: links,
    channelKeywords: meta.keywords || '',
    isFamilySafe: meta.isFamilySafe,
  };
})())"""

CHANNEL_VIDEOS = DEEP_FIND + r"""
JSON.stringify((() => {
  // 2026 primary: lockupViewModel; legacy fallback: videoRenderer
  const lockups = window.__ytqcFind(window.ytInitialData, 'lockupViewModel');
  let vids = lockups.map(l => {
    const md = (l.metadata || {}).lockupMetadataViewModel || {};
    const parts = window.__ytqcFind(md, 'metadataParts').flat();
    const texts = parts.map(p => ((p.text || {}).content) || '').filter(Boolean);
    const views = texts.find(t => /view/i.test(t)) || '';
    const age = texts.find(t => /ago/i.test(t)) || '';
    return {id: l.contentId || '', title: ((md.title || {}).content) || '', views: views, age: age};
  }).filter(v => v.id);
  if (!vids.length) {
    vids = window.__ytqcFind(window.ytInitialData, 'videoRenderer').map(v => ({
      id: v.videoId || '',
      title: (((v.title || {}).runs || [])[0] || {}).text || '',
      views: ((v.viewCountText || {}).simpleText) || '',
      age: ((v.publishedTimeText || {}).simpleText) || '',
    })).filter(v => v.id);
  }
  return {n: vids.length, vids: vids.slice(0, 30)};
})())"""

# Full catalog read: ytInitialData (page 1, ~30) + YouTube continuation data API
# (/youtubei/v1/browse) for up to __PAGES__ more pages (~30 each). Data-only — no
# scroll/visibility dependency (the grid virtualizes and infinite-scroll does NOT
# fire in a backgrounded automation tab; the API does, reliably). Async: the bridge
# awaits the returned promise (validated live 2026).
CHANNEL_VIDEOS_ALL = DEEP_FIND + r"""
(async () => { try {
  const F = window.__ytqcFind;
  const seen = new Set(); const out = [];
  const collect = (root) => {
    F(root, 'lockupViewModel').forEach(l => {
      const id = l.contentId || '';
      if (!id || seen.has(id)) return;
      const md = (l.metadata || {}).lockupMetadataViewModel || {};
      const title = ((md.title || {}).content) || '';
      const texts = F(md, 'metadataParts').flat()
        .map(p => ((p.text || {}).content) || '').filter(Boolean);
      const views = texts.find(t => /view/i.test(t)) || '';
      const age = texts.find(t => /ago/i.test(t)) || '';
      if (title) { seen.add(id); out.push({id: id, title: title, views: views, age: age}); }
    });
  };
  const nextTok = (root) => {
    const c = F(root, 'continuationCommand').map(x => x && x.token).filter(Boolean);
    return c[0] || '';
  };
  collect(window.ytInitialData);
  let tok = nextTok(window.ytInitialData);
  const key = (window.ytcfg && window.ytcfg.get) ? window.ytcfg.get('INNERTUBE_API_KEY') : '';
  const ctx = (window.ytcfg && window.ytcfg.get) ? window.ytcfg.get('INNERTUBE_CONTEXT') : null;
  let pages = __PAGES__;
  while (tok && pages > 0 && key && ctx) {
    try {
      const r = await fetch('/youtubei/v1/browse?key=' + key, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({context: ctx, continuation: tok}),
      });
      const j = await r.json();
      collect(j);
      tok = nextTok(j);
    } catch (e) { break; }
    pages--;
  }
  return JSON.stringify({n: out.length, vids: out});
} catch (e) { return JSON.stringify({n: 0, vids: [], error: String(e).slice(0, 150)}); } })()"""

# ── VidIQ extension overlay (light DOM — no shadow root; validated live 2026) ──
# Anchor on stable ids + visible-text labels, never on VidIQ's hashed class names.
# Both probes self-report present:false when the panel is absent/not-yet-rendered,
# so the scraper can poll them. Each returns verbatim strings (no coercion).
#
# Video watch page: VidIQ injects div#video-companion-root.vidiq-react into
# #secondary. Its Overview tab (default) shows the channel card + video SEO score.
VIDIQ_VIDEO_SCRAPE = r"""JSON.stringify((() => {
  let root = document.getElementById('video-companion-root');
  if (!root) {                       // fallback: smallest #secondary node with the labels
    const sec = document.querySelector('#secondary');
    if (sec) {
      const c = Array.from(sec.querySelectorAll('*')).filter(e => {
        const t = e.textContent || '';
        return t.includes('Overview') && t.includes('Subs');
      });
      c.sort((a, b) => a.textContent.length - b.textContent.length);
      root = c[0] || null;
    }
  }
  if (!root) return {present: false};
  const t = (root.innerText || '').replace(/\s+/g, ' ').trim();
  const g = (re) => { const m = t.match(re); return m ? m[1].trim() : ''; };
  const subscribers = g(/Subs\s+([\d.,]+[KMB]?)/i);
  const seo_score = g(/([\d.]+)\s*\/\s*100\s*vidIQ SEO/i);
  // gate readiness on an actual value: labels render before VidIQ's async fetch
  // populates the numbers, so wait for a real value rather than just a label.
  if (!subscribers && !seo_score) return {present: false};
  const locked = /Controversial Keywords/i.test(t) &&
                 /(Upgrade to Boost|Unlock controversial)/i.test(t);
  return {
    present: true,
    subscribers: subscribers,
    total_views: g(/Subs\s+[\d.,]+[KMB]?\s+Views\s+([\d.,]+[KMB]?)/i),
    video_count: g(/([\d.,]+)\s+videos/i),
    channel_age: g(/(\d+\s+years?\s+old)/i),
    seo_score: seo_score,
    controversial_locked: locked,
    raw_text: t.slice(0, 2000),
  };
})())"""

# Channel page: VidIQ injects a "Quick channel stats" block into #page-header
# (adds a .vidiq-scope class). Present on every channel tab incl. /videos.
VIDIQ_CHANNEL_SCRAPE = r"""JSON.stringify((() => {
  const hdr = document.querySelector('#page-header');
  if (!hdr) return {present: false};
  const full = (hdr.innerText || '').replace(/\s+/g, ' ').trim();
  const g = (re) => { const m = full.match(re); return m ? m[1].trim() : ''; };
  const subscribers = g(/Subscribers\s+([\d.,]+[KMB]?)/i);
  const rank = g(/Ranked\s*(#[\d.,]+[KMB]?)/i);
  const views_gained_7d = g(/Views gained \(7 days\)\s*([+\-]?[\d.,]+)/i);
  const est_monthly_earnings = g(/Est\. monthly earnings\s*(US?\$[\d.,]+[KMB]?)/i);
  // gate on a populated value, not just the panel scaffold (async-rendered)
  if (!subscribers && !rank && !views_gained_7d && !est_monthly_earnings)
    return {present: false};
  let similar = [];
  const sm = full.match(/Similar channels/i);
  if (sm) {
    const tail = full.slice(sm.index + sm[0].length);
    const re = /([A-Za-z0-9][A-Za-z0-9 ._'&-]*?)\s+[\d.,]+[KMB]?\s+subscribers/gi;
    let m;
    while ((m = re.exec(tail)) && similar.length < 8) similar.push(m[1].trim());
  }
  const qi = full.search(/Quick channel stats|View channel stats/i);
  return {
    present: true,
    subscribers: subscribers,
    subscribers_growth: g(/Subscribers\s+[\d.,]+[KMB]?\s+([+\-][\d.,]+%)/i),
    views_gained_7d: views_gained_7d,
    video_count: g(/([\d.,]+)\s+videos/i),
    rank: rank,
    est_monthly_earnings: est_monthly_earnings,
    avg_video_length: g(/Avg\. video length\s*([\d.,]+\s*\w+)/i),
    upload_frequency: g(/Upload frequency\s*(~?[\d.,]+\s*uploads?\s*per\s*\w+)/i),
    similar_channels: similar,
    raw_text: (qi >= 0 ? full.slice(qi) : full).slice(0, 2000),
  };
})())"""
