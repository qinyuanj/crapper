"""Go functions and cyclomatic complexity, following crap4go.

Decision points are `if`, `for`, `range`, each `switch` / type-switch case,
each `select` clause, and `&&` / `||`. Coverage later uses Go's statement
profile. The namespace is the package import path; methods are namespaced by
their receiver type (`import/path.Widget`) and named `Run`, which is the join
uml-viewer makes between a class and its operations.
"""

from pathlib import Path

from crapper.languages.treesitter import (
    absolute_path,
    binary_logic,
    child_of_type,
    child_text,
    complexity,
    descendant_text,
    descendants,
    end_line,
    parse,
    start_line,
)
from crapper.languages.language import Language, LanguageFactory
from crapper.model import Function

_DECISIONS = {
    "if_statement",
    "for_statement",
    "expression_case",
    "type_case",
    "communication_case",
    "default_case",
}
_DECLARATIONS = {"function_declaration", "method_declaration"}


def _is_decision(node) -> bool:
    return node.type in _DECISIONS or binary_logic(node)


def _skip(_node) -> bool:
    return False


def _receiver_type(data: bytes, method) -> str | None:
    params = child_of_type(method, "parameter_list")
    return None if params is None else descendant_text(data, params, "type_identifier")


def _name_and_namespace(data: bytes, node, package_ns: str) -> tuple[str, str] | None:
    if node.type == "function_declaration":
        name = child_text(data, node, "identifier")
        return (name, package_ns) if name else None
    name = child_text(data, node, "field_identifier")
    receiver = _receiver_type(data, node)
    if not name or not receiver:
        return None
    return name, f"{package_ns}.{receiver}"


def _go_module(start: Path) -> tuple[str, Path] | None:
    """The `module` line of the nearest go.mod, and its directory."""

    for directory in (start, *start.parents):
        candidate = directory / "go.mod"
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("module "):
                    return line.split(None, 1)[1].strip(), directory
            return None
    return None


def package_namespace(path: str, package_name: str) -> str:
    """Import path of the package, or the package clause when no go.mod exists."""

    directory = Path(path).resolve().parent
    found = _go_module(directory)
    if found is None:
        return package_name
    module, module_dir = found
    relative = directory.relative_to(module_dir).as_posix()
    if relative == ".":
        return module
    return f"{module}/{relative}"


def functions_in_source(
    source: str, path: str, project_root: str | None = None
) -> list[Function]:
    data, tree = parse(source, "go")
    package_name = descendant_text(data, tree.root_node, "package_identifier") or ""
    package_ns = package_namespace(absolute_path(path, project_root), package_name)
    found: list[Function] = []
    for node in descendants(tree.root_node):
        if node.type not in _DECLARATIONS or child_of_type(node, "block") is None:
            continue
        named = _name_and_namespace(data, node, package_ns)
        if named is None:
            continue
        name, namespace = named
        found.append(
            Function(
                name=name,
                namespace=namespace,
                complexity=complexity(node, _is_decision, _skip),
                start_line=start_line(node),
                end_line=end_line(node),
                path=path,
                language="go",
            )
        )
    return found


class Go(Language):
    def functions(self, source: str, path: str, project_root: str) -> list[Function]:
        return functions_in_source(source, path, project_root)


class GoFactory(LanguageFactory):
    def create(self) -> Language:
        return Go()
