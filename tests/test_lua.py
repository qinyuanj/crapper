import shutil
from pathlib import Path

import pytest

from crapper.analyze import analyze_files
from crapper.cli import parse_args, run, select_files
from crapper.discover import is_test_file, language_of
from crapper.languages.lua import functions_in_source


def test_functions_methods_and_assigned_closures():
    source = """
local M = {}

function M.choose(x, ready)
  if x > 0 and x < 10 then
    return 1
  elseif x == 0 or ready then
    return 0
  end
  for i = 1, x do
    if i == 2 then return i end
  end
  for _, v in ipairs(ready) do
    while v do break end
  end
  repeat x = x - 1 until x <= 0
  goto done
  ::done::
  return x
end

function Account:deposit(amount)
  self.balance = self.balance + amount
end

local function helper(n)
  return n and n > 0 or false
end

local first, second = 1, function(s)
  if s then return s end
end

M.bar = function() return 1 end

function global_fn() end

return M
"""
    functions = functions_in_source(source, "src/demo/box.lua", "/proj")
    assert [(fn.namespace, fn.name, fn.complexity) for fn in functions] == [
        ("demo.box", "M.choose", 10),
        ("demo.box", "Account:deposit", 1),
        ("demo.box", "helper", 3),
        ("demo.box", "second", 2),
        ("demo.box", "M.bar", 1),
        ("demo.box", "global_fn", 1),
    ]


def test_nested_and_anonymous_functions_stay_inside_their_parent():
    source = """
local function outer(xs)
  local function inner(n)
    if n then return 1 end
    return 0
  end
  local pick = function(n) return n or 0 end
  table.sort(xs, function(a, b) return a < b and true end)
  return inner(1)
end

table.sort({}, function(a, b) if a then return true end end)

local t = { f = function() end }
"""
    functions = functions_in_source(source, "a.lua", "/proj")
    assert [(fn.name, fn.complexity) for fn in functions] == [("outer", 4)]


def test_local_function_expression_starts_at_local():
    source = "\nlocal f =\n  function()\n    return 1\n  end\n"
    [found] = functions_in_source(source, "a.lua", "/proj")
    assert (found.start_line, found.end_line) == (2, 5)


def test_namespace_strips_lua_roots_and_init():
    source = "function f() end\n"

    def namespace(path, root="/proj"):
        return functions_in_source(source, path, root)[0].namespace

    assert namespace("/proj/src/demo/app.lua") == "demo.app"
    assert namespace("./src/lua/demo/app.lua", ".") == "demo.app"
    assert namespace("lua/demo/init.lua") == "demo"
    assert namespace("demo/util/init.lua") == "demo.util"
    assert namespace("init.lua") == "init"
    assert namespace("vendor/src/x.lua") == "vendor.src.x"


def test_strings_and_comments_do_not_add_decisions():
    source = """
function literal()
  local text = "if x and y then"
  -- if x or y then
  --[[ while true do end ]]
  return text
end
"""
    [found] = functions_in_source(source, "a.lua", "/proj")
    assert found.complexity == 1


def test_lua_files_and_busted_specs_are_recognized():
    assert language_of("src/demo/app.lua") == "lua"
    assert is_test_file("src/demo/app_spec.lua")
    assert is_test_file("spec/app.lua")
    assert not is_test_file("src/demo/app.lua")


@pytest.fixture
def installed_lua_project(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "lua_project"
    shutil.copytree(fixture, tmp_path, dirs_exist_ok=True)
    for tool in ("crapper", "mutator", "uml-viewer"):
        shutil.copytree(fixture, tmp_path / ".uml-viewer" / tool / "lua-fixture")
    shutil.copytree(
        fixture,
        tmp_path / ".uml-viewer" / "uml-viewer" / ".uml-viewer" / "lua-fixture",
    )
    (tmp_path / ".gitignore").write_text(".uml-viewer/\n", encoding="utf-8")
    return tmp_path


def test_default_lua_scan_excludes_installed_tooling(installed_lua_project):
    root = installed_lua_project
    files = select_files(parse_args(["--root", str(root)]))
    entries = analyze_files(files, root, None)
    assert {(entry.namespace, entry.name) for entry in entries} == {
        ("calc", "M.classify"),
        ("calc", "M.total"),
        ("calc.util", "between"),
        ("calc.util", "M.clamp"),
        ("calc.util", "M.untested"),
    }
    assert len(entries) == 5
    assert run(["--root", str(root), "--no-coverage"]) == 0
    snapshot = (root / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    assert ".uml-viewer" not in snapshot


@pytest.mark.parametrize(
    "selection, expected_files, expected_entries",
    [
        (["-s", "src"], ["src/calc/init.lua", "src/calc/util.lua"], 5),
        (
            ["-s", ".uml-viewer"],
            [
                ".uml-viewer/crapper/lua-fixture/src/calc/init.lua",
                ".uml-viewer/crapper/lua-fixture/src/calc/util.lua",
                ".uml-viewer/mutator/lua-fixture/src/calc/init.lua",
                ".uml-viewer/mutator/lua-fixture/src/calc/util.lua",
                ".uml-viewer/uml-viewer/lua-fixture/src/calc/init.lua",
                ".uml-viewer/uml-viewer/lua-fixture/src/calc/util.lua",
            ],
            15,
        ),
        (
            [".uml-viewer"],
            [
                ".uml-viewer/crapper/lua-fixture/src/calc/init.lua",
                ".uml-viewer/crapper/lua-fixture/src/calc/util.lua",
                ".uml-viewer/mutator/lua-fixture/src/calc/init.lua",
                ".uml-viewer/mutator/lua-fixture/src/calc/util.lua",
                ".uml-viewer/uml-viewer/lua-fixture/src/calc/init.lua",
                ".uml-viewer/uml-viewer/lua-fixture/src/calc/util.lua",
            ],
            15,
        ),
        (
            ["-s", ".uml-viewer/crapper/lua-fixture/src/calc/init.lua"],
            [".uml-viewer/crapper/lua-fixture/src/calc/init.lua"],
            2,
        ),
        (
            [".uml-viewer/crapper/lua-fixture/src/calc/init.lua"],
            [".uml-viewer/crapper/lua-fixture/src/calc/init.lua"],
            2,
        ),
    ],
)
def test_explicit_lua_sources_override_tooling_pruning(
    installed_lua_project, selection, expected_files, expected_entries
):
    root = installed_lua_project
    argv = ["--root", str(root), "--no-coverage", *selection]
    files = select_files(parse_args(argv))
    assert [path.relative_to(root).as_posix() for path in files] == expected_files
    assert len(analyze_files(files, root, None)) == expected_entries
    assert run(argv) == 0


def test_a_source_that_does_not_parse_is_refused_not_measured():
    # tree-sitter recovers from a syntax error and still yields functions,
    # so a broken file would publish CRAP for code that cannot run.
    with pytest.raises(ValueError, match="broken.lua"):
        functions_in_source("function M.choose(x\n  return x\nend\n", "src/broken.lua")
