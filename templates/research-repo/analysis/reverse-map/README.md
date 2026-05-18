# Reverse Functional Map

Persistent state for evidence-guided reverse functional mapping.

Use this when the user triggers `/goal Исследование` or asks to continue the reverse engineering run.
The agent must read `state.md`, run `python -m one_c_autoresearch reverse-map claim`, process exactly one workitem, update coverage and evidence, then run doctor checks.
