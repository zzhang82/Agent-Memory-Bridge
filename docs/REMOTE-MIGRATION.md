# Remote Authority Migration Rehearsal

This procedure prepares evidence for moving one existing AMB SQLite/WAL
authority to an official remote-authority deployment. It does not authorize a
cutover, stop a running service, switch clients, or create a release.

The source database contains sensitive memory. Keep snapshots, the rehearsal
directory, manifests, reports, logs, and generated receipt secrets private.
Do not commit or publish them.

## 1. Create a consistent snapshot

On the current authority host, create a consistent backup with the documented
AMB online SQLite backup command. Rehearsal does not require stopping the live
authority; the final cutover backup requires the approved write-stop below.
Copy the resulting standalone
backup file to the isolated rehearsal host or directory. Do not copy a live
`bridge.db` while a `-wal` file exists, and do not manufacture a backup by
copying only one file from an active SQLite/WAL set.

Verify the copied snapshot before rehearsal:

```bash
agent-memory-bridge verify-backup --input /private/path/bridge-snapshot.db --json
```

The rehearsal script requires schema v12, full integrity verification, no
source `-wal` or `-shm` sidecar, and a source path different from the configured
live bridge database.

If full verification reports existing content issues, stop the migration path.
SQLite integrity alone does not satisfy this gate. Preserve an immutable copy
and the issue counts for owner review; do not silently normalize durable memory
or weaken restore validation. A rejected source produces a non-pass JSON report
and nonzero exit status. No candidate/rollback copy is created on that path.

## 2. Rehearse in a new private directory

Choose an absent or empty private directory. The script creates only isolated
copies below it, including candidate and rollback databases, logs, receipt
secrets, manifests, and a report.

```bash
python scripts/rehearse_remote_migration.py \
  --source-snapshot /private/path/bridge-snapshot.db \
  --runtime-dir /private/path/amb-migration-rehearsal
```

The script first validates the supplied snapshot, then uses AMB's backup and
restore APIs to create an isolated candidate. Restore intentionally rotates the
database epoch. It opens the candidate, performs a representative existing
memory browse/recall, and writes one unique marker only to the candidate copy.
It then restores the staged snapshot into a separate rollback copy and repeats
the read/recall check.

Each candidate and rollback pass uses a new empty configuration file and clears
all inherited `AGENT_MEMORY_BRIDGE_*` settings. Its bridge home, database, logs,
and recall-receipt secret remain below the private rehearsal directory. Telemetry,
command-backed/semantic retrieval, classifier activity, watchers, reflex and
consolidation tasks, and the embedding scheduler are disabled for the rehearsal.

## 3. Inspect the evidence

The runtime directory contains:

- `pre-manifest.json`: source schema, epoch, namespaces, signal/run counts, and
  full durable-table row-content hashes.
- `post-manifest.json`: candidate state after the unique rehearsal write.
- `rollback-manifest.json`: isolated rollback state.
- `rehearsal-report.json`: checks, backup/restore receipts, and the expected
  epoch rotations, source-byte digests, and index health before startup, after
  startup, after the marker write, and after rollback.

FTS tables, projections, and embedding sidecars are fingerprinted separately
from durable tables. Durable comparisons allow only the documented
`bridge_metadata.database_epoch` change caused by restore. Equal row counts do
not pass the comparison: each row is content-hashed. The report preserves any
original embedding deficit and accounts for the one additional marker row rather
than treating an incomplete derived index as durable-data loss.

The report always labels E9 as `INCONCLUSIVE`. A synthetic fixture proves the
tooling only. E9 can become release evidence only after an independent reviewer
inspects a real, consistent snapshot of the source database, its source provenance, the isolated
candidate behavior, and the rollback artifacts.

## 4. Cutover remains separate

After a successful rehearsal, retain the snapshot and rollback copy until the
owner separately approves production cutover. At cutover time, stop the old
authority, create a new final consistent backup, repeat the approved migration
procedure, switch clients only after candidate checks, and retain the rollback
path through reconciliation. A lost client response does not prove a write did
not commit.
