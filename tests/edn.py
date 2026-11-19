"""Read the EDN subset `.metrics/crap.edn` is written in.

crapper writes one EDN map — `{:entries [{:name … :namespace … :complexity …
:coverage … :crap …} …]}` — and uml-viewer reads it with a Clojure EDN reader.
The suite checks that contract without a second language runtime (ADR 0007), so
this reader accepts exactly the forms `crapper.metrics` can emit: maps, vectors,
keyword keys, strings, integers, decimals, nil and booleans, with EDN's commas
counting as whitespace. Anything else raises `EdnError`, which is the point — a
snapshot that stops being readable EDN fails the test that reads it instead of
passing as text.
"""

import re

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\Z")
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_WHITESPACE = " \t\r\n,"
_ENDS = _WHITESPACE + '{}[]()";'


class Keyword:
    """An EDN keyword: `:entries` is not the string `"entries"`."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, Keyword) and other.name == self.name

    def __hash__(self):
        return hash((Keyword, self.name))

    def __repr__(self):
        return ":" + self.name


class EdnError(ValueError):
    """Text that is not the EDN subset `.metrics/crap.edn` is written in."""


class _Reader:
    def __init__(self, text: str):
        self.text = text
        self.index = 0

    def fail(self, message: str) -> EdnError:
        return EdnError(f"{message} at offset {self.index}")

    def skip_space(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in _WHITESPACE:
            self.index += 1

    def read(self):
        self.skip_space()
        if self.index >= len(self.text):
            raise self.fail("unexpected end of EDN")
        char = self.text[self.index]
        if char == "{":
            return self._read_map()
        if char == "[":
            return self._read_vector()
        if char == '"':
            return self._read_string()
        if char == ":":
            return self._read_keyword()
        return self._read_atom()

    def _read_map(self) -> dict:
        self.index += 1
        result: dict = {}
        while True:
            self.skip_space()
            if self.index >= len(self.text):
                raise self.fail("unterminated map")
            if self.text[self.index] == "}":
                self.index += 1
                return result
            key = self.read()
            if not isinstance(key, Keyword):
                raise self.fail(f"map key must be a keyword, not {key!r}")
            if key in result:
                raise self.fail(f"duplicate key {key!r}")
            result[key] = self.read()

    def _read_vector(self) -> list:
        self.index += 1
        result: list = []
        while True:
            self.skip_space()
            if self.index >= len(self.text):
                raise self.fail("unterminated vector")
            if self.text[self.index] == "]":
                self.index += 1
                return result
            result.append(self.read())

    def _read_string(self) -> str:
        self.index += 1
        chars: list[str] = []
        while True:
            if self.index >= len(self.text):
                raise self.fail("unterminated string")
            char = self.text[self.index]
            self.index += 1
            if char == '"':
                return "".join(chars)
            if char != "\\":
                chars.append(char)
                continue
            if self.index >= len(self.text):
                raise self.fail("unterminated escape")
            escape = self.text[self.index]
            self.index += 1
            if escape == "u":
                digits = self.text[self.index:self.index + 4]
                if len(digits) != 4 or any(digit not in "0123456789abcdefABCDEF" for digit in digits):
                    raise self.fail("invalid \\u escape")
                chars.append(chr(int(digits, 16)))
                self.index += 4
                continue
            if escape not in _ESCAPES:
                raise self.fail(f"unknown string escape \\{escape}")
            chars.append(_ESCAPES[escape])

    def _read_keyword(self) -> Keyword:
        self.index += 1
        start = self.index
        while self.index < len(self.text) and self.text[self.index] not in _ENDS:
            self.index += 1
        if start == self.index:
            raise self.fail("empty keyword")
        return Keyword(self.text[start:self.index])

    def _read_atom(self):
        start = self.index
        while self.index < len(self.text) and self.text[self.index] not in _ENDS:
            self.index += 1
        token = self.text[start:self.index]
        if token == "nil":
            return None
        if token in {"true", "false"}:
            return token == "true"
        if _NUMBER.match(token):
            return float(token) if any(char in token for char in ".eE") else int(token)
        raise self.fail(f"unsupported EDN token {token!r}")


def read_edn(text: str):
    """Read exactly one EDN form, with nothing but whitespace after it."""

    reader = _Reader(text)
    value = reader.read()
    reader.skip_space()
    if reader.index != len(reader.text):
        raise reader.fail("trailing data after the first EDN form")
    return value
