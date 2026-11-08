# Coding standards

Judgement calls for review. Mechanical rules live in `tests/test_guardrails.py` and the pre-commit hook, not here.

## Cross-platform paths

crapper runs on Linux and native Windows and must give the same results on both.

- **Paths read from tool output** (LCOV `SF:`, Go profiles, `git status`, config) reuse the existing anchoring rule, `absolute_lcov_sources` in `runners.py`, rather than adding another copy. Its rule: a relative path has `\` replaced by `/` before it is joined to a base, and a drive-letter path (`C:\x`) counts as absolute on every platform. On POSIX, `Path("src\\a.ts")` is a single segment.
- **Files crapper writes** are UTF-8 with LF line endings (`newline="\n"`), whatever the platform.
- **Tests that handle paths** include a backslash case and, where absolute paths matter, a drive-letter case. Tests must mean the same thing on Linux CI and on Windows.

## Tests observe what the user sees

Coverage tests assert the percentage `CoverageBundle.percent_for` returns for a function, or the report table. A rewritten report's text is at most a secondary check.
