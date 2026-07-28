# Verification Runbook

Release `0.3.0` exposes only the canonical generated-repository runtime.
`0.2.0` is the exact last compatible legacy release.

## Local Checks

```bash
python scripts/checks/test_template.py
python scripts/checks/test_doctor.py
python scripts/checks/test_research_repo.py --repo-path <target-repo>
pytest -q
```

For workspace changes:

```bash
npm --prefix web/workspace ci
npm --prefix web/workspace run typecheck
npm --prefix web/workspace test
npm --prefix web/workspace run build
(cd web/workspace && npx playwright install chromium && npm run test:e2e)
```

Build release artifacts:

```bash
python scripts/build_workspace_template.py
python -m build
```

Final `dist/` must contain only the `0.3.0` wheel, `0.3.0` source
distribution, and separate `research-template.zip`. The wheel and source
distribution must not contain the template archive or maintenance scripts.
The exact last compatible `0.2.0` source baseline is commit
`a7ca2e03d7b7fc4e5245634b574df37a799f55b7`; the release job builds and
fingerprints its wheel before clean-install and upgrade checks.

## Required Evidence

| Gate | Required proof |
| --- | --- |
| Canonical parity | Normalized two-way runtime, CLI, route, schema, test, web-source, compiled-asset, and archive comparison against the verified target; only declared version fields may differ. |
| Removed authorities | Scoped source, active-doc, skill, generated-repository, wheel, sdist, compiled-workspace, archive, and installed-environment scans reject forbidden files, imports, commands, routes, registrations, `CUS-*`, and subject-card markers. OpenSpec history is excluded. |
| Synchronization | Default invocation mutates nothing and emits a sorted content-free add/change/delete plan plus a fingerprint binding reference, manifest, destination, and change set. Apply requires the matching fingerprint and unchanged inputs. |
| Stale files | Unexpected extras fail verification; full-tree replacement removes reviewed stale files; interruption blocks packaging and a new preview/apply converges all owned trees. |
| Safety | Customer generations, binaries, credentials, absolute workstation paths, and customer outputs are rejected before mutation. Non-empty unapproved destinations are rejected. |
| Maintenance | Exactly six repository-local scripts create, synchronize, package, and verify; none are installed or copied into generated repositories. |
| Runtime behavior | Canonical source routing, immutable generations, stable `DIF-*` identity/lineage, semantic/physical closure, bounded unsupported diagnostics, large CSV fields, DIF/MRQ flow, dispatcher, and invocation inspector pass target-first tests. |
| Workspace | Canonical repositories open; legacy-authority repositories are rejected without mutation; CLI works without web extras; stale configuration saves preserve external edits. |
| Release | Build and fingerprint `0.2.0` from the exact pre-cleanup commit in an isolated worktree, clear outputs, then test clean and `0.2.0`-to-`0.3.0` installs both without extras and with workspace extras. |
| Fresh repository | Bootstrap an empty destination, replace only declared tokens, then run canonical doctor, Python tests, web tests/build, exact workflow-catalog comparison, parity, and forbidden scans. |

The upgrade check must prove that removed modules, commands, entry points, and
package data are absent exactly as in a clean install. Missing imports and
commands are the intended breaking result; compatibility adapters are a
failure.

## CI And Final Review

Run the applicable Python gates on Ubuntu, Windows, and macOS. Run frontend and
browser gates on Ubuntu. Before release, also run:

```bash
openspec validate --all --strict
git diff --check
```

Review the final diff and distributions directly. A green helper alone does
not prove that customer data, unrelated changes, stale archive members, or
unexplained parity differences are absent.
