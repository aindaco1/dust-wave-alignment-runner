from __future__ import annotations

from pathlib import Path
from typing import Any

from .benchmark import (
    BENCHMARK_ADAPTERS,
    MAXIMUM_FIXTURE_DURATION_MS,
    MAXIMUM_FIXTURES,
    MINIMUM_FIXTURE_DURATION_MS,
    _identifier,
    _input_path,
    _require_adapter_identity,
)
from .benchmark_review import (
    BENCHMARK_REVIEW_WORKSPACE_SCHEMA,
    MAXIMUM_REVIEW_PACKET_BYTES,
)
from .contract import (
    ContractError,
    canonical_json_bytes,
    read_bounded_json,
    sha256_hex,
    validate_request,
)
from .result_contract import load_bound_result, write_immutable

REQUEST_FILENAME = "request.json"
PRIMARY_RESULT_FILENAME = "result-primary.json"


def discover_benchmark_review_workspace(
    input_root: Path,
    fixtures_root: Path,
    adapter_name: str,
    output: Path,
) -> dict[str, Any]:
    """Discover and validate convention-based private review fixtures."""

    root = input_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ContractError("input-root must be a directory.")
    fixtures = _private_directory(root, fixtures_root, "fixtures-root")
    if adapter_name not in BENCHMARK_ADAPTERS:
        raise ContractError("adapter must be whisperx or stable-ts.")
    adapter = dict(BENCHMARK_ADAPTERS[adapter_name])

    descriptors: list[dict[str, str]] = []
    language_counts = {"en": 0, "es": 0}
    for entry in sorted(fixtures.iterdir(), key=lambda path: path.name):
        if entry.is_symlink():
            raise ContractError("fixtures-root cannot contain symbolic links.")
        if not entry.is_dir():
            continue
        request_candidate = entry / REQUEST_FILENAME
        result_candidate = entry / PRIMARY_RESULT_FILENAME
        request_exists = request_candidate.exists()
        result_exists = result_candidate.exists()
        if not request_exists and not result_exists:
            continue
        fixture_id = _identifier(entry.name, "fixture directory name")
        if not request_exists or not result_exists:
            raise ContractError(
                f"{fixture_id} must contain {REQUEST_FILENAME} and "
                f"{PRIMARY_RESULT_FILENAME}."
            )
        request_path = _input_path(root, request_candidate, f"{fixture_id} request")
        result_path = _input_path(
            root,
            result_candidate,
            f"{fixture_id} primary result",
        )
        validated = validate_request(
            read_bounded_json(request_path),
            root,
            adapter_name,
        )
        duration_ms = validated.payload["audio"]["durationMs"]
        if not (
            MINIMUM_FIXTURE_DURATION_MS <= duration_ms <= MAXIMUM_FIXTURE_DURATION_MS
        ):
            raise ContractError(
                f"{fixture_id} audio duration must be between two and five minutes."
            )
        request_adapter = validated.payload["adapter"]
        for key in ("name", "model", "modelVersion", "settingsVersion"):
            if request_adapter[key] != adapter[key]:
                raise ContractError(
                    f"{fixture_id} request adapter identity is not pinned."
                )
        result = load_bound_result(
            result_path,
            validated,
            adapter_name,
            adapter["runnerDigest"],
        )
        if result is None:
            raise ContractError(f"{fixture_id} requires a primary result file.")
        _require_adapter_identity(result["manifest"], adapter, fixture_id)
        descriptors.append(
            {
                "fixtureId": fixture_id,
                "requestPath": request_path.relative_to(root).as_posix(),
                "resultPath": result_path.relative_to(root).as_posix(),
            }
        )
        language_counts[validated.payload["language"]] += 1
        if len(descriptors) > MAXIMUM_FIXTURES:
            raise ContractError(f"fixtures exceed the {MAXIMUM_FIXTURES}-fixture cap.")

    if not descriptors:
        raise ContractError("fixtures-root contains no complete benchmark fixtures.")
    workspace = {
        "schemaVersion": BENCHMARK_REVIEW_WORKSPACE_SCHEMA,
        "adapter": adapter_name,
        "fixtures": descriptors,
    }
    content = canonical_json_bytes(workspace) + b"\n"
    written = write_immutable(output, content, MAXIMUM_REVIEW_PACKET_BYTES)
    return {
        "written": str(written),
        "workspaceSha256": sha256_hex(content),
        "workspaceBytes": len(content),
        "fixtureCount": len(descriptors),
        "englishFixtureCount": language_counts["en"],
        "spanishFixtureCount": language_counts["es"],
    }


def _private_directory(root: Path, reference: Path, field: str) -> Path:
    candidate = reference.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ContractError(f"{field} cannot be a symbolic link.")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ContractError(f"{field} escapes input-root.") from error
    if not resolved.is_dir():
        raise ContractError(f"{field} must be a directory.")
    return resolved
