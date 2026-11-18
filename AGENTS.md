## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues on this repo (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Development environment

`scripts/setup-ubuntu.sh` installs Python 3.12, Lua 5.4, Busted, LuaCov, cluacov 1.0.0, Babashka (`bb` 1.13.228), and an editable `.venv`. There is no daemon to start.

`scripts/setup-windows.ps1` is the Windows counterpart for the Lua side: it builds Lua 5.4.8 with a winlibs MinGW-w64 GCC it unpacks into `%USERPROFILE%\.local`, installs LuaRocks 3.12.0, Busted, LuaCov and cluacov 1.0.0, puts `luacov-reporter-lcov` in `%USERPROFILE%\.luarocks`, and persists `PATH`, `LUA_PATH` and `LUA_CPATH` as user environment variables. No administrator prompt and no WSL: `powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1`, once per machine, then a new shell. `docs/adr/0004-windows-lua-toolchain-native-mingw.md` records why that is native MinGW rather than MSVC, WSL2 or CI-only. Without it the Lua tests skip rather than fail, which reads as a green suite.

`luacov-reporter-lcov` is installed into `~/.luarocks`, not `/usr/local`. Lua searches `/usr/local` before `./?.lua` and the user tree after it. Coverage-gate tests stub `./luacov/reporter/lcov.lua` and require that stub to load, and `setup-windows.ps1` keeps the same split by deleting any deploy-tree copy. Ubuntu's `lua` alternative may be Lua 5.1; the tool and the tests use `lua5.4`. On Windows `lua` must be the interpreter the script built, so the script prepends its own `bin` to `PATH` and fails loudly when `Get-Command lua` resolves anywhere else.

Verify with `.venv/bin/python -m ruff check`, `.venv/bin/python -m pytest`, and `./crapper`. On native Windows (PowerShell) create the venv with `python -m venv .venv; & .venv\Scripts\python.exe -m pip install -e ".[dev]"`, then verify with `& .venv\Scripts\python.exe -m ruff check`, `& .venv\Scripts\python.exe -m pytest`, and `crapper.cmd`. CI on `ubuntu-latest` is the Linux check from Windows: push the branch, then `gh run watch --repo qinyuanj/crapper`. A worktree under `.agents/worktrees/` has no `.venv`; run the main checkout's `.venv` interpreter from the worktree root, and pytest's `pythonpath = ["src"]` makes it test the worktree.

CI runs Ruff, guardrails (process boundary, no CR in golden files, README and help agree on exit codes), and the full test suite. For clones that previously enabled the removed pre-commit hook, clear the local setting with `git config --local --unset core.hooksPath`.
