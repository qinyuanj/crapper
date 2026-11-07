"""Windows-style paths name the same modules as POSIX ones, on any host.

uml-viewer joins the snapshot on :namespace, so a Windows run must produce
`pkg.mod`, never `src\\pkg\\mod` or a drive letter.
"""

import pytest

from crapper.coverage import normalize_path, suffix_match
from crapper.discover import excluded
from crapper.languages import functions_in_file

SOURCES = {
    "python": ("src\\shop\\cart.py", "def total(items):\n    return sum(items)\n", "shop.cart"),
    "typescript": ("src\\shop\\cart.ts", "export function total(a: number) { return a; }\n", "shop.cart"),
    "lua": ("src\\shop\\cart.lua", "local M = {}\nfunction M.total(a) return a end\nreturn M\n", "shop.cart"),
    "clojure": ("src\\shop\\cart.clj", "(ns shop.cart)\n(defn total [a] a)\n", "shop.cart"),
}


@pytest.mark.parametrize("language", sorted(SOURCES))
def test_backslash_paths_name_the_posix_module(language):
    path, source, namespace = SOURCES[language]
    functions = functions_in_file(language, source, path, "C:\\work\\shop")
    assert [function.namespace for function in functions] == [namespace]
    assert functions == functions_in_file(language, source, path.replace("\\", "/"), "C:/work/shop")


@pytest.mark.parametrize(("raw", "expected"), [
    ("C:\\work\\shop\\src\\cart.py", "C:/work/shop/src/cart.py"),
    (".\\src\\cart.py", "src/cart.py"),
    ("file:C:/work/shop/src/cart.py", "C:/work/shop/src/cart.py"),
    ("src\\\\cart.py", "src/cart.py"),
])
def test_coverage_paths_normalize_to_forward_slashes(raw, expected):
    assert normalize_path(raw) == expected


def test_a_windows_report_path_matches_its_posix_suffix():
    assert suffix_match("C:\\work\\shop\\src\\shop\\cart.py", "shop/cart.py")
    assert not suffix_match("C:\\work\\shop\\src\\shop\\cart.py", "other/cart.py")


@pytest.mark.parametrize("pattern", ["src\\gen", "src/gen", ".\\src\\gen\\", "src\\**\\*.py"])
def test_exclusions_take_either_separator(tmp_path, pattern):
    assert excluded(tmp_path / "src" / "gen" / "a.py", tmp_path, [pattern]) is True
