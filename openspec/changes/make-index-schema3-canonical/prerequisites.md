## Closed prerequisites

- The completed `add-complete-bsl-analyzer-search-surface` change is archived as `2026-08-06-add-complete-bsl-analyzer-search-surface`.
- The operator-approved cutover set contains only `Presail/sppr-research-ver2`; `/Projects/OneC/sppr-research` is explicitly excluded.
- BSL Analyzer commit `3c97c237200fcfe90547f8960bb93d3a623df4d7` passed its workspace tests, was installed from the release build, and was live-probed by the target workspace with contract 1.3 and the complete capability surface.

## Before-change target manifest

Target: `/run/media/egor/D6B64A72B64A52E3/Projects/OneC/Presail/sppr-research-ver2`.

| Protected surface | Files | SHA-256 aggregate |
| --- | ---: | --- |
| active source generation `cbaf6c602508cd8b4be462d3f24d783971effc79f0d61de9752f654a2b90910b` | 33,983 | `275469f4813c5c6d959ce866c48221f8fab4d10425f0ede545d43d86b20792df` |
| `analysis/indexes/generations` | 17 | `74ca98a8d1e2d1f1c4f055baad977427e4a99379ba5c7c454998c196f5a2a5a5` |
| `analysis/migration-requirements/generations` | 6 | `555d1607c5efd1904a4b76a5016f12518368588fadb11e99e05cd74e164f230a` |
| `analysis/dif-classifications/generations` | 32 | `c916de7cbe012e55b3fe89d9cd5e66737c0b7ad8a5e20eebf6fafe98b34284a0` |
| `research/generations` | 2 | `687ec4c8a4307a9ab264f015e46060132b2fba6411965eddf21f074badec745f` |
| `outputs` | 1 | `339160d47955a35daa536e2504e7cebd9d75b3a93075e0a29b67fb91f7a21b57` |

Tracked indexing configuration fingerprint: `a25b2bbc65f9e655111fc1201dcf3657fa087242968fe4e384a6c14e8f9d7ef8`.

Active-pointer fingerprints:

- `active-consolidation-generation.json`: `fa38c636095054256b77eab8299e2ede57ed78c4889a02aae694eb30c5dcbc3c`
- `active-dif-classification-generation.json`: `e5910b31494006068a985e39f9ea32758f2540099ac222db53a1445623203f06`
- `active-diff-generation.json`: `eb7b791e156e947700cd31922ffe4e8c74053fdf45fa611492df68589fcf59d4`
- `active-generation.json`: `f40eddc2562f94004a653bfe99216a1d8013c5b4aa1feae88642f04056bbe829`
- `active-source-generation.json`: `b3cc8dfa0963c550de25413b4e744a9741b9e8c177c77f815affdc496782f3d3`
- `infobases.toml`: `1baeb8452129dbae7ae2977b0c55293753af2b2a83fc6a14fb8d8bead2605388`

## Synchronization safety verification

The original read-only synchronization preview produced plan fingerprint `sha256:52ebd41d27bdc226e5798eb67e63fab48d3185c5d576b2a9f84f722860b5ac09`, but its deletion set included protected customer generations and outputs. That plan was rejected and never applied.

After confining existing-project synchronization to the explicitly owned runtime files and trees, the replacement preview produced fingerprint `sha256:669a549796690da8a2d10ed045dfcc6d54f06be2e36d1be6f1b5ddffc92bc287`: 11 runtime changes and zero protected paths. Project configuration remains a separate reviewed cutover. The before-change aggregates above must still match after cutover.

## After-change verification

- All six protected-surface file counts and aggregate fingerprints match the before-change table exactly.
- All six active-pointer fingerprints match exactly.
- The only tracked configuration replacement is `research/indexing.toml`; its schema-3 fingerprint is `d838f2b500cbe8f27b6f74bbf332b8c67955f74b91d9f68033132825f32e310d`.
- The operational garbage-collection preview returned zero candidates and zero reclaimable bytes; nothing was deleted.
- Release rollback was rehearsed at the contract boundary: stop active actions, restore a matching application release together with its reviewed schema-3 configuration, validate readiness, and explicitly create only missing indexes. No in-product configuration downgrade, customer-data rollback, or operational-index migration exists.
