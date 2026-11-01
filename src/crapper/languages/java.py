"""Java methods and cyclomatic complexity, following crap4java.

Constructors, abstract methods, and methods inside anonymous or local classes
are ignored. Nested member types become their own namespace (`pkg.Outer.Inner`)
so uml-viewer can attach scores to that class. JaCoCo's binary name
(`pkg.Outer$Inner`) is kept for coverage lookup.
"""

import re

from crapper.languages.treesitter import (
    binary_logic,
    child_of_type,
    complexity,
    enclosing_names,
    end_line,
    has_ancestor,
    node_text,
    parse,
    start_line,
)
from crapper.languages.language import Language, LanguageFactory
from crapper.model import Function

_PACKAGE = re.compile(r"(?m)^\s*package\s+([a-zA-Z_][\w.]*)\s*;")
_NESTED_TYPES = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
}
_EXECUTABLES = {"method_declaration", "constructor_declaration", "lambda_expression"}
_DECISIONS = {
    "if_statement",
    "for_statement",
    "enhanced_for_statement",
    "while_statement",
    "do_statement",
    "catch_clause",
    "ternary_expression",
    "switch_label",
}


def _is_decision(node) -> bool:
    return node.type in _DECISIONS or binary_logic(node)


def _skip(node) -> bool:
    return node.type in _NESTED_TYPES or node.type == "class_body"


def _package_name(source: str) -> str | None:
    match = _PACKAGE.search(source)
    return match.group(1) if match else None


def _qualify(package: str | None, names: list[str], nested: str) -> str:
    body = nested.join(names)
    if package:
        return f"{package}.{body}" if body else package
    return body


def functions_in_source(source: str, path: str) -> list[Function]:
    data, tree = parse(source, "java")
    package = _package_name(source)
    found: list[Function] = []

    def visit(node) -> None:
        if node.type == "method_declaration" and not has_ancestor(node, _EXECUTABLES):
            if child_of_type(node, "block") is not None:
                ident = child_of_type(node, "identifier")
                names = enclosing_names(data, node, _NESTED_TYPES, "identifier")
                if ident is not None and names:
                    found.append(
                        Function(
                            name=node_text(data, ident),
                            namespace=_qualify(package, names, "."),
                            complexity=complexity(node, _is_decision, _skip),
                            start_line=start_line(node),
                            end_line=end_line(node),
                            path=path,
                            language="java",
                            jacoco_class=_qualify(package, names, "$"),
                        )
                    )
            return
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return found


class Java(Language):
    def functions(self, source: str, path: str, project_root: str) -> list[Function]:
        return functions_in_source(source, path)


class JavaFactory(LanguageFactory):
    def create(self) -> Language:
        return Java()
