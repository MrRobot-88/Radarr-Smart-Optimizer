#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: radarr_protected_cut_guard_patch.py RADARR_SCRIPT")

p = Path(sys.argv[1])
src = p.read_text(encoding="utf-8")

EXPECTED = "c9e0d6d52cd468ba080378aabae2b3377c06e93e0eaf691ef50beb59c9e6c5bb"
actual = hashlib.sha256(src.encode("utf-8")).hexdigest()
if actual != EXPECTED:
    raise SystemExit("STOP: Radarr optimizer hash mismatch: " + actual)

helper_anchor = "def evaluate_release(item, release, state):\n"
if src.count(helper_anchor) != 1:
    raise SystemExit(
        "STOP: evaluate_release anchor count=%d"
        % src.count(helper_anchor)
    )

helper = r'''
def _radarr_cut_suffix_tokens(text, movie_title="", movie_year=0):
    """Return release/file tokens after the known movie title and year."""
    tokens = re.findall(r"[a-z0-9]+", str(text or "").lower())
    movie_tokens = re.findall(
        r"[a-z0-9]+",
        str(movie_title or "").lower()
    )

    if (
        movie_tokens
        and len(tokens) >= len(movie_tokens)
        and tokens[:len(movie_tokens)] == movie_tokens
    ):
        tokens = tokens[len(movie_tokens):]

    year = str(int(movie_year or 0)) if movie_year else ""

    if year and tokens and tokens[0] == year:
        tokens = tokens[1:]

    return tokens


def radarr_protected_cut(
    text,
    movie_title="",
    movie_year=0,
    edition="",
):
    """
    Detect cuts that must never be replaced by an ordinary/theatrical release.

    Protected:
      * Extended / Extended Cut / Extended Edition / Extended Version
      * Director Cut / Directors Cut / Director's Cut
      * DC abbreviation when it appears as an edition or near the front of the
        release suffix, e.g. Movie.2012.DC.1080p.BluRay...

    The known movie title is stripped first so titles containing words such as
    "Extended" or "DC" do not accidentally trigger edition protection.
    """
    suffix = _radarr_cut_suffix_tokens(
        text,
        movie_title,
        movie_year
    )

    edition_norm = " ".join(
        re.findall(
            r"[a-z0-9]+",
            str(edition or "").lower()
        )
    )

    suffix_norm = " ".join(suffix)
    combined = (edition_norm + " " + suffix_norm).strip()

    if re.search(
        r"\bextended(?:\s+(?:cut|edition|version))?\b",
        combined
    ):
        return True

    if re.search(
        r"\bdirector(?:s|\s+s)?\s+cut\b",
        combined
    ):
        return True

    # Explicit edition metadata saying DC is authoritative.
    if "dc" in edition_norm.split():
        return True

    # Standalone DC is commonly placed immediately after title/year.
    # Restrict it to the first few suffix tokens to avoid treating a release
    # group ending in "-DC" as an edition.
    if "dc" in suffix[:4]:
        return True

    return False


def radarr_cut_replacement_allowed(item, release):
    """
    If the CURRENT library file is a protected non-theatrical cut, the
    replacement must explicitly advertise a protected cut as well.
    """
    movie_file = item.get("movie_file") or {}
    movie_title = item.get("title") or ""
    movie_year = item.get("year") or 0

    current_text = str(
        movie_file.get("relativePath")
        or movie_file.get("path")
        or ""
    )

    current_edition = str(
        movie_file.get("edition")
        or ""
    )

    if not radarr_protected_cut(
        current_text,
        movie_title,
        movie_year,
        current_edition,
    ):
        return True

    candidate_title = str(
        release.get("title")
        or ""
    )

    candidate_edition = str(
        release.get("edition")
        or ""
    )

    return radarr_protected_cut(
        candidate_title,
        movie_title,
        movie_year,
        candidate_edition,
    )


'''

src = src.replace(helper_anchor, helper + helper_anchor, 1)

rule_anchor = '''    if not radarr_rejections_ok(release):
        return None, "radarr rejection"

'''

if src.count(rule_anchor) != 1:
    raise SystemExit(
        "STOP: rejection rule anchor count=%d"
        % src.count(rule_anchor)
    )

rule = '''    if not radarr_rejections_ok(release):
        return None, "radarr rejection"

    # EDITION SAFETY RULE:
    # An ordinary/theatrical release may never replace an existing
    # Extended / Extended Cut / Director's Cut / DC movie.
    if not radarr_cut_replacement_allowed(item, release):
        print(
            "    CUT RULE: REJECT theatrical/ordinary candidate "
            "for protected Extended/Director's Cut/DC current file",
            flush=True
        )
        return None, "theatrical cannot replace protected cut"

'''

src = src.replace(rule_anchor, rule, 1)

p.write_text(src, encoding="utf-8")

print("RADARR PROTECTED-CUT GUARD PATCH COMPLETE")
print("SHA256", hashlib.sha256(src.encode("utf-8")).hexdigest())
