# Private benchmark review packet

The H1 launch gate needs human-reviewed word boundaries and cut previews; those
decisions cannot be inferred from the alignment model. The review-packet tools
remove the surrounding clerical work without weakening that boundary. They
select a deterministic bilingual sample, bind it to exact private inputs, and
materialize the accepted bundle files from one completed review export.

Keep the workspace, packet, completion, and materialized files outside Git in
an encrypted or otherwise access-controlled directory. They contain transcript
words and timing evidence. Do not include reviewer identity, email, tokens,
credentials, or unrelated episode material.

## Build the packet

Create a private workspace manifest:

```json
{
  "schemaVersion": "alignment-benchmark-review-workspace-v1",
  "adapter": "whisperx",
  "fixtures": [
    {
      "fixtureId": "opera-en-01",
      "requestPath": "fixtures/opera-en-01/request.json",
      "resultPath": "fixtures/opera-en-01/result-primary.json"
    }
  ]
}
```

Every request and result uses the existing pinned runner contract. Each source
must be a two-to-five-minute regular non-symlink file below `--input-root`; its
audio, transcript projection, adapter, runner, and result digests are rechecked
before a word can enter the packet.

```sh
uv run dustwave-align benchmark-review-packet \
  --manifest /private/alignment-benchmark/review-workspace.json \
  --input-root /private/alignment-benchmark \
  --output /private/alignment-benchmark/review/packet.json
```

The immutable mode-`0600` packet contains no local paths. It selects up to 500
valid aligned candidates per language, distributed as evenly as possible
across fixtures and across each fixture timeline. Sixty per language are also
marked for cut-preview review. Invalid, missing, interpolated, or explicitly
unaligned candidates are not silently promoted into the review sample.

The content-free command response reports exact counts and English/Spanish
shortfalls. A shortfall is evidence that the private corpus is not yet large
enough; it is not filled with duplicate or synthetic words.

## Completion contract

The reviewer listens to the referenced private source, adjusts the suggested
candidate boundaries, and exports exactly one decision for every packet word:

```json
{
  "schemaVersion": "alignment-benchmark-review-completion-v1",
  "packetSha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "reviews": [
    {
      "fixtureId": "opera-en-01",
      "wordId": "word_001",
      "startsAtMs": 1040,
      "endsAtMs": 1320,
      "scorable": true,
      "acceptedWithoutClipping": true
    }
  ]
}
```

`acceptedWithoutClipping` is a boolean only for a scorable word selected for a
preview review; it is `null` otherwise. The completion digest prevents a stale
review from being applied to a newer packet. Every fixture/word pair is exact,
unique, bounded by the source duration, and monotonic in projection order.

## Materialize bundle inputs

```sh
uv run dustwave-align benchmark-review-materialize \
  --packet /private/alignment-benchmark/review/packet.json \
  --completion /private/alignment-benchmark/review/completion.json \
  --input-root /private/alignment-benchmark \
  --output-root review/materialized
```

The materializer validates every decision before writing. It then creates:

- one canonical `gold/<fixtureId>.json` file per fixture;
- one canonical `reviews/previews.json` file;
- one checksummed `materialization.json` path/digest index.

All outputs are immutable, atomically installed, mode `0600`, and safe to
recreate idempotently. Standard output contains only paths, hashes, and counts.
Reference only `review/materialized/materialization.json` from a
`alignment-benchmark-workspace-v2` manifest; the bundler resolves and verifies
the exact fixture paths and digests without manual copying. The existing
`benchmark-bundle` command remains the final source of the Worker submission
and independently revalidates every generated gold word against the original
reviewed projection and primary/replay result.

This automation does not assert rights, mark boundaries, claim clean-runner
reproduction, or decide H1 pass/fail. Those facts remain explicit private human
or measured evidence and the Podcast Worker evaluates the final bundle again.
