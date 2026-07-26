## ADDED Requirements

### Requirement: Plan deterministic non-overlapping work units
The coordinator SHALL select supported pending work from durable repository state and assign explicit non-overlapping entity sets to deterministic unit identifiers.

#### Scenario: Replan unchanged backlog
- **WHEN** the same backlog, configuration, and source fingerprints are planned again
- **THEN** the coordinator produces the same unit membership and identifiers

### Requirement: Isolate read-only workers
Each worker SHALL receive only its context, exact allowed source files, output schema, and private writable result directory and SHALL have no write access to the research repository.

#### Scenario: Worker attempts a repository mutation
- **WHEN** a worker command attempts to edit the repository, queue, Git history, `CUS-*`, or `MRQ-*`
- **THEN** the run rejects the unit and publishes no decision

### Requirement: Validate structured decisions and traces
The coordinator SHALL validate decision schema, entity assignment, evidence paths and ranges, source fingerprints, task state, trace summary, and mutation-specific semantic gates before application.

#### Scenario: Evidence is outside allowed sources
- **WHEN** a decision cites a file not included in the unit's allowed sources
- **THEN** the decision is rejected and remains unapplied

### Requirement: Apply through a single writer
The system SHALL apply accepted decisions under one repository lock, revalidate shared inputs, update canonical graphs atomically, and run expensive generators and checks once per applied block.

#### Scenario: One unit fails during block application
- **WHEN** a narrow decision is invalid before mutation
- **THEN** that unit remains rejected while independent valid units may proceed

#### Scenario: Shared publication fails
- **WHEN** canonical graph publication or a configured block gate fails
- **THEN** the writer restores the block snapshot and records a rollback event

### Requirement: Resume and compact runs safely
The coordinator SHALL resume compatible unfinished runs, reuse validated completed decisions, terminate registered worker process groups on interruption, and compact only fully applied runs into reproducible audit packages.

#### Scenario: Resume after interruption
- **WHEN** a compatible run is restarted
- **THEN** applied units are skipped, validated decisions are reused, and only unfinished units execute

### Requirement: Keep execution policy configurable
The template SHALL obtain model name, worker ceiling, timeout, source roles, and optional cleanup paths from project configuration, environment, or explicit command arguments rather than customer-specific constants.

#### Scenario: Fresh repository uses defaults
- **WHEN** no execution override is configured
- **THEN** the coordinator uses documented conservative defaults without naming a customer product or assuming a specific Git branch
