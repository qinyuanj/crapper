# Keep the Python package as crapper's shell

Rewriting crapper in Rust or Go is permitted, but analysis takes about 4 s of a 65 s monopoly refresh, and uml-viewer installs crapper as a Python package that monopoly's preflight checks by import path, while mutator imports `require_lua_cluacov`. crapper stays a Python package and CLI. Python optimizations come first. If later measurement shows a remaining hotspot, only the Lua path is moved to native code, shipped as a Python extension; the other six languages stay in Python.
