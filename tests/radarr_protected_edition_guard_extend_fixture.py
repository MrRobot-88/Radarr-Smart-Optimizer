#!/usr/bin/env python3
import ast
import py_compile
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: radarr_protected_edition_guard_extend_fixture.py RADARR_SCRIPT")

p=sys.argv[1]
py_compile.compile(p,doraise=True)
print("COMPILE PASS")

src=open(p,encoding="utf-8").read()
tree=ast.parse(src)

wanted={"_radarr_cut_suffix_tokens","radarr_protected_cut","radarr_cut_replacement_allowed"}
nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in wanted]
assert {n.name for n in nodes} == wanted

mini=ast.Module(
    body=[ast.Import(names=[ast.alias(name="re")]),*nodes],
    type_ignores=[],
)
ns={}
exec(compile(ast.fix_missing_locations(mini),"<edition-fixture>","exec"),ns)

allowed=ns["radarr_cut_replacement_allowed"]

def item(path,title="Blade Runner",year=1982,edition=""):
    return {
        "title":title,
        "year":year,
        "movie_file":{
            "relativePath":path,
            "edition":edition,
        },
    }

ordinary={"title":"Blade Runner 1982 1080p BluRay x265-GROUP"}

# Existing protected editions must never be replaced by ordinary/theatrical.
for current in (
    item("Blade Runner (1982) Extended Edition Bluray-1080p x265.mkv"),
    item("Blade Runner (1982) Limited Edition Bluray-1080p x265.mkv"),
    item("Blade Runner (1982) Special Edition Bluray-1080p x265.mkv"),
    item("Blade Runner (1982) Bluray-1080p x265.mkv",edition="Limited Edition"),
    item("Blade Runner (1982) Bluray-1080p x265.mkv",edition="Special Edition"),
):
    assert allowed(current,ordinary) is False

# Explicitly protected replacements remain eligible under this guard.
for candidate in (
    {"title":"Blade Runner 1982 Extended Edition 1080p BluRay x265-GROUP"},
    {"title":"Blade Runner 1982 Limited Edition 1080p BluRay x265-GROUP"},
    {"title":"Blade Runner 1982 Special Edition 1080p BluRay x265-GROUP"},
    {"title":"Blade Runner 1982 Directors Cut 1080p BluRay x265-GROUP"},
):
    assert allowed(
        item("Blade Runner (1982) Limited Edition Bluray-1080p x265.mkv"),
        candidate,
    ) is True

# Ordinary current is unaffected.
assert allowed(
    item("Blade Runner (1982) Bluray-1080p x265.mkv"),
    ordinary,
) is True

assert "Limited Edition" in src
assert "Special Edition" in src
assert 'return None, "theatrical cannot replace protected cut"' in src

print("EXTENDED EDITION -> THEATRICAL BLOCK PASS")
print("LIMITED EDITION -> THEATRICAL BLOCK PASS")
print("SPECIAL EDITION -> THEATRICAL BLOCK PASS")
print("EDITION METADATA LIMITED/SPECIAL PASS")
print("PROTECTED -> PROTECTED PASS")
print("ORDINARY -> ORDINARY UNAFFECTED PASS")
print("ALL RADARR PROTECTED-EDITION EXTENSION FIXTURES PASSED")
