# crapper

crapper computes CRAP scores for source functions and publishes them as the snapshot uml-viewer reads.

## Language

**Snapshot**:
The `.metrics/crap.edn` file a run publishes: one entry per function with complexity, coverage, and CRAP.
_Avoid_: report, metrics file

**Complete refresh**:
A `--full-tree` run that publishes every source under the configured source roots.
_Avoid_: full scan

**Coverage shard**:
One Busted process running a subset of the selected spec files under LuaCov; shard stats are summed into one coverage result.
_Avoid_: worker, partition
