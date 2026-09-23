#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: radarr_protected_edition_guard_extend_patch.py RADARR_SCRIPT")

p=Path(sys.argv[1])
src=p.read_text(encoding="utf-8")

EXPECTED="5f29c59fb3a4d1c5223c14af49ed3b7455e3c8f9b09de4b780e99cc079b4fd18"
actual=hashlib.sha256(src.encode("utf-8")).hexdigest()
if actual != EXPECTED:
    raise SystemExit("STOP: Radarr optimizer hash mismatch: "+actual)

old_doc='''      * Extended / Extended Cut / Extended Edition / Extended Version
      * Director Cut / Directors Cut / Director's Cut
      * DC abbreviation when it appears as an edition or near the front of the
'''
new_doc='''      * Extended / Extended Cut / Extended Edition / Extended Version
      * Limited Edition
      * Special Edition
      * Director Cut / Directors Cut / Director's Cut
      * DC abbreviation when it appears as an edition or near the front of the
'''

if src.count(old_doc) != 1:
    raise SystemExit("STOP: protected-edition doc anchor count=%d" % src.count(old_doc))
src=src.replace(old_doc,new_doc,1)

old_regex='''    if re.search(
        r"\\bextended(?:\\s+(?:cut|edition|version))?\\b",
        combined
    ):
        return True

    if re.search(
'''
new_regex='''    if re.search(
        r"\\bextended(?:\\s+(?:cut|edition|version))?\\b",
        combined
    ):
        return True

    if re.search(
        r"\\b(?:limited|special)\\s+edition\\b",
        combined
    ):
        return True

    if re.search(
'''

if src.count(old_regex) != 1:
    raise SystemExit("STOP: extended-regex anchor count=%d" % src.count(old_regex))
src=src.replace(old_regex,new_regex,1)

old_comment='''    # An ordinary/theatrical release may never replace an existing
    # Extended / Extended Cut / Director's Cut / DC movie.
'''
new_comment='''    # An ordinary/theatrical release may never replace an existing
    # Extended / Limited Edition / Special Edition / Director's Cut / DC movie.
'''
if src.count(old_comment) != 1:
    raise SystemExit("STOP: safety-rule comment anchor count=%d" % src.count(old_comment))
src=src.replace(old_comment,new_comment,1)

old_print='''            "for protected Extended/Director's Cut/DC current file",
'''
new_print='''            "for protected Extended/Limited/Special/Director's Cut/DC current file",
'''
if src.count(old_print) != 1:
    raise SystemExit("STOP: cut-rule log anchor count=%d" % src.count(old_print))
src=src.replace(old_print,new_print,1)

p.write_text(src,encoding="utf-8")

print("RADARR PROTECTED-EDITION EXTENSION PATCH COMPLETE")
print("SHA256",hashlib.sha256(src.encode("utf-8")).hexdigest())
