from __future__ import annotations

import argparse
import json
import platform
import resource
import sys
import time
from pathlib import Path
from typing import Any

from .benchmark import build_benchmark_submission
from .contract import (
    ContractError,
    canonical_json_bytes,
    file_sha256,
    read_bounded_json,
    sha256_hex,
    validate_request,
    validate_runner_digest,
)
from .projection import project_tokens
from .result_contract import (
    MAX_RESULT_BYTES,
    load_bound_result,
    resolved_output,
    write_immutable,
)

ADAPTERS = {"fixture", "stable-ts", "whisperx"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="dustwave-align")
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate")
    _request_arguments(validate, include_output=False)
    run = subcommands.add_parser("run")
    _request_arguments(run, include_output=True)
    bundle = subcommands.add_parser(
        "benchmark-bundle",
        help="Build one immutable private benchmark submission.",
    )
    bundle.add_argument("--manifest", type=Path, required=True)
    bundle.add_argument("--input-root", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "validate":
            validated = _validated_request(arguments)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "jobId": validated.payload["jobId"],
                        "language": validated.payload["language"],
                        "wordCount": len(validated.words),
                    },
                    separators=(",", ":"),
                )
            )
            return
        if arguments.command == "benchmark-bundle":
            print(
                json.dumps(
                    build_benchmark_submission(
                        arguments.manifest,
                        arguments.input_root,
                        arguments.output,
                    ),
                    separators=(",", ":"),
                )
            )
            return
        _run(arguments)
    except (ContractError, RuntimeError, OSError) as error:
        print(f"dustwave-align: {error}", file=sys.stderr)
        raise SystemExit(1) from error


def _request_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_output: bool,
) -> None:
    parser.add_argument("--adapter", choices=sorted(ADAPTERS), required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    if include_output:
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--runner-digest", required=True)


def _validated_request(arguments: argparse.Namespace):
    payload = read_bounded_json(arguments.request)
    return validate_request(payload, arguments.input_root, arguments.adapter)


def _run(arguments: argparse.Namespace) -> None:
    validated = _validated_request(arguments)
    runner_digest = validate_runner_digest(arguments.runner_digest)
    existing = load_bound_result(
        arguments.output,
        validated,
        arguments.adapter,
        runner_digest,
    )
    if existing is not None:
        print(
            json.dumps(
                {
                    "written": str(resolved_output(arguments.output)),
                    "manifestSha256": existing["manifestSha256"],
                    "wordCount": len(existing["manifest"]["candidateWords"]),
                    "projectionIssueCount": len(
                        existing["manifest"]["projectionIssues"]
                    ),
                    "reused": True,
                },
                separators=(",", ":"),
            )
        )
        return
    started_at = time.perf_counter()
    tokens, adapter_version = _load_adapter(arguments.adapter)(validated)
    if file_sha256(validated.audio_path) != validated.payload["audio"]["sha256"]:
        raise ContractError("Audio changed while the alignment adapter was running.")
    candidate_words, projection_issues = project_tokens(validated.words, tokens)
    elapsed_seconds = round(time.perf_counter() - started_at, 3)
    adapter = validated.payload["adapter"]
    manifest: dict[str, Any] = {
        "schemaVersion": "2",
        "jobId": validated.payload["jobId"],
        "alignmentRevisionId": validated.payload["alignmentRevisionId"],
        "language": validated.payload["language"],
        "sourceAudioSha256": validated.payload["audio"]["sha256"],
        "transcriptContentSha256": validated.payload["transcript"]["contentSha256"],
        "transcriptProjectionSha256": validated.payload["transcript"][
            "projectionSha256"
        ],
        "adapter": {
            "name": arguments.adapter,
            "version": adapter_version,
            "model": adapter["model"],
            "modelVersion": adapter["modelVersion"],
            "settingsVersion": adapter["settingsVersion"],
            "runnerDigest": runner_digest,
        },
        "candidateWords": candidate_words,
        "projectionIssues": projection_issues,
        "resource": {
            "inputDurationMinutes": round(
                validated.payload["audio"]["durationMs"] / 60_000, 3
            ),
            "wallClockMinutes": round(elapsed_seconds / 60, 3),
            "peakMemoryMb": _peak_memory_mb(),
            "runner": f"python-{sys.version_info.major}.{sys.version_info.minor}",
        },
    }
    manifest_bytes = canonical_json_bytes(manifest)
    result = {
        "manifest": manifest,
        "manifestSha256": sha256_hex(manifest_bytes),
    }
    output = write_immutable(
        arguments.output,
        canonical_json_bytes(result) + b"\n",
        MAX_RESULT_BYTES,
    )
    print(
        json.dumps(
            {
                "written": str(output),
                "manifestSha256": result["manifestSha256"],
                "wordCount": len(candidate_words),
                "projectionIssueCount": len(projection_issues),
                "reused": False,
            },
            separators=(",", ":"),
        )
    )


def _load_adapter(name: str):
    if name == "fixture":
        from .adapters.fixture import run

        return run
    if name == "stable-ts":
        from .adapters.stable_ts import run

        return run
    if name == "whisperx":
        from .adapters.whisperx import run

        return run
    raise RuntimeError("Unsupported adapter.")


def _peak_memory_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if platform.system() == "Darwin" else 1024
    return round(peak / divisor, 3)


if __name__ == "__main__":
    main()
