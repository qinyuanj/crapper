# crapper

crapper computes CRAP scores for source functions and publishes them as the snapshot uml-viewer reads.

## Language

**Snapshot**:
The `.metrics/crap.edn` file a run publishes: one entry per function with complexity, coverage, and CRAP.
_Avoid_: report, metrics file

**Complete refresh**:
A `--full-tree` run that publishes every source under the configured source roots.
_Avoid_: full scan

**Scored**:
A function with a CRAP score; one whose coverage is N/A is unscored.
_Avoid_: covered (a scored function may have 0% coverage)

**Unmeasured run**:
A `--threshold` run with coverage requested in which at least one function was analyzed and none was scored. It exits 3 after the snapshot is published.
_Avoid_: uncovered run, empty run

**Coverage shard**:
One Busted process running a subset of the selected spec files under LuaCov; shard stats are summed into one coverage result.
_Avoid_: worker, partition
