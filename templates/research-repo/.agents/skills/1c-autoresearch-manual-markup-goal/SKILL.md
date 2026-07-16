---
name: 1c-autoresearch-manual-markup-goal
description: Classify and physically clean one `/goal Ручная разметка` batch.
---

# Manual Cleanup Goal

Read `AGENTS.md` and `docs/method/manual-markup-goal.md`. Use structured
read-only workers and one writer. Resolve every `manual_review` before block
publication and run expensive rebuilds once per block.
