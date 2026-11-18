# Run the Lua suite on Windows with a native MinGW toolchain

crapper measures Lua coverage where it runs, so on Windows the Lua tests have to run instead of skipping. They need Lua 5.4, LuaRocks, Busted, LuaCov, cluacov 1.0.0 and `luacov-reporter-lcov`. `scripts/setup-ubuntu.sh` provides them on Ubuntu; `scripts/setup-windows.ps1` is its counterpart on native Windows, and installs everything under `%USERPROFILE%\.local` and `%APPDATA%\luarocks` — no administrator rights, no WSL.

LuaRocks' builtin build type invokes `x86_64-w64-mingw32-gcc` for a C rock, and `luafilesystem` and `cluacov` are C rocks. The MSVC Build Tools therefore do not stand in for a compiler: they fail with `Failed compiling object src/lfs.o`, and driving them would mean overriding `CC`, `LD` and `LIBFLAG` with `/OUT:`-style flags that the builtin build type does not do. A winlibs MinGW-w64 build, prepended to the PATH for the run and never persisted, is the smaller decision — and it also compiles the interpreter, since Lua publishes no official Windows binaries.

Two LuaRocks details are recorded because both fail silently. The config file is read from the deploy tree, `%APPDATA%\luarocks\config-5.4.lua`, not from `~/.luarocks`. And rock trees are not on Lua's default search path, so `LUA_PATH` and `LUA_CPATH` are persisted as user environment variables — without them `require('busted.runner')` fails where busted is installed. `luacov-reporter-lcov` stays in the user tree, as on Ubuntu, where it is load-bearing: `/usr/local` is searched before `./?.lua`, and a copy there would shadow the stub that the coverage-gate tests write at `./luacov/reporter/lcov.lua`. Windows repeats the split and deletes any deploy-tree copy so the two platforms have one shape; there `LUA_PATH` starts with `./?.lua`, so the stub is reached first in any case.

## Considered Options

- **WSL2**: runs `setup-ubuntu.sh` unchanged, but needs WSL on the host and turns crapper's Windows-specific coverage — Job Objects, path handling — into Linux tests that never run.
- **CI only**: nothing to install, and a Windows checkout keeps skipping the Lua suite, which reads as a green row. That is the silence this work exists to end.
- **MSVC for Lua, MinGW for the C rocks**: what the first attempt on this machine did, and it works — `vcvars64` plus `cl` builds the interpreter, and MinGW is still needed for `luafilesystem` and `cluacov`. Rejected as the default because it needs two toolchains and an activated developer prompt, where one MinGW unpacks, builds and goes away.

## Consequences

`scripts/setup-windows.ps1` pins Lua 5.4.8, LuaRocks 3.12.0 and GCC 16.2.0, verifies every download against a SHA256 before unpacking it, and ends by loading `lfs`, `luacov.runner`, `busted.runner` and `cluacov.version` from the interpreter it installed. `tests/test_guardrails.py` fails when a rock is added to one setup script and not the other, so the supported installation path cannot drift away from the Linux entry point quietly; `luafilesystem` is the one name allowed there alone, because LuaCov loads it directly and Busted does not pull it in on Windows.
