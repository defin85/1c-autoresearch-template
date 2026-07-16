# Manual Cleanup Goal

`/goal Ручная разметка` processes one `manual_markup` batch. Initialize only
when no durable queue exists. Workers read normalized source context and return
structured decisions; one writer applies accepted decisions. Use the model from
`CODEX_MANUAL_MODEL`, inspect unresolved rows directly, and never publish a
batch while `manual_review` remains. Rebuild expensive indexes once per block.
