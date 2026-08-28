# Migration & Rollback (ASR-4)

Status: current as of schema v7 (2026-08-28)

## Schema versions

| Version | Phase | Content |
|---:|---|---|
| 1 | P1 | `jobs` — job model, idempotency key, lease columns, retry/terminal state |
| 2 | P1 | `app_settings` — first-setup, report directory, LLM configuration |
| 3 | P2 | `job_artifacts` — per-stage artifacts (transcript, chunks, report) |
| 4 | P2 | jobs columns for ASR identity/建议; settings LLM base_url/version fields |
| 5 | P6 (ASR-2) | model install/download/active state (frozen contract tables) |
| 6 | P6 (ASR-2) | replaced the v5 single model table with three normalized tables |
| 7 | P6 (ASR-3) | persistent ASR provider identity/routing recommendation fields |

Migrations live in `src/evoblue_video_mcp/storage/migrations.py` as ordered
`(version, apply)` pairs with frozen SQL — never the live ORM. `init_db` applies
everything newer than the recorded `schema_migrations` version, so startup is
idempotent and a fresh install converges on the same schema as an upgrade.

## Rules

1. **Forward-only.** There is no automated downgrade path. A schema change is a
   new versioned migration; editing an already-released migration is a defect.
2. **Backup before upgrade (P7 installer requirement).** The installer copies
   `evoblue.db` to `evoblue.db.bak-<old-version>` before running a new version.
   Rollback = restore the backup alongside the previous bundle. Data written by
   the newer version after the upgrade is not preserved.
3. **Markdown is the recovery asset.** Completed reports exist as Markdown files
   on disk independently of SQLite (`docs/MARKDOWN_SCHEMA.md`); a catastrophic
   database loss is recoverable by the index rebuild (P3, `docs/PRD.md`).
4. **ASR model state is disposable by design.** Installed models, downloads and
   provider registrations live in separate tables plus the models directory;
   losing them costs a re-download, never a job or a report. `waiting_for_model`
   jobs reconcile themselves once the model is reinstalled (ASR-3).

## Rollback drills (verify before each release)

- upgrade v(N-1) → v(N) on a populated database, then downgrade by restoring the
  pre-upgrade backup: engine boots, history intact;
- kill the engine mid-migration: startup re-runs `init_db` idempotently and
  completes the schema;
- completed Markdown reports remain readable and unchanged across both drills.
