# Read the snapshot in the suite, not with Babashka

The snapshot `.metrics/crap.edn` is a contract with an EDN reader: uml-viewer joins its rows on `:namespace` and `:name`. The suite proved that by shelling out to `bb -e` and `clojure.edn/read-string`, which put Babashka on the test path of every host while nothing in crapper reads EDN with it. The writer is Python (`crapper.metrics`), and the remaining `bb` references in the product are a project's own `bb.edn` and its `--coverage-command` — the user's toolchain, not crapper's. `scripts/setup-ubuntu.sh` installs `bb` for ADR 0005's reason, a Linux development entry point that runs the suite; the supported host has no reason to carry a second language runtime for one assertion.

The suite now reads the snapshot with `tests/edn.py`, a strict reader for exactly the subset `crapper.metrics` writes: maps, vectors, keyword keys, strings, integers, decimals and nil, with EDN's commas-as-whitespace. Everything else raises, which is the point — a snapshot that stops being readable EDN fails the test that reads it instead of passing as text.

The test still asks the same question, and asks more of it: the reader builds the rows, so the assertions are about the values an EDN reader would see rather than about substrings that happen to appear in the file. `tests/test_edn.py` feeds the reader the shapes it must reject, so the oracle cannot quietly accept anything.

## Considered Options

- **Install Babashka on Windows**: the strongest oracle, but a second language runtime and a new pinned download on the only supported host, for one assertion — against ADR 0004's preference for a minimal native toolchain.
- **Use `bb` when present and skip otherwise**: a skip is a green row that measured nothing, and #7 exists because the Windows row has to mean something.
- **Remove `bb` from `scripts/setup-ubuntu.sh`**: ADR 0005 keeps that script as an environment for restoring Linux, and Babashka is part of a working Clojure/Lua box there; the suite no longer requiring it is not a reason to shrink what that entry point provides.
- **Depend on `edn-format` from PyPI**: a new dependency, and a network install in CI, for a sixty-line reader.
- **Compare the snapshot with a byte-for-byte golden**: it pins every row of a snapshot spanning seven languages and has to be regenerated whenever an unrelated parser moves; the Lua fixture keeps its goldens because that is what they are for.

## Consequences

`tests/test_guardrails.py` now records the toolchain both setup scripts must provide and the tools only the Linux entry point provides, so `bb`'s absence on the supported host is a declared decision instead of an accident, and a tool that changes sides has to be re-declared.
