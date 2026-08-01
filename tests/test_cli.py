from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dustwave_alignment_runner.cli import main
from dustwave_alignment_runner.contract import canonical_json_bytes, sha256_hex


def test_fixture_cli_writes_deterministic_non_passing_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio = tmp_path / "episode.audio"
    audio.write_bytes(b"owned fixture audio")
    cues = [
        {
            "cueId": "cue_1",
            "startsAtMs": 0,
            "endsAtMs": 2_000,
            "words": [
                {"wordId": "word_1", "text": "hello"},
                {"wordId": "word_2", "text": "mundo"},
            ],
        }
    ]
    request = {
        "schemaVersion": "2",
        "jobId": "job_fixture",
        "alignmentRevisionId": "alignment_fixture",
        "language": "es",
        "audio": {
            "path": "episode.audio",
            "sha256": sha256_hex(audio.read_bytes()),
            "durationMs": 2_000,
        },
        "transcript": {
            "contentSha256": "a" * 64,
            "projectionSha256": sha256_hex(canonical_json_bytes(cues)),
            "cues": cues,
        },
        "adapter": {
            "name": "fixture",
            "model": "fixture",
            "modelVersion": "1",
            "settingsVersion": "1",
        },
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    output = tmp_path / "result.json"
    monkeypatch.setenv("DUSTWAVE_ALLOW_FIXTURE_ADAPTER", "1")
    monkeypatch.setattr(
        "sys.argv",
        [
            "dustwave-align",
            "run",
            "--adapter",
            "fixture",
            "--request",
            str(request_path),
            "--input-root",
            str(tmp_path),
            "--output",
            str(output),
            "--runner-digest",
            f"sha256:{'0' * 64}",
        ],
    )

    main()
    first = output.read_bytes()
    main()
    assert output.read_bytes() == first
    result = json.loads(first)
    assert result["manifestSha256"] == sha256_hex(
        canonical_json_bytes(result["manifest"])
    )
    assert {word["timingOrigin"] for word in result["manifest"]["candidateWords"]} == {
        "interpolated"
    }
    assert os.stat(output).st_mode & 0o777 == 0o600


def test_fixture_cli_rejects_existing_result_for_different_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio = tmp_path / "episode.audio"
    audio.write_bytes(b"owned fixture audio")
    cues = [
        {
            "cueId": "cue_1",
            "startsAtMs": 0,
            "endsAtMs": 1_000,
            "words": [{"wordId": "word_1", "text": "hola"}],
        }
    ]
    request = {
        "schemaVersion": "2",
        "jobId": "job_fixture",
        "alignmentRevisionId": "alignment_fixture",
        "language": "es",
        "audio": {
            "path": "episode.audio",
            "sha256": sha256_hex(audio.read_bytes()),
            "durationMs": 1_000,
        },
        "transcript": {
            "contentSha256": "a" * 64,
            "projectionSha256": sha256_hex(canonical_json_bytes(cues)),
            "cues": cues,
        },
        "adapter": {
            "name": "fixture",
            "model": "fixture",
            "modelVersion": "1",
            "settingsVersion": "1",
        },
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    output = tmp_path / "result.json"
    monkeypatch.setenv("DUSTWAVE_ALLOW_FIXTURE_ADAPTER", "1")
    argv = [
        "dustwave-align",
        "run",
        "--adapter",
        "fixture",
        "--request",
        str(request_path),
        "--input-root",
        str(tmp_path),
        "--output",
        str(output),
        "--runner-digest",
        f"sha256:{'0' * 64}",
    ]
    monkeypatch.setattr("sys.argv", argv)
    main()
    argv[-1] = f"sha256:{'1' * 64}"

    with pytest.raises(SystemExit):
        main()
    assert f"sha256:{'0' * 64}" in output.read_text(encoding="utf-8")


def test_benchmark_review_cli_dispatches_packet_and_materialization(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    packet_calls = []
    materialization_calls = []
    monkeypatch.setattr(
        "dustwave_alignment_runner.cli.build_benchmark_review_packet",
        lambda *arguments: packet_calls.append(arguments) or {"fixtureCount": 12},
    )
    monkeypatch.setattr(
        "dustwave_alignment_runner.cli.materialize_benchmark_review",
        lambda *arguments: materialization_calls.append(arguments)
        or {"goldFileCount": 12},
    )
    manifest = tmp_path / "workspace.json"
    packet = tmp_path / "packet.json"
    completion = tmp_path / "completion.json"
    output = tmp_path / "materialized"

    monkeypatch.setattr(
        "sys.argv",
        [
            "dustwave-align",
            "benchmark-review-packet",
            "--manifest",
            str(manifest),
            "--input-root",
            str(tmp_path),
            "--output",
            str(packet),
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out) == {"fixtureCount": 12}
    assert packet_calls == [(manifest, tmp_path, packet)]

    monkeypatch.setattr(
        "sys.argv",
        [
            "dustwave-align",
            "benchmark-review-materialize",
            "--packet",
            str(packet),
            "--completion",
            str(completion),
            "--input-root",
            str(tmp_path),
            "--output-root",
            str(output),
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out) == {"goldFileCount": 12}
    assert materialization_calls == [(packet, completion, tmp_path, output)]


def test_benchmark_review_cli_dispatches_discovery(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = []
    monkeypatch.setattr(
        "dustwave_alignment_runner.cli.discover_benchmark_review_workspace",
        lambda *arguments: calls.append(arguments) or {"fixtureCount": 12},
    )
    fixtures = tmp_path / "fixtures"
    output = tmp_path / "review-workspace.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "dustwave-align",
            "benchmark-review-discover",
            "--adapter",
            "whisperx",
            "--fixtures-root",
            str(fixtures),
            "--input-root",
            str(tmp_path),
            "--output",
            str(output),
        ],
    )

    main()

    assert json.loads(capsys.readouterr().out) == {"fixtureCount": 12}
    assert calls == [(tmp_path, fixtures, "whisperx", output)]


def test_benchmark_resource_cli_dispatches_import(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = []
    monkeypatch.setattr(
        "dustwave_alignment_runner.cli.build_benchmark_resource_file",
        lambda *arguments: calls.append(arguments) or {"runCount": 2},
    )
    english = tmp_path / "english.json"
    spanish = tmp_path / "spanish.json"
    output = tmp_path / "resources.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "dustwave-align",
            "benchmark-resource-import",
            "--adapter",
            "whisperx",
            "--evidence",
            str(english),
            "--evidence",
            str(spanish),
            "--input-root",
            str(tmp_path),
            "--output",
            str(output),
        ],
    )

    main()

    assert json.loads(capsys.readouterr().out) == {"runCount": 2}
    assert calls == [([english, spanish], tmp_path, "whisperx", output)]


def test_benchmark_finalize_cli_dispatches_attested_bundle(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = []
    monkeypatch.setattr(
        "dustwave_alignment_runner.cli.finalize_benchmark_submission",
        lambda *arguments: calls.append(arguments) or {"fixtureCount": 12},
    )
    review_workspace = tmp_path / "review-workspace.json"
    materialization = tmp_path / "materialization.json"
    resources = tmp_path / "resources.json"
    workspace_output = tmp_path / "workspace.json"
    submission_output = tmp_path / "submission.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "dustwave-align",
            "benchmark-finalize",
            "--review-workspace",
            str(review_workspace),
            "--review-materialization",
            str(materialization),
            "--resource-runs",
            str(resources),
            "--input-root",
            str(tmp_path),
            "--submission-id",
            "submission_final_01",
            "--corpus-version",
            "corpus_v1",
            "--confirm-no-duplicate-billable-jobs",
            "--confirm-clean-environment-reproduced",
            "--workspace-output",
            str(workspace_output),
            "--submission-output",
            str(submission_output),
        ],
    )

    main()

    assert json.loads(capsys.readouterr().out) == {"fixtureCount": 12}
    assert calls == [
        (
            review_workspace,
            materialization,
            resources,
            tmp_path,
            "submission_final_01",
            "corpus_v1",
            True,
            True,
            workspace_output,
            submission_output,
        )
    ]
