from __future__ import annotations

from pathlib import Path
from typing import Any

from .benchmark import (
    BENCHMARK_WORKSPACE_SCHEMA_V2,
    MAXIMUM_BENCHMARK_BYTES,
    MAXIMUM_FIXTURES,
    _bounded_array,
    _exact_keys,
    _identifier,
    _input_path,
    _mapping,
    build_benchmark_submission,
)
from .benchmark_review import BENCHMARK_REVIEW_WORKSPACE_SCHEMA
from .contract import (
    ContractError,
    canonical_json_bytes,
    read_bounded_json,
    sha256_hex,
)
from .result_contract import write_immutable

PRIMARY_RESULT_FILENAME = "result-primary.json"
REPLAY_RESULT_FILENAME = "result-replay.json"


def finalize_benchmark_submission(
    review_workspace_path: Path,
    review_materialization_path: Path,
    resource_runs_path: Path,
    input_root: Path,
    submission_id: str,
    corpus_version: str,
    confirm_no_duplicate_billable_jobs: bool,
    confirm_clean_environment_reproduced: bool,
    workspace_output: Path,
    submission_output: Path,
) -> dict[str, Any]:
    root = input_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ContractError("input-root must be a directory.")
    if not confirm_no_duplicate_billable_jobs:
        raise ContractError(
            "Explicit no-duplicate-billable-jobs confirmation is required."
        )
    if not confirm_clean_environment_reproduced:
        raise ContractError(
            "Explicit clean-environment-reproduction confirmation is required."
        )
    normalized_submission_id = _identifier(submission_id, "submissionId")
    normalized_corpus_version = _identifier(corpus_version, "corpusVersion")
    review_workspace_file = _input_path(
        root,
        review_workspace_path,
        "review-workspace",
    )
    review_workspace = _mapping(
        read_bounded_json(review_workspace_file),
        "review workspace",
    )
    _exact_keys(
        review_workspace,
        {"schemaVersion", "adapter", "fixtures"},
        "review workspace",
    )
    if review_workspace["schemaVersion"] != BENCHMARK_REVIEW_WORKSPACE_SCHEMA:
        raise ContractError("Review workspace schemaVersion is unsupported.")
    adapter = review_workspace["adapter"]
    if adapter not in {"stable-ts", "whisperx"}:
        raise ContractError("Review workspace adapter is unsupported.")
    values = _bounded_array(
        review_workspace["fixtures"],
        "review workspace fixtures",
        MAXIMUM_FIXTURES,
    )
    if not values:
        raise ContractError("Review workspace fixtures must not be empty.")

    fixtures: list[dict[str, Any]] = []
    seen_fixture_ids: set[str] = set()
    for index, value in enumerate(values):
        field = f"review workspace fixtures[{index}]"
        descriptor = _mapping(value, field)
        _exact_keys(
            descriptor,
            {"fixtureId", "requestPath", "resultPath"},
            field,
        )
        fixture_id = _identifier(descriptor["fixtureId"], f"{field}.fixtureId")
        if fixture_id in seen_fixture_ids:
            raise ContractError("Review workspace fixture IDs must be unique.")
        seen_fixture_ids.add(fixture_id)
        request_path = _input_path(
            root,
            descriptor["requestPath"],
            f"{field}.requestPath",
        )
        result_path = _input_path(
            root,
            descriptor["resultPath"],
            f"{field}.resultPath",
        )
        if result_path.name != PRIMARY_RESULT_FILENAME:
            raise ContractError(
                f"{field}.resultPath must use {PRIMARY_RESULT_FILENAME}."
            )
        replay_path = _input_path(
            root,
            result_path.with_name(REPLAY_RESULT_FILENAME),
            f"{field}.replayResultPath",
        )
        fixtures.append(
            {
                "fixtureId": fixture_id,
                "requestPath": request_path.relative_to(root).as_posix(),
                "resultPath": result_path.relative_to(root).as_posix(),
                "replayResultPath": replay_path.relative_to(root).as_posix(),
                "duplicateBillableJobCreated": False,
            }
        )

    materialization = _input_path(
        root,
        review_materialization_path,
        "review-materialization",
    )
    resources = _input_path(root, resource_runs_path, "resource-runs")
    workspace_path = _private_output_file(root, workspace_output, "workspace-output")
    workspace = {
        "schemaVersion": BENCHMARK_WORKSPACE_SCHEMA_V2,
        "submissionId": normalized_submission_id,
        "corpusVersion": normalized_corpus_version,
        "adapter": adapter,
        "fixtures": fixtures,
        "reviewMaterializationPath": materialization.relative_to(root).as_posix(),
        "resourceRunsPath": resources.relative_to(root).as_posix(),
        "cleanEnvironmentReproduced": True,
    }
    workspace_content = canonical_json_bytes(workspace) + b"\n"
    written_workspace = write_immutable(
        workspace_path,
        workspace_content,
        MAXIMUM_BENCHMARK_BYTES,
    )
    submission = build_benchmark_submission(
        written_workspace,
        root,
        submission_output,
    )
    return {
        "workspaceWritten": str(written_workspace),
        "workspaceSha256": sha256_hex(workspace_content),
        **submission,
    }


def _private_output_file(root: Path, value: Path, field: str) -> Path:
    candidate = value.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ContractError(f"{field} cannot be a symbolic link.")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ContractError(f"{field} escapes input-root.") from error
    return resolved
