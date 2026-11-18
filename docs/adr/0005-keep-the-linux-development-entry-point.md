# Keep the Linux development entry point, unsupported

Native Windows x64 is the only host the quality loop delivers on; Linux x64 left with the same decision that removed macOS (qinyuanj/home-uml-viewer#5, ADR 0002 there). crapper's prose follows: the run instructions, the host list and the coding standards name one host, and no Linux or WSL2 install reads as supported.

`scripts/setup-ubuntu.sh` stays anyway. It is the only way to run this suite on Linux, which makes an Ubuntu box usable as crapper's development entry point rather than as a host. It installs the same Lua toolchain the supported host needs — Lua 5.4, Busted, LuaCov, cluacov 1.0.0, `luacov-reporter-lcov` — plus Babashka, so the work of restoring Linux support starts from a working environment instead of a reconstruction. It is not an installation path for anything the loop delivers.

The POSIX branches in `src/crapper/platform.py` and the `/mnt/` warning in `src/crapper/cli.py` stay for the same reason. They are unreachable from the supported host and reachable from that entry point, and they are what Linux support would run on. Deleting them is a separate decision, in the way the namespace isolation path in `crates/mutator` is kept rather than tidied away with the host that used it.

Keeping them has a price, and it is paid here rather than discovered later: nothing in CI runs them. crapper's CI is `windows-latest` only, so the POSIX branches in `platform.py`, the `sh` spelling in `tests/host_shell.py` (used by the coverage-command, process-tree and stamp fixtures) and the `/mnt/` warning spec in `tests/test_platform.py` have no lane anywhere. A change to any of them can break it without a red build. That is the cost of naming one host, taken deliberately; the same sentence appears in `docs/mutation-linux.md` in mutator for the namespace views.

What does not change is the check between the two setup scripts. `tests/test_guardrails.py` still compares every rock `setup-ubuntu.sh` installs with what `setup-windows.ps1` pins, in both directions and with the same version: a rock on one side and not the other is a host that silently skips the Lua tests needing it, and Windows, having no second installation path, has nothing to fall back on. The framing moved to the Windows script as the supported path; the drift check did not move with it.

Restoring Linux would need the kept entry point to run somewhere a row can be taken and the declarations to name Linux again. Until then the support list stays at one host.
