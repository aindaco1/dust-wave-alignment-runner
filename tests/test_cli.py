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
