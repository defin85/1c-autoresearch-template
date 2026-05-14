# Agent Repo Map

This repository is a reusable template for concrete 1C autoresearch repositories. Keep customer source dumps, generated indexes, and deliverables out of this template.

## Top-Level Map

| Path | Purpose | Edit When |
| --- | --- | --- |
| `AGENTS.md` | Mandatory repository rules for Codex and other agents. | Global agent behavior, safety boundaries, or verification entry points change. |
| `README.md` | Human and agent overview of the template contract. | Public workflow, bootstrap usage, layout, or high-level process changes. |
| `project.example.toml` | Example manifest for concrete research repositories. | Manifest schema or source/MCP/web policy changes. |
| `docs/agent/` | Agent navigation, repo map, and verification runbook. | Codex onboarding or repeatable agent workflows change. |
| `docs/method/` | Reusable 1C analysis methodology. | Evidence levels, queue design, output contract, or analysis method changes. |
| `scripts/bootstrap/` | Creates concrete research repositories from `templates/research-repo/`. | Bootstrap arguments, template copying, or token replacement changes. |
| `scripts/checks/` | Template and generated-repo validation tests. | Validation rules, smoke tests, or doctor expectations change. |
| `scripts/doctor.ps1` | Primary health check for both template and research repositories. | Repository contract, queue validation, manifest policy, or automation output changes. |
| `templates/research-repo/` | Files copied into a concrete research repository. | Concrete project layout, queue workflow, or generated repo instructions change. |
| `examples/` | Small examples of intended command shapes. | User-facing examples need to reflect current bootstrap arguments. |

## Change Routing

| Change Area | Read First | Likely Files | Verification |
| --- | --- | --- | --- |
| Template documentation | `AGENTS.md`, `README.md`, `docs/agent/index.md` | `README.md`, `docs/agent/*`, `docs/method/*` | `scripts/checks/Test-Template.ps1`, `scripts/doctor.ps1` |
| Bootstrap behavior | `README.md`, `scripts/bootstrap/New-1cResearchRepo.ps1` | `scripts/bootstrap/New-1cResearchRepo.ps1`, `templates/research-repo/*` | `scripts/checks/Test-Doctor.ps1` |
| Research repo contract | `templates/research-repo/AGENTS.md`, `templates/research-repo/project.toml` | `templates/research-repo/*`, `scripts/checks/Test-ResearchRepo.ps1`, `scripts/doctor.ps1` | `scripts/checks/Test-ResearchRepo.ps1 -RepoPath <target-repo>`, `<target-repo>\scripts\doctor.ps1` |
| Queue workflow | `docs/method/queue-design.md`, `templates/research-repo/analysis/queue/*` | Queue docs, queue scripts, queue skill | `scripts/checks/Test-Doctor.ps1`, generated repo doctor |
| Manifest/MCP/web policy | `project.example.toml`, `templates/research-repo/project.toml` | Manifests, `scripts/doctor.ps1`, research `AGENTS.md`, optional `.codex/1c-mcp.toml` checks | `scripts/doctor.ps1 -Json -Deep`, targeted doctor smoke tests |
| Agent instructions | `AGENTS.md`, `docs/agent/index.md` | Root and template `AGENTS.md`, `docs/agent/*`, `.agents/skills/*` | `scripts/checks/Test-Template.ps1`, generated repo validation |

## System Of Record

- `AGENTS.md`: mandatory behavior and safety rules.
- `docs/agent/repo-map.md`: where to look and what to edit.
- `docs/agent/verification.md`: canonical verification matrix.
- `docs/method/1c-autoresearch-process.md`: analysis method and feature output contract.
- `docs/method/queue-design.md`: queue semantics and worker rules.
- `templates/research-repo/`: generated research repository contract.

For generated research repositories, `.codex/1c-mcp.toml` is an optional local manifest. When it exists, it must match the MCP server, URL, and service root declared in `project.toml`.

Prefer updating the system-of-record document first, then update short references elsewhere.
