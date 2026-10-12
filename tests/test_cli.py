import subprocess
import sys
from pathlib import Path

import pytest

from crapper.cli import _changed_files, _positionals, _take, main, parse_args, run
from crapper.discover import language_of


def test_language_detection():
    assert language_of("src/app/core.clj") == "clojure"
    assert language_of("src/app/core.cljc") == "clojure"
    assert language_of("Widget.java") == "java"
    assert language_of("board.go") == "go"
    assert language_of("ui/view.tsx") == "typescript"
    assert language_of("src/lib.rs") == "rust"
    assert language_of("src/crapper/cli.py") == "python"
    assert language_of("types.d.ts") is None
    assert language_of("notes.md") is None


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_project_snapshot_is_readable_as_crap4clj_edn(tmp_path):
    _write(
        tmp_path,
        "src/demo/core.clj",
        "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n",
    )
    _write(
        tmp_path,
        "src/demo/Board.java",
        "package demo;\npublic class Board {\n  public int place(int x) {\n    if (x > 0 && ready) return 1;\n    return 0;\n  }\n}\n",
    )
    _write(
        tmp_path,
        "board.go",
        "package demo\n\nfunc Place(x int) int {\n\tif x > 0 {\n\t\treturn x\n\t}\n\treturn 0\n}\n",
    )
    _write(
        tmp_path,
        "src/ui/view.ts",
        "export function view(ok: boolean): number {\n  return ok ? 1 : 0;\n}\n",
    )
    _write(tmp_path, "Cargo.toml", '[package]\nname = "demo"\nversion = "0.1.0"\n')
    _write(
        tmp_path,
        "src/lib.rs",
        "pub fn open(ok: bool) -> i32 { if ok { 1 } else { 0 } }\n",
    )
    _write(tmp_path, "src/demo/core_test.clj", "(ns demo.core-test)\n(defn should-skip [] 1)\n")
    _write(tmp_path, "board_test.go", "package demo\nfunc TestPlace(t *testing.T) {}\n")

    code = run(["--root", str(tmp_path), "--no-coverage"])
    assert code == 0
    text = (tmp_path / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    assert ':namespace "demo.core"' in text
    assert ':name "choose"' in text
    assert ':namespace "demo.Board"' in text
    assert ':name "place"' in text
    assert ':namespace "demo"' in text and ':name "Place"' in text
    assert ':namespace "ui.view"' in text
    assert ':namespace "demo"' in text and ':name "open"' in text
    assert "should-skip" not in text
    assert "TestPlace" not in text

    reader = subprocess.run(
        [
            "bb",
            "-e",
            (
                "(require '[clojure.edn :as edn])"
                "(let [data (edn/read-string (slurp *in*))]"
                " (doseq [entry (:entries data)]"
                "   (println (:namespace entry) (:name entry)"
                "            (:complexity entry) (pr-str (:coverage entry)))))"
            ),
        ],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reader.returncode == 0, reader.stderr
    assert "demo.core choose 2 nil" in reader.stdout
    assert "demo.Board place 3 nil" in reader.stdout


def test_existing_coverage_feeds_the_snapshot(tmp_path):
    _write(
        tmp_path,
        "src/demo/core.clj",
        "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n",
    )
    lcov = tmp_path / "target" / "coverage" / "lcov.info"
    lcov.parent.mkdir(parents=True)
    lcov.write_text(
        "SF:src/demo/core.clj\nDA:3,1\nDA:4,1\nend_of_record\n",
        encoding="utf-8",
    )
    code = run(
        ["--root", str(tmp_path), "--use-existing-coverage", "src/demo/core.clj"]
    )
    assert code == 0
    text = (tmp_path / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    assert ":coverage 100.0" in text
    assert ":crap 2.0" in text


def test_threshold_exits_when_the_worst_score_is_too_high(tmp_path):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n")
    lcov = tmp_path / "target" / "coverage" / "lcov.info"
    lcov.parent.mkdir(parents=True)
    lcov.write_text("SF:src/demo/core.clj\nDA:3,0\nDA:4,0\nend_of_record\n", encoding="utf-8")
    code = run(
        [
            "--root",
            str(tmp_path),
            "--use-existing-coverage",
            "--threshold",
            "1",
        ]
    )
    assert code == 2


def test_help_and_unknown_option(capsys):
    assert run(["--help"]) == 0
    assert "uml-viewer" in capsys.readouterr().out
    assert run(["--not-an-option"]) == 1
    assert run(["--root"]) == 1
    assert run(["--threshold", "nope"]) == 1
    assert run(["--no-coverage", "--coverage-command", "true"]) == 1


def test_selects_directories_filters_and_changed_files(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n")
    _write(tmp_path, "notes.md", "not source\n")
    assert run(["--root", str(tmp_path), "--no-coverage", "--source-root", "src"]) == 0
    assert run(["--root", str(tmp_path), "--no-coverage", "src"]) == 0
    assert run(["--root", str(tmp_path), "--no-coverage", "src/demo/core.clj", "core"]) == 0
    assert run(["--root", str(tmp_path), "--no-coverage", "notes.md"]) == 0
    assert "No source files" in capsys.readouterr().out

    class Result:
        returncode = 0
        stdout = ' M src/demo/core.clj\nR  old.clj -> src/demo/core.clj\n?? "src/demo/core.clj"\n'
        stderr = ""

    monkeypatch.setattr("crapper.cli.subprocess.run", lambda *args, **kwargs: Result())
    assert run(["--root", str(tmp_path), "--no-coverage", "--changed"]) == 0

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "git failed"

    monkeypatch.setattr("crapper.cli.subprocess.run", lambda *args, **kwargs: Failed())
    assert run(["--root", str(tmp_path), "--no-coverage", "--changed"]) == 0


def test_run_asks_for_coverage_when_it_is_enabled(tmp_path, monkeypatch):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  x)\n")
    called = []
    monkeypatch.setattr("crapper.cli.run_coverage", lambda *args: called.append(args))
    assert run(["--root", str(tmp_path), "--coverage-command", "true"]) == 0
    assert called


def test_empty_option_value_is_rejected():
    with pytest.raises(ValueError, match="requires a value"):
        _take(["ok", "--opt", ""], 1, "--opt")
    with pytest.raises(ValueError, match="requires a value"):
        _take(["--opt", "-1"], 0, "--opt")


def test_parse_args_reads_the_process_arguments_after_the_program(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crapper", "--threshold", "5"])
    options = parse_args()
    assert options.threshold == 5.0
    assert options.positionals == []
    assert options.action == "analyze"


def test_flags_that_only_store_a_boolean():
    assert parse_args(["--use-existing-coverage"]).use_existing_coverage is True
    assert parse_args(["--changed"]).changed is True
    assert parse_args(["--no-coverage"]).no_coverage is True
    conflict = parse_args(["--no-coverage", "--coverage-command", "true"])
    assert conflict.exit_code == 1


def test_changed_files_follow_git_status(tmp_path, monkeypatch, capsys):
    seen = {}

    class Result:
        returncode = 0
        stdout = "\nM  x\n?? sources/keep.py\n"
        stderr = ""

    def fake(*_args, **kwargs):
        seen.update(kwargs)
        return Result()

    monkeypatch.setattr("crapper.cli.subprocess.run", fake)
    found = _changed_files(tmp_path)
    assert seen["check"] is False
    assert seen["capture_output"] is True
    assert seen["text"] is True
    assert found == [
        (tmp_path / "x").resolve(),
        (tmp_path / "sources" / "keep.py").resolve(),
    ]

    class Failed:
        returncode = 1
        stdout = "?? sources/keep.py\n"
        stderr = "git failed"

    monkeypatch.setattr("crapper.cli.subprocess.run", lambda *_a, **_k: Failed())
    assert _changed_files(tmp_path) == []
    assert capsys.readouterr().err.strip() == "git failed"

    class Silent:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr("crapper.cli.subprocess.run", lambda *_a, **_k: Silent())
    assert _changed_files(tmp_path) == []
    assert capsys.readouterr().err.strip() == "git status failed"


def test_relative_paths_are_resolved_under_the_project_root(tmp_path):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n")
    existing, filters = _positionals(tmp_path, ["src/demo/core.clj"])
    assert existing == [(tmp_path / "src/demo/core.clj").resolve()]
    assert filters == []
    assert run(["--root", str(tmp_path), "--no-coverage", "src/demo/core.clj"]) == 0
    text = (tmp_path / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    assert ':name "choose"' in text
    assert "parse_args" not in text


def test_changed_selection_ignores_files_git_did_not_name(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "src/kept.clj", "(ns demo.kept)\n\n(defn kept [] 1)\n")
    _write(tmp_path, "src/other.clj", "(ns demo.other)\n\n(defn other [] 1)\n")

    class Result:
        returncode = 0
        stdout = "?? src/kept.clj\n"
        stderr = ""

    monkeypatch.setattr("crapper.cli.subprocess.run", lambda *_a, **_k: Result())
    assert run(["--root", str(tmp_path), "--no-coverage", "--changed"]) == 0
    text = (tmp_path / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    assert ':name "kept"' in text
    assert "other" not in text
    assert "No source files" not in capsys.readouterr().out


def test_threshold_equal_to_the_score_is_allowed(tmp_path):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [] 1)\n")
    lcov = tmp_path / "target" / "coverage" / "lcov.info"
    lcov.parent.mkdir(parents=True)
    lcov.write_text("SF:src/demo/core.clj\nDA:1,1\nDA:3,1\nend_of_record\n", encoding="utf-8")
    code = run(
        ["--root", str(tmp_path), "--use-existing-coverage", "--threshold", "1", "src/demo/core.clj"]
    )
    assert code == 0


def test_threshold_with_no_scores_does_not_call_max_on_an_empty_list(tmp_path):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [] 1)\n")
    assert run(["--root", str(tmp_path), "--no-coverage", "--threshold", "1"]) == 0


def test_business_roots_exclude_exact_directories_without_duplicate_metrics(tmp_path):
    sources = {
        "client/main.lua": "client_main",
        "client/_trigger/event.lua": "client_trigger",
        "common/shared.lua": "shared",
        "server/main.lua": "server_main",
        "server/_trigger/nested/event.lua": "server_trigger",
        "server/_trigger_extra/event.lua": "trigger_extra",
        "packages/vendor.lua": "package_vendor",
    }
    for path, name in sources.items():
        _write(tmp_path, path, f"function {name}() return 1 end\n")
    assert run([
        "--root", str(tmp_path), "--no-coverage",
        "--source-root", "client", "--source-root", "common",
        "--source-root", "server", "--source-root", "server/_trigger_extra",
        "--source-root", "packages",
        "--exclude", "packages", "--exclude", "server/_trigger",
    ]) == 0
    text = (tmp_path / ".metrics/crap.edn").read_text(encoding="utf-8")
    for name in ("client_main", "client_trigger", "shared", "server_main", "trigger_extra"):
        assert text.count(f':name "{name}"') == 1
    assert ':name "server_trigger"' not in text
    assert ':name "package_vendor"' not in text


def test_glob_exclusions_match_path_segments_and_directory_descendants(tmp_path):
    sources = {
        "client/generated/deep/code.lua": "client_generated",
        "generated/root.lua": "root_generated",
        "server/generated/code.lua": "server_generated",
        "server/private.lua": "server_private",
        "server/deep/private.lua": "deep_private",
        "common/code1.lua": "code_one",
        "common/code2.lua": "code_two",
        "common/main.lua": "main",
    }
    for path, name in sources.items():
        _write(tmp_path, path, f"function {name}() return 1 end\n")
    assert run([
        "--root", str(tmp_path), "--no-coverage",
        "--exclude", "**/generated", "--exclude", "server/*.lua",
        "--exclude", "common/code?.lua",
    ]) == 0
    text = (tmp_path / ".metrics/crap.edn").read_text(encoding="utf-8")
    assert text.count(':name "deep_private"') == 1
    assert text.count(':name "main"') == 1
    assert text.count(":name ") == 2


@pytest.mark.parametrize("selection", [[], ["client"], ["client/main.lua", "client/private.lua"], ["main"]])
def test_exclusions_apply_to_default_explicit_and_filtered_selection(tmp_path, selection):
    _write(tmp_path, "client/main.lua", "function main() return 1 end\n")
    _write(tmp_path, "client/private.lua", "function private() return 1 end\n")
    assert run([
        "--root", str(tmp_path), "--no-coverage", "--exclude", "client/private.lua",
        *selection,
    ]) == 0
    text = (tmp_path / ".metrics/crap.edn").read_text(encoding="utf-8")
    assert ':name "main"' in text
    assert ':name "private"' not in text


def test_exclusions_apply_to_git_changed_selection(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    _write(tmp_path, "client/main.lua", "function main() return 1 end\n")
    _write(tmp_path, "server/_trigger/event.lua", "function trigger() return 1 end\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    assert run([
        "--root", str(tmp_path), "--no-coverage", "--changed",
        "--exclude", "server/_trigger",
    ]) == 0
    text = (tmp_path / ".metrics/crap.edn").read_text(encoding="utf-8")
    assert ':name "main"' in text
    assert ':name "trigger"' not in text


def test_source_roots_keep_default_exclusions_and_allow_explicit_test_files(tmp_path):
    _write(tmp_path, "client/main.lua", "function main() return 1 end\n")
    for directory in ("spec", "vendor", "target", "lua_modules", ".luarocks"):
        _write(tmp_path, f"{directory}/tool.lua", "function skipped() return 1 end\n")
    assert run(["--root", str(tmp_path), "--no-coverage"]) == 0
    text = (tmp_path / ".metrics/crap.edn").read_text(encoding="utf-8")
    assert ':name "main"' in text
    assert ':name "skipped"' not in text
    assert run(["--root", str(tmp_path), "--no-coverage", "spec/tool.lua"]) == 0
    text = (tmp_path / ".metrics/crap.edn").read_text(encoding="utf-8")
    assert ':name "skipped"' in text


def test_cli_accepts_native_paths_with_spaces_and_backslash_exclusions(tmp_path):
    root = tmp_path / "business project"
    _write(root, "server/main.lua", "function main() return 1 end\n")
    _write(root, "server/_trigger/event.lua", "function trigger() return 1 end\n")
    _write(root, "server/generated/nested/event.lua", "function generated() return 1 end\n")
    result = subprocess.run([
        sys.executable, "-m", "crapper", "--root", str(root), "--no-coverage",
        "--source-root", str(root / "server"), "--source-root", str(root / "server" / "generated"),
        "--exclude", "./server\\_trigger\\", "--exclude", "server\\generated\\**",
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    text = (root / ".metrics/crap.edn").read_text(encoding="utf-8")
    assert text.count(':name "main"') == 1
    assert text.count(":name ") == 1


def test_exclude_requires_a_value_and_is_documented(capsys):
    assert run(["--exclude"]) == 1
    assert "--exclude requires a value" in capsys.readouterr().err
    assert run(["--help"]) == 0
    assert "--exclude" in capsys.readouterr().out


def test_main_exits_with_the_status(monkeypatch):
    monkeypatch.setattr("crapper.cli.run", lambda _argv=None: 4)
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 4
    else:
        raise AssertionError("main did not exit")
