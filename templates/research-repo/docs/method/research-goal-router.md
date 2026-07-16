# Research Goal Router

`/goal Исследование` processes exactly one deterministic research unit. Read
`project.toml`, inspect durable state, skip manual-cleanup and parallel-adapter
tasks, claim one ready queue task, record source evidence, validate it, update
the durable status, and stop. Use bounded `CUS-*` or `MRQ-*` context commands;
do not scan complete canonical registries.
