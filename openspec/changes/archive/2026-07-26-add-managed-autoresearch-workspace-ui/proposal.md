## Why

The template exposes a complete but CLI-oriented autoresearch workflow, so an analyst must edit manifests, invoke commands, inspect queues, and open separate generated dashboards manually. A managed browser workspace is needed so a user can configure infobases, choose execution policy and agent systems, customize prompts, run every stage, resolve blockers, and inspect results without entering the CLI.

## What Changes

- Add an optional local web application based on React-admin Open Source, React, TypeScript, and Material UI.
- Add an initial setup wizard for creating or opening a research project, selecting source trees and infobases, configuring access policy, testing connections, and selecting enabled stages.
- Add a project workspace that presents the end-to-end research pipeline as dependency-aware stages with readiness, progress, blockers, actions, and outputs.
- Allow each stage to select an agent system, model, concurrency, timeout, tool policy, fallback, prompt template, and user prompt supplement.
- Add a local Python API and durable run manager that invoke only allowlisted `one_c_autoresearch` operations as background process groups.
- Provide fixed Codex CLI and Claude Code agent-system adapters behind one validated capability contract; do not add a generic command adapter or accept unverified providers.
- Add persisted run state, bounded logs, artifacts, approvals, and an SSE event stream with replay and polling fallback so long operations update only affected UI regions.
- Embed or link the existing clean-comparison, review, and functional-gap dashboards only as stage result views; they do not become the controlling application.
- Preserve research repository files as the source of truth for project and analysis state while keeping UI runtime data and secrets outside concrete repositories.
- Keep the CLI fully supported for automation and recovery; the browser shall not expose arbitrary command execution or arbitrary host-file access.

## Capabilities

### New Capabilities

- `managed-autoresearch-workspace`: Configure, execute, monitor, review, and resume the complete autoresearch process through a browser-only managed workspace.

### Modified Capabilities

None. There are no published base OpenSpec capability contracts in this repository.

## Impact

- Adds an optional frontend application, a local API/runtime package, packaged static assets, and UI-focused tests.
- Adds optional Python web dependencies and frontend build dependencies without changing the core CLI installation path.
- Reads and writes existing `project.toml`, analysis queues, stage artifacts, and verification results through existing package APIs or allowlisted CLI adapters.
- Adds user-scope SQLite operational state and owner-only credential files outside generated research repositories.
- Extends bootstrap, documentation, doctor checks, and fresh-repository verification for the optional managed workspace.
