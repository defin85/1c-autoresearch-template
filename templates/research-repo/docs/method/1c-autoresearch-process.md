# 1C Autoresearch Process

Use static source evidence first:

- vendor baseline from `project.toml`;
- customer `cf`;
- customer `cfe`;
- cached indexes under `analysis/cache/`.

Use live infobase evidence only when a task explicitly requests it.

## Feature Pack Contract

Each functional feature should have:

- `brief.md`;
- `findings.md`;
- `evidence.csv`;
- `open-questions.md`;
- `review.md`;
- optional `artifacts/`.

## Required Coverage

For deep dives, check:

- metadata;
- BSL;
- forms;
- validations;
- lifecycle;
- roles and rights;
- background jobs;
- business processes and tasks;
- extension overrides;
- vendor delta.
