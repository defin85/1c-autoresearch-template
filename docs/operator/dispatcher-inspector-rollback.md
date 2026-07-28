# Dispatcher inspector database upgrade and rollback

The dispatcher database is disposable operational state, but its upgrade is
backed up once before inspector columns are added. The service uses SQLite's
online backup API, which includes committed WAL content, and writes
`dispatcher.pre-inspector.sqlite` beside `dispatcher.sqlite` with mode `0600`.
Only the local repository owner may read or restore that file.

The migration is additive and idempotent. Serving an older frontend against the
new backend is supported: it ignores the additional projection fields and
events. Do not run an older backend against the upgraded database because old
positional invocation inserts are incompatible with the added columns.

To roll back the backend, stop the workspace service, preserve the current
database for diagnostics, replace `dispatcher.sqlite` with
`dispatcher.pre-inspector.sqlite`, and restart the service. Operational history
created after the backup is lost; repository sources, generations, and
deliverables are unaffected. Verify ownership and mode `0600` before restart.

## Extension source-scope rollback

Extension decisions live in `research/infobases.toml`. Source, DIF
classification, consolidation, and decision generations remain immutable.

An older static frontend may be served while the current backend and CLI stay
running. The older page may not expose extension review, but backend and CLI
mutation entrypoints still reject an unreviewed discovered UUID. Use
`one-c-autoresearch source-scope --repo-path <repo>` to inspect the blocker.

Treat a full runtime downgrade as read-only recovery:

1. Stop the workspace server and all dispatcher or acquisition workers.
2. Do not run mutation commands from a runtime that predates extension-scope
   enforcement.
3. Preserve `research/infobases.toml`, every active pointer, and every immutable
   generation.
4. Reinstall the enforcing runtime before restarting mutation entrypoints.
5. Run `source-scope` and `status`, resolve every
   `extension_scope_required` blocker, reacquire sources, and resume later
   stages.

Reinstallation does not rewrite prior generations. The tracked decisions and
immutable pointers remain the recovery boundary.
