import os
import subprocess
import sys
from pathlib import Path

import pytest

from crapper.cli import HELP, _positionals, _take, main, parse_args, run, select_files
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


def test_selects_directories_filters_and_changed_files(tmp_path, capsys, git):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n")
    _write(tmp_path, "notes.md", "not source\n")
    assert run(["--root", str(tmp_path), "--no-coverage", "--source-root", "src"]) == 0
    assert run(["--root", str(tmp_path), "--no-coverage", "src"]) == 0
    assert run(["--root", str(tmp_path), "--no-coverage", "src/demo/core.clj", "core"]) == 0
    assert run(["--root", str(tmp_path), "--no-coverage", "notes.md"]) == 0
    assert "No source files" in capsys.readouterr().out

    git(tmp_path, "init", "-q")
    assert run(["--root", str(tmp_path), "--no-coverage", "--changed"]) == 0
    assert ':name "choose"' in (tmp_path / ".metrics" / "crap.edn").read_text(encoding="utf-8")


def test_changed_outside_a_repository_selects_nothing(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert run(["--root", str(tmp_path), "--no-coverage", "--changed"]) == 0
    captured = capsys.readouterr()
    assert "No source files" in captured.out
    assert captured.err.strip()


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


def test_relative_paths_are_resolved_under_the_project_root(tmp_path):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n")
    existing, filters = _positionals(tmp_path, ["src/demo/core.clj"])
    assert existing == [(tmp_path / "src/demo/core.clj").resolve()]
    assert filters == []
    assert run(["--root", str(tmp_path), "--no-coverage", "src/demo/core.clj"]) == 0
    text = (tmp_path / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    assert ':name "choose"' in text
    assert "parse_args" not in text


def test_changed_selection_ignores_files_git_did_not_name(tmp_path, capsys, git):
    _write(tmp_path, "src/other.clj", "(ns demo.other)\n\n(defn other [] 1)\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    _write(tmp_path, "src/kept.clj", "(ns demo.kept)\n\n(defn kept [] 1)\n")
    assert run(["--root", str(tmp_path), "--no-coverage", "--changed"]) == 0
    text = (tmp_path / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    assert ':name "kept"' in text
    assert "other" not in text
    assert "No source files" not in capsys.readouterr().out


def test_changed_leaves_out_directories_the_walk_skips(tmp_path, git):
    git(tmp_path, "init", "-q")
    for name in ("vendor/lib/v.clj", "node_modules/p/n.clj", ".uml-viewer/x/u.clj", "src/kept.clj"):
        stem = Path(name).stem
        _write(tmp_path, name, f"(ns demo.{stem})\n\n(defn {stem}fn [] 1)\n")
    assert run(["--root", str(tmp_path), "--no-coverage", "--changed"]) == 0
    text = (tmp_path / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    assert ':name "keptfn"' in text
    assert all(name not in text for name in ("vfn", "nfn", "ufn"))


def test_changed_from_a_subdirectory_root_finds_files_in_new_directories(tmp_path, git):
    git(tmp_path, "init", "-q")
    project = tmp_path / "app"
    _write(project, "src/new dir/core.clj", "(ns demo.core)\n\n(defn choose [] 1)\n")
    _write(tmp_path, "other/side.clj", "(ns demo.side)\n\n(defn side [] 1)\n")
    assert run(["--root", str(project), "--no-coverage", "--changed"]) == 0
    text = (project / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    assert ':name "choose"' in text
    assert "side" not in text


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


def test_report_is_flushed_before_the_metrics_line_on_a_shared_stream(tmp_path):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n")
    source = Path(__file__).resolve().parents[1] / "src"
    completed = subprocess.run(
        [sys.executable, "-m", "crapper", "--root", str(tmp_path), "--no-coverage"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [os.environ.get("PYTHONPATH"), str(source)]))},
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.index("choose") < completed.stdout.index("Wrote ")


def test_parse_args_reads_every_option():
    options = parse_args(
        ["-s", "src", "--source-root", "lib", "--root", "/proj", "--coverage-command", "make cov",
         "--threshold", "7.5", "--changed", "--use-existing-coverage", "core", "util"]
    )
    assert options.action == "analyze"
    assert options.source_roots == ["src", "lib"]
    assert options.project_root == Path("/proj")
    assert options.coverage_command == "make cov"
    assert options.threshold == 7.5
    assert options.changed is True
    assert options.use_existing_coverage is True
    assert options.no_coverage is False
    assert options.positionals == ["core", "util"]


def test_usage_errors_name_the_problem_and_print_help_to_stderr(capsys):
    cases = {
        ("--nope",): "Unknown option: --nope",
        ("-s",): "-s requires a value",
        ("--coverage-command", "--changed"): "--coverage-command requires a value",
        ("--threshold", "high"): "--threshold requires a number",
        ("--no-coverage", "--coverage-command", "true"): "--no-coverage cannot be combined with --coverage-command",
    }
    for argv, message in cases.items():
        assert run(list(argv)) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == f"{message}\n\n{HELP}"
    assert run(["-h", "--nope"]) == 0
    assert capsys.readouterr().out == HELP


def test_threshold_failure_names_both_scores(tmp_path, capsys):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n")
    lcov = tmp_path / "target" / "coverage" / "lcov.info"
    lcov.parent.mkdir(parents=True)
    lcov.write_text("SF:src/demo/core.clj\nDA:3,0\nDA:4,0\nend_of_record\n", encoding="utf-8")
    assert run(["--root", str(tmp_path), "--use-existing-coverage", "--threshold", "5.25"]) == 2
    assert "CRAP threshold exceeded: 6.0 > 5.2" in capsys.readouterr().err


def test_changed_selection_skips_tests_and_unknown_files(tmp_path, git):
    git(tmp_path, "init", "-q")
    for name in ("src/core.clj", "notes.md", "test/core_test.clj", "src/util_test.clj"):
        _write(tmp_path, name, "(ns x)\n")
    options = parse_args(["--root", str(tmp_path), "--changed"])
    assert select_files(options) == [(tmp_path / "src" / "core.clj").resolve()]


CLJ = "(ns demo.{name})\n\n(defn {name} [] 1)\n"


def _tree(root: Path) -> None:
    for relative in ("src/keep.clj", "src/gen/made.clj", "lib/util.clj", "lib/gen/deep.clj"):
        name = Path(relative).stem
        _write(root, relative, CLJ.format(name=name))


def _names(root: Path) -> list[str]:
    text = (root / ".metrics" / "crap.edn").read_text(encoding="utf-8")
    return sorted(line.split(':name "', 1)[1].split('"', 1)[0] for line in text.splitlines() if ":name" in line)


def test_exclude_filters_the_default_walk_and_source_roots(tmp_path):
    _tree(tmp_path)
    assert run(["--root", str(tmp_path), "--no-coverage", "--exclude", "src/gen", "--exclude", "lib/g*/"]) == 0
    assert _names(tmp_path) == ["keep", "util"]
    assert run(["--root", str(tmp_path), "--no-coverage", "-s", "src", "--exclude", "**/gen"]) == 0
    assert _names(tmp_path) == ["keep"]


def test_exclude_filters_named_directories_and_reports_named_files(tmp_path, capsys):
    _tree(tmp_path)
    argv = ["--root", str(tmp_path), "--no-coverage", "--exclude", "**/gen", "lib", "src/gen/made.clj"]
    assert run(argv) == 0
    assert _names(tmp_path) == ["util"]
    assert "Skipping excluded src/gen/made.clj\n" in capsys.readouterr().err


def test_an_excluded_named_file_is_dropped_even_when_it_is_not_source(tmp_path, capsys):
    _write(tmp_path, "gen/notes.md", "x\n")
    assert run(["--root", str(tmp_path), "--no-coverage", "--exclude", "gen", "gen/notes.md"]) == 0
    captured = capsys.readouterr()
    assert "Skipping excluded gen/notes.md" in captured.err
    assert "No source files" in captured.out


def test_exclude_filters_changed_files(tmp_path, git):
    git(tmp_path, "init", "-q")
    _tree(tmp_path)
    assert run(["--root", str(tmp_path), "--no-coverage", "--changed", "--exclude", "**/gen"]) == 0
    assert _names(tmp_path) == ["keep", "util"]


def test_parse_args_collects_exclusions_and_full_tree():
    options = parse_args(["--exclude", "a", "--exclude", "b/**", "--full-tree"])
    assert options.exclusions == ["a", "b/**"]
    assert options.full_tree is True
    assert parse_args([]).exclusions == []
    assert parse_args([]).full_tree is False


def test_scope_usage_errors_name_the_problem(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "file.clj").write_text("(ns x)\n", encoding="utf-8")
    outside = tmp_path.parent
    root = ["--root", str(tmp_path)]
    cases = {
        ("--exclude", "../x"): "--exclude must be a path or glob inside the project root: ../x",
        ("--exclude", "/abs"): "--exclude must be a path or glob inside the project root: /abs",
        ("--exclude",): "--exclude requires a value",
        ("--full-tree", "src"): "--full-tree cannot be combined with path arguments",
        ("--full-tree", "--changed"): "--full-tree cannot be combined with --changed",
        ("--full-tree", "--use-existing-coverage"): "--full-tree cannot be combined with --use-existing-coverage",
    }
    for argv, message in cases.items():
        assert run([*root, *argv]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == f"{message}\n\n{HELP}"
    roots = {
        "missing": f"Source root does not exist: {tmp_path.resolve() / 'missing'}",
        str(outside): f"Source root must be inside project root: {outside.resolve()}",
        "..": f"Source root must be inside project root: {outside.resolve()}",
    }
    for name, message in roots.items():
        assert run([*root, "--full-tree", "-s", name]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == f"Source selection failed: {message}\n"
    assert not (tmp_path / ".metrics").exists()


def test_without_full_tree_a_missing_source_root_is_still_allowed(tmp_path, capsys):
    assert run(["--root", str(tmp_path), "--no-coverage", "-s", "missing", "-s", ".."]) == 0


def test_full_tree_accepts_existing_roots_and_a_file_root(tmp_path):
    _tree(tmp_path)
    assert run(["--root", str(tmp_path), "--no-coverage", "--full-tree", "-s", "src", "-s", "lib/util.clj"]) == 0
    assert _names(tmp_path) == ["keep", "made", "util"]
    assert run(["--root", str(tmp_path), "--no-coverage", "--full-tree", "-s", "."]) == 0
    assert _names(tmp_path) == ["deep", "keep", "made", "util"]


def test_full_tree_publishes_every_class_under_the_roots_minus_exclusions(tmp_path, monkeypatch):
    _tree(tmp_path)
    calls = []
    monkeypatch.setattr("crapper.cli.run_coverage", lambda *args: calls.append(args))
    assert run(["--root", str(tmp_path), "--full-tree", "--exclude", "lib/gen"]) == 0
    assert _names(tmp_path) == ["keep", "made", "util"]
    assert [path.name for path in calls[0][1]] == ["util.clj", "made.clj", "keep.clj"]


def test_full_tree_without_coverage_publishes_and_honours_the_threshold(tmp_path, monkeypatch):
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n\n(defn choose [x]\n  (if x 1 0))\n")
    monkeypatch.setattr("crapper.cli.run_coverage", lambda *_args: pytest.fail("coverage ran"))
    assert run(["--root", str(tmp_path), "--full-tree", "--no-coverage"]) == 0
    assert _names(tmp_path) == ["choose"]
    lcov = tmp_path / "target" / "coverage" / "lcov.info"
    lcov.parent.mkdir(parents=True)
    lcov.write_text("SF:src/demo/core.clj\nDA:3,0\nDA:4,0\nend_of_record\n", encoding="utf-8")
    monkeypatch.setattr("crapper.cli.run_coverage", lambda *_args: None)
    (tmp_path / ".metrics" / "crap.edn").unlink()
    assert run(["--root", str(tmp_path), "--full-tree", "--threshold", "1"]) == 2
    assert ":crap 6.0" in (tmp_path / ".metrics" / "crap.edn").read_text(encoding="utf-8")


def test_full_tree_reads_the_project_root_by_default_from_the_current_directory(tmp_path, monkeypatch):
    _tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert run(["--full-tree", "--no-coverage", "-s", "lib"]) == 0
    assert _names(tmp_path) == ["deep", "util"]


def test_a_snapshot_that_cannot_be_published_keeps_the_previous_one(tmp_path, monkeypatch, capsys):
    _tree(tmp_path)
    previous = tmp_path / ".metrics" / "crap.edn"
    previous.parent.mkdir()
    previous.write_text("{:entries []}\n", encoding="utf-8")

    def broken(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("crapper.atomic.os.replace", broken)
    assert run(["--root", str(tmp_path), "--no-coverage"]) == 1
    assert capsys.readouterr().err == "Snapshot publication failed: disk full\n"
    assert previous.read_text(encoding="utf-8") == "{:entries []}\n"
    assert [path.name for path in previous.parent.iterdir()] == ["crap.edn"]


def _scope_tree(root: Path) -> None:
    for relative in ("src/a.py", "src/x/c.py", "lib/b.py"):
        _write(root, relative, "x = 1\n")


def _chosen(root: Path, *argv: str) -> list[str]:
    files = select_files(parse_args(["--root", str(root), *argv]))
    return [path.relative_to(root.resolve()).as_posix() for path in files]


def test_source_roots_narrow_changed_files(tmp_path, git, capsys):
    git(tmp_path, "init", "-q")
    _scope_tree(tmp_path)
    assert _chosen(tmp_path, "-s", "src", "--changed") == ["src/a.py", "src/x/c.py"]
    assert _chosen(tmp_path, "--changed", "-s", "src/x", "-s", "lib") == ["lib/b.py", "src/x/c.py"]
    assert capsys.readouterr().err == ""


def test_a_named_file_outside_the_source_roots_is_skipped_with_a_message(tmp_path, capsys):
    _scope_tree(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-elsewhere.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    assert _chosen(tmp_path, "-s", "src", "lib/b.py", str(outside)) == []
    assert capsys.readouterr().err == (
        "Skipping lib/b.py; it is outside the source roots\n"
        f"Skipping {outside.resolve().as_posix()}; it is outside the source roots\n"
    )


def test_a_named_directory_keeps_only_files_under_the_source_roots(tmp_path, capsys):
    _scope_tree(tmp_path)
    assert _chosen(tmp_path, "-s", "src", ".") == ["src/a.py", "src/x/c.py"]
    assert _chosen(tmp_path, "-s", "src/x", "-s", "lib", "src") == ["src/x/c.py"]
    assert capsys.readouterr().err == ""


def test_a_named_file_inside_a_source_root_is_all_that_is_chosen(tmp_path, capsys):
    _scope_tree(tmp_path)
    assert _chosen(tmp_path, "-s", "src", "src/a.py") == ["src/a.py"]
    assert _chosen(tmp_path, "-s", "src/a.py", "src") == ["src/a.py"]
    assert capsys.readouterr().err == ""


def test_source_roots_alone_still_walk_the_roots_then_filter_and_exclude(tmp_path):
    _scope_tree(tmp_path)
    assert _chosen(tmp_path, "-s", "src") == ["src/a.py", "src/x/c.py"]
    assert _chosen(tmp_path, "-s", "src", "c.py") == ["src/x/c.py"]
    assert _chosen(tmp_path, "-s", "src", "--exclude", "src/x") == ["src/a.py"]
    assert _chosen(tmp_path, "-s", "src", "src", "--exclude", "src/x") == ["src/a.py"]


def test_help_says_source_roots_narrow_paths_and_changed():
    assert "outside the source roots" in HELP
