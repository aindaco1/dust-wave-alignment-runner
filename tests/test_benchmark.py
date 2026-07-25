from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from dustwave_alignment_runner.benchmark import (
    BENCHMARK_ADAPTERS,
    BENCHMARK_RUNNER_REPOSITORY,
    BENCHMARK_RUNNER_REVISION,
    build_benchmark_submission,
)
from dustwave_alignment_runner.contract import (
    ContractError,
    canonical_json_bytes,
    sha256_hex,
)


def test_builds_one_private_canonical_submission_from_bound_artifacts(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    output = tmp_path / "private" / "submission.json"

    first = build_benchmark_submission(
        paths["workspace"],
        tmp_path,
        output,
    )
    second = build_benchmark_submission(
        paths["workspace"],
        tmp_path,
        output,
    )

    assert first == second
    assert first == {
        "written": str(output),
        "submissionSha256": sha256_hex(output.read_bytes()),
        "submissionBytes": len(output.read_bytes()),
        "fixtureCount": 1,
        "englishFixtureCount": 1,
        "spanishFixtureCount": 0,
        "englishGoldWordCount": 2,
        "spanishGoldWordCount": 0,
        "previewReviewCount": 1,
        "resourceRunCount": 2,
        "idempotencyCheckCount": 1,
        "cleanEnvironmentReproduced": True,
    }
    assert os.stat(output).st_mode & 0o777 == 0o600
    submission = json.loads(output.read_text(encoding="utf-8"))
    assert submission["schemaVersion"] == "alignment-benchmark-submission-v1"
    assert submission["runner"] == {
        "repository": BENCHMARK_RUNNER_REPOSITORY,
        "revision": BENCHMARK_RUNNER_REVISION,
    }
    assert submission["benchmark"]["adapter"] == BENCHMARK_ADAPTERS["whisperx"]
    fixture = submission["benchmark"]["fixtures"][0]
    assert fixture == {
        "fixtureId": "fixture_en_01",
        "language": "en",
        "audioDurationMs": 120_000,
        "sourceAudioSha256": sha256_hex(b"private owned audio"),
        "transcriptRevisionSha256": "a" * 64,
        "resultManifestSha256": paths["primary_digest"],
        "goldWords": [
            {
                "wordId": "word_1",
                "cueId": "cue_1",
                "text": "hello",
                "startsAtMs": 100,
                "endsAtMs": 400,
            },
            {
                "wordId": "word_2",
                "cueId": "cue_1",
                "text": "world",
                "startsAtMs": 500,
                "endsAtMs": 900,
            },
        ],
        "candidateWords": [
            {
                "wordId": "word_1",
                "cueId": "cue_1",
                "text": "hello",
                "startsAtMs": 101,
                "endsAtMs": 399,
                "confidence": 0.98,
                "timingOrigin": "forced_alignment",
                "unalignedReason": None,
            },
            {
                "wordId": "word_2",
                "cueId": "cue_1",
                "text": "world",
                "startsAtMs": 501,
                "endsAtMs": 899,
                "confidence": 0.97,
                "timingOrigin": "forced_alignment",
                "unalignedReason": None,
            },
        ],
    }
    assert submission["benchmark"]["idempotencyChecks"] == [
        {
            "fixtureId": "fixture_en_01",
            "semanticOutputStable": True,
            "maximumTimingDeltaMs": 1,
            "duplicateBillableJobCreated": False,
        }
    ]
    assert output.read_bytes() == canonical_json_bytes(submission) + b"\n"
    assert "requestPath" not in output.read_text(encoding="utf-8")
    assert "goldPath" not in output.read_text(encoding="utf-8")


def test_rejects_tampered_result_identity_before_writing(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    result = json.loads(paths["primary"].read_text(encoding="utf-8"))
    result["manifest"]["adapter"]["version"] = "tampered"
    result["manifestSha256"] = sha256_hex(canonical_json_bytes(result["manifest"]))
    _write_json(paths["primary"], result)
    output = tmp_path / "submission.json"

    with pytest.raises(ContractError, match="adapter identity is not pinned"):
        build_benchmark_submission(paths["workspace"], tmp_path, output)

    assert not output.exists()


def test_rejects_gold_that_differs_from_reviewed_projection(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    gold = json.loads(paths["gold"].read_text(encoding="utf-8"))
    gold["goldWords"][0]["text"] = "changed"
    _write_json(paths["gold"], gold)

    with pytest.raises(
        ContractError,
        match="does not match the reviewed projection",
    ):
        build_benchmark_submission(
            paths["workspace"],
            tmp_path,
            tmp_path / "submission.json",
        )


def test_rejects_workspace_path_escape_and_immutable_output_change(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    output = tmp_path / "submission.json"
    build_benchmark_submission(paths["workspace"], tmp_path, output)
    output.write_text("different", encoding="utf-8")

    with pytest.raises(ContractError, match="different immutable bytes"):
        build_benchmark_submission(paths["workspace"], tmp_path, output)

    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    _write_json(
        outside,
        {"schemaVersion": "alignment-benchmark-previews-v1", "reviews": []},
    )
    workspace = json.loads(paths["workspace"].read_text(encoding="utf-8"))
    workspace["previewReviewsPath"] = f"../{outside.name}"
    _write_json(paths["workspace"], workspace)
    escaped_output = tmp_path / "escaped-submission.json"

    with pytest.raises(ContractError, match="escapes input-root"):
        build_benchmark_submission(paths["workspace"], tmp_path, escaped_output)

    assert not escaped_output.exists()
    outside.unlink()


def _workspace(root: Path) -> dict[str, Any]:
    audio = root / "audio.bin"
    audio.write_bytes(b"private owned audio")
    cues = [
        {
            "cueId": "cue_1",
            "startsAtMs": 0,
            "endsAtMs": 1_000,
            "words": [
                {"wordId": "word_1", "text": "hello"},
                {"wordId": "word_2", "text": "world"},
            ],
        }
    ]
    request = {
        "schemaVersion": "2",
        "jobId": "job_en_01",
        "alignmentRevisionId": "alignment_en_01",
        "language": "en",
        "audio": {
            "path": "audio.bin",
            "sha256": sha256_hex(audio.read_bytes()),
            "durationMs": 120_000,
        },
        "transcript": {
            "contentSha256": "a" * 64,
            "projectionSha256": sha256_hex(canonical_json_bytes(cues)),
            "cues": cues,
        },
        "adapter": {
            "name": "whisperx",
            "model": "default",
            "modelVersion": "default-en-es-v1",
            "settingsVersion": "whisperx-align-v1",
        },
    }
    request_path = root / "request.json"
    _write_json(request_path, request)
    primary_candidates = [
        _candidate("word_1", "hello", 101, 399, 0.98),
        _candidate("word_2", "world", 501, 899, 0.97),
    ]
    replay_candidates = [
        _candidate("word_1", "hello", 100, 400, 0.97),
        _candidate("word_2", "world", 500, 900, 0.96),
    ]
    primary = root / "primary.json"
    replay = root / "replay.json"
    primary_digest = _write_result(primary, request, primary_candidates)
    _write_result(replay, request, replay_candidates)
    gold = root / "gold.json"
    _write_json(
        gold,
        {
            "schemaVersion": "alignment-benchmark-gold-v1",
            "fixtureId": "fixture_en_01",
            "goldWords": [
                {
                    "wordId": "word_1",
                    "cueId": "cue_1",
                    "text": "hello",
                    "startsAtMs": 100,
                    "endsAtMs": 400,
                },
                {
                    "wordId": "word_2",
                    "cueId": "cue_1",
                    "text": "world",
                    "startsAtMs": 500,
                    "endsAtMs": 900,
                },
            ],
        },
    )
    previews = root / "previews.json"
    _write_json(
        previews,
        {
            "schemaVersion": "alignment-benchmark-previews-v1",
            "reviews": [
                {
                    "fixtureId": "fixture_en_01",
                    "wordId": "word_1",
                    "acceptedWithoutClipping": True,
                }
            ],
        },
    )
    resources = root / "resources.json"
    _write_json(
        resources,
        {
            "schemaVersion": "alignment-benchmark-resources-v1",
            "runs": [
                {
                    "language": "en",
                    "inputDurationMinutes": 60,
                    "wallClockMinutes": 12.5,
                    "peakMemoryMb": 1_024,
                    "peakDiskMb": 2_048,
                    "runner": "github-actions-ubuntu-24.04",
                },
                {
                    "language": "es",
                    "inputDurationMinutes": 60,
                    "wallClockMinutes": 13.5,
                    "peakMemoryMb": 1_100,
                    "peakDiskMb": 2_100,
                    "runner": "github-actions-ubuntu-24.04",
                },
            ],
        },
    )
    workspace = root / "workspace.json"
    _write_json(
        workspace,
        {
            "schemaVersion": "alignment-benchmark-workspace-v1",
            "submissionId": "benchmark_submission_01",
            "corpusVersion": "rights-cleared-bilingual-v1",
            "adapter": "whisperx",
            "fixtures": [
                {
                    "fixtureId": "fixture_en_01",
                    "requestPath": "request.json",
                    "resultPath": "primary.json",
                    "replayResultPath": "replay.json",
                    "goldPath": "gold.json",
                    "duplicateBillableJobCreated": False,
                }
            ],
            "previewReviewsPath": "previews.json",
            "resourceRunsPath": "resources.json",
            "cleanEnvironmentReproduced": True,
        },
    )
    return {
        "workspace": workspace,
        "gold": gold,
        "primary": primary,
        "primary_digest": primary_digest,
    }


def _candidate(
    word_id: str,
    text: str,
    starts_at_ms: int,
    ends_at_ms: int,
    confidence: float,
) -> dict[str, Any]:
    return {
        "wordId": word_id,
        "cueId": "cue_1",
        "text": text,
        "startsAtMs": starts_at_ms,
        "endsAtMs": ends_at_ms,
        "confidence": confidence,
        "timingOrigin": "forced_alignment",
        "unalignedReason": None,
    }


def _write_result(
    path: Path,
    request: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> str:
    manifest = {
        "schemaVersion": "2",
        "jobId": request["jobId"],
        "alignmentRevisionId": request["alignmentRevisionId"],
        "language": request["language"],
        "sourceAudioSha256": request["audio"]["sha256"],
        "transcriptContentSha256": request["transcript"]["contentSha256"],
        "transcriptProjectionSha256": request["transcript"]["projectionSha256"],
        "adapter": BENCHMARK_ADAPTERS["whisperx"],
        "candidateWords": candidates,
        "projectionIssues": [],
        "resource": {
            "inputDurationMinutes": 2,
            "wallClockMinutes": 0.5,
            "peakMemoryMb": 1_024,
            "runner": "python-3.12",
        },
    }
    digest = sha256_hex(canonical_json_bytes(manifest))
    _write_json(path, {"manifest": manifest, "manifestSha256": digest})
    return digest


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")
