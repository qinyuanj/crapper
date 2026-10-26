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

Verify with `.venv/bin/python -m pytest` and `./crapper`.
