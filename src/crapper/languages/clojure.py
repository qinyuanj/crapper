"""Clojure cyclomatic complexity, ported from crap4clj.

Decision points are the same forms crap4clj counts: `if` / `when` and their
variants, `and`, `or`, `loop`, `catch`, and each clause of `cond`, `condp`,
`case`, `cond->`, `cond->>`, `some->`, and `some->>`.
"""

import re

from crapper.languages.language import Language, LanguageFactory
from crapper.languages.treesitter import strip_src
from crapper.model import Function

_DECISION = re.compile(
    r"\((if-not|if-let|if-some|when-not|when-let|when-some|when-first|if|when|and|or|loop|catch)[\s\)]"
)
_COND = re.compile(r"\((some->>|some->|cond->>|cond->|cond|condp|case)[\s\)]")
_DEFN = re.compile(r"(?s)^\(\s*defn-?\s+([^\s\(\)\[\]\{\}\"]+)")
_NS = re.compile(r"\(\s*ns\s+([A-Za-z0-9*+!_?.\-/]+)")
_IN_NS = re.compile(r"\(\s*in-ns\s+'([A-Za-z0-9*+!_?.\-/]+)\s*\)")
_IN_NS_QUOTE = re.compile(
    r"\(\s*in-ns\s+\(quote\s+([A-Za-z0-9*+!_?.\-/]+)\)\s*\)"
)

# The head symbol and the forms before the first clause.
_LEADING_FORMS = {
    "condp": 3,
    "case": 2,
    "cond->": 2,
    "cond->>": 2,
    "some->": 2,
    "some->>": 2,
}
_THREAD_FORMS = {"some->", "some->>"}
_CHAR_DELIMITERS = set("()[]{}\";,")
_STRING = re.compile(r'"((?:[^"\\]|\\.)*\\?)("?)', re.S)


def _blank(match: re.Match) -> str:
    return '"' + re.sub(r"[^\n]", " ", match.group(1)) + match.group(2)


def _strip_strings(text: str) -> str:
    """Blank string contents, keeping the quotes and newlines."""

    return _STRING.sub(_blank, text)


def _strip_comments(text: str) -> str:
    return "\n".join(re.sub(r";.*", "", line) for line in text.splitlines())


def without_strings_and_comments(source: str) -> str:
    return _strip_comments(_strip_strings(source))


def _count_top_level_forms(text: str, start: int) -> int:
    """Elements of the list whose first element starts at `start`."""

    depth = 1
    forms = 0
    in_form = False
    for ch in text[start:]:
        if ch in ")}]":
            depth -= 1
            if depth == 0:
                break
        elif ch.isspace():
            in_form = depth != 1
        else:
            if not in_form:
                forms += 1
            in_form = True
            if ch in "({[":
                depth += 1
    return forms


def _pairwise_clause_count(form_type: str, remaining: int) -> int:
    base = remaining // 2
    if form_type == "case" and remaining % 2 == 1:
        return base + 1
    return base


def _count_clauses(text: str, match: re.Match) -> int:
    form_type = match.group(1)
    remaining = _count_top_level_forms(text, match.start(1)) - _LEADING_FORMS.get(form_type, 1)
    if form_type in _THREAD_FORMS:
        return remaining
    return _pairwise_clause_count(form_type, remaining)


def _count_cond_decisions(clean: str) -> int:
    return sum(_count_clauses(clean, match) for match in _COND.finditer(clean))


def cyclomatic_complexity(fn_text: str) -> int:
    clean = without_strings_and_comments(fn_text)
    simple = len(_DECISION.findall(clean))
    return 1 + simple + _count_cond_decisions(clean)


def _consume_char(source: str, index: int, line: int) -> tuple[int, int]:
    """`\\c` takes the next character, then runs to whitespace or a delimiter."""

    i = index + 2
    while i < len(source) and not source[i].isspace() and source[i] not in _CHAR_DELIMITERS:
        i += 1
    return i, line


def _consume_comment(source: str, index: int, line: int) -> tuple[int, int]:
    end = source.find("\n", index)
    if end == -1:
        return len(source), line
    return end + 1, line + 1


def _consume_string(source: str, index: int, line: int) -> tuple[int, int]:
    end = _STRING.match(source, index).end()
    return end, line + source.count("\n", index, end)


def _remember_form(
    forms: list[dict], source: str, start: int, start_line: int, end: int, line: int
) -> None:
    form_text = source[start : end + 1]
    matched = _DEFN.match(form_text)
    if matched is not None:
        forms.append(dict(name=matched.group(1), start_line=start_line, end_line=line, text=form_text))


def _close_form(forms, source, depth, form_start, form_line, index, line):
    if depth == 1:
        _remember_form(forms, source, form_start, form_line, index, line)
        return 0, None, None
    return max(0, depth - 1), form_start, form_line


_SKIPS = {";": _consume_comment, '"': _consume_string, "\\": _consume_char}


def _extract_top_level_defns(source: str) -> list[dict]:
    forms: list[dict] = []
    index, line, depth = 0, 1, 0
    form_start = form_line = None
    while index < len(source):
        ch = source[index]
        skip = _SKIPS.get(ch)
        if skip is not None:
            index, line = skip(source, index, line)
            continue
        if ch == "(":
            if depth == 0:
                form_start, form_line = index, line
            depth += 1
        elif ch == ")":
            depth, form_start, form_line = _close_form(
                forms, source, depth, form_start, form_line, index, line
            )
        elif ch == "\n":
            line += 1
        index += 1
    return forms


def extract_functions(source: str) -> list[dict]:
    found = []
    for form in _extract_top_level_defns(source):
        found.append(
            {
                "name": form["name"],
                "start_line": form["start_line"],
                "end_line": form["end_line"],
                "complexity": cyclomatic_complexity(form["text"]),
            }
        )
    return found


def declared_namespace(source: str) -> str | None:
    for pattern in (_NS, _IN_NS, _IN_NS_QUOTE):
        match = pattern.search(source)
        if match:
            return match.group(1)
    return None


def namespace_from_path(path: str, source_root: str | None) -> str:
    relative = path.replace("\\", "/")
    root = (source_root or "").replace("\\", "/").rstrip("/")
    if root and root != "." and relative.startswith(root + "/"):
        relative = relative[len(root) + 1 :]
    relative = re.sub(r"\.(?:clj[cs]?|bb)$", "", strip_src(relative))
    return relative.replace("/", ".").replace("_", "-")


def functions_in_source(
    source: str, path: str, source_root: str | None = None
) -> list[Function]:
    namespace = declared_namespace(source) or namespace_from_path(path, source_root)
    return [
        Function(
            name=item["name"],
            namespace=namespace,
            complexity=item["complexity"],
            start_line=item["start_line"],
            end_line=item["end_line"],
            path=path,
            language="clojure",
        )
        for item in extract_functions(source)
    ]


class Clojure(Language):
    def functions(self, source: str, path: str, project_root: str) -> list[Function]:
        return functions_in_source(source, path, project_root)


class ClojureFactory(LanguageFactory):
    def create(self) -> Language:
        return Clojure()
