"""Per-language function extraction."""

from crapper.languages.clojure import ClojureFactory
from crapper.languages.golang import GoFactory
from crapper.languages.java import JavaFactory
from crapper.languages.language import LanguageFactory, UnknownFactory
from crapper.languages.lua import LuaFactory
from crapper.languages.python import PythonFactory
from crapper.languages.rust import RustFactory
from crapper.languages.typescript import TypeScriptFactory
from crapper.model import Function

_FACTORIES: dict[str, LanguageFactory] = {
    "clojure": ClojureFactory(),
    "java": JavaFactory(),
    "go": GoFactory(),
    "typescript": TypeScriptFactory(),
    "python": PythonFactory(),
    "rust": RustFactory(),
    "lua": LuaFactory(),
}
_UNKNOWN = UnknownFactory()


def functions_in_file(
    language: str, source: str, path: str, project_root: str
) -> list[Function]:
    """Functions in one file. `path` is project-relative; either separator names the same module."""

    factory = _FACTORIES.get(language, _UNKNOWN)
    return factory.create().functions(source, path.replace("\\", "/"), project_root)
