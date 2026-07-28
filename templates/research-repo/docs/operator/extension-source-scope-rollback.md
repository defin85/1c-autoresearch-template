# Extension source-scope rollback

Extension decisions live in `research/infobases.toml`. Source, DIF
classification, consolidation, and decision generations remain immutable.

## Frontend-only rollback

An older static frontend may be served while the current backend and CLI stay
running. The older page may not expose extension review, but backend and CLI
mutation entrypoints still reject an unreviewed discovered UUID. Use
`one-c-autoresearch source-scope --repo-path <repo>` to inspect the blocker.

## Full-runtime downgrade

Treat a full downgrade as read-only recovery:

1. Stop the workspace server and all dispatcher or acquisition workers.
2. Do not run `apply`, `run-next`, `run-until-blocked`, source acquisition, or
   any other mutation command from a runtime that predates extension-scope
   enforcement.
3. Preserve `research/infobases.toml`, every active pointer, and every immutable
   generation. Read or copy those files only.
4. Reinstall the enforcing runtime before restarting mutation entrypoints.
5. Run `source-scope` and `status`. Resolve every `extension_scope_required`
   blocker, reacquire sources, and then resume later stages.

Reinstallation does not migrate or rewrite prior generations. The tracked
decisions and immutable pointers remain the recovery boundary.
