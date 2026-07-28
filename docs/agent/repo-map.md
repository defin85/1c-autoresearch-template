# Agent Repo Map

This repository has one project runtime and one small repository-local
maintenance boundary.

## Ownership

| Path | Authority |
| --- | --- |
| `templates/research-repo/` | Canonical portable generated repository. |
| `src/one_c_autoresearch/` | Installable canonical runtime; normalized two-way parity with the scaffold is required. |
| `web/workspace/` | Canonical workspace source. |
| `src/one_c_autoresearch/workspace_static/` | Compiled workspace package assets; never hand-edit. |
| `scripts/sync_generated_runtime.py` | Preview and fingerprint deterministic owned-tree replacement. |
| `scripts/build_workspace_template.py` | Build the separate deterministic `dist/research-template.zip`. |
| `scripts/bootstrap/new_research_repo.py` | Copy the validated scaffold into an empty destination. |
| `scripts/checks/test_template.py` | Root/scaffold parity and forbidden-authority gate. |
| `scripts/checks/test_doctor.py` | Fresh-bootstrap smoke gate. |
| `scripts/checks/test_research_repo.py` | Delegate to a target repository's canonical doctor. |
| `tests/test_generated_runtime_sync.py` | Root-only synchronization coverage. |
| `tests/test_template_release.py` | Root-only package and release coverage. |

No other root maintenance script or root-only test is part of the supported
surface. Generated runtime tests are synchronized from the verified canonical
target.

## Active Documentation

Only these files are active root documentation:

- `README.md`
- `AGENTS.md`
- `docs/agent/repo-map.md`
- `docs/agent/verification.md`
- `docs/operator/dispatcher-inspector-rollback.md`
- `docs/operator/extension-source-scope-rollback.md`

OpenSpec archives are retained as non-executable history and excluded from
active-contract scans.

## Change Routing

| Change | Start Here | Verify |
| --- | --- | --- |
| Canonical runtime or CLI | Verified target, then `templates/research-repo/src/` | Target tests, normalized two-way parity, forbidden scans |
| Workspace | `templates/research-repo/web/workspace/` and `web/workspace/` | Frontend tests, typecheck, build, browser acceptance |
| Synchronization | `scripts/sync_generated_runtime.py` | Preview/apply, input drift, stale-file, interruption and convergence tests |
| Bootstrap | `scripts/bootstrap/new_research_repo.py` | Empty/non-empty destination tests and fresh-repository verification |
| Packaging | `pyproject.toml`, `scripts/build_workspace_template.py` | Wheel, sdist, separate archive and clean/upgrade install scans |
| Verification contract | `docs/agent/verification.md` | Ubuntu, Windows, and macOS CI where supported |

Release `0.3.0` deliberately removes the legacy queue, `CUS`, subject-card,
reverse-map, functional-gap, manual-cleanup, old dashboard, and
compatibility-reader surfaces. Do not add adapters. Consumers needing those
interfaces stay on `0.2.0`.
