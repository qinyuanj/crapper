"""Shared tree-sitter helpers for Java, Go, TypeScript, Rust, Python, and Lua."""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def parser_for(language: str):
    from tree_sitter_language_pack import download, get_parser

    download([language])
    return get_parser(language)


def parse(source: str, language: str):
    data = source.encode("utf-8")
    tree = parser_for(language).parse(data)
    return data, tree


def node_text(data: bytes, node) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8")


def start_line(node) -> int:
    return node.start_point[0] + 1


def end_line(node) -> int:
    row, col = node.end_point
    if col == 0:
        return max(start_line(node), row)
    return row + 1


def child_of_type(node, *types: str):
    for child in node.children:
        if child.type in types:
            return child
    return None


def child_text(data: bytes, node, *types: str) -> str | None:
    child = child_of_type(node, *types)
    return None if child is None else node_text(data, child)


def ancestors(node):
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def has_ancestor(node, types) -> bool:
    return any(item.type in types for item in ancestors(node))


def enclosing_names(data: bytes, node, types, *ident_types: str) -> list[str]:
    """Names of the enclosing `types` nodes, outermost first."""

    owners = [item for item in ancestors(node) if item.type in types]
    names = [child_text(data, owner, *ident_types) for owner in reversed(owners)]
    return [name for name in names if name is not None]


def descendants(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def first_of_type(node, type_name: str):
    """The first node of `type_name` in `node`'s subtree, `node` included."""

    return next((item for item in descendants(node) if item.type == type_name), None)


def descendant_text(data: bytes, node, type_name: str) -> str | None:
    found = first_of_type(node, type_name)
    return None if found is None else node_text(data, found)


def complexity(node, is_decision, skip) -> int:
    """McCabe complexity of `node`: 1 plus each decision in the subtree."""

    def walk(current) -> int:
        if skip(current):
            return 0
        score = 1 if is_decision(current) else 0
        for child in current.children:
            score += walk(child)
        return score

    return 1 + walk(node)


def binary_logic(node, operators=("&&", "||")) -> bool:
    return node.type == "binary_expression" and any(child.type in operators for child in node.children)


def strip_root(relative: str, source_root: str | None) -> str:
    """`relative` below `source_root`, or without one leading `./`."""

    root = (source_root or "").replace("\\", "/").rstrip("/")
    if root and root != "." and relative.startswith(root + "/"):
        return relative[len(root) + 1 :]
    return relative.removeprefix("./")


def strip_src(relative: str) -> str:
    if relative.startswith("src/"):
        return relative[4:]
    if "/src/" in relative:
        return relative.split("/src/", 1)[1]
    return relative


def absolute_path(path: str, project_root: str | None) -> str:
    file_path = Path(path)
    if file_path.is_absolute():
        return str(file_path)
    return str((Path(project_root or "") / file_path).resolve())
