from __future__ import annotations

import argparse
import errno
import json
import os
import platform
import resource
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

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

ADAPTERS = {"fixture", "stable-ts", "whisperx"}
MAX_RESULT_BYTES = 256 * 1024 * 1024


def main() -> None:
    parser = argparse.ArgumentParser(prog="dustwave-align")
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate")
    _request_arguments(validate, include_output=False)
    run = subcommands.add_parser("run")
    _request_arguments(run, include_output=True)
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
    existing = _reuse_existing_result(
        arguments.output,
        validated,
        arguments.adapter,
        runner_digest,
    )
    if existing is not None:
        print(
            json.dumps(
                {
                    "written": str(_resolved_output(arguments.output)),
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
    _write_immutable(arguments.output, canonical_json_bytes(result) + b"\n")
    print(
        json.dumps(
            {
                "written": str(arguments.output.resolve()),
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


def _write_immutable(output: Path, content: bytes) -> None:
    if len(content) > MAX_RESULT_BYTES:
        raise ContractError("Result exceeds its bounded size.")
    output = _resolved_output(output)
    if output.is_symlink():
        raise ContractError("Output cannot be a symbolic link.")
    if output.exists():
        if not output.is_file():
            raise ContractError("Output must be a regular file.")
        if output.read_bytes() == content:
            return
        raise ContractError("Output exists with different immutable bytes.")
    descriptor, temporary = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temporary, output)
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise
            if not output.is_file() or output.read_bytes() != content:
                raise ContractError(
                    "A concurrent job created different immutable output."
                ) from error
        os.unlink(temporary)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _reuse_existing_result(
    output: Path,
    validated,
    adapter_name: str,
    runner_digest: str,
) -> dict[str, Any] | None:
    path = _resolved_output(output)
    if path.is_symlink():
        raise ContractError("Output cannot be a symbolic link.")
    if not path.exists():
        return None
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_RESULT_BYTES:
        raise ContractError("Existing output is not a bounded regular file.")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError("Existing output is unreadable or invalid.") from error
    if not isinstance(result, dict) or set(result) != {
        "manifest",
        "manifestSha256",
    }:
        raise ContractError("Existing output has an invalid result envelope.")
    manifest = result["manifest"]
    if not isinstance(manifest, dict):
        raise ContractError("Existing output manifest must be an object.")
    digest = result["manifestSha256"]
    if (
        not isinstance(digest, str)
        or sha256_hex(canonical_json_bytes(manifest)) != digest
    ):
        raise ContractError("Existing output manifest digest is invalid.")
    payload = validated.payload
    expected = {
        "schemaVersion": "2",
        "jobId": payload["jobId"],
        "alignmentRevisionId": payload["alignmentRevisionId"],
        "language": payload["language"],
        "sourceAudioSha256": payload["audio"]["sha256"],
        "transcriptContentSha256": payload["transcript"]["contentSha256"],
        "transcriptProjectionSha256": payload["transcript"]["projectionSha256"],
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ContractError("Existing output is bound to different source inputs.")
    existing_adapter = manifest.get("adapter")
    requested_adapter = payload["adapter"]
    expected_adapter = {
        "name": adapter_name,
        "model": requested_adapter["model"],
        "modelVersion": requested_adapter["modelVersion"],
        "settingsVersion": requested_adapter["settingsVersion"],
        "runnerDigest": runner_digest,
    }
    if not isinstance(existing_adapter, dict) or any(
        existing_adapter.get(key) != value for key, value in expected_adapter.items()
    ):
        raise ContractError("Existing output is bound to different adapter inputs.")
    candidate_words = manifest.get("candidateWords")
    expected_word_ids = [word.word_id for word in validated.words]
    if (
        not isinstance(candidate_words, list)
        or len(candidate_words) != len(expected_word_ids)
        or [
            candidate.get("wordId") if isinstance(candidate, dict) else None
            for candidate in candidate_words
        ]
        != expected_word_ids
    ):
        raise ContractError("Existing output has an invalid ordered word projection.")
    if not isinstance(manifest.get("projectionIssues"), list):
        raise ContractError("Existing output projectionIssues must be an array.")
    return result


def _resolved_output(output: Path) -> Path:
    expanded = output.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    expanded.parent.mkdir(parents=True, exist_ok=True)
    parent = expanded.parent.resolve(strict=True)
    return parent / expanded.name


def _peak_memory_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if platform.system() == "Darwin" else 1024
    return round(peak / divisor, 3)


if __name__ == "__main__":
    main()
