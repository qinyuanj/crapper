# Pin cluacov 1.0.0 for Lua coverage

LuaCov silently switches to cluacov's C line hook and `deepactivelines` whenever the interpreter can load it. The active-line analysis changes which lines count, including the zero-hit lines of `includeuntestedfiles`, so the same project measured with and without cluacov publishes different CRAP scores. crapper therefore requires exactly cluacov 1.0.0 on the Lua 5.4 interpreter it selects and stops before running Busted otherwise. mutator collects its own Lua coverage and calls the same `require_lua_cluacov` check before running Busted.

## Considered Options

- **Pure-Lua LuaCov only**: reproducible only if nothing ever installs cluacov, which crapper cannot prevent.
- **cluacov optional**: faster where present, but two coverage baselines for one project.

## Consequences

Snapshots recorded without cluacov change once on the first refresh with it; they are not migrated.
