"""Load Cloverage, LCOV, JaCoCo, and Go coverage profiles.

Clojure prefers Cloverage's per-line form counts, then LCOV. Java uses JaCoCo
instruction counters. Go uses statement profiles. TypeScript, Rust, Python, and Lua use LCOV
line hits. A file with no coverage data scores N/A rather than 0%.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from crapper.model import Function

_SPAN = re.compile(
    r'<span[^>]*title="(\d+) out of (\d+) forms covered"[^>]*>\s*(\d+)&nbsp;'
)
_LCOV_DA = re.compile(r"DA:(\d+),(\d+)")
_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE)
_LCOV_REPORTS = (
    "target/coverage/lcov.info",
    "coverage/lcov.info",
    "target/coverage/typescript/lcov.info",
    "target/coverage/rust/lcov.info",
    "target/coverage/python/lcov.info",
    "target/coverage/lua/lcov.info",
    "target/coverage/**/lcov.info",
    "coverage/**/lcov.info",
)
_GO_REPORTS = (
    "target/coverage/coverage.out",
    "target/coverage/go/coverage.out",
    "coverage.out",
    "*/target/coverage/go/coverage.out",
    "*/*/target/coverage/go/coverage.out",
)
_JACOCO_REPORTS = (
    "target/site/jacoco/jacoco.xml",
    "*/target/site/jacoco/jacoco.xml",
    "*/*/target/site/jacoco/jacoco.xml",
)


def normalize_path(path: str) -> str:
    text = unquote(path).replace("\\", "/")
    if text.startswith("file:"):
        text = text[5:]
    while text.startswith("./"):
        text = text[2:]
    while "//" in text:
        text = text.replace("//", "/")
    return text


def _segments(path: str) -> list[str]:
    return [part for part in normalize_path(path).split("/") if part]


def suffix_match(path: str, suffix: str) -> bool:
    suffix_parts = _segments(suffix)
    return bool(suffix_parts) and _segments(path)[-len(suffix_parts) :] == suffix_parts


def _candidates(source_path: str) -> list[str]:
    relative = normalize_path(source_path)
    absolute = normalize_path(str(Path(source_path).resolve())) if source_path else relative
    no_src = re.sub(r"^src/", "", relative)
    absolute_no_src = re.sub(r"/src/", "/", absolute)
    return [value for value in dict.fromkeys([relative, absolute, no_src, absolute_no_src]) if value]


def coverage_percent(covered: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return 100.0 * covered / total


def percent_for_range(lines: dict[int, tuple[int, int]] | None, start: int, end: int) -> float | None:
    if lines is None:
        return None
    covered = 0
    total = 0
    for line in range(start, end + 1):
        entry = lines.get(line)
        if entry is None:
            continue
        covered += entry[0]
        total += entry[1]
    return coverage_percent(covered, total)


def parse_form_coverage(html: str) -> dict[int, tuple[int, int]]:
    found: dict[int, tuple[int, int]] = {}
    for covered, total, line in _SPAN.findall(html):
        total_forms = int(total)
        if total_forms <= 0:
            continue
        found[int(line)] = (int(covered), total_forms)
    return found


def parse_lcov(text: str) -> dict[str, dict[int, tuple[int, int]]]:
    out: dict[str, dict[int, tuple[int, int]]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            current = out[line[3:]] = {}
        elif line == "end_of_record":
            current = None
        elif current is not None and (match := _LCOV_DA.match(line)):
            hits = int(match.group(2))
            current[int(match.group(1))] = (1 if hits > 0 else 0, 1)
    return out


def read_fresh_lcov(report: Path, source_base: Path) -> dict[str, dict[int, tuple[int, int]]]:
    """Validate required line coverage, resolving SF against the execution cwd.

    This strict reader belongs to the managed lifecycle; parse_lcov stays permissive.
    """
    text = report.read_text(encoding="utf-8")
    out: dict[str, dict[int, tuple[int, int]]] = {}
    sources: set[str] = set()
    source = None
    lines: dict[int, tuple[int, int]] = {}
    counts: dict[str, int] = {}
    ancillary = {"TN", "FN", "FNDA", "FNF", "FNH", "BRDA", "BRF", "BRH"}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        tag, _, value = line.partition(":")
        if tag == "SF":
            if source is not None or not value or "\x00" in value:
                raise ValueError(f"invalid SF on line {number}")
            path = Path(value.replace("\\", "/"))
            if not path.is_absolute():
                path = source_base / path
            path = path.resolve()
            if not path.is_file():
                raise ValueError(f"unusable LCOV source: {value}")
            # Opening verifies readability without imposing a source encoding.
            with path.open("rb") as stream:
                stream.read(1)
            source = str(path)
            identity = os.path.normcase(source)
            if identity in sources:
                raise ValueError(f"duplicate LCOV source: {value}")
            sources.add(identity)
            lines, counts = {}, {}
        elif line == "end_of_record":
            if source is None or counts.get("LF") != len(lines) or counts.get("LH") != sum(v[0] for v in lines.values()):
                raise ValueError(f"missing or inconsistent LF/LH on line {number}")
            out[source] = lines
            source = None
        elif tag == "DA":
            match = re.fullmatch(r"([1-9]\d*),(\d+)(?:,[0-9a-fA-F]{32})?", value)
            if source is None or match is None:
                raise ValueError(f"invalid DA on line {number}")
            index, hits = map(int, match.groups()[:2])
            if index in lines:
                raise ValueError(f"duplicate DA on line {number}")
            lines[index] = (int(hits > 0), 1)
        elif tag in {"LF", "LH"}:
            if source is None or tag in counts or re.fullmatch(r"\d+", value) is None:
                raise ValueError(f"invalid {tag} on line {number}")
            counts[tag] = int(value)
        elif tag not in ancillary:
            raise ValueError(f"invalid LCOV record on line {number}: {line}")
    if source is not None or not out:
        raise ValueError("LCOV requires at least one complete source record")
    return out


def parse_go_profile(text: str) -> dict[str, list[tuple[int, int, int, int]]]:
    """Map file → (start_line, end_line, statements, hits)."""

    out: dict[str, list[tuple[int, int, int, int]]] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("mode:"):
            continue
        fields = line.split()
        if len(fields) != 3 or ":" not in fields[0]:
            raise ValueError(f"invalid coverage segment on line {line_number}: {raw!r}")
        file_part, range_part = fields[0].split(":", 1)
        start_text, end_text = range_part.split(",", 1)
        start_line = int(start_text.split(".", 1)[0])
        end_line = int(end_text.split(".", 1)[0])
        statements = int(fields[1])
        hits = int(fields[2])
        out.setdefault(file_part, []).append((start_line, end_line, statements, hits))
    return out


def _profile_segments(profile, path: str):
    if profile is None:
        return None
    for candidate in _candidates(path):
        for key, value in profile.items():
            if _same_file(key, candidate):
                return value
    return None


def _same_file(key: str, candidate: str) -> bool:
    if normalize_path(key) == candidate:
        return True
    return suffix_match(key, candidate) or suffix_match(candidate, key)


def go_percent(
    profile: dict[str, list[tuple[int, int, int, int]]] | None,
    path: str,
    start: int,
    end: int,
) -> float | None:
    segments = _profile_segments(profile, path)
    if segments is None:
        return None
    total = 0
    covered = 0
    for seg_start, seg_end, statements, hits in segments:
        if seg_end < start or seg_start > end:
            continue
        total += statements
        if hits > 0:
            covered += statements
    return coverage_percent(covered, total)


@dataclass
class JacocoMethod:
    missed: int
    covered: int
    line: int

    @property
    def percent(self) -> float:
        return coverage_percent(self.covered, self.missed + self.covered)


def _jacoco_from_index(classes, class_names, method_name, line) -> float | None:
    """The method on `line`, else the overload nearest to it."""

    pool = [method for name in class_names for method in classes.get(f"{name}#{method_name}", [])]
    if not pool:
        return None
    return min(pool, key=lambda method: abs(method.line - line)).percent


def _instruction_counter(method):
    counters = (child for child in method.findall("counter") if child.get("type") == "INSTRUCTION")
    return next(counters, None)


def _method_line(method) -> int:
    try:
        return int(method.get("line") or "0")
    except ValueError:
        return 0


def parse_jacoco_index(text: str) -> dict[str, list[JacocoMethod]]:
    """Key methods as `binary.class.name#method`."""

    root = ET.fromstring(_DOCTYPE.sub("", text))
    found: dict[str, list[JacocoMethod]] = {}
    for class_node in root.iter("class"):
        class_name = class_node.get("name", "").replace("/", ".")
        for method in class_node.findall("method"):
            counter = _instruction_counter(method)
            if counter is None:
                continue
            missed = int(counter.get("missed") or "0")
            covered = int(counter.get("covered") or "0")
            key = f"{class_name}#{method.get('name', '')}"
            found.setdefault(key, []).append(JacocoMethod(missed, covered, _method_line(method)))
    return found


class _ExactIndex:
    """First record per normcased absolute path, as a linear scan would find it."""

    def __init__(self, records: dict[str, dict[int, tuple[int, int]]]):
        self._records: dict[str, dict[int, tuple[int, int]]] = {}
        for key, lines in records.items():
            self._records.setdefault(os.path.normcase(key), lines)

    def get(self, path: Path) -> dict[int, tuple[int, int]] | None:
        return self._records.get(os.path.normcase(str(path)))


class _SuffixIndex:
    """Legacy exact-then-suffix path matching, indexed once instead of per call.

    The suffix fallback returns the earliest record, in report order, whose path
    ends with any candidate, so each suffix keeps its earliest record position.
    """

    def __init__(self, records: dict[str, dict]):
        self._normalized = {normalize_path(key): value for key, value in records.items()}
        self._suffixes: dict[tuple[str, ...], tuple[int, dict]] = {}
        for position, (key, value) in enumerate(self._normalized.items()):
            parts = _segments(key)
            for length in range(1, len(parts) + 1):
                self._suffixes.setdefault(tuple(parts[-length:]), (position, value))
        self._found: dict[str, dict | None] = {}

    def get(self, source_path: str):
        if source_path not in self._found:
            self._found[source_path] = self._match(source_path)
        return self._found[source_path]

    def _match(self, source_path: str):
        if not self._normalized:
            return None
        candidates = _candidates(source_path)
        for candidate in candidates:
            if candidate in self._normalized:
                return self._normalized[candidate]
        matches = [self._suffixes[key] for key in (tuple(_segments(c)) for c in candidates)
                   if key and key in self._suffixes]
        return min(matches, key=lambda match: match[0])[1] if matches else None


@dataclass
class CoverageBundle:
    """Coverage for every analyzed function.

    Record lookups are indexed on first use, so the record maps must be
    complete before the first `percent_for` call.
    """

    lcov: dict[str, dict[int, tuple[int, int]]] = field(default_factory=dict)
    go_profile: dict[str, list[tuple[int, int, int, int]]] | None = None
    jacoco: dict[str, list[JacocoMethod]] | None = None
    form_html: dict[str, dict[int, tuple[int, int]]] = field(default_factory=dict)
    managed_lua: dict[str, dict[int, tuple[int, int]]] | None = None
    fresh_lcov: dict[str, dict[int, tuple[int, int]]] | None = None
    source_root: Path | None = None
    _indexes: dict[str, object] = field(default_factory=dict, init=False, repr=False, compare=False)

    def _index(self, name: str, build):
        if name not in self._indexes:
            self._indexes[name] = build()
        return self._indexes[name]

    def percent_for(self, function: Function) -> float | None:
        start, end = function.start_line, function.end_line
        if function.language == "lua" and self.managed_lua is not None:
            index = self._index("managed_lua", lambda: _ExactIndex(self.managed_lua))
            lines = index.get((self.source_root / function.path).resolve())
            return percent_for_range(lines, start, end)
        if function.language == "java":
            names = (function.jacoco_class or function.namespace, function.namespace)
            return _jacoco_from_index(self.jacoco or {}, names, function.name, start)
        if function.language == "go":
            return go_percent(self.go_profile, function.path, start, end)
        lines = self._html_lines(function.path)
        if lines is None:
            lines = self._lcov_lines(function.path)
        return percent_for_range(lines, start, end)

    def _html_lines(self, path: str) -> dict[int, tuple[int, int]] | None:
        return self._index("form_html", lambda: _SuffixIndex(self.form_html)).get(path)

    def _lcov_lines(self, path: str) -> dict[int, tuple[int, int]] | None:
        if self.fresh_lcov is not None:
            # Fresh sources use project-anchored exact identity; legacy imports
            # retain their historical suffix matching.
            index = self._index("fresh_lcov", lambda: _ExactIndex(self.fresh_lcov))
            return index.get((self.source_root / path).resolve())
        return self._index("lcov", lambda: _SuffixIndex(self.lcov)).get(path)


def _read(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _merge_texts(paths: list[Path]) -> list[str]:
    texts: list[str] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        text = _read(path)
        if text:
            texts.append(text)
    return texts


def _report_texts(root: Path, patterns: tuple[str, ...]) -> list[str]:
    """Each report once, in pattern order. A later LCOV record for a file wins."""

    return _merge_texts([path for pattern in patterns for path in root.glob(pattern)])


def _merge_lcov(root: Path) -> dict[str, dict[int, tuple[int, int]]]:
    merged: dict[str, dict[int, tuple[int, int]]] = {}
    for text in _report_texts(root, _LCOV_REPORTS):
        merged.update(parse_lcov(text))
    return merged


def _merge_lists(texts: list[str], parse) -> dict[str, list]:
    merged: dict[str, list] = {}
    for text in texts:
        for key, items in parse(text).items():
            merged.setdefault(key, []).extend(items)
    return merged


def _merge_forms(root: Path) -> dict[str, dict[int, tuple[int, int]]]:
    coverage_dir = root / "target" / "coverage"
    found: dict[str, dict[int, tuple[int, int]]] = {}
    for html_path in coverage_dir.rglob("*.html"):
        text = _read(html_path)
        if text and "forms covered" in text:
            relative = html_path.relative_to(coverage_dir).as_posix().removesuffix(".html")
            found[relative] = parse_form_coverage(text)
    return found


def load_bundle(root: Path) -> CoverageBundle:
    bundle = CoverageBundle(lcov=_merge_lcov(root), form_html=_merge_forms(root))
    bundle.go_profile = _merge_lists(_report_texts(root, _GO_REPORTS), parse_go_profile) or None
    bundle.jacoco = _merge_lists(_report_texts(root, _JACOCO_REPORTS), parse_jacoco_index) or None
    return bundle
