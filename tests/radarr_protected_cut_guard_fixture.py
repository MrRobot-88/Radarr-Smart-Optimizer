#!/usr/bin/env python3
import ast
import py_compile
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: radarr_protected_cut_guard_fixture.py RADARR_SCRIPT")

p=sys.argv[1]
py_compile.compile(p,doraise=True)
print("COMPILE PASS")

src=open(p,encoding="utf-8").read()
tree=ast.parse(src)

wanted={
    "_radarr_cut_suffix_tokens",
    "radarr_protected_cut",
    "radarr_cut_replacement_allowed",
}

nodes=[
    n for n in tree.body
    if isinstance(n,ast.FunctionDef) and n.name in wanted
]

assert {n.name for n in nodes} == wanted

mini=ast.Module(
    body=[
        ast.Import(names=[ast.alias(name="re")]),
        *nodes,
    ],
    type_ignores=[],
)

ns={}
exec(compile(ast.fix_missing_locations(mini),"<cut-fixture>","exec"),ns)

cut=ns["radarr_protected_cut"]
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

# Current protected cut -> theatrical/ordinary candidate MUST be rejected.
cases=[
    (
        item("Blade Runner (1982) Extended Cut Bluray-1080p x265.mkv"),
        {"title":"Blade Runner 1982 1080p BluRay x265-GROUP"},
    ),
    (
        item("Blade Runner (1982) Directors Cut Bluray-1080p x265.mkv"),
        {"title":"Blade Runner 1982 1080p BluRay x265-GROUP"},
    ),
    (
        item("Blade Runner (1982) Director's Cut Bluray-1080p x265.mkv"),
        {"title":"Blade Runner 1982 1080p BluRay x265-GROUP"},
    ),
    (
        item("Blade Runner (1982) DC Bluray-1080p x265.mkv"),
        {"title":"Blade Runner 1982 1080p BluRay x265-GROUP"},
    ),
]

for current,candidate in cases:
    assert allowed(current,candidate) is False

# Protected current -> protected candidate is allowed by THIS rule.
assert allowed(
    item("Blade Runner (1982) Extended Bluray-1080p x265.mkv"),
    {"title":"Blade Runner 1982 Extended Cut 1080p BluRay x265-GROUP"},
) is True

assert allowed(
    item("Blade Runner (1982) Directors Cut Bluray-1080p x265.mkv"),
    {"title":"Blade Runner 1982 DC 1080p BluRay x265-GROUP"},
) is True

# Ordinary current -> ordinary candidate is unaffected.
assert allowed(
    item("Blade Runner (1982) Bluray-1080p x265.mkv"),
    {"title":"Blade Runner 1982 1080p BluRay x265-GROUP"},
) is True

# Edition metadata is also honored.
assert allowed(
    item("Blade Runner (1982) Bluray-1080p x265.mkv",edition="Director's Cut"),
    {"title":"Blade Runner 1982 1080p BluRay x265-GROUP"},
) is False

# Do not false-trigger on a movie title containing "Extended".
assert cut(
    "Extended Family 2024 1080p WEB-DL x265-GROUP",
    "Extended Family",
    2024,
    "",
) is False

# Do not false-trigger on a title beginning with DC.
assert cut(
    "DC League of Super Pets 2022 1080p BluRay x265-GROUP",
    "DC League of Super Pets",
    2022,
    "",
) is False

assert 'return None, "theatrical cannot replace protected cut"' in src
assert "if not radarr_cut_replacement_allowed(item, release):" in src

print("EXTENDED -> THEATRICAL BLOCK PASS")
print("DIRECTORS CUT -> THEATRICAL BLOCK PASS")
print("DC -> THEATRICAL BLOCK PASS")
print("PROTECTED -> PROTECTED PASS")
print("ORDINARY -> ORDINARY UNAFFECTED PASS")
print("EDITION METADATA PASS")
print("TITLE FALSE-POSITIVE GUARDS PASS")
print("ALL RADARR PROTECTED-CUT FIXTURES PASSED")
