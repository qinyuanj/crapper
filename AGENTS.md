## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues on this repo (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Development environment

`scripts/setup-ubuntu.sh` installs Python 3.12, Lua 5.4, Busted, LuaCov, cluacov 1.0.0, Babashka (`bb` 1.13.228), and an editable `.venv`. There is no daemon to start.

`luacov-reporter-lcov` is installed into `~/.luarocks`, not `/usr/local`. Lua searches `/usr/local` before `./?.lua` and the user tree after it. Coverage-gate tests stub `./luacov/reporter/lcov.lua` and require that stub to load. Ubuntu's `lua` alternative may be Lua 5.1; the tool and the tests use `lua5.4`.

Verify with `.venv/bin/python -m pytest` and `./crapper`. On native Windows the setup is README §Native Windows, and the same checks are `.\.venv\Scripts\python -m pytest` and `crapper.cmd`. A worktree under `.agents/worktrees/` has no `.venv`; run the main checkout's `.venv` interpreter from the worktree root, and pytest's `pythonpath = ["src"]` makes it test the worktree.

Enable the versioned pre-commit hook once per clone with `git config core.hooksPath .githooks`; it runs `tests/test_guardrails.py` (process boundary, no CR in golden files, README and help agree on exit codes).
