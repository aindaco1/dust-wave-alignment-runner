from __future__ import annotations

import errno
import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from .contract import (
    ContractError,
    ValidatedRequest,
    canonical_json_bytes,
    parse_strict_json,
    sha256_hex,
)

MAX_RESULT_BYTES = 256 * 1024 * 1024


def load_bound_result(
    output: Path,
    validated: ValidatedRequest,
    adapter_name: str,
    runner_digest: str,
) -> dict[str, Any] | None:
    path = resolved_output(output)
    if path.is_symlink():
        raise ContractError("Output cannot be a symbolic link.")
    if not path.exists():
        return None
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_RESULT_BYTES:
        raise ContractError("Existing output is not a bounded regular file.")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ContractError("Existing output is unreadable or invalid.") from error
    result = parse_strict_json(content, "Existing output")
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


def write_immutable(output: Path, content: bytes, maximum_bytes: int) -> Path:
    if not 0 < len(content) <= maximum_bytes:
        raise ContractError("Output exceeds its bounded size.")
    path = resolved_output(output)
    if path.is_symlink():
        raise ContractError("Output cannot be a symbolic link.")
    if path.exists():
        if not path.is_file():
            raise ContractError("Output must be a regular file.")
        if path.read_bytes() == content:
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ContractError(
                    "Existing output must not be group or world accessible."
                )
            return path
        raise ContractError("Output exists with different immutable bytes.")
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temporary, path)
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise
            if not path.is_file() or path.read_bytes() != content:
                raise ContractError(
                    "A concurrent job created different immutable output."
                ) from error
        os.unlink(temporary)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    return path


def resolved_output(output: Path) -> Path:
    expanded = output.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    expanded.parent.mkdir(parents=True, exist_ok=True)
    parent = expanded.parent.resolve(strict=True)
    return parent / expanded.name
