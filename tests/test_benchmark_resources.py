from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dustwave_alignment_runner.benchmark import BENCHMARK_ADAPTERS
from dustwave_alignment_runner.benchmark_resources import (
    build_benchmark_resource_file,
)
from dustwave_alignment_runner.contract import (
    ContractError,
    canonical_json_bytes,
    sha256_hex,
)


def test_imports_content_free_workflow_resources_deterministically(
    tmp_path: Path,
) -> None:
    english = tmp_path / "english.json"
    spanish = tmp_path / "spanish.json"
    _write_json(english, _evidence("en", "job_english", 62.423))
    _write_json(spanish, _evidence("es", "job_spanish", 60))
    output = tmp_path / "private" / "resources.json"

    first = build_benchmark_resource_file(
        [spanish, english],
        tmp_path,
        "whisperx",
        output,
    )
    second = build_benchmark_resource_file(
        [english, spanish],
        tmp_path,
        "whisperx",
        output,
    )

    assert first == second
    assert first == {
        "written": str(output),
        "resourceSha256": sha256_hex(output.read_bytes()),
        "resourceBytes": len(output.read_bytes()),
        "runCount": 2,
        "englishSixtyMinuteRunCount": 1,
        "spanishSixtyMinuteRunCount": 1,
        "englishSixtyMinuteRunShortfall": 0,
        "spanishSixtyMinuteRunShortfall": 0,
    }
    assert os.stat(output).st_mode & 0o777 == 0o600
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == {
        "schemaVersion": "alignment-benchmark-resources-v1",
        "runs": [
            {
                "language": "en",
                "inputDurationMinutes": 62.423,
                "wallClockMinutes": 6.831,
                "peakMemoryMb": 1946.996,
                "peakDiskMb": 3200.125,
                "runner": "python-3.12",
            },
            {
                "language": "es",
                "inputDurationMinutes": 60,
                "wallClockMinutes": 6.831,
                "peakMemoryMb": 1946.996,
                "peakDiskMb": 3200.125,
                "runner": "python-3.12",
            },
        ],
    }
    serialized = output.read_text(encoding="utf-8")
    assert "job_english" not in serialized
    assert "processorManifestSha256" not in serialized


def test_resource_import_reports_truthful_shortfalls(tmp_path: Path) -> None:
    evidence = tmp_path / "english.json"
    _write_json(evidence, _evidence("en", "job_english", 59.9))

    result = build_benchmark_resource_file(
        [evidence],
        tmp_path,
        "whisperx",
        tmp_path / "resources.json",
    )

    assert result["englishSixtyMinuteRunShortfall"] == 1
    assert result["spanishSixtyMinuteRunShortfall"] == 1


def test_resource_import_rejects_tampering_duplicates_and_paths(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.json"
    value = _evidence("en", "job_english", 62.423)
    value["runner"]["revision"] = "f" * 40
    _write_json(evidence, value)
    with pytest.raises(ContractError, match="runner identity is not pinned"):
        build_benchmark_resource_file(
            [evidence],
            tmp_path,
            "whisperx",
            tmp_path / "resources.json",
        )

    value = _evidence("en", "job_english", 62.423)
    value["quality"]["projectionIssueCount"] = 1
    _write_json(evidence, value)
    with pytest.raises(ContractError, match="quality counts are inconsistent"):
        build_benchmark_resource_file(
            [evidence],
            tmp_path,
            "whisperx",
            tmp_path / "resources.json",
        )

    _write_json(evidence, _evidence("en", "job_english", 62.423))
    with pytest.raises(ContractError, match="job IDs must be unique"):
        build_benchmark_resource_file(
            [evidence, evidence],
            tmp_path,
            "whisperx",
            tmp_path / "resources.json",
        )

    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    _write_json(outside, _evidence("es", "job_spanish", 60))
    try:
        with pytest.raises(ContractError, match="escapes input-root"):
            build_benchmark_resource_file(
                [Path(f"../{outside.name}")],
                tmp_path,
                "whisperx",
                tmp_path / "resources.json",
            )
    finally:
        outside.unlink()


def _evidence(language: str, job_id: str, duration: float) -> dict:
    adapter = BENCHMARK_ADAPTERS["whisperx"]
    return {
        "schemaVersion": "alignment-workflow-evidence-v2",
        "jobId": job_id,
        "alignmentRevisionId": f"revision_{language}",
        "processorManifestSha256": "a" * 64,
        "resultManifestSha256": "b" * 64,
        "adapter": {
            "name": adapter["name"],
            "version": adapter["version"],
            "modelVersion": adapter["modelVersion"],
            "settingsVersion": adapter["settingsVersion"],
        },
        "runner": {
            "revision": "e611801d2af82dcdb079444b7e8a7eea4309d1a6",
            "digest": adapter["runnerDigest"],
        },
        "quality": {
            "schemaVersion": "alignment-result-quality-v1",
            "wordCount": 10_176,
            "alignedWordCount": 10_176,
            "unalignedWordCount": 0,
            "interpolatedWordCount": 0,
            "invalidWordCount": 0,
            "projectionIssueCount": 0,
            "alignedWordRatio": 1,
            "structurallyEligible": True,
        },
        "resource": {
            "language": language,
            "inputDurationMinutes": duration,
            "wallClockMinutes": 6.831,
            "peakMemoryMb": 1946.996,
            "peakDiskMb": 3200.125,
            "runner": "python-3.12",
        },
        "resourceMeasurement": {
            "diskMethod": "filesystem-delta-plus-input-v1",
            "sampleIntervalMs": 1000,
        },
    }


def _write_json(path: Path, value: dict) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")
