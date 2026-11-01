import pytest

from crapper.coverage import (
    CoverageBundle,
    go_percent,
    load_bundle,
    parse_form_coverage,
    parse_go_profile,
    parse_jacoco_index,
    parse_lcov,
    suffix_match,
)
from crapper.model import Function


def function(**kwargs):
    defaults = dict(
        name="choose",
        namespace="demo.core",
        complexity=2,
        start_line=3,
        end_line=5,
        path="src/demo/core.clj",
        language="clojure",
        jacoco_class=None,
    )
    defaults.update(kwargs)
    return Function(**defaults)


def test_cloverage_form_counts_and_lcov_lines():
    html = '<span class="pc" title="1 out of 2 forms covered">   4&nbsp;&nbsp;(if x 1)</span>'
    assert parse_form_coverage(html) == {4: (1, 2)}
    lcov = "SF:src/demo/core.clj\nDA:3,1\nDA:4,0\nDA:9,3\nend_of_record\n"
    parsed = parse_lcov(lcov)
    bundle = CoverageBundle(lcov=parsed, form_html={"src/demo/core.clj": {4: (1, 2)}})
    # Form HTML wins over LCOV for Clojure, matching crap4clj.
    assert bundle.percent_for(function(start_line=3, end_line=5)) == 50.0


def test_lcov_percentage_when_there_is_no_html():
    lcov = parse_lcov("SF:src/demo/core.clj\nDA:3,1\nDA:4,0\nend_of_record\n")
    bundle = CoverageBundle(lcov=lcov)
    assert bundle.percent_for(function(start_line=3, end_line=4)) == 50.0
    assert bundle.percent_for(function(path="src/missing.clj")) is None


def test_go_profile_matches_crap4go_statement_ranges():
    profile = parse_go_profile(
        "mode: set\ngithub.com/acme/demo/board.go:4.1,6.2 2 1\ngithub.com/acme/demo/board.go:8.1,9.2 1 0\n"
    )
    assert go_percent(profile, "board.go", 4, 6) == 100.0
    assert go_percent(profile, "board.go", 8, 9) == 0.0
    assert go_percent(profile, "other.go", 1, 3) is None


def test_jacoco_instruction_coverage_joins_on_class_and_method():
    xml = """<?xml version="1.0"?>
    <report>
      <package>
        <class name="demo/pkg/Board">
          <method name="place" line="4">
            <counter type="INSTRUCTION" missed="1" covered="3"/>
          </method>
        </class>
        <class name="demo/pkg/Board$Inner">
          <method name="tick" line="9">
            <counter type="INSTRUCTION" missed="0" covered="2"/>
          </method>
        </class>
      </package>
    </report>
    """
    bundle = CoverageBundle(jacoco=parse_jacoco_index(xml))
    outer = function(
        name="place",
        namespace="demo.pkg.Board",
        language="java",
        jacoco_class="demo.pkg.Board",
        start_line=4,
        path="src/demo/pkg/Board.java",
    )
    inner = function(
        name="tick",
        namespace="demo.pkg.Board.Inner",
        language="java",
        jacoco_class="demo.pkg.Board$Inner",
        start_line=9,
        path="src/demo/pkg/Board.java",
    )
    assert bundle.percent_for(outer) == 75.0
    assert bundle.percent_for(inner) == 100.0


def test_jacoco_skips_methods_without_an_instruction_counter():
    xml = """<report>
      <class name="demo/pkg/Board">
        <method name="skip" line="2">
          <counter type="LINE" missed="1" covered="0"/>
        </method>
        <method name="bad" line="nope">
          <counter type="INSTRUCTION" missed="1" covered="1"/>
        </method>
        <counter type="INSTRUCTION" missed="0" covered="1"/>
      </class>
    </report>"""
    found = parse_jacoco_index(xml)
    assert list(found) == ["demo.pkg.Board#bad"]
    assert found["demo.pkg.Board#bad"][0].line == 0


def test_missing_go_profile_is_na():
    assert go_percent(None, "board.go", 1, 3) is None


def test_load_bundle_merges_each_report(tmp_path):
    lcov = tmp_path / "target" / "coverage" / "lcov.info"
    lcov.parent.mkdir(parents=True)
    lcov.write_text("SF:src/demo/core.clj\nDA:3,1\nDA:4,0\nend_of_record\n", encoding="utf-8")
    go = tmp_path / "target" / "coverage" / "go" / "coverage.out"
    go.parent.mkdir(parents=True)
    go.write_text("mode: set\nboard.go:1.1,2.2 1 1\n", encoding="utf-8")
    jacoco = tmp_path / "target" / "site" / "jacoco" / "jacoco.xml"
    jacoco.parent.mkdir(parents=True)
    jacoco.write_text(
        '<report><class name="demo/Board"><method name="place" line="4">'
        '<counter type="INSTRUCTION" missed="0" covered="1"/></method></class></report>',
        encoding="utf-8",
    )
    html = tmp_path / "target" / "coverage" / "src" / "demo" / "core.clj.html"
    html.parent.mkdir(parents=True)
    html.write_text(
        '<span title="1 out of 2 forms covered">   4&nbsp;&nbsp;(if x 1)</span>',
        encoding="utf-8",
    )
    (tmp_path / "target" / "coverage" / "index.html").write_text("<html></html>", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    assert bundle.percent_for(function(start_line=3, end_line=4)) == 50.0
    assert bundle.go_profile is not None
    assert bundle.jacoco is not None


def test_suffix_match_needs_a_suffix_and_accepts_the_whole_path():
    assert suffix_match("demo/core.clj", "./demo/core.clj") is True
    assert suffix_match("", "") is False


def test_a_span_with_a_single_form_is_kept():
    html = '<span title="1 out of 1 forms covered">   7&nbsp;(f)</span>'
    assert parse_form_coverage(html) == {7: (1, 1)}


def test_a_broken_go_profile_names_the_line():
    with pytest.raises(ValueError, match="line 2:"):
        parse_go_profile("mode: set\nbroken\n")


def test_go_segments_touching_either_end_of_the_function_count():
    profile = parse_go_profile(
        "mode: set\nboard.go:1.1,4.2 1 1\nboard.go:6.1,8.2 1 0\nboard.go:9.1,9.9 1 0\n"
    )
    assert go_percent(profile, "board.go", 4, 6) == 50.0
    assert go_percent(profile, "board.go", 4, 4) == 100.0


def test_go_profile_key_may_be_a_suffix_of_the_source_path():
    profile = parse_go_profile("mode: set\ndemo/board.go:4.1,6.2 1 1\n")
    assert go_percent(profile, "pkg/demo/board.go", 4, 6) == 100.0


def test_go_profile_key_matches_a_source_path_with_a_literal_percent():
    profile = parse_go_profile("mode: set\npkg/a%2541.go:4.1,6.2 1 1\n")
    assert go_percent(profile, "pkg/a%2541.go", 4, 6) == 100.0


def test_jacoco_counter_without_covered_is_zero():
    xml = """<report><class name="demo/Board">
      <method name="place" line="4"><counter type="INSTRUCTION" missed="2"/></method>
    </class></report>"""
    method = parse_jacoco_index(xml)["demo.Board#place"][0]
    assert (method.missed, method.covered, method.percent) == (2, 0, 0.0)


def test_form_html_is_keyed_by_source_path_and_wins_over_lcov(tmp_path):
    coverage = tmp_path / "target" / "coverage"
    html = coverage / "src" / "demo" / "core.clj.html"
    html.parent.mkdir(parents=True)
    html.write_text('<span title="1 out of 4 forms covered">   3&nbsp;(f)</span>', encoding="utf-8")
    (coverage / "index.html").write_text("<html></html>", encoding="utf-8")
    (coverage / "empty.html").write_text("", encoding="utf-8")
    (coverage / "folder.html").mkdir()
    (coverage / "lcov.info").write_text("SF:src/demo/core.clj\nDA:3,1\nend_of_record\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    assert bundle.form_html == {"src/demo/core.clj": {3: (1, 4)}}
    assert bundle.percent_for(function(start_line=3, end_line=3)) == 25.0


def test_every_lcov_location_is_read(tmp_path):
    places = [
        "target/coverage/lcov.info",
        "coverage/lcov.info",
        "target/coverage/typescript/lcov.info",
        "target/coverage/rust/lcov.info",
        "target/coverage/python/lcov.info",
        "target/coverage/lua/lcov.info",
        "target/coverage/app/lcov.info",
        "coverage/web/src-tauri/lcov.info",
    ]
    for index, place in enumerate(places):
        path = tmp_path / place
        path.parent.mkdir(parents=True, exist_ok=True)
        records = "".join(
            f"SF:f{name}.py\nDA:{index + 1},1\nend_of_record\n" for name in range(index, len(places))
        )
        path.write_text(records, encoding="utf-8")
    # f<k> is in every report up to place k; the record read last, place k, wins.
    assert load_bundle(tmp_path).lcov == {f"f{index}.py": {index + 1: (1, 1)} for index in range(len(places))}


def test_every_go_profile_location_is_read(tmp_path):
    places = [
        "target/coverage/coverage.out",
        "target/coverage/go/coverage.out",
        "coverage.out",
        "svc/target/coverage/go/coverage.out",
        "cmd/svc/target/coverage/go/coverage.out",
    ]
    for index, place in enumerate(places):
        path = tmp_path / place
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"mode: set\nf{index}.go:1.1,2.2 1 1\n", encoding="utf-8")
    assert sorted(load_bundle(tmp_path).go_profile) == [f"f{index}.go" for index in range(len(places))]


def test_every_jacoco_location_is_read(tmp_path):
    places = [
        "target/site/jacoco/jacoco.xml",
        "api/target/site/jacoco/jacoco.xml",
        "svc/api/target/site/jacoco/jacoco.xml",
    ]
    for index, place in enumerate(places):
        path = tmp_path / place
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'<report><class name="demo/C{index}"><method name="m" line="1">'
            '<counter type="INSTRUCTION" missed="0" covered="1"/></method></class></report>',
            encoding="utf-8",
        )
    assert sorted(load_bundle(tmp_path).jacoco) == [f"demo.C{index}#m" for index in range(len(places))]


def test_lcov_ignores_lines_outside_a_record():
    parsed = parse_lcov("DA:1,1\nSF:a.clj\nDA:2,1\nDA:3,0\nend_of_record\nDA:4,1\nSF:a.clj\nDA:5,2\n")
    assert parsed == {"a.clj": {5: (1, 1)}}
