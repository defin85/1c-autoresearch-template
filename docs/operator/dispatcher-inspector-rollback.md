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
