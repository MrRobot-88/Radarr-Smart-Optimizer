#!/usr/bin/env python3

import os
import sys
import json
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ============================================================
# RADARR SMART OPTIMIZER
#
# Default = DRY RUN
# Live    = --live
#
# IMPORTANT:
# - NEVER deletes media files
# - NEVER calls Radarr DELETE endpoints
# - NEVER touches Deluge directly
# - Radarr performs normal Completed Download Handling/import
# - Search budgets are configurable; defaults are conservative for scheduled use
# ============================================================

# ============================================================
# QUICK SETUP - most users only need to edit this block
# ============================================================
# 1) Paste your Radarr API key below.
# 2) Check the URL if Radarr is not on the same machine.
# 3) Set how many interactive searches this script may do per run.
# 4) IMPORTANT: review NORMAL_PROFILE_ID and UHD_PROFILE_ID below.
#
# Environment variables still work and override these values, which is
# useful for Docker, cron and Synology Task Scheduler.
RADARR_API_KEY = "PASTE_YOUR_RADARR_API_KEY_HERE"
RADARR_URL_DEFAULT = "http://127.0.0.1:7878"
SEARCHES_PER_RUN = 50

RADARR_URL = os.environ.get("RADARR_URL", RADARR_URL_DEFAULT).rstrip("/")
API_KEY = os.environ.get("RADARR_KEY", RADARR_API_KEY).strip()
SEARCHES_PER_RUN = int(os.environ.get("RADARR_SEARCHES_PER_RUN", SEARCHES_PER_RUN))

if API_KEY == "PASTE_YOUR_RADARR_API_KEY_HERE":
    API_KEY = ""

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))\nSTATE_FILE = os.environ.get(\n    "RADARR_OPTIMIZER_STATE",\n    os.path.join(SCRIPT_DIR, "radarr-smart-optimizer-state.json")\n)

NORMAL_PROFILE_ID = 4
UHD_PROFILE_ID = 5

DAILY_SEARCH_BUDGET = 300
MIN_SEEDERS = 1
MIN_SAVING_PERCENT = 5.0

# Search cooldowns
COOLDOWN_RESOLUTION_UPGRADE = 30
COOLDOWN_X264 = 90
COOLDOWN_X265_LARGE = 180
COOLDOWN_X265_COMPACT = 365
COOLDOWN_4K = 180

# Don't deliberately grab the exact same release again for this long
ATTEMPT_COOLDOWN_DAYS = 365

# These are NOT hard quality limits.
# They are only used for PRIORITY.
LARGE_1080P_MIB = 1800
COMPACT_1080P_X265_MIB = 1200
LARGE_2160P_MIB = 6000

# Hard ceiling for a 1080p -> 2160p resolution upgrade.
# 8 GiB = 8192 MiB.
MAX_4K_UPGRADE_MIB = 8192

# Prevent one large series from consuming the whole daily budget.
MAX_SEARCHES_PER_SERIES_PER_RUN = 3

LIVE = "--live" in sys.argv

if not API_KEY:
    print("ERROR: Radarr API key is not configured.")
    print()
    print("Edit RADARR_API_KEY near the top of this script, or set RADARR_KEY.")
    print("Then run: python3 radarr-smart-optimizer.py")
    sys.exit(1)


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def age_days(timestamp):
    if not timestamp:
        return 999999
    return (now_ts() - int(timestamp)) / 86400.0


def mib(value):
    try:
        return float(value) / 1024 / 1024
    except Exception:
        return 0.0


def api(method, path, data=None):
    url = RADARR_URL + "/api/v3" + path

    headers = {
        "X-Api-Key": API_KEY,
        "Accept": "application/json"
    }

    body = None

    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            raw = response.read()

            if not raw:
                return None

            return json.loads(raw.decode("utf-8"))

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            "%s %s -> HTTP %s\n%s" %
            (method, path, e.code, detail)
        )

    except Exception as e:
        raise RuntimeError(
            "%s %s -> %s" %
            (method, path, e)
        )


def get(path):
    return api("GET", path)


def post(path, data):
    return api("POST", path, data)


# ============================================================
# STATE
# ============================================================

def blank_state():
    return {
        "version": 1,
        "movies": {},
        "attempted_releases": {},
        "daily": {}
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return blank_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        state.setdefault("version", 1)
        state.setdefault("movies", {})
        state.setdefault("attempted_releases", {})
        state.setdefault("daily", {})

        return state

    except Exception as e:
        print("WARNING: Could not read state file:")
        print(" ", e)
        print("Using empty state for this run.")
        return blank_state()


def save_state(state):
    if not LIVE:
        return

    tmp = STATE_FILE + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)

    os.replace(tmp, STATE_FILE)


def today_key():
    return datetime.now().strftime("%Y-%m-%d")


def searches_used_today(state):
    return int(
        state.get("daily", {})
             .get(today_key(), {})
             .get("searches", 0)
    )


def increment_search_count(state):
    day = today_key()

    state.setdefault("daily", {})
    state["daily"].setdefault(day, {"searches": 0})

    state["daily"][day]["searches"] += 1

    # Remove ancient daily counters.
    keys = sorted(state["daily"].keys())

    if len(keys) > 60:
        for old in keys[:-60]:
            state["daily"].pop(old, None)


def mark_movie_searched(state, movie_id):
    key = str(movie_id)
    state["movies"].setdefault(key, {})
    entry = state["movies"][key]
    entry["last_search"] = now_ts()
    entry["search_cycles"] = min(2, int(entry.get("search_cycles", 0)) + 1)


def release_key(release):
    for field in (
        "guid",
        "downloadUrl",
        "infoUrl",
        "title"
    ):
        value = release.get(field)
        if value:
            return str(value)

    return str(release.get("title", "UNKNOWN"))


def release_recently_attempted(state, release):
    key = release_key(release)

    ts = state.get("attempted_releases", {}).get(key)

    if not ts:
        return False

    return age_days(ts) < ATTEMPT_COOLDOWN_DAYS


def mark_release_attempted(state, release):
    key = release_key(release)
    state.setdefault("attempted_releases", {})
    state["attempted_releases"][key] = now_ts()


def clean_old_attempts(state):
    cutoff = ATTEMPT_COOLDOWN_DAYS + 30

    remove = []

    for key, ts in state.get("attempted_releases", {}).items():
        if age_days(ts) > cutoff:
            remove.append(key)

    for key in remove:
        state["attempted_releases"].pop(key, None)


# ============================================================
# MEDIA PARSING
# ============================================================

def codec_from_text(text):
    """
    Detect VIDEO codec from a release title.

    Normalized values:
      x265
      x264
      av1
      vp9
      vp8
      vc1
      unknown

    MKV/MP4 are containers and intentionally do NOT determine codec.
    """
    text = text or ""

    # H.265 / HEVC
    if re.search(
        r'(?i)(?:\bx[ ._-]?265\b|\bh[ ._-]?265\b|\bhevc\b)',
        text
    ):
        return "x265"

    # H.264 / AVC
    if re.search(
        r'(?i)(?:\bx[ ._-]?264\b|\bh[ ._-]?264\b|\bavc\b)',
        text
    ):
        return "x264"

    # AV1 / AV01
    if re.search(
        r'(?i)(?:\bav[ ._-]?1\b|\bav01\b)',
        text
    ):
        return "av1"

    # VP9 / VP09
    if re.search(
        r'(?i)(?:\bvp[ ._-]?9\b|\bvp09\b)',
        text
    ):
        return "vp9"

    # VP8 / VP08
    if re.search(
        r'(?i)(?:\bvp[ ._-]?8\b|\bvp08\b)',
        text
    ):
        return "vp8"

    # VC-1 / VC1
    if re.search(
        r'(?i)\bvc[ ._-]?1\b',
        text
    ):
        return "vc1"

    return "unknown"


def audio_channels_from_text(text):
    text = (text or "").lower()

    patterns = [
        (7.1, r"\b7[\s._-]?1\b"),
        (5.1, r"\b5[\s._-]?1\b"),
        (2.1, r"\b2[\s._-]?1\b"),
        (2.0, r"\b2[\s._-]?0\b"),
        (1.0, r"\b1[\s._-]?0\b"),
    ]

    for channels, pattern in patterns:
        if re.search(pattern, text):
            return channels

    if "stereo" in text:
        return 2.0

    return None


DANGEROUS_EXTENSIONS = (
    "exe", "scr", "bat", "cmd", "msi", "com",
    "pif", "vbs", "js", "jar", "ps1"
)


def dangerous_release_title(text):
    """
    Hard-block executable/script payloads.
    MKV, MP4 and other normal media containers are unaffected.
    """
    text = text or ""

    pattern = (
        r'(?i)\.(?:'
        + '|'.join(re.escape(ext) for ext in DANGEROUS_EXTENSIONS)
        + r')(?=$|[\s._\-\[\]\(\)])'
    )

    return bool(re.search(pattern, text))


def dynamic_range_from_text(text):
    """
    Conservative release-title classification.

    Returns:
      SDR_UNKNOWN
      HDR
      DV_ONLY
      DV_HDR

    IMPORTANT:
    DV-only is never acceptable.
    Dolby Vision must explicitly also advertise HDR/HDR10/HDR10+.
    """
    text = (text or "").lower()

    has_dv = bool(
        re.search(
            r"\b(dv|dovi|dolby[\s._-]?vision)\b",
            text
        )
    )

    has_hdr = bool(
        re.search(
            r"\b(hdr10\+?|hdr|hlg)\b",
            text
        )
    )

    if has_dv and has_hdr:
        return "DV_HDR"

    if has_dv:
        return "DV_ONLY"

    if has_hdr:
        return "HDR"

    return "SDR_UNKNOWN"


def dynamic_range_allowed(existing_hdr, candidate_range):
    """
    Dynamic-range replacement policy.

    Candidate SDR_UNKNOWN means the release title does not explicitly
    advertise HDR/DV. For selection purposes it is allowed exactly like
    ordinary SDR UNLESS the existing file is positively known to be HDR.

    Rules:
      existing SDR/unknown -> SDR_UNKNOWN : ALLOW
      existing SDR/unknown -> HDR         : ALLOW
      existing SDR/unknown -> DV_HDR      : ALLOW

      existing HDR         -> SDR_UNKNOWN : BLOCK
      existing HDR         -> HDR         : ALLOW
      existing HDR         -> DV_HDR      : ALLOW

      DV_ONLY is ALWAYS blocked.
    """

    if candidate_range == "DV_ONLY":
        return False

    if existing_hdr and candidate_range == "SDR_UNKNOWN":
        return False

    return True


def hdr_from_text(text):
    return dynamic_range_from_text(text) in ("HDR", "DV_HDR")

def hdr_from_media_info(media):
    if not media:
        return False

    pieces = []

    for key in (
        "videoDynamicRange",
        "videoDynamicRangeType",
        "videoCodec",
        "videoProfile"
    ):
        value = media.get(key)

        if value:
            pieces.append(str(value))

    text = " ".join(pieces).lower()

    return bool(
        re.search(
            r"(dolby|dovi|\bdv\b|hdr|hlg|pq)",
            text
        )
    )


def current_audio_channels(media):
    if not media:
        return None

    value = media.get("audioChannels")

    try:
        if value is not None:
            return float(value)
    except Exception:
        pass

    return None


def current_codec(file_obj):
    media = file_obj.get("mediaInfo") or {}

    codec = codec_from_text(
        " ".join([
            str(media.get("videoCodec", "")),
            str(file_obj.get("sceneName", "")),
            str(file_obj.get("relativePath", ""))
        ])
    )

    return codec


def quality_resolution(quality_obj):
    q = quality_obj or {}

    if "quality" in q:
        q = q.get("quality") or {}

    resolution = q.get("resolution")

    try:
        if resolution:
            return int(resolution)
    except Exception:
        pass

    name = str(q.get("name", ""))

    match = re.search(r"(2160|1080|720|480)", name)

    if match:
        return int(match.group(1))

    return 0


def file_resolution(file_obj):
    media = file_obj.get("mediaInfo") or {}

    width = media.get("width")
    height = media.get("height")

    try:
        height = int(height or 0)
    except Exception:
        height = 0

    if height >= 2000:
        return 2160

    if height >= 1000:
        return 1080

    if height >= 700:
        return 720

    return quality_resolution(file_obj.get("quality"))


# ============================================================
# QUEUE
# ============================================================

def active_movie_ids():
    ids = set()

    page = 1

    while True:
        path = (
            "/queue?page=%d&pageSize=100"
            "&includeUnknownSeriesItems=true"
        ) % page

        data = get(path)

        if not data:
            break

        records = data.get("records", [])

        for item in records:
            movie_id = item.get("movieId")

            if movie_id:
                ids.add(int(movie_id))

        total = int(data.get("totalRecords", len(records)))

        if page * 100 >= total:
            break

        page += 1

    return ids


# ============================================================
# LOCAL LIBRARY CANDIDATES
# ============================================================

def cooldown_for(item):
    if item["resolution"] < item["target_resolution"]:
        return COOLDOWN_RESOLUTION_UPGRADE

    if item["profile_id"] == UHD_PROFILE_ID:
        return COOLDOWN_4K

    if item["codec"] != "x265":
        return COOLDOWN_X264

    if item["size_mib"] <= COMPACT_1080P_X265_MIB:
        return COOLDOWN_X265_COMPACT

    return COOLDOWN_X265_LARGE


def priority_score(item):
    """
    Higher = search earlier.

    IMPORTANT:
    This only decides WHICH existing episodes deserve one of
    our scarce interactive searches.

    It does NOT decide which release wins after searching.
    Profile 5 can still prefer a valid <=8 GiB 2160p release.
    If no valid 2160p exists, smaller qualifying 1080p releases
    remain fully eligible.
    """

    res = item["resolution"]
    target = item["target_resolution"]
    size = item["size_mib"]
    codec = item["codec"]

    score = 0.0

    # --------------------------------------------------------
    # PROFILE 5 / 4K-PREFERRED SERIES
    # --------------------------------------------------------
    if item["profile_id"] == UHD_PROFILE_ID:

        # Missing target resolution is important, but don't give
        # every 1080p episode an identical gigantic score.
        if res < 2160:
            score += 600000

            # Lower-than-1080p files are much more urgent.
            if res < 1080:
                score += 300000

            # Larger existing files have more optimization
            # potential if no acceptable 4K release exists.
            score += min(size * 100, 300000)

            # x264 gets extra attention because x265 often gives
            # worthwhile space savings.
            if codec == "x264":
                score += 100000
            elif codec == "unknown":
                score += 50000

            # Already tiny 1080p x265 files can still eventually
            # be searched for 4K, but should not steal today's
            # scarce slots from much larger files.
            if (
                res == 1080
                and codec == "x265"
                and size <= COMPACT_1080P_X265_MIB
            ):
                score -= 150000

        else:
            # Already 2160p: only optimization potential matters.
            score += min(size * 25, 200000)

            if codec == "x264":
                score += 75000

            if size >= LARGE_2160P_MIB:
                score += 100000

        return score

    # --------------------------------------------------------
    # NORMAL 1080P PROFILE
    # --------------------------------------------------------

    # Resolution deficiency has highest priority.
    if res < target:
        score += 800000
        score += (target - res) * 500

    # x264 generally has greater compression-saving potential.
    if codec == "x264":
        score += 200000
    elif codec == "unknown":
        score += 100000

    # Large files deserve attention.
    if size >= LARGE_1080P_MIB:
        score += 200000

    score += min(size * 50, 250000)

    # Compact 1080p x265 is already in a very good state.
    if (
        res >= 1080
        and codec == "x265"
        and size <= COMPACT_1080P_X265_MIB
    ):
        score -= 200000

    return score

def collect_candidates(state, queued_ids):
    movies = get("/movie")
    items = []

    # Permanent hands-off list: LOTR + Hobbit trilogies.
    ignored_tmdb = {120, 121, 122, 49051, 57158, 122917}

    stats = {
        "movies": len(movies),
        "with_file": 0,
        "ignored": 0,
        "queued": 0,
        "eligible": 0,
    }

    for movie in movies:
        movie_id = movie.get("id")
        tmdb_id = movie.get("tmdbId")

        if tmdb_id in ignored_tmdb:
            stats["ignored"] += 1
            continue

        if not movie.get("hasFile"):
            continue

        stats["with_file"] += 1

        if movie_id in queued_ids:
            stats["queued"] += 1
            continue

        # Optimizer search-cycle policy:
        #   cycle 0: eligible now
        #   cycle 1: wait at least 180 days
        #   cycle 2: permanently excluded from this optimizer
        history = state.get("movies", {}).get(str(movie_id), {})
        cycles = int(history.get("search_cycles", 0))
        last_search = history.get("last_search")

        if cycles >= 2:
            continue

        if cycles == 1 and last_search and age_days(last_search) < 180:
            continue

        movie_file = movie.get("movieFile") or {}
        if not movie_file:
            continue

        size_bytes = movie_file.get("size") or 0
        if size_bytes <= 0:
            continue

        resolution = file_resolution(movie_file)
        if not resolution:
            continue

        # Optimize actual 1080p and 2160p files regardless of
        # the Radarr quality profile assigned to the movie.
        # Ignore 720p and lower.
        if resolution not in (1080, 2160):
            continue

        profile_id = movie.get("qualityProfileId")

        # Never change the resolution target for normal optimization.
        # 1080p stays 1080p; 2160p stays 2160p.
        # A 1080p -> 2160p upgrade is only allowed separately
        # where the existing UHD-profile rules explicitly permit it.
        target_resolution = resolution

        media = movie_file.get("mediaInfo") or {}

        item = {
            "movie_id": movie_id,
            "tmdb_id": tmdb_id,
            "title": movie.get("title") or "Unknown movie",
            "year": movie.get("year"),
            "profile_id": profile_id,
            "resolution": resolution,
            "target_resolution": target_resolution,
            "size_bytes": size_bytes,
            "size_mib": mib(size_bytes),
            "codec": current_codec(media),
            "audio_channels": radarr_current_audio_channels(movie_file),
            "atmos": radarr_current_atmos(movie_file),
            "dynamic_range": radarr_current_dynamic_range(movie_file),
            "movie_file": movie_file,
        }

        items.append(item)
        stats["eligible"] += 1

    return items, stats


def rejection_allowed(rejection):
    """
    We ONLY ignore Radarr's cutoff rejection because the
    optimizer intentionally evaluates replacements beyond
    Radarr's normal cutoff.

    Every other Radarr rejection remains respected.
    """

    reason = ""

    if isinstance(rejection, dict):
        reason = str(
            rejection.get("reason")
            or rejection.get("message")
            or ""
        )
    else:
        reason = str(rejection)

    reason = reason.lower()

    return "existing file meets cutoff" in reason


def radarr_rejections_ok(release):
    rejected = release.get("rejections") or []

    for rejection in rejected:
        if not rejection_allowed(rejection):
            return False

    return True


def candidate_resolution(release):
    return quality_resolution(release.get("quality"))



def candidate_resolution_from_release(release):
    return quality_resolution(release.get("quality"))


def evaluate_release(item, release, state):
    title = release.get("title") or ""

    if dangerous_release_title(title):
        return None, "dangerous"

    if not radarr_rejections_ok(release):
        return None, "radarr rejection"

    if release_recently_attempted(state, release):
        return None, "recently attempted"

    seeders = release.get("seeders")
    try:
        seeders = int(seeders)
    except (TypeError, ValueError):
        return None, "unknown seeders"

    if seeders < MIN_SEEDERS:
        return None, "not enough seeders"

    candidate_resolution = candidate_resolution_from_release(release)
    if not candidate_resolution:
        return None, "unknown resolution"

    current_resolution = item["resolution"]

    # Never downgrade resolution.
    if candidate_resolution < current_resolution:
        return None, "resolution downgrade"

    # Higher resolution is only allowed toward the UHD target.
    if candidate_resolution > current_resolution:
        if item["target_resolution"] != 2160 or candidate_resolution != 2160:
            return None, "resolution upgrade not allowed"

    size_bytes = release.get("size") or 0
    try:
        size_bytes = int(size_bytes)
    except (TypeError, ValueError):
        return None, "unknown size"

    if size_bytes <= 0:
        return None, "unknown size"

    candidate_mib = mib(size_bytes)
    current_mib = item["size_mib"]

    saving = ((current_mib - candidate_mib) / current_mib) * 100.0

    # 5% saving is required only for same-resolution replacement.
    # A legitimate UHD-profile 1080p -> 2160p upgrade may be larger.
    if candidate_resolution == current_resolution:
        if saving < MIN_SAVING_PERCENT:
            return None, "less than 5 percent saving"

    candidate_codec = codec_from_text(title)
    candidate_channels = audio_channels_from_text(title)
    candidate_atmos = radarr_candidate_atmos(title)
    candidate_dr = radarr_candidate_dynamic_range(title)

    # 4K Dolby Vision must explicitly include HDR fallback and be 10-25 GiB.
    if candidate_resolution == 2160 and candidate_dr == "DV_ONLY":
        return None, "4K DV without HDR fallback"
    if candidate_resolution == 2160 and candidate_dr == "DV_HDR":
        gib = size_bytes / float(1024 ** 3)
        if gib < 10 or gib > 25:
            return None, "4K DV HDR outside 10-25 GiB"

    if not radarr_dynamic_range_allowed(item["dynamic_range"], candidate_dr):
        return None, "dynamic range protection"

    if not radarr_audio_allowed(
        item["audio_channels"],
        item["atmos"],
        candidate_channels,
        candidate_atmos
    ):
        return None, "audio protection"

    return {
        "release": release,
        "title": title,
        "resolution": candidate_resolution,
        "size_bytes": size_bytes,
        "size_mib": candidate_mib,
        "saving_percent": saving,
        "codec": candidate_codec,
        "audio_channels": candidate_channels,
        "atmos": candidate_atmos,
        "dynamic_range": candidate_dr,
        "seeders": seeders,
    }, None


def choose_best(item, releases, state):
    accepted = []

    for release in releases:
        choice, reason = evaluate_release(item, release, state)
        if choice:
            accepted.append(choice)

    if not accepted:
        return None

    current_res = item["resolution"]

    # UHD profile: a valid higher-resolution candidate wins over
    # same-resolution storage optimization.
    higher = [x for x in accepted if x["resolution"] > current_res]
    pool = higher if higher else [
        x for x in accepted if x["resolution"] == current_res
    ]

    if not pool:
        return None

    # Same resolution: storage saving is the primary goal.
    # x265/HEVC is only a secondary preference, never a reason
    # to choose a larger file over a smaller qualifying x264 file.
    pool.sort(key=lambda x: (
        x["size_bytes"],
        0 if x["codec"] == "x265" else 1,
        -x["seeders"],
        x["title"].lower()
    ))

    return pool[0]


def describe_item(number, item):
    print(
        "%2d. %s (%s)" %
        (
            number,
            item.get("title", "Unknown"),
            item.get("year", "?")
        )
    )

    print(
        "    Current: %sp | %s | %.0f MiB | %.1fch | Atmos=%s | DR=%s" %
        (
            item.get("resolution", 0),
            item.get("codec") or "unknown",
            item.get("size_mib", 0),
            item.get("audio_channels", 0),
            item.get("atmos", False),
            item.get("dynamic_range", "SDR_UNKNOWN")
        )
    )

    print(
        "    Target: %sp | profile %s" %
        (
            item.get("target_resolution", 0),
            item.get("profile_id", "?")
        )
    )


def describe_choice(choice):
    print(
        "    FOUND: %s" %
        choice.get("title", choice["release"].get("title", "Unknown"))
    )

    print(
        "    New: %sp | %s | %.0f MiB | %.1fch | Atmos=%s | DR=%s | Seeders=%s" %
        (
            choice.get("resolution", 0),
            choice.get("codec") or "unknown",
            choice.get("size_mib", 0),
            choice.get("audio_channels") or 0,
            choice.get("atmos", False),
            choice.get("dynamic_range", "SDR_UNKNOWN"),
            choice.get("seeders", "?")
        )
    )

    if choice.get("saving_percent") is not None:
        print(
            "    Saving: %.1f%%" %
            choice["saving_percent"]
        )



# ============================================================
# RADARR MEDIA PROTECTION
# ============================================================

def radarr_current_dynamic_range(movie_file):
    media=(movie_file or {}).get("mediaInfo") or {}

    dr=str(media.get("videoDynamicRange") or "").lower()
    typ=str(media.get("videoDynamicRangeType") or "").lower()

    extra=" ".join([
        str((movie_file or {}).get("sceneName") or ""),
        str((movie_file or {}).get("relativePath") or "")
    ]).lower()

    if (
        "dolby vision" in typ
        or "dovi" in typ
        or " dv" in (" " + typ)
        or "dv " in (typ + " ")
        or "dolby vision" in extra
        or "dovi" in extra
    ):
        return "DV_HDR"

    if any(x in dr or x in typ or x in extra for x in (
        "hdr10+",
        "hdr10plus",
        "hdr10",
        "hdr",
        "hlg"
    )):
        return "HDR"

    return "SDR_UNKNOWN"


def radarr_current_atmos(movie_file):
    media=(movie_file or {}).get("mediaInfo") or {}

    text=" ".join([
        str(media.get("audioCodec") or ""),
        str((movie_file or {}).get("sceneName") or ""),
        str((movie_file or {}).get("relativePath") or "")
    ]).lower()

    return "atmos" in text


def radarr_current_audio_channels(movie_file):
    media=(movie_file or {}).get("mediaInfo") or {}

    try:
        return float(media.get("audioChannels") or 0)
    except (TypeError, ValueError):
        return 0.0


def radarr_candidate_atmos(title):
    return "atmos" in str(title or "").lower()


def radarr_candidate_dynamic_range(title):
    t=str(title or "").lower()

    dv=(
        "dolby vision" in t
        or "dovi" in t
        or bool(re.search(
            r"(?<![a-z0-9])dv(?![a-z0-9])",
            t
        ))
    )

    hdr=any(x in t for x in (
        "hdr10+",
        "hdr10plus",
        "hdr10",
        "hdr",
        "hlg"
    ))

    if dv and hdr:
        return "DV_HDR"

    if dv:
        return "DV_ONLY"

    if hdr:
        return "HDR"

    return "SDR_UNKNOWN"


def radarr_dynamic_range_allowed(current, candidate):
    # DV without HDR fallback is never accepted.
    if candidate == "DV_ONLY":
        return False

    # Existing DV+HDR must remain DV+HDR.
    if current == "DV_HDR":
        return candidate == "DV_HDR"

    # Existing HDR may remain HDR or become DV+HDR.
    if current == "HDR":
        return candidate in ("HDR", "DV_HDR")

    # SDR/unknown may move to HDR/DV or remain SDR/unknown.
    return candidate in (
        "SDR_UNKNOWN",
        "HDR",
        "DV_HDR"
    )


def radarr_audio_allowed(
    current_channels,
    current_atmos,
    candidate_channels,
    candidate_atmos
):
    # Known 5.1/7.1 can never become lower-channel audio.
    if current_channels >= 5.0:
        if (
            not candidate_channels
            or candidate_channels < current_channels
        ):
            return False

    # Atmos may never disappear.
    if current_atmos and not candidate_atmos:
        return False

    return True




# ============================================================
# RADARR MEDIA PROTECTION
# ============================================================

def radarr_current_dynamic_range(movie_file):
    media=(movie_file or {}).get("mediaInfo") or {}

    dr=str(media.get("videoDynamicRange") or "").lower()
    typ=str(media.get("videoDynamicRangeType") or "").lower()

    extra=" ".join([
        str((movie_file or {}).get("sceneName") or ""),
        str((movie_file or {}).get("relativePath") or "")
    ]).lower()

    if (
        "dolby vision" in typ
        or "dovi" in typ
        or " dv" in (" " + typ)
        or "dv " in (typ + " ")
        or "dolby vision" in extra
        or "dovi" in extra
    ):
        return "DV_HDR"

    if any(x in dr or x in typ or x in extra for x in (
        "hdr10+",
        "hdr10plus",
        "hdr10",
        "hdr",
        "hlg"
    )):
        return "HDR"

    return "SDR_UNKNOWN"


def radarr_current_atmos(movie_file):
    media=(movie_file or {}).get("mediaInfo") or {}

    text=" ".join([
        str(media.get("audioCodec") or ""),
        str((movie_file or {}).get("sceneName") or ""),
        str((movie_file or {}).get("relativePath") or "")
    ]).lower()

    return "atmos" in text


def radarr_current_audio_channels(movie_file):
    media=(movie_file or {}).get("mediaInfo") or {}

    try:
        return float(media.get("audioChannels") or 0)
    except (TypeError, ValueError):
        return 0.0


def radarr_candidate_atmos(title):
    return "atmos" in str(title or "").lower()


def radarr_candidate_dynamic_range(title):
    t=str(title or "").lower()

    dv=(
        "dolby vision" in t
        or "dovi" in t
        or bool(re.search(
            r"(?<![a-z0-9])dv(?![a-z0-9])",
            t
        ))
    )

    hdr=any(x in t for x in (
        "hdr10+",
        "hdr10plus",
        "hdr10",
        "hdr",
        "hlg"
    ))

    if dv and hdr:
        return "DV_HDR"

    if dv:
        return "DV_ONLY"

    if hdr:
        return "HDR"

    return "SDR_UNKNOWN"


def radarr_dynamic_range_allowed(current, candidate):
    # DV without HDR fallback is never accepted.
    if candidate == "DV_ONLY":
        return False

    # Existing DV+HDR must remain DV+HDR.
    if current == "DV_HDR":
        return candidate == "DV_HDR"

    # Existing HDR may remain HDR or become DV+HDR.
    if current == "HDR":
        return candidate in ("HDR", "DV_HDR")

    # SDR/unknown may move to HDR/DV or remain SDR/unknown.
    return candidate in (
        "SDR_UNKNOWN",
        "HDR",
        "DV_HDR"
    )


def radarr_audio_allowed(
    current_channels,
    current_atmos,
    candidate_channels,
    candidate_atmos
):
    # Known 5.1/7.1 can never become lower-channel audio.
    if current_channels >= 5.0:
        if (
            not candidate_channels
            or candidate_channels < current_channels
        ):
            return False

    # Atmos may never disappear.
    if current_atmos and not candidate_atmos:
        return False

    return True



def main():
    state = load_state()

    if LIVE:
        clean_old_attempts(state)

    print()
    print("=" * 68)
    print("RADARR SMART OPTIMIZER")
    print("=" * 68)

    if LIVE:
        print("MODE: LIVE")
    else:
        print("MODE: DRY RUN -- NOTHING WILL BE DOWNLOADED")

    print("Daily interactive-search budget:", DAILY_SEARCH_BUDGET)
    print("Minimum same-resolution saving: %.1f%%" % MIN_SAVING_PERCENT)
    print()

    used = searches_used_today(state)

    # Maximum interactive searches in one execution.
    PER_RUN_SEARCH_BUDGET = max(1, SEARCHES_PER_RUN)

    if LIVE:
        remaining = min(
            PER_RUN_SEARCH_BUDGET,
            max(0, DAILY_SEARCH_BUDGET - used)
        )
    else:
        # Dry run does NOT consume persistent budget.
        remaining = min(
            PER_RUN_SEARCH_BUDGET,
            DAILY_SEARCH_BUDGET
        )

    print(
        "Persistent searches already used today:",
        used
    )

    print(
        "Searches available this run:",
        remaining
    )

    if remaining <= 0:
        print()
        print("Daily search budget exhausted.")
        print("Nothing to do.")
        return

    print()
    print("Reading Radarr queue...")

    queued_ids = active_movie_ids()

    print(
        "Movies currently represented in queue:",
        len(queued_ids)
    )

    print()

    candidates, stats = collect_candidates(
        state,
        queued_ids
    )

    print("Movies in Radarr:", stats["movies"])
    print("Movies with files:", stats["with_file"])
    print("Permanent LOTR/Hobbit ignores:", stats["ignored"])
    print("Queued movies skipped:", stats["queued"])

    print()
    print(
        "Eligible local optimization candidates:",
        len(candidates)
    )

    if not candidates:
        print("Nothing currently needs an optimizer search.")
        return

    # Movies are already eligible local candidates.
    # Search larger files first so successful optimizations can
    # recover more storage early.
    candidates.sort(
        key=lambda x: (
            x.get("resolution", 0),
            x.get("size_mib", 0)
        ),
        reverse=True
    )

    selected = candidates[:remaining]

    print()
    print(
        "Highest-priority movies selected for this run:",
        len(selected)
    )
    print()

    searches = 0
    grabs = 0
    no_match = 0
    errors = 0

    for number, item in enumerate(selected, 1):
        print("-" * 68)

        describe_item(number, item)

        movie_id = item["movie_id"]

        try:
            # THIS is the expensive interactive indexer search.
            releases = get(
                "/release?movieId=%d"
                % movie_id
            )

            searches += 1

            if LIVE:
                increment_search_count(state)
                mark_movie_searched(
                    state,
                    movie_id
                )

                # Save immediately so a crash/restart does not
                # accidentally reset our search budget.
                save_state(state)

        except Exception as e:
            errors += 1
            print("    SEARCH ERROR:", e)
            print()
            continue

        choice = choose_best(
            item,
            releases,
            state
        )

        if not choice:
            no_match += 1
            print("    KEEP CURRENT: no qualifying replacement.")
            print()
            continue

        describe_choice(choice)

        if not LIVE:
            print("    DRY RUN: WOULD GRAB")
            print()
            continue

        # ----------------------------------------------------
        # SAFETY CHECK AGAIN immediately before grabbing.
        # ----------------------------------------------------

        try:
            fresh_queue = active_movie_ids()

            if movie_id in fresh_queue:
                print(
                    "    SKIP: movie entered Radarr queue "
                    "while we were evaluating it."
                )
                print()
                continue

        except Exception as e:
            print(
                "    SKIP: could not perform final queue safety check:",
                e
            )
            print()
            continue

        try:
            # The ONLY Radarr write operation used to initiate
            # replacement.
            #
            # NO DELETE.
            # NO filesystem manipulation.
            # NO direct Deluge manipulation.
            post(
                "/release",
                choice["release"]
            )

            grabs += 1

            mark_release_attempted(
                state,
                choice["release"]
            )

            save_state(state)

            print("    LIVE: RELEASE SENT TO RADARR")
            print(
                "    Existing movie remains until Radarr "
                "successfully downloads and imports replacement."
            )


        except Exception as e:
            errors += 1
            print("    GRAB ERROR:", e)

        print()

    print("=" * 68)
    print("SUMMARY")
    print("=" * 68)

    print("Interactive searches this run:", searches)

    if LIVE:
        print("Releases sent to Radarr:", grabs)
        print(
            "Persistent searches used today:",
            searches_used_today(state)
        )
    else:
        print("Downloads started: 0")
        print("State changes: 0")

    print("No qualifying replacement:", no_match)
    print("Errors:", errors)

    if not LIVE:
        print()
        print(
            "DRY RUN COMPLETE -- no downloads, deletions, "
            "or persistent cooldown changes were made."
        )


if __name__ == "__main__":
    main()
