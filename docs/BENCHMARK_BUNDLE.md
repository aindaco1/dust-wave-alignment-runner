# Private benchmark evidence bundle

`dustwave-align benchmark-bundle` assembles the H1 English/Spanish
word-alignment evidence accepted by the Dust Wave Podcast admin API. It does
not upload anything, run a model, decide whether an adapter passes, or print
transcript text. The Podcast Worker independently validates the closed schema
and re-runs the quality evaluator at import time.

Keep the entire workspace outside Git in an encrypted or otherwise
access-controlled directory. It contains source audio hashes, human-reviewed
transcript words and boundaries, candidate timings, and preview decisions.
Do not put reviewer names, email addresses, credentials, access tokens, raw
provider responses, or unrelated episode material in these files.

## Build command

```sh
uv run dustwave-align benchmark-bundle \
  --manifest /private/alignment-benchmark/workspace.json \
  --input-root /private/alignment-benchmark \
  --output /private/alignment-benchmark/out/submission.json
```

Every referenced input must be a regular non-symlink file beneath
`--input-root`. The output is canonical JSON, installed atomically without
overwriting different bytes, capped at 8 MiB, and created with mode `0600`.
An exact rerun reuses the same bytes. Standard output contains only counts,
the output path, byte length, and SHA-256—never words or local input paths.

The emitted file targets the exact Podcast launch pins:

- repository `aindaco1/dust-wave-alignment-runner`;
- execution revision `e611801d2af82dcdb079444b7e8a7eea4309d1a6`;
- runner digest
  `sha256:8a7cda2702487a1d542d5fb740efe8580ca9edd99f405d722d610536c73a3a11`;
- WhisperX `3.8.6` with the `default-en-es-v1` identity, or stable-ts
  `2.19.1` with the `openai-whisper-base` identity.

The bundler may be run from a later reviewed source revision: the revision in
the evidence identifies the exact adapter execution that produced the primary
and replay results. The Podcast Worker rejects a bundle after its configured
execution pins change, so target drift fails closed.

The runner digest is the SHA-256 of the deterministic Git tar archive for the
execution revision (`git archive --format=tar REVISION`). It gives operators a
reproducible source-artifact identity in addition to the reviewed commit pin.

## Workspace manifest

`workspace.json` has this exact shape:

```json
{
  "schemaVersion": "alignment-benchmark-workspace-v1",
  "submissionId": "opera-bilingual-whisperx-v1",
  "corpusVersion": "opera-rights-cleared-bilingual-v1",
  "adapter": "whisperx",
  "fixtures": [
    {
      "fixtureId": "opera-en-01",
      "requestPath": "fixtures/opera-en-01/request.json",
      "resultPath": "fixtures/opera-en-01/result-primary.json",
      "replayResultPath": "fixtures/opera-en-01/result-replay.json",
      "goldPath": "fixtures/opera-en-01/gold.json",
      "duplicateBillableJobCreated": false
    }
  ],
  "previewReviewsPath": "reviews/previews.json",
  "resourceRunsPath": "runs/resources.json",
  "cleanEnvironmentReproduced": true
}
```

Use a new `submissionId` whenever evidence changes. A repeated ID with
different canonical evidence is deliberately rejected by the Worker.

Each fixture must use a two-to-five-minute rights-cleared source. Its request
is the exact runner schema `2` request and still references audio beneath the
workspace root. The bundler re-hashes that audio, revalidates the reviewed
projection, verifies both result envelopes and their canonical digests, and
binds them to the pinned adapter identity. Primary and replay paths must be
distinct. `duplicateBillableJobCreated` comes from the orchestration/provider
review; semantic stability and maximum timing delta are calculated from the
two result files rather than entered by hand.

## Human-gold file

One gold file is required per fixture:

```json
{
  "schemaVersion": "alignment-benchmark-gold-v1",
  "fixtureId": "opera-en-01",
  "goldWords": [
    {
      "wordId": "word_001",
      "cueId": "cue_001",
      "text": "reviewed-word",
      "startsAtMs": 1040,
      "endsAtMs": 1320,
      "scorable": true
    }
  ]
}
```

`wordId`, `cueId`, and text must exactly match the approved runner request;
gold IDs must be unique and boundaries monotonic and inside the source.
`scorable` is optional and defaults to true. Candidate records are selected
from the verified runner result by gold word ID, so reviewers do not duplicate
candidate data by hand.

The launch gate still requires at least 12 distinct fixtures and 400 scorable
gold words in each language. Those are evaluation thresholds, not structural
bundle requirements: an incomplete or failing bundle can be retained
privately for diagnosis without unlocking word features.

## Preview-review file

```json
{
  "schemaVersion": "alignment-benchmark-previews-v1",
  "reviews": [
    {
      "fixtureId": "opera-en-01",
      "wordId": "word_001",
      "acceptedWithoutClipping": true
    }
  ]
}
```

Every fixture/word pair must be unique and refer to a scorable gold word. The
launch threshold is at least 100 unique reviews with at least 95% accepted
without clipping. Keep reviewer identity in the separately controlled
operational record, not in the import bundle.

## Resource-run file

```json
{
  "schemaVersion": "alignment-benchmark-resources-v1",
  "runs": [
    {
      "language": "en",
      "inputDurationMinutes": 60,
      "wallClockMinutes": 18.2,
      "peakMemoryMb": 4096,
      "peakDiskMb": 8192,
      "runner": "github-actions-ubuntu-24.04-clean"
    },
    {
      "language": "es",
      "inputDurationMinutes": 60,
      "wallClockMinutes": 19.1,
      "peakMemoryMb": 4210,
      "peakDiskMb": 8192,
      "runner": "github-actions-ubuntu-24.04-clean"
    }
  ]
}
```

At least one positive 60-minute run is required per language. Record measured
values from the clean runner; do not estimate or backfill them.

## Final review and import

Before import:

1. Verify rights and human-gold review in the private operational record.
2. Run both the primary and replay alignments with the exact pinned adapter,
   revision, digest, request, audio, and reviewed projection.
3. Confirm provider/orchestration logs show no duplicate billable job for each
   replay and set the per-fixture boolean truthfully.
4. Record measured clean-runner English and Spanish resource runs and set
   `cleanEnvironmentReproduced` only after reproduction is complete.
5. Build the immutable submission, record its displayed SHA-256 privately, and
   inspect only on an approved workstation.
6. In `/admin/podcasts/`, use the Super-admin benchmark import. The server
   stores canonical raw input only in private R2, keeps aggregate evidence in
   D1, and computes pass/fail again. A passing import still requires explicit
   review before any exact alignment revision can be approved.
