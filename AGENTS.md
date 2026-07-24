# Repository guidance

- Keep the core contract and fixture adapter lightweight; heavyweight model
  imports belong inside their adapter modules.
- Never commit episode audio, transcripts, model caches, credentials, or
  benchmark reviewer identities.
- Every adapter must emit one stable candidate record per input word. Missing
  timing requires an explicit reason; never silently interpolate a passing
  boundary.
- Pin release artifacts and Python versions. Run core tests without downloading
  speech models.
- Treat output manifests as immutable evidence: canonical JSON, digest, atomic
  write, and no overwrite with different bytes.
