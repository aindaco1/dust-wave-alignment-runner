from __future__ import annotations

from pathlib import Path
from typing import Any

from .benchmark import (
    BENCHMARK_ADAPTERS,
    BENCHMARK_RESOURCES_SCHEMA,
    BENCHMARK_RUNNER_REVISION,
    MAXIMUM_BENCHMARK_BYTES,
    MAXIMUM_RESOURCE_RUNS,
    _boolean,
    _bounded_array,
    _exact_keys,
    _identifier,
    _input_path,
    _integer,
    _mapping,
    _number,
    _safe_text,
    _sha256,
)
from .contract import (
    ContractError,
    canonical_json_bytes,
    read_bounded_json,
    sha256_hex,
)
from .result_contract import write_immutable

WORKFLOW_EVIDENCE_SCHEMA = "alignment-workflow-evidence-v2"
DISK_MEASUREMENT_METHOD = "filesystem-delta-plus-input-v1"


def build_benchmark_resource_file(
    evidence_paths: list[Path],
    input_root: Path,
    adapter_name: str,
    output: Path,
) -> dict[str, Any]:
    root = input_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ContractError("input-root must be a directory.")
    if adapter_name not in BENCHMARK_ADAPTERS:
        raise ContractError("adapter must be whisperx or stable-ts.")
    values = _bounded_array(
        evidence_paths,
        "evidence",
        MAXIMUM_RESOURCE_RUNS,
    )
    if not values:
        raise ContractError("evidence must contain at least one artifact.")
    adapter = BENCHMARK_ADAPTERS[adapter_name]
    seen_jobs: set[str] = set()
    runs: list[tuple[str, str, dict[str, Any]]] = []
    for index, reference in enumerate(values):
        field = f"evidence[{index}]"
        evidence_path = _input_path(root, reference, field)
        evidence = _mapping(read_bounded_json(evidence_path), field)
        _exact_keys(
            evidence,
            {
                "schemaVersion",
                "jobId",
                "alignmentRevisionId",
                "processorManifestSha256",
                "resultManifestSha256",
                "adapter",
                "runner",
                "quality",
                "resource",
                "resourceMeasurement",
            },
            field,
        )
        if evidence["schemaVersion"] != WORKFLOW_EVIDENCE_SCHEMA:
            raise ContractError(f"{field} schemaVersion is unsupported.")
        job_id = _identifier(evidence["jobId"], f"{field}.jobId")
        if job_id in seen_jobs:
            raise ContractError("Workflow evidence job IDs must be unique.")
        seen_jobs.add(job_id)
        _identifier(
            evidence["alignmentRevisionId"],
            f"{field}.alignmentRevisionId",
        )
        _sha256(
            evidence["processorManifestSha256"],
            f"{field}.processorManifestSha256",
        )
        _sha256(
            evidence["resultManifestSha256"],
            f"{field}.resultManifestSha256",
        )
        _validate_adapter(evidence["adapter"], adapter, field)
        _validate_runner(evidence["runner"], adapter, field)
        _validate_quality(evidence["quality"], field)
        resource = _validated_resource(evidence["resource"], field)
        _validate_measurement(evidence["resourceMeasurement"], field)
        runs.append((resource["language"], job_id, resource))

    normalized = [resource for _, _, resource in sorted(runs)]
    payload = {
        "schemaVersion": BENCHMARK_RESOURCES_SCHEMA,
        "runs": normalized,
    }
    content = canonical_json_bytes(payload) + b"\n"
    written = write_immutable(output, content, MAXIMUM_BENCHMARK_BYTES)
    qualifying = {
        language: sum(
            1
            for run in normalized
            if run["language"] == language and run["inputDurationMinutes"] >= 60
        )
        for language in ("en", "es")
    }
    return {
        "written": str(written),
        "resourceSha256": sha256_hex(content),
        "resourceBytes": len(content),
        "runCount": len(normalized),
        "englishSixtyMinuteRunCount": qualifying["en"],
        "spanishSixtyMinuteRunCount": qualifying["es"],
        "englishSixtyMinuteRunShortfall": int(qualifying["en"] == 0),
        "spanishSixtyMinuteRunShortfall": int(qualifying["es"] == 0),
    }


def _validate_adapter(value: Any, expected: dict[str, str], field: str) -> None:
    adapter = _mapping(value, f"{field}.adapter")
    _exact_keys(
        adapter,
        {"name", "version", "modelVersion", "settingsVersion"},
        f"{field}.adapter",
    )
    for key in ("name", "version", "modelVersion", "settingsVersion"):
        if adapter[key] != expected[key]:
            raise ContractError(f"{field} adapter identity is not pinned.")


def _validate_runner(value: Any, adapter: dict[str, str], field: str) -> None:
    runner = _mapping(value, f"{field}.runner")
    _exact_keys(runner, {"revision", "digest"}, f"{field}.runner")
    if (
        runner["revision"] != BENCHMARK_RUNNER_REVISION
        or runner["digest"] != adapter["runnerDigest"]
    ):
        raise ContractError(f"{field} runner identity is not pinned.")


def _validate_quality(value: Any, field: str) -> None:
    quality = _mapping(value, f"{field}.quality")
    _exact_keys(
        quality,
        {
            "schemaVersion",
            "wordCount",
            "alignedWordCount",
            "unalignedWordCount",
            "interpolatedWordCount",
            "invalidWordCount",
            "projectionIssueCount",
            "alignedWordRatio",
            "structurallyEligible",
        },
        f"{field}.quality",
    )
    if quality["schemaVersion"] != "alignment-result-quality-v1":
        raise ContractError(f"{field} quality schemaVersion is unsupported.")
    word_count = _integer(
        quality["wordCount"],
        1,
        25_000,
        f"{field}.quality.wordCount",
    )
    aligned = _integer(
        quality["alignedWordCount"],
        0,
        word_count,
        f"{field}.quality.alignedWordCount",
    )
    unaligned = _integer(
        quality["unalignedWordCount"],
        0,
        word_count,
        f"{field}.quality.unalignedWordCount",
    )
    issue_counts = [
        _integer(quality[key], 0, 25_000, f"{field}.quality.{key}")
        for key in (
            "interpolatedWordCount",
            "invalidWordCount",
            "projectionIssueCount",
        )
    ]
    ratio = _number(
        quality["alignedWordRatio"],
        0,
        1,
        f"{field}.quality.alignedWordRatio",
    )
    if abs(ratio - aligned / word_count) > 0.000_001:
        raise ContractError(f"{field} aligned word ratio is inconsistent.")
    if unaligned != word_count - aligned or any(issue_counts):
        raise ContractError(f"{field} structural quality counts are inconsistent.")
    if not _boolean(
        quality["structurallyEligible"],
        f"{field}.quality.structurallyEligible",
    ):
        raise ContractError(f"{field} is not structurally eligible.")


def _validated_resource(value: Any, field: str) -> dict[str, Any]:
    resource = _mapping(value, f"{field}.resource")
    _exact_keys(
        resource,
        {
            "language",
            "inputDurationMinutes",
            "wallClockMinutes",
            "peakMemoryMb",
            "peakDiskMb",
            "runner",
        },
        f"{field}.resource",
    )
    language = resource["language"]
    if language not in {"en", "es"}:
        raise ContractError(f"{field}.resource.language must be en or es.")
    return {
        "language": language,
        "inputDurationMinutes": _number(
            resource["inputDurationMinutes"],
            0.001,
            10_000,
            f"{field}.resource.inputDurationMinutes",
        ),
        "wallClockMinutes": _number(
            resource["wallClockMinutes"],
            0.001,
            10_000,
            f"{field}.resource.wallClockMinutes",
        ),
        "peakMemoryMb": _number(
            resource["peakMemoryMb"],
            0.001,
            1_000_000,
            f"{field}.resource.peakMemoryMb",
        ),
        "peakDiskMb": _number(
            resource["peakDiskMb"],
            0.001,
            1_000_000,
            f"{field}.resource.peakDiskMb",
        ),
        "runner": _safe_text(
            resource["runner"],
            200,
            f"{field}.resource.runner",
        ),
    }


def _validate_measurement(value: Any, field: str) -> None:
    measurement = _mapping(value, f"{field}.resourceMeasurement")
    _exact_keys(
        measurement,
        {"diskMethod", "sampleIntervalMs"},
        f"{field}.resourceMeasurement",
    )
    if measurement["diskMethod"] != DISK_MEASUREMENT_METHOD:
        raise ContractError(f"{field} disk measurement method is unsupported.")
    _integer(
        measurement["sampleIntervalMs"],
        100,
        60_000,
        f"{field}.resourceMeasurement.sampleIntervalMs",
    )
