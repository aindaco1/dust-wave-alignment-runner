from __future__ import annotations

from pathlib import Path

import pytest

from dustwave_alignment_runner.contract import (
    ContractError,
    canonical_json_bytes,
    sha256_hex,
    validate_request,
)


def request_fixture(root: Path) -> dict:
    audio = root / "episode.audio"
    audio.write_bytes(b"owned fixture audio")
    cues = [
        {
            "cueId": "cue_1",
            "startsAtMs": 0,
            "endsAtMs": 2_000,
            "words": [
                {"wordId": "word_1", "text": "Ópera,"},
                {"wordId": "word_2", "text": "wave"},
            ],
        }
    ]
    return {
        "schemaVersion": "1",
        "jobId": "job_fixture",
        "alignmentRevisionId": "alignment_fixture",
        "language": "es",
        "audio": {
            "path": "episode.audio",
            "sha256": sha256_hex(audio.read_bytes()),
            "durationMs": 2_000,
        },
        "transcript": {
            "sha256": sha256_hex(canonical_json_bytes(cues)),
            "cues": cues,
        },
        "adapter": {
            "name": "fixture",
            "model": "fixture",
            "modelVersion": "1",
            "settingsVersion": "1",
        },
    }


def test_accepts_canonical_checksums_and_stable_words(tmp_path: Path) -> None:
    request = request_fixture(tmp_path)
    validated = validate_request(request, tmp_path, "fixture")

    assert validated.audio_path == tmp_path / "episode.audio"
    assert [word.word_id for word in validated.words] == ["word_1", "word_2"]


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value["audio"].update(sha256="0" * 64), "Audio SHA-256"),
        (
            lambda value: value["transcript"].update(sha256="0" * 64),
            "Transcript SHA-256",
        ),
        (
            lambda value: value["transcript"]["cues"][0]["words"].append(
                {"wordId": "word_1", "text": "duplicate"}
            ),
            "wordId values",
        ),
        (
            lambda value: value.update(extra=True),
            "missing or unknown",
        ),
    ],
)
def test_rejects_changed_or_ambiguous_input(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    request = request_fixture(tmp_path)
    mutation(request)
    if "wordId values" in message:
        cues = request["transcript"]["cues"]
        request["transcript"]["sha256"] = sha256_hex(canonical_json_bytes(cues))
    with pytest.raises(ContractError, match=message):
        validate_request(request, tmp_path, "fixture")


def test_rejects_audio_path_escape(tmp_path: Path) -> None:
    request = request_fixture(tmp_path)
    outside = tmp_path.parent / "outside.audio"
    outside.write_bytes(b"outside")
    request["audio"]["path"] = "../outside.audio"
    request["audio"]["sha256"] = sha256_hex(outside.read_bytes())

    with pytest.raises(ContractError, match="escapes"):
        validate_request(request, tmp_path, "fixture")


def test_rejects_unsafe_model_reference(tmp_path: Path) -> None:
    request = request_fixture(tmp_path)
    request["adapter"]["model"] = "../../unreviewed-model"

    with pytest.raises(ContractError, match="safe package or model"):
        validate_request(request, tmp_path, "fixture")
