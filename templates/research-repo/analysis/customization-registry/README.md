# Customization Registry

Canonical atomic `CUS-*` records live in `customization-items.jsonl`. Evidence,
links, and lineage are separate appendable relations. Initialize or rebuild the
layer with `python -m one_c_autoresearch customization-registry bootstrap`.

Generated views are disposable and must be rebuilt from the JSONL records.
