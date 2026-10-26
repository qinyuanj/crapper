"""Rust functions and cyclomatic complexity.

Decision points are `if` / `if let`, `for`, `while` / `while let`, `loop`,
each `match` arm, `?`, and `&&` / `||`. Free functions are namespaced by the
module path (`crate::foo::bar`). Methods are namespaced by the self type
(`crate::foo::Widget`) so uml-viewer can join them to that type. Test code is
not scored: bodies inside a `tests` module (inline or a `tests.rs` file), a
`#![cfg(test)]` file, and any item marked `#[test]` or `#[cfg(test)]`, alone or
inside the item that carries it.
"""

import re
from pathlib import Path

from crapper.languages.treesitter import (
    binary_logic,
    child_of_type,
    complexity,
    descendants,
    end_line,
    node_text,
    parse,
    start_line,
)
from crapper.languages.language import Language, LanguageFactory
from crapper.model import Function

_DECISIONS = {
    "if_expression",
    "for_expression",
    "while_expression",
    "loop_expression",
    "match_arm",
    "try_expression",
}
_PACKAGE_BLOCK = re.compile(r"(?ms)^\[package\](.*?)(?:^\[|\Z)")
_NAME = re.compile(r'(?m)^name\s*=\s*"([^"]+)"')
_TEST_WORD = re.compile(r"\btest\b")


def _is_decision(node) -> bool:
    return node.type in _DECISIONS or binary_logic(node)


def _skip(_node) -> bool:
    return False


def _has_block(node) -> bool:
    return child_of_type(node, "block") is not None


def _ancestor_mods(data: bytes, node) -> list[str]:
    names: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "mod_item":
            ident = child_of_type(current, "identifier")
            if ident is not None:
                names.append(node_text(data, ident))
        current = current.parent
    names.reverse()
    return names


def _in_mod_named(data: bytes, node, name: str) -> bool:
    return name in _ancestor_mods(data, node)


def _test_attribute(text: str) -> bool:
    compact = "".join(text.split())
    if compact in {"#[test]", "#![test]"}:
        return True
    if not compact.startswith(("#[cfg(", "#![cfg(")) or "not(" in compact:
        return False
    return _TEST_WORD.search(compact) is not None


def _marked_test(data: bytes, node) -> bool:
    """True when an outer attribute right before `node` marks it as test code."""

    sibling = node.prev_named_sibling
    while sibling is not None and sibling.type in {"attribute_item", "line_comment", "block_comment"}:
        if sibling.type == "attribute_item" and _test_attribute(node_text(data, sibling)):
            return True
        sibling = sibling.prev_named_sibling
    return False


def _in_test_item(data: bytes, node) -> bool:
    current = node
    while current is not None:
        if _marked_test(data, current):
            return True
        current = current.parent
    return False


def _test_file(data: bytes, root, modules: list[str]) -> bool:
    if "tests" in modules:
        return True
    return any(
        child.type == "inner_attribute_item" and _test_attribute(node_text(data, child))
        for child in root.children
    )


def _impl_type(data: bytes, impl) -> str | None:
    saw_for = False
    chosen = None
    for child in impl.children:
        if child.type == "for":
            saw_for = True
            chosen = None
            continue
        if child.type in {"type_identifier", "generic_type", "scoped_type_identifier"}:
            if child.type == "type_identifier":
                chosen = node_text(data, child)
            else:
                ident = child_of_type(child, "type_identifier")
                if ident is None:
                    for item in descendants(child):
                        if item.type == "type_identifier":
                            ident = item
                            break
                chosen = node_text(data, ident) if ident is not None else None
            if saw_for:
                return chosen
    return chosen


def _crate_name(path: str) -> tuple[str, Path | None]:
    current = Path(path).resolve().parent
    while True:
        cargo = current / "Cargo.toml"
        if cargo.is_file():
            text = cargo.read_text(encoding="utf-8")
            block = _PACKAGE_BLOCK.search(text)
            if block:
                match = _NAME.search(block.group(1))
                name = match.group(1) if match else current.name
                return name.replace("-", "_"), current
        if current.parent == current:
            break
        current = current.parent
    return "crate", None


def _file_modules(path: str, crate_name: str, crate_root: Path | None) -> list[str]:
    file_path = Path(path).resolve()
    if crate_root is None:
        return [crate_name, file_path.stem] if file_path.stem not in {"lib", "main"} else [crate_name]
    try:
        relative = file_path.relative_to(crate_root).as_posix()
    except ValueError:
        return [crate_name]
    if relative.startswith("src/"):
        relative = relative[4:]
    if relative in {"lib.rs", "main.rs"}:
        return [crate_name]
    parts = relative.split("/")
    if parts[-1] == "mod.rs":
        parts = parts[:-1]
    elif parts[-1].endswith(".rs"):
        parts[-1] = parts[-1][:-3]
    if parts[:1] == ["bin"] and len(parts) >= 2:
        return parts[1:]
    return [crate_name, *parts]


def _namespace(modules: list[str], extra: list[str], type_name: str | None) -> str:
    parts = [*modules, *extra]
    if type_name:
        parts.append(type_name)
    return "::".join(part for part in parts if part)


def _absolute(path: str, project_root: str | None) -> str:
    file_path = Path(path)
    if file_path.is_absolute():
        return str(file_path)
    if project_root:
        return str((Path(project_root) / file_path).resolve())
    return str(file_path.resolve())


def _impl_owner(data: bytes, node):
    parent = node.parent
    if parent is None or parent.type != "declaration_list":
        return None
    owner = parent.parent
    if owner is None or owner.type != "impl_item":
        return None
    return _impl_type(data, owner)


def _record_function(data: bytes, node, modules: list[str], path: str) -> Function | None:
    if node.type != "function_item" or not _has_block(node):
        return None
    if node.parent is not None and node.parent.type == "block":
        return None
    if _in_mod_named(data, node, "tests") or _in_test_item(data, node):
        return None
    ident = child_of_type(node, "identifier")
    if ident is None:
        return None
    return Function(
        name=node_text(data, ident),
        namespace=_namespace(modules, _ancestor_mods(data, node), _impl_owner(data, node)),
        complexity=complexity(node, _is_decision, _skip),
        start_line=start_line(node),
        end_line=end_line(node),
        path=path,
        language="rust",
    )


def functions_in_source(
    source: str, path: str, project_root: str | None = None
) -> list[Function]:
    data, tree = parse(source, "rust")
    located = _absolute(path, project_root)
    crate_name, crate_root = _crate_name(located)
    modules = _file_modules(located, crate_name, crate_root)
    found: list[Function] = []
    if _test_file(data, tree.root_node, modules):
        return found
    for node in descendants(tree.root_node):
        recorded = _record_function(data, node, modules, path)
        if recorded is not None:
            found.append(recorded)
    return found


class Rust(Language):
    def functions(self, source: str, path: str, project_root: str) -> list[Function]:
        return functions_in_source(source, path, project_root)


class RustFactory(LanguageFactory):
    def create(self) -> Language:
        return Rust()
