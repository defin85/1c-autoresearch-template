# Minimal Example

This example documents the intended command shape for a concrete 1C research repo.

```bash
python -m one_c_autoresearch new-repo \
  --target-path ./example_research \
  --project-id example \
  --product "1C Document Management" \
  --baseline-version "2.1" \
  --target-version "2.1" \
  --next-vendor-version "3.0" \
  --vendor-baseline ./vendor \
  --target-cf ./customer/cf \
  --target-cfe ./customer/cfe \
  --next-vendor ./vendor30 \
  --rlm-vendor-baseline vendor \
  --rlm-target-cf customer_cf \
  --rlm-target-cfe customer_cfe \
  --rlm-next-vendor vendor30
```
