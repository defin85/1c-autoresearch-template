# Reverse Functional Map

Persistent state for evidence-guided reverse functional mapping.

Use this when the user triggers `/goal Исследование` or asks to continue the reverse engineering run.
The agent must read `state.md`, run `python -m one_c_autoresearch reverse-map claim`, process exactly one workitem, update coverage and evidence, then run doctor checks.

When a row is marked `needs_infobase_data`, record the live check in
`infobase-checks.csv`. Prefer equivalent read-only checks against custom and
vendor infobases when both are configured. Closed checks must point to evidence;
unclosed checks must remain visible through `unresolved.csv` and final open
questions.
