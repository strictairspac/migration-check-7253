# Schema v1
SQLAlchemy metadata creates users/audits/batches/mappings/runs/target_rows. Unique natural names, foreign keys, and target unique(run_id,key_digest) enforce linkage. Successful workers atomically commit accepted target rows; cancelled/failed runs do not persist partial targets. Restore replaces a run's target set under a run row lock and checks the logical backup checksum before commit.

# Schema v2 — corrections and versioned targets
Adds two tables (no column changes to existing tables, so `create_all` upgrades an existing volume safely):

- `run_versions` — one immutable row per published run version (`unique(run_id, version)`, indexed `(run_id, request_id)`). Holds kind initial/correction, the accepted rows and their source row numbers, rejected rows with reasons, replacement reasons versus the previous version, checksum, row hashes, the version's logical backup (run_version-bound, schema version 2), and per-version recovery proofs. A correction request_id is unique per run and stores a digest of its content so retries replay instead of republishing.
- `version_rows` — target rows per version (`unique(run_id, version, key_digest)`), so restoring a version's backup rewrites only that version.

`target_rows` keeps its v1 definition and semantics: it always mirrors the highest published version. Publishing a correction writes the new version row, its version_rows, the run result and the backup in one transaction; failure rolls back and the previous version stays current. Legacy completed runs with no version record are promoted from their persisted target_rows on first read.
