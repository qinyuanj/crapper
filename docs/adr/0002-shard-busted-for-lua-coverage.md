# Shard Busted for Lua coverage

On monopoly (547 spec files, 567 sources), Busted under LuaCov and cluacov takes about 92% of a 65 s complete refresh, so faster parsing alone cannot move end-to-end time. crapper therefore runs every selected spec file split across `cpu_count` Busted processes by default, each writing its own LuaCov stats, and sums line hits before producing one LCOV. Summing is order-independent, so the snapshot is byte-identical to a single-process run as long as spec files share no state. `--lua-shards 1` restores the single process. A failure in any shard fails coverage as a whole.

## Considered Options

- **Single process (status quo)**: no assumption about spec independence, but end-to-end time stays dominated by one Lua process.
- **Opt-in sharding**: safe default, but the common path would keep the slow run.
- **Run only affected specs**: rejected; it gives each project several coverage baselines, which ADR-0001 exists to prevent.

## Consequences

A project whose spec files depend on shared global state or run order may see different coverage or failures under sharding; it uses the single-process switch. Adoption is gated on a byte-identical monopoly snapshot.
