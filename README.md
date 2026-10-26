# crapper

CRAP scores for Clojure, Java, Go, TypeScript, Rust, Python, and Lua. One run detects the language of each source file, applies that language's complexity and coverage rules, and writes the snapshot [uml-viewer](https://github.com/unclebob/uml-viewer) already reads.

The formula is the one from [crap4clj](https://github.com/unclebob/crap4clj), [crap4java](https://github.com/unclebob/crap4java), and [crap4go](https://github.com/unclebob/crap4go):

```text
CRAP = CC² × (1 − coverage)³ + CC
```

`CC` is cyclomatic complexity. `coverage` is the fraction of the function exercised by tests. A score of 1–5 is low risk, 5–30 is worth a look, and 30+ is complex and under-tested.

## Run

```bash
./crapper
```

The first run creates `.venv` and installs the tool. From a project root it walks the tree, skips `test`, `spec`, `vendor`, `node_modules`, and `target`, and writes two results:

- a table on stdout, worst score first
- `.metrics/crap.edn`, atomically replaced after successful analysis; legacy selections with no sources leave it unchanged

```bash
./crapper --no-coverage                  # complexity only; coverage is N/A
./crapper --use-existing-coverage        # read reports already on disk
./crapper src/demo/core.clj src/ui       # these files and trees
./crapper --changed                      # git additions and edits
./crapper combat                         # path fragment, same idea as crap4clj
./crapper --threshold 30                 # exit 2 when the worst score is higher
```

## Source roots and exclusions

Repeat `--source-root` to analyze several trees in one snapshot. Relative roots
are resolved from `--root` (the current directory by default); absolute roots
also work. Overlapping roots produce each source file once. For a Lua business
tree with client, common, and server code:

```bash
crapper --root /path/to/project --no-coverage \
  --source-root client --source-root common --source-root server \
  --exclude packages --exclude server/_trigger
```

`--exclude` may be repeated. Each value is anchored at the project root, even
when selecting another source root, an explicit file or directory, a path
filter, or `--changed`. An exact file excludes that file; an exact directory
excludes all descendants. `server/_trigger` therefore preserves
`client/_trigger` and `server/_trigger_extra`. Exclusions also apply to explicitly
named test files. Existing default skips, including `lua_modules` and
`.luarocks`, still apply during discovery; naming an individual test file
explicitly remains supported.

Exclusion patterns use `/` or `\` as separators on every platform. An optional
leading `./` and trailing separator are ignored. Matching is case-sensitive.
`*` matches zero or more characters within one segment, `?` matches one, and
`[abc]`, `[a-z]`, and `[!abc]` match character sets, ranges, and their complements.
A whole `**` segment matches zero or more path segments. A matching directory
excludes its descendants, including when matched by a glob. For example,
`server/*.lua` excludes immediate Lua files, `**/generated` excludes generated
directories at any depth, and `server/**/private?.lua` matches across nested
directories. Quote globs so the shell passes them unchanged. Use project-relative
paths without `..`; sources explicitly selected outside the project root are
not matched by project-relative exclusions.

These CLI scenarios were tested with backslash exclusions, absolute source
roots, and project paths containing spaces.

## Complete business-tree refresh

Use `--full-tree` to publish every source under the configured roots, with the
same exclusions and default skips described above. Without `--source-root`, the
project root is the business tree. Each declared root must exist and resolve
inside the project root; an existing empty directory is valid. This avoids
publishing an empty snapshot because a root was mistyped or bypassing
project-relative exclusions with an outside root.

```bash
crapper --root /path/to/project --full-tree \
  --source-root client --source-root common --source-root server \
  --exclude packages --exclude server/_trigger

# Complete complexity-only snapshot; coverage and CRAP remain N/A.
crapper --root /path/to/project --full-tree --no-coverage \
  --source-root client --source-root common --source-root server
```

`--full-tree` rejects `--changed`, every positional path or filter, and
`--use-existing-coverage`. These options retain their existing selection behavior
when `--full-tree` is absent. `--no-coverage` explicitly selects a complete
complexity snapshot; it does not claim a fresh covered quality refresh.

Deleting sources removes their entries on the next complete refresh. Deleting
the final source atomically replaces the previous snapshot with `{:entries []}`.
When the tree is empty and no custom command was requested, the CLI explains
that no coverage ran; there are no modules from which to infer a built-in run.
An explicit custom coverage command still passes through the coverage failure
gate before an empty snapshot can be published: it must produce a fresh valid
`coverage/lcov.info` with at least one complete record for an existing source.
A zero-executable-line record (`LF:0`, `LH:0`) is valid, including a source outside
the selected roots. Empty built-in runs do not infer modules or clear artifacts
belonging to previous modules; those artifacts are not read into the empty snapshot.

The CLI prepares the text report before publishing the snapshot. The snapshot
writer renders and encodes all entries, stages a complete file beside the
destination, then atomically replaces it. Source reading, report rendering,
staging, or replacement errors return nonzero and preserve the previous
snapshot. Staging artifacts are cleaned up; a failed publication creates no
incomplete destination. Threshold checks occur after valid publication and
retain exit code 2.

Complete-tree selection, fresh standard Lua reporter coverage across client,
common, and server roots, deletion, empty refreshes, and filesystem failure
scenarios were tested through the CLI with project paths containing spaces.

## Snapshot

`.metrics/crap.edn` has the same shape crap4clj writes:

```clojure
{:entries [{:name "place"
            :namespace "demo.Board"
            :complexity 3
            :coverage 75.0
            :crap 3.1}]}
```

uml-viewer groups entries by `:namespace` and joins each operation on `:name`. `nil` coverage means the function was found but no coverage data applied, and CRAP is `nil` too.

| Language | `:namespace` | `:name` |
| --- | --- | --- |
| Clojure | the `ns` | the `defn` / `defn-` name |
| Java | `package.Class`, or `package.Outer.Inner` | the method name |
| Go | the package import path, or `import/path.Receiver` | the function or method name |
| TypeScript | the dotted module path, or `module.Class` | the function or method name |
| Rust | `crate::module`, or `crate::module::Type` | the function or method name |
| Python | the dotted module path, or `module.Class` | the function or method name |
| Lua | the `require` path (`src/`, `lua/`, `src/lua/` and a trailing `init` dropped) | the name as written: `helper`, `M.foo`, `Account:deposit` |

Rename or move is a new entry. There is no identity matching across runs.

## What each language counts

Clojure follows crap4clj: `if` / `when` and their variants, `and`, `or`, `loop`, `catch`, and each clause of `cond`, `condp`, `case`, `cond->`, `cond->>`, `some->`, and `some->>`. Coverage prefers Cloverage's per-line form counts and falls back to LCOV.

Java follows crap4java: `if`, loops, `catch`, `?:`, each `switch` label, and `&&` / `||`. Constructors and methods inside anonymous classes are omitted. Coverage is JaCoCo's `INSTRUCTION` counter.

Go follows crap4go: `if`, `for`, `range`, each `switch` and `select` clause, and `&&` / `||`. Coverage is `go test -coverprofile`.

TypeScript and TSX use the same structural decisions as Java, including `&&` inside JSX. Top-level functions, class methods, and top-level arrow functions are entries. Nested callbacks stay inside the enclosing function. Coverage is LCOV. A `coverage` script is used as-is. A Vitest project runs `vitest --coverage` (installing `@vitest/coverage-v8` into `node_modules` when it is missing, without editing `package.json`). Other test scripts are wrapped in `c8`.

Rust counts `if`, loops, each `match` arm, `?`, and `&&` / `||`. Test code is skipped: a `tests` module or `tests.rs` file, a file or module opening with `#![cfg(test)]`, and items marked `#[test]`, `#[bench]`, `#[<runner>::test]`, or a `cfg` that holds only in test builds (`cfg(test)`, `cfg(all(test, ...))`; `cfg(any(test, ...))` and `cfg(not(test))` still count). Coverage is LCOV from `cargo llvm-cov` or `cargo tarpaulin`, run in the nearest directory that contains `Cargo.toml`. When neither tool is installed, the run installs `cargo-llvm-cov`.

Python counts `if`, `elif`, `for`, `while`, `except`, each `match` case, a comprehension filter, a conditional expression, and each `and` / `or`. Nested functions stay inside the enclosing function. Coverage is LCOV from `coverage.py`, running pytest when the project uses it and `unittest` otherwise. When the project's interpreter cannot import `coverage`, Python coverage is N/A and nothing is installed; a missing `pytest` is installed into that interpreter.

Lua counts `if`, `elseif`, `while`, `repeat`, both `for` forms, and each `and` / `or`. `goto` does not count. Function declarations and function expressions assigned to a name are entries; nested functions and unassigned closures stay inside the enclosing function. Coverage is LCOV from busted with luacov, cluacov 1.0.0, and `luacov-reporter-lcov`, run with a Lua 5.4 interpreter (`lua5.4`, then `lua`) in the nearest directory that contains `.busted` or a rockspec. LuaCov switches to cluacov's C hook and active-line analysis whenever it can load it, which changes which lines count, so crapper requires that exact version. Nothing is installed automatically. LuaCov records only the selected sources, not spec and support files.

Lua coverage splits the spec files Busted's configuration selects (its `ROOT` and `pattern`) across coverage shards, one Busted process each, and sums their line hits before one LCOV report is written. The shard count defaults to the CPU count and never exceeds the number of spec files. Every shard must pass; one failing shard fails the run like a failed single Busted run. Spec files that share global state or depend on run order need `--lua-shards 1`, which keeps one process:

```bash
./crapper --full-tree --lua-shards 1     # one Busted process
./crapper --full-tree --lua-shards 4     # at most four
```

## Coverage commands

By default a run deletes the previous report for each language it is about to measure and regenerates it:

| Language | Command | Report |
| --- | --- | --- |
| Clojure | `clj -M:cov --lcov` (needs `deps.edn` or `bb.edn`) | `target/coverage/` |
| Java | JaCoCo Maven plugin `0.8.12` in each module with `pom.xml` | `target/site/jacoco/jacoco.xml` |
| Go | `go test ./... -coverprofile=...` | `target/coverage/go/coverage.out` |
| TypeScript | `npm run coverage`, or Vitest `--coverage`, or `npx c8 ... npm test` | `coverage/lcov.info` or `target/coverage/typescript/lcov.info` |
| Rust | `cargo llvm-cov` or `cargo tarpaulin`, per Cargo package | `target/coverage/rust/lcov.info` |
| Python | `coverage run` with pytest or unittest, then `coverage lcov` | `target/coverage/python/lcov.info` |
| Lua | Lua 5.4 runs the Busted and LuaCov entrypoint modules with coverage and the LCOV reporter | `target/coverage/lua/lcov.info` |

For languages other than Lua, a missing tool or a failed test run leaves that language at N/A and still writes the snapshot. Pass `--coverage-command` to replace those defaults with one command of your own.

## Coverage failure gate

A custom `--coverage-command` that exits nonzero or cannot start stops the CLI before analysis and snapshot publication. Built-in Lua coverage also stops if Lua 5.4 is unavailable, cluacov 1.0.0 is not the version that interpreter loads, Busted fails, LuaCov stats are missing, or LCOV conversion fails or produces no report. Diagnostics identify the failed stage, and the CLI exits **1**. A failed Busted run never proceeds to conversion, even when it leaves partial stats.

On these failures, `.metrics/crap.edn` remains byte-for-byte unchanged if it already exists; otherwise no snapshot is created. The preserved snapshot describes an earlier run, so consumers must check the CLI result before treating it as current. `--use-existing-coverage` explicitly bypasses coverage execution and retains the legacy permissive report loader.

Custom commands use a bounded ownership convention: **`coverage/lcov.info` is the required output**; **`coverage/luacov.stats.out` and root `luacov.stats.out` are managed stats**. Choosing `--coverage-command` authorizes removal of exactly these three files before shell execution, including a command that later fails. Parent directories, other stats locations, configs, and unrelated files remain caller-owned. Configure your reporter to write this LCOV path and one of these stats paths; arbitrary paths are never inferred from shell text. Only the fresh required LCOV participates in custom metrics: alternate LCOV, HTML, Go, and JaCoCo reports cannot substitute or contaminate it. Failed commands may leave partial new artifacts; the gate does not restore old coverage.

Built-in Lua enumerates every module's existing `target/coverage/lua[/<module-slug>]/lcov.info` and sibling `luacov.stats.out`, removes all of them before any runner executes, then requires fresh stats and valid LCOV for every module. Cleanup removes files individually and preserves unrelated siblings. Colliding module slugs fail before execution. The standard `luacov-reporter-lcov` reporter handles conversion, with `includeuntestedfiles` set to the selected Lua files for each module so untested selected sources retain zero-hit records without recursively scanning unrelated directories.

Fresh custom and Lua LCOV must contain at least one completed `SF` record, positive unique `DA` line numbers with nonnegative hit counts, and exactly one consistent `LF`/`LH` count per source. Optional DA MD5 checksums and standard function/branch metadata are accepted; counts may appear in either order. A completed `LF:0`/`LH:0` record is valid. Empty, incomplete, unreadable, duplicate-source, or malformed output fails with exit **1** before publication. Every `SF` must resolve to a readable regular file: relative custom paths resolve against `--root`, relative built-in Lua paths against that module's Busted working directory. Absolute paths and forward or backward separators work; source paths are never guessed from the report directory or suffix searches. Custom commands that change directories must emit absolute or project-relative source paths. Built-in Lua coverage uses canonical paths and ignores alternate stale Lua reports even in mixed-language projects.

Custom command strings retain shell syntax and run from `--root` through `/bin/sh`. Paths and arguments containing spaces need quoting. Built-in Lua tools run as argument vectors in the selected interpreter. Command failure, Lua failure stages, and paths containing spaces are covered by real CLI acceptance tests.

A metric threshold failure is a different result: `--threshold` exits **2** after successful analysis has published a valid snapshot. Other language runners retain their existing warning-and-N/A behavior.

## WSL2

Native Windows is not supported: crapper exits with status 1 and points at
this section. Run it inside WSL2 with Ubuntu.

1. In an administrator PowerShell, run `wsl --install` and reboot.
2. Open Ubuntu and clone into the Linux home directory, not `/mnt/c`.
   Windows drives are slow under WSL2 and break symlinks:

   ```bash
   cd ~
   git clone -b lua https://github.com/qinyuanj/crapper.git
   ```

3. Run `crapper/scripts/setup-ubuntu.sh`. It installs Python, Lua 5.4,
   busted, luacov, cluacov 1.0.0, luacov-reporter-lcov, and Babashka, then
   creates `.venv`.
4. Edit in VS Code with the WSL extension (`code .` from Ubuntu).

Under WSL2, crapper warns when the project is under `/mnt/`.

## Development

Prepare and verify an environment for the current checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
```

`scripts/bench.py` times the three refresh modes on a copy of a project and compares each snapshot byte for byte with recorded references:

```bash
.venv/bin/python scripts/bench.py /tmp/copy --reference /tmp/ref --record -- --source-root src
.venv/bin/python scripts/bench.py /tmp/copy --reference /tmp/ref -- --source-root src
```

Local runs without the Lua toolchain can skip integration tests, so check the pytest summary before claiming Lua coverage was validated.
