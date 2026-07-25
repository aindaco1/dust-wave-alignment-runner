# Dust Wave alignment runner

This repository is the model-processing boundary for Dust Wave Podcast word
alignment. It accepts a bounded, checksummed local request, runs one explicitly
selected adapter, projects adapter tokens back onto stable transcript word IDs,
and atomically writes a canonical, checksummed result manifest.

The public Podcast Worker does not import Python, PyTorch, model code, or raw
episode media. Audio and reviewed transcripts remain private runner inputs.

## Adapters

- `stable-ts` pins the PyPI `stable-ts==2.19.1` artifact and aligns reviewed
  English or Spanish text with `model.align`.
- `whisperx` pins the non-yanked stable `whisperx==3.8.6` release and uses its
  English/Spanish forced-alignment models with interpolation disabled.
- `fixture` exists only for core contract tests. It requires
  `DUSTWAVE_ALLOW_FIXTURE_ADAPTER=1` and marks every timing as `interpolated`,
  so it cannot pass the Podcast word-edit quality gate.

Speaker diarization is deliberately excluded.

## Core development

```sh
uv sync --dev
uv run ruff check .
uv run pytest
```

Core CI does not install model extras or download model weights.

## Private benchmark bundle

The runner also assembles the human-reviewed H1 evidence into the exact
Podcast import contract without uploading it:

```sh
uv run dustwave-align benchmark-bundle \
  --manifest /private/alignment-benchmark/workspace.json \
  --input-root /private/alignment-benchmark \
  --output /private/alignment-benchmark/out/submission.json
```

It reuses the runner request/result validators, derives candidate and
idempotency evidence from exact primary/replay results, accepts separate
human-gold, preview-review, and measured resource files, and writes one
canonical immutable mode-`0600` file. The server remains the source of truth
for pass/fail and re-evaluates the bundle during private Super-admin import.
See [the private benchmark bundle contract](docs/BENCHMARK_BUNDLE.md).

## Run contract

```sh
uv sync --extra whisperx
uv run dustwave-align run \
  --adapter whisperx \
  --request /private/job/request.json \
  --input-root /private/job \
  --output /private/job/result.json \
  --runner-digest sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

Request schema `2` must contain:

- schema, job, alignment-revision, and language identifiers;
- a regular audio file underneath `--input-root`, its SHA-256 and duration;
- canonical reviewed cues and stable word IDs, the approved transcript content
  SHA-256, and a separate SHA-256 of canonical cue/word projection JSON; and
- explicit adapter model/model-version/settings metadata.

The runner rejects traversal, symlink escapes, oversized or changed audio,
duplicate IDs, invalid cue windows, empty lexical words, unknown fields, and
non-canonical transcript hashes. Model references cannot be absolute paths,
URLs, or traversal references. Output contains no raw path, email, token, or
provider credential. The runner verifies the audio digest both before and after
model execution to detect mutation inside a job.

If an output already exists, the runner verifies its canonical digest and its
exact binding to the audio, transcript revision, ordered words, adapter
settings, and runner digest before reusing it. New output is installed with an
atomic no-overwrite operation and mode `0600`, including under concurrent jobs.

## Launch status

This runner contract does not itself claim the H1 gate passes. Launch still
requires 12 rights-cleared two-to-five-minute fixtures and at least 400
human-marked words per language, 100 reviewed cut previews, 60-minute resource
runs, idempotent reruns, and a clean-environment reproduction evaluated by the
Podcast repository's `alignment-quality.ts`.
