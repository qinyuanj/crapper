# Record zero coverage for a Lua source LuaCov did not report

crapper hands LuaCov the selected sources as `includeuntestedfiles`, so a source no spec required still gets a zero-hit record in the report. On native Windows that only holds for ASCII names. Lua's `io.open` takes a byte string and the Windows C runtime reads it in the ANSI codepage, so the UTF-8 name crapper writes into the config cannot be opened: on this machine `lfs.dir` listed `src/unused ü.lua` as `75 6E 75 73 65 64 20 A8 B9 2E 6C 75 61` (GBK) while the config held `C3 BC` (UTF-8), and the reporter dropped the file it was told to record. ASCII, single-quoted and `]==]` names are unaffected. The same window opens for any report that misses a selected source for any other reason.

crapper therefore stops treating the coverage tool as the authority on what was measured. After reading a module's fresh LCOV, every selected source with no record in it gets a zero-hit record: each of its physical lines is recorded as zero hits and one form. Functions in that file score 0% coverage and `CRAP = CC² + CC`, where before they had nil coverage and no row at all.

No record means the file was never loaded by the test run — LuaCov records every file it executes — so zero is the accurate reading rather than a guess. Keeping the nil was the alternative, and it hides the file from the loop: the code a user asked to measure is exactly the code the report would omit, so a quality gate would never flag it. The cost runs one way on purpose. If a future reporter drops a file it did measure, crapper reports 0% where the true number was higher: the score is understated, visibly, instead of the row disappearing.

Every physical line is recorded, including blank lines and `end`, rather than LuaCov's executable subset. With every hit zero the percentage is 0 either way, so reproducing the subset would mean running Lua's line scanner from Python, or a second Lua pass, to compute the same zero. The synthesised records feed `percent_for` only; they are never spliced into `lcov.info`, which stays what the coverage tool produced.

## Considered Options

- **Write the config paths in the host ANSI codepage**: only names the codepage can represent would survive, the config would stop being host-independent, and a name outside the codepage would fail the same way it does now.
- **Declare the non-ASCII name a limitation and keep nil**: the row disappears, and the test would have to assert the absence — recording the defect as the contract is worse than the defect.
- **Run LuaCov's line scanner from Python to mark only executable lines**: a second implementation of Lua's scanner, for a value that cannot differ from the one above.
- **Splice the synthesised records into `lcov.info`**: the file would then claim measurements nobody took; it is an artifact of the tool, not of crapper's inference.

## Consequences

Two costs are not visible from the code, so they are recorded here. A synthesised 0% row is a real number to everything downstream: it re-ranks in the CRAP panel, because `CRAP = CC² + CC` with coverage 0 is the highest score a function can carry, and it can fail a `--threshold` run that the same tree passed the day before. And a failure mode that used to be loud becomes quiet-but-wrong: before this change a selected source missing from the report simply had no row, which someone notices, where now it has a plausible zero, which nobody does. Both are the intended direction — a visible understatement over an invisible omission — but a threshold that moves because a file became unreportable should be read against this paragraph, not against the code.

`managed_lua` holds a record for every selected Lua source, so `percent_for` returns a number for every Lua function in the selection and a zero appears where a file was never run. The record is keyed on the resolved path and looked up through the same `_ExactIndex` as the reported ones, so it merges with the report instead of shadowing it: a file the report does carry keeps its measured lines. `tests/test_lua_coverage.py` covers both sides — a source whose name no Windows Lua can open, and a required source whose measured hits must survive the change.
