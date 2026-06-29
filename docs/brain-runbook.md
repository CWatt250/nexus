# Brain Runbook — gpt-oss:120b

Operational notes for the Phase 39 brain (gpt-oss:120b, local, $0).

## Benchmark: cold-load caveat

First benchmark call after a cold gpt-oss load will read low
(~24-25 t/s). Let the model settle one call then re-run — warm
decode is 27+ t/s. This is expected behavior, not a regression.
