# 1C Autoresearch Template

This repository maintains the canonical `one-c-autoresearch` runtime and the
scaffold used to create reproducible 1C research repositories. Release `0.3.0`
removes the legacy queue, `CUS`, subject-card, reverse-map, functional-gap,
manual-cleanup, old dashboard, and compatibility-reader authorities. `0.2.0`
is the last compatible legacy release.

Existing legacy consumers must either remain on `0.2.0` or migrate their
evidence to the canonical DIF/MRQ workflow before upgrading. The upgrade does
not import, convert, or delete customer repositories or evidence.

## Canonical Runtime

Install the command-line runtime:

```bash
pip install one-c-autoresearch==0.3.0
one-c-autoresearch --help
```

The optional managed workspace is installed separately:

```bash
pip install 'one-c-autoresearch[workspace]==0.3.0'
one-c-autoresearch-workspace --workspace-root /path/to/research-projects
```

The runtime owns canonical source acquisition, immutable source and difference
generations, stable `DIF-*` identities and lineage, DIF classification, MRQ
consolidation and decisions, dispatcher execution, and invocation inspection.
It does not contain template-generation or template-maintenance commands.

## Template Maintenance

The complete repository-local maintenance surface is:

- `scripts/sync_generated_runtime.py`
- `scripts/build_workspace_template.py`
- `scripts/bootstrap/new_research_repo.py`
- `scripts/checks/test_template.py`
- `scripts/checks/test_doctor.py`
- `scripts/checks/test_research_repo.py`

Create a repository only in an empty destination:

```bash
python scripts/bootstrap/new_research_repo.py --help
```

Preview synchronization before applying the fingerprinted plan:

```bash
python scripts/sync_generated_runtime.py --help
```

Build the deterministic template release artifact:

```bash
python scripts/build_workspace_template.py
```

`dist/research-template.zip` is published beside the `0.3.0` wheel and source
distribution. It is not package data inside `one_c_autoresearch`.

## Verification

```bash
python scripts/checks/test_template.py
python scripts/checks/test_doctor.py
python scripts/checks/test_research_repo.py --repo-path <target-repo>
pytest -q
```

The release gate also verifies canonical two-way parity, forbidden-authority
absence, clean and `0.2.0`-to-`0.3.0` installations, frontend tests/build, and
the exact contents of `dist/`. See `docs/agent/verification.md`.

## Active Documentation

The active root documentation surface is exactly:

- `README.md`
- `AGENTS.md`
- `docs/agent/repo-map.md`
- `docs/agent/verification.md`
- `docs/operator/dispatcher-inspector-rollback.md`
- `docs/operator/extension-source-scope-rollback.md`

Historical OpenSpec archives are retained as non-executable design history.
Customer sources, credentials, generated indexes, and deliverables belong only
in concrete research repositories.
