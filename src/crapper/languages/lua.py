"""Lua functions and cyclomatic complexity.

Decision points: `if`, `elseif`, `while`, `repeat`, both `for` forms, and each
`and` / `or`. Entries are `function` declarations and function expressions
assigned to a name (`local f = function`, `M.bar = function`). The name is
written as in the source: `helper`, `M.foo`, `Account:deposit`. A nested
function stays inside the enclosing function, and an anonymous closure that
is never assigned is not an entry. The namespace is the `require` path.

One tree-sitter query finds functions and decisions in C. Syntax-tree ranges
nest, so containment by byte range stands in for walking ancestors and subtrees.
"""

from bisect import bisect_left
from functools import cache

from tree_sitter import Query, QueryCursor

from crapper.languages.treesitter import (
    end_line,
    node_text,
    parse,
    parser_for,
    start_line,
)
from crapper.languages.language import Language, LanguageFactory
from crapper.model import Function

_QUERY = """
(function_declaration) @function
(function_definition) @function
(if_statement) @decision
(elseif_statement) @decision
(while_statement) @decision
(repeat_statement) @decision
(for_statement) @decision
(binary_expression "and") @decision
(binary_expression "or") @decision
"""

_ROOTS = ("src/lua/", "src/", "lua/")


@cache
def _query() -> Query:
    return Query(parser_for("lua").language, _QUERY)


def _relative(path: str, project_root: str | None) -> str:
    relative = path.replace("\\", "/")
    root = (project_root or "").replace("\\", "/").rstrip("/")
    if root and root != "." and relative.startswith(root + "/"):
        relative = relative[len(root) + 1 :]
    while relative.startswith("./"):
        relative = relative[2:]
    return relative


def _strip_lua_root(relative: str) -> str:
    for root in _ROOTS:
        if relative.startswith(root):
            return relative[len(root) :]
    return relative


def _module_namespace(path: str, project_root: str | None) -> str:
    relative = _strip_lua_root(_relative(path, project_root))
    if relative.endswith(".lua"):
        relative = relative[: -len(".lua")]
    if relative.endswith("/init"):
        relative = relative[: -len("/init")]
    return relative.replace("/", ".")


def _top_level(functions) -> list:
    """Functions not inside another function, in source order."""

    found = []
    open_ends: list[int] = []
    for node in sorted(functions, key=lambda node: (node.start_byte, -node.end_byte)):
        while open_ends and open_ends[-1] <= node.start_byte:
            open_ends.pop()
        if not open_ends:
            found.append(node)
        open_ends.append(node.end_byte)
    return found


def _complexity(node, decisions: list[tuple[int, int]]) -> int:
    """1 plus each decision inside `node`, nested functions included."""

    first = bisect_left(decisions, (node.start_byte, -1))
    score = 1
    for start, end in decisions[first:]:
        if start >= node.end_byte:
            break
        if end <= node.end_byte:
            score += 1
    return score


def _assigned_name(data: bytes, node):
    """The target of `name = function ... end`, or None for an unbound closure."""

    values = node.parent
    if values is None or values.type != "expression_list":
        return None
    assignment = values.parent
    if assignment is None or assignment.type != "assignment_statement":
        return None
    targets = [child for child in assignment.children if child.type == "variable_list"]
    if not targets:
        return None
    index = values.named_children.index(node)
    names = targets[0].named_children
    if index >= len(names):
        return None
    return node_text(data, names[index])


def _declaration_start(node):
    """`local f = function` starts at `local`, so the header shows the visibility."""

    assignment = node.parent.parent
    declaration = assignment.parent
    if declaration is not None and declaration.type == "variable_declaration":
        return declaration
    return assignment


def _entry(data: bytes, node):
    if node.type == "function_declaration":
        name = node.child_by_field_name("name")
        if name is None:
            return None
        return node_text(data, name), node
    name = _assigned_name(data, node)
    if name is None:
        return None
    return name, _declaration_start(node)


def functions_in_source(
    source: str, path: str, project_root: str | None = None
) -> list[Function]:
    data, tree = parse(source, "lua")
    if tree.root_node.has_error:
        raise ValueError(f"Lua source does not parse: {path}")
    namespace = _module_namespace(path, project_root)
    captures = QueryCursor(_query()).captures(tree.root_node)
    # A binary expression holds one operator, so no decision is captured twice.
    decisions = sorted((node.start_byte, node.end_byte) for node in captures.get("decision", []))
    found: list[Function] = []
    for node in _top_level(captures.get("function", [])):
        entry = _entry(data, node)
        if entry is None:
            continue
        name, header = entry
        found.append(
            Function(
                name=name,
                namespace=namespace,
                complexity=_complexity(node, decisions),
                start_line=start_line(header),
                end_line=end_line(node),
                path=path,
                language="lua",
            )
        )
    return found


class Lua(Language):
    def functions(self, source: str, path: str, project_root: str) -> list[Function]:
        return functions_in_source(source, path, project_root)


class LuaFactory(LanguageFactory):
    def create(self) -> Language:
        return Lua()
