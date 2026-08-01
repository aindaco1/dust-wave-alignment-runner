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
from dustwave_alignment_runner.benchmark_review import (
    _balanced_selection,
    build_benchmark_review_packet,
    materialize_benchmark_review,
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


def test_builds_and_materializes_one_private_review_packet(tmp_path: Path) -> None:
    review_workspace = _review_workspace(tmp_path)
    packet_path = tmp_path / "private" / "review-packet.json"

    first = build_benchmark_review_packet(review_workspace, tmp_path, packet_path)
    second = build_benchmark_review_packet(review_workspace, tmp_path, packet_path)

    assert first == second
    assert first == {
        "written": str(packet_path),
        "packetSha256": sha256_hex(packet_path.read_bytes()),
        "packetBytes": len(packet_path.read_bytes()),
        "fixtureCount": 1,
        "englishReviewWordCount": 2,
        "spanishReviewWordCount": 0,
        "englishPreviewReviewCount": 2,
        "spanishPreviewReviewCount": 0,
        "englishReviewWordShortfall": 498,
        "spanishReviewWordShortfall": 500,
    }
    assert os.stat(packet_path).st_mode & 0o777 == 0o600
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["fixtures"][0]["reviewWords"] == [
        {
            "wordId": "word_1",
            "cueId": "cue_1",
            "text": "hello",
            "candidateStartsAtMs": 101,
            "candidateEndsAtMs": 399,
            "confidence": 0.98,
            "timingOrigin": "forced_alignment",
            "previewReviewRequired": True,
        },
        {
            "wordId": "word_2",
            "cueId": "cue_1",
            "text": "world",
            "candidateStartsAtMs": 501,
            "candidateEndsAtMs": 899,
            "confidence": 0.97,
            "timingOrigin": "forced_alignment",
            "previewReviewRequired": True,
        },
    ]
    assert "requestPath" not in packet_path.read_text(encoding="utf-8")
    assert "resultPath" not in packet_path.read_text(encoding="utf-8")

    completion_path = tmp_path / "private" / "completion.json"
    _write_json(
        completion_path,
        {
            "schemaVersion": "alignment-benchmark-review-completion-v1",
            "packetSha256": first["packetSha256"],
            "reviews": [
                {
                    "fixtureId": "fixture_en_01",
                    "wordId": "word_1",
                    "startsAtMs": 100,
                    "endsAtMs": 400,
                    "scorable": True,
                    "acceptedWithoutClipping": True,
                },
                {
                    "fixtureId": "fixture_en_01",
                    "wordId": "word_2",
                    "startsAtMs": 500,
                    "endsAtMs": 900,
                    "scorable": True,
                    "acceptedWithoutClipping": False,
                },
            ],
        },
    )

    materialized = materialize_benchmark_review(
        packet_path,
        completion_path,
        tmp_path,
        Path("review-output"),
    )
    assert (
        materialize_benchmark_review(
            packet_path,
            completion_path,
            tmp_path,
            Path("review-output"),
        )
        == materialized
    )

    materialization_path = tmp_path / "review-output" / "materialization.json"
    gold_path = tmp_path / "review-output" / "gold" / "fixture_en_01.json"
    preview_path = tmp_path / "review-output" / "reviews" / "previews.json"
    assert materialized == {
        "written": str(materialization_path),
        "materializationSha256": sha256_hex(materialization_path.read_bytes()),
        "goldFileCount": 1,
        "englishScorableGoldWordCount": 2,
        "spanishScorableGoldWordCount": 0,
        "previewReviewCount": 2,
        "previewAcceptedWithoutClippingCount": 1,
    }
    assert json.loads(gold_path.read_text(encoding="utf-8"))["goldWords"] == [
        {
            "wordId": "word_1",
            "cueId": "cue_1",
            "text": "hello",
            "startsAtMs": 100,
            "endsAtMs": 400,
            "scorable": True,
        },
        {
            "wordId": "word_2",
            "cueId": "cue_1",
            "text": "world",
            "startsAtMs": 500,
            "endsAtMs": 900,
            "scorable": True,
        },
    ]
    assert json.loads(preview_path.read_text(encoding="utf-8"))["reviews"] == [
        {
            "fixtureId": "fixture_en_01",
            "wordId": "word_1",
            "acceptedWithoutClipping": True,
        },
        {
            "fixtureId": "fixture_en_01",
            "wordId": "word_2",
            "acceptedWithoutClipping": False,
        },
    ]
    assert all(
        os.stat(path).st_mode & 0o777 == 0o600
        for path in (materialization_path, gold_path, preview_path)
    )


def test_review_materialization_rejects_stale_incomplete_and_escaping_inputs(
    tmp_path: Path,
) -> None:
    review_workspace = _review_workspace(tmp_path)
    packet_path = tmp_path / "review-packet.json"
    build_benchmark_review_packet(review_workspace, tmp_path, packet_path)
    completion_path = tmp_path / "completion.json"
    _write_json(
        completion_path,
        {
            "schemaVersion": "alignment-benchmark-review-completion-v1",
            "packetSha256": "0" * 64,
            "reviews": [],
        },
    )

    with pytest.raises(ContractError, match="different packet"):
        materialize_benchmark_review(
            packet_path,
            completion_path,
            tmp_path,
            Path("review-output"),
        )

    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["packetSha256"] = sha256_hex(packet_path.read_bytes())
    _write_json(completion_path, completion)
    with pytest.raises(ContractError, match="every packet word"):
        materialize_benchmark_review(
            packet_path,
            completion_path,
            tmp_path,
            Path("review-output"),
        )

    completion["reviews"] = [
        {
            "fixtureId": "fixture_en_01",
            "wordId": "word_1",
            "startsAtMs": 100,
            "endsAtMs": 400,
            "scorable": True,
            "acceptedWithoutClipping": True,
        },
        {
            "fixtureId": "fixture_en_01",
            "wordId": "word_2",
            "startsAtMs": 500,
            "endsAtMs": 900,
            "scorable": True,
            "acceptedWithoutClipping": True,
        },
    ]
    _write_json(completion_path, completion)
    with pytest.raises(ContractError, match="output-root escapes"):
        materialize_benchmark_review(
            packet_path,
            completion_path,
            tmp_path,
            Path("../outside"),
        )


def test_review_selection_is_balanced_bounded_and_evenly_spaced() -> None:
    candidates = [[{} for _ in range(1_000)], [{} for _ in range(100)]]

    selected = _balanced_selection(candidates, [0, 1], 500)

    assert len(selected[0]) == 400
    assert len(selected[1]) == 100
    assert selected[0][0] == 0
    assert selected[0][-1] == 999
    assert selected[1] == list(range(100))
    assert len(set(selected[0])) == 400


def test_review_packet_rejects_candidate_projection_tampering(tmp_path: Path) -> None:
    review_workspace = _review_workspace(tmp_path)
    primary_path = tmp_path / "primary.json"
    primary = json.loads(primary_path.read_text(encoding="utf-8"))
    primary["manifest"]["candidateWords"][0]["text"] = "tampered"
    primary["manifestSha256"] = sha256_hex(canonical_json_bytes(primary["manifest"]))
    _write_json(primary_path, primary)

    with pytest.raises(ContractError, match="reviewed projection"):
        build_benchmark_review_packet(
            review_workspace,
            tmp_path,
            tmp_path / "review-packet.json",
        )


def test_workspace_v2_consumes_exact_review_materialization(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    materialization_path = _materialized_review(tmp_path)
    workspace = json.loads(paths["workspace"].read_text(encoding="utf-8"))
    workspace["schemaVersion"] = "alignment-benchmark-workspace-v2"
    workspace["reviewMaterializationPath"] = materialization_path.relative_to(
        tmp_path
    ).as_posix()
    workspace.pop("previewReviewsPath")
    workspace["fixtures"][0].pop("goldPath")
    workspace_v2 = tmp_path / "workspace-v2.json"
    _write_json(workspace_v2, workspace)
    output = tmp_path / "submission-v2.json"

    result = build_benchmark_submission(workspace_v2, tmp_path, output)

    submission = json.loads(output.read_text(encoding="utf-8"))
    assert result["englishGoldWordCount"] == 2
    assert result["previewReviewCount"] == 2
    assert submission["benchmark"]["fixtures"][0]["goldWords"][0] == {
        "wordId": "word_1",
        "cueId": "cue_1",
        "text": "hello",
        "startsAtMs": 100,
        "endsAtMs": 400,
        "scorable": True,
    }

    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    gold_path = tmp_path / materialization["goldFiles"][0]["path"]
    gold_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ContractError, match="digest does not match"):
        build_benchmark_submission(
            workspace_v2,
            tmp_path,
            tmp_path / "tampered-submission.json",
        )


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


def _review_workspace(root: Path) -> Path:
    _workspace(root)
    review_workspace = root / "review-workspace.json"
    _write_json(
        review_workspace,
        {
            "schemaVersion": "alignment-benchmark-review-workspace-v1",
            "adapter": "whisperx",
            "fixtures": [
                {
                    "fixtureId": "fixture_en_01",
                    "requestPath": "request.json",
                    "resultPath": "primary.json",
                }
            ],
        },
    )
    return review_workspace


def _materialized_review(root: Path) -> Path:
    review_workspace = _review_workspace(root)
    packet_path = root / "review" / "packet.json"
    packet_result = build_benchmark_review_packet(
        review_workspace,
        root,
        packet_path,
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    gold = json.loads((root / "gold.json").read_text(encoding="utf-8"))
    gold_by_word = {word["wordId"]: word for word in gold["goldWords"]}
    reviews = []
    for fixture in packet["fixtures"]:
        for word in fixture["reviewWords"]:
            reviewed = gold_by_word[word["wordId"]]
            reviews.append(
                {
                    "fixtureId": fixture["fixtureId"],
                    "wordId": word["wordId"],
                    "startsAtMs": reviewed["startsAtMs"],
                    "endsAtMs": reviewed["endsAtMs"],
                    "scorable": True,
                    "acceptedWithoutClipping": (
                        True if word["previewReviewRequired"] else None
                    ),
                }
            )
    completion_path = root / "review" / "completion.json"
    _write_json(
        completion_path,
        {
            "schemaVersion": "alignment-benchmark-review-completion-v1",
            "packetSha256": packet_result["packetSha256"],
            "reviews": reviews,
        },
    )
    materialize_benchmark_review(
        packet_path,
        completion_path,
        root,
        Path("review/materialized"),
    )
    return root / "review" / "materialized" / "materialization.json"


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
