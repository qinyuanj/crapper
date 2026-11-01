"""Rust functions and cyclomatic complexity.

Decision points are `if` / `if let`, `for`, `while` / `while let`, `loop`,
each `match` arm, `?`, and `&&` / `||`. Free functions are namespaced by the
module path (`crate::foo::bar`). Methods are namespaced by the self type
(`crate::foo::Widget`) so uml-viewer can join them to that type. Test code is
not scored: anything inside a `tests` module (inline or a `tests.rs` file), a
file or module that opens with `#![cfg(test)]`, or an item marked `#[test]`,
`#[bench]`, `#[<runner>::test]`, or a `cfg` that holds only under `test`
(`cfg(test)`, `cfg(all(test, ...))`; not `cfg(any(test, ...))`).
"""

import re
from pathlib import Path

from crapper.languages.treesitter import (
    absolute_path,
    binary_logic,
    child_of_type,
    child_text,
    complexity,
    descendant_text,
    descendants,
    enclosing_names,
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
_PACKAGE_BLOCK = re.compile(r"(?ms)^\[package\].*?(?=^\[|\Z)")
_SELF_TYPES = {"type_identifier", "generic_type", "scoped_type_identifier"}
_NAME = re.compile(r'(?m)^name\s*=\s*"([^"]+)"')


def _is_decision(node) -> bool:
    return node.type in _DECISIONS or binary_logic(node)


def _skip(_node) -> bool:
    return False


def _has_block(node) -> bool:
    return child_of_type(node, "block") is not None


def _cfg_items(tree) -> list[list]:
    """The comma-separated predicates inside a cfg `token_tree`."""

    items: list[list] = [[]]
    for child in tree.children[1:-1]:
        if child.type == ",":
            items.append([])
        else:
            items[-1].append(child)
    return [item for item in items if item]


def _requires_test(data: bytes, item: list) -> bool:
    """True when this cfg predicate can only hold in a `cfg(test)` build."""

    name = node_text(data, item[0])
    if len(item) == 1:
        return name == "test"
    if item[1].type != "token_tree":
        return False
    args = _cfg_items(item[1])
    if name == "all":
        return any(_requires_test(data, arg) for arg in args)
    if name == "any":
        return bool(args) and all(_requires_test(data, arg) for arg in args)
    return False


def _test_attribute(data: bytes, item) -> bool:
    attribute = child_of_type(item, "attribute")
    if attribute is None or not attribute.children:
        return False
    path = node_text(data, attribute.children[0])
    arguments = child_of_type(attribute, "token_tree")
    if arguments is None:
        return path.split("::")[-1] in {"test", "bench"}
    if path != "cfg":
        return False
    predicates = _cfg_items(arguments)
    return len(predicates) == 1 and _requires_test(data, predicates[0])


def _marked_test(data: bytes, node) -> bool:
    """True when an outer attribute right before `node` marks it as test code."""

    sibling = node.prev_named_sibling
    while sibling is not None and sibling.type in {"attribute_item", "line_comment", "block_comment"}:
        if sibling.type == "attribute_item" and _test_attribute(data, sibling):
            return True
        sibling = sibling.prev_named_sibling
    return False


def _inner_test(data: bytes, container) -> bool:
    return any(
        child.type == "inner_attribute_item" and _test_attribute(data, child)
        for child in container.children
    )


def _test_module(data: bytes, node) -> bool:
    if node.type == "source_file":
        return _inner_test(data, node)
    if node.type != "mod_item":
        return False
    ident = child_of_type(node, "identifier")
    if ident is not None and node_text(data, ident) == "tests":
        return True
    body = child_of_type(node, "declaration_list")
    return body is not None and _inner_test(data, body)


def _in_test_code(data: bytes, node) -> bool:
    current = node
    while current is not None:
        if _marked_test(data, current) or _test_module(data, current):
            return True
        current = current.parent
    return False


def _impl_type(data: bytes, impl) -> str | None:
    """The self type of `impl Type` or `impl Trait for Type`, by its last path segment."""

    node = impl.child_by_field_name("type")
    if node is None or node.type not in _SELF_TYPES:
        return None
    return child_text(data, node, "type_identifier") or descendant_text(data, node, "type_identifier")


def _crate_name(path: str) -> tuple[str, Path | None]:
    for directory in Path(path).resolve().parents:
        cargo = directory / "Cargo.toml"
        if not cargo.is_file():
            continue
        block = _PACKAGE_BLOCK.search(cargo.read_text(encoding="utf-8"))
        if block:
            match = _NAME.search(block.group())
            name = match.group(1) if match else directory.name
            return name.replace("-", "_"), directory
    return "crate", None


def _crate_relative(file_path: Path, crate_root: Path) -> list[str]:
    """Module segments of a file below its crate root. `lib.rs`, `main.rs`, and `mod.rs` add none."""

    relative = file_path.relative_to(crate_root).as_posix().removeprefix("src/")
    if relative in {"lib.rs", "main.rs"}:
        return []
    *parts, name = relative.split("/")
    if name != "mod.rs":
        parts.append(name.removesuffix(".rs"))
    return parts


def _file_modules(path: str, crate_name: str, crate_root: Path | None) -> list[str]:
    file_path = Path(path).resolve()
    if crate_root is None:
        return [crate_name] if file_path.stem in {"lib", "main"} else [crate_name, file_path.stem]
    parts = _crate_relative(file_path, crate_root)
    if parts[:1] == ["bin"] and len(parts) >= 2:
        return parts[1:]
    return [crate_name, *parts]


def _namespace(modules: list[str], extra: list[str], type_name: str | None) -> str:
    parts = [*modules, *extra, type_name]
    return "::".join(part for part in parts if part)


def _impl_owner(data: bytes, node):
    parent = node.parent
    if parent.type != "declaration_list" or parent.parent.type != "impl_item":
        return None
    return _impl_type(data, parent.parent)


def _record_function(data: bytes, node, modules: list[str], path: str) -> Function | None:
    if node.type != "function_item" or not _has_block(node) or node.parent.type == "block":
        return None
    ident = child_of_type(node, "identifier")
    if ident is None or _in_test_code(data, node):
        return None
    mods = enclosing_names(data, node, {"mod_item"}, "identifier")
    return Function(
        name=node_text(data, ident),
        namespace=_namespace(modules, mods, _impl_owner(data, node)),
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
    located = absolute_path(path, project_root)
    crate_name, crate_root = _crate_name(located)
    modules = _file_modules(located, crate_name, crate_root)
    found: list[Function] = []
    if "tests" in modules[1:]:
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
