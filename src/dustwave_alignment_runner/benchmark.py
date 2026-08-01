from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from .contract import (
    MAX_REQUEST_BYTES,
    ContractError,
    ValidatedRequest,
    canonical_json_bytes,
    read_bounded_json,
    sha256_hex,
    validate_request,
)
from .result_contract import load_bound_result, write_immutable

BENCHMARK_WORKSPACE_SCHEMA = "alignment-benchmark-workspace-v1"
BENCHMARK_GOLD_SCHEMA = "alignment-benchmark-gold-v1"
BENCHMARK_PREVIEWS_SCHEMA = "alignment-benchmark-previews-v1"
BENCHMARK_RESOURCES_SCHEMA = "alignment-benchmark-resources-v1"
BENCHMARK_SUBMISSION_SCHEMA = "alignment-benchmark-submission-v1"
BENCHMARK_RUNNER_REPOSITORY = "aindaco1/dust-wave-alignment-runner"
BENCHMARK_RUNNER_REVISION = "e611801d2af82dcdb079444b7e8a7eea4309d1a6"
BENCHMARK_RUNNER_DIGEST = (
    "sha256:8a7cda2702487a1d542d5fb740efe8580ca9edd99f405d722d610536c73a3a11"
)
BENCHMARK_ADAPTERS: dict[str, dict[str, str]] = {
    "whisperx": {
        "name": "whisperx",
        "version": "3.8.6",
        "model": "default",
        "modelVersion": "default-en-es-v1",
        "settingsVersion": "whisperx-align-v1",
        "runnerDigest": BENCHMARK_RUNNER_DIGEST,
    },
    "stable-ts": {
        "name": "stable-ts",
        "version": "2.19.1",
        "model": "base",
        "modelVersion": "openai-whisper-base",
        "settingsVersion": "stable-ts-align-v1",
        "runnerDigest": BENCHMARK_RUNNER_DIGEST,
    },
}

MAXIMUM_BENCHMARK_BYTES = 8 * 1024 * 1024
MAXIMUM_FIXTURES = 64
MAXIMUM_TOTAL_WORDS = 25_000
MAXIMUM_WORDS_PER_FIXTURE = 2_000
MAXIMUM_PREVIEW_REVIEWS = 2_000
MAXIMUM_RESOURCE_RUNS = 20
MAXIMUM_IDEMPOTENCY_CHECKS = 128
MINIMUM_FIXTURE_DURATION_MS = 120_000
MAXIMUM_FIXTURE_DURATION_MS = 300_000
PLAIN_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UNALIGNED_REASON = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069<>]+$")
TIMING_ORIGINS = {"forced_alignment", "model", "editor", "interpolated"}


def build_benchmark_submission(
    manifest_path: Path,
    input_root: Path,
    output: Path,
) -> dict[str, Any]:
    root = input_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ContractError("input-root must be a directory.")
    manifest_file = _input_path(root, manifest_path, "manifest")
    workspace = read_bounded_json(manifest_file)
    _exact_keys(
        workspace,
        {
            "schemaVersion",
            "submissionId",
            "corpusVersion",
            "adapter",
            "fixtures",
            "previewReviewsPath",
            "resourceRunsPath",
            "cleanEnvironmentReproduced",
        },
        "benchmark workspace",
    )
    if workspace["schemaVersion"] != BENCHMARK_WORKSPACE_SCHEMA:
        raise ContractError("Unsupported benchmark workspace schemaVersion.")
    submission_id = _identifier(workspace["submissionId"], "submissionId")
    corpus_version = _identifier(workspace["corpusVersion"], "corpusVersion")
    adapter_name = workspace["adapter"]
    if adapter_name not in BENCHMARK_ADAPTERS:
        raise ContractError("adapter must be whisperx or stable-ts.")
    adapter = dict(BENCHMARK_ADAPTERS[adapter_name])
    clean_reproduced = _boolean(
        workspace["cleanEnvironmentReproduced"],
        "cleanEnvironmentReproduced",
    )

    fixture_descriptors = _bounded_array(
        workspace["fixtures"],
        "fixtures",
        MAXIMUM_FIXTURES,
    )
    if not fixture_descriptors:
        raise ContractError("fixtures must contain at least one fixture.")
    fixtures: list[dict[str, Any]] = []
    idempotency_checks: list[dict[str, Any]] = []
    seen_fixture_ids: set[str] = set()
    eligible_preview_keys: set[tuple[str, str]] = set()
    total_words = 0
    language_fixture_counts = {"en": 0, "es": 0}
    language_gold_word_counts = {"en": 0, "es": 0}
    for index, descriptor in enumerate(fixture_descriptors):
        fixture, idempotency = _build_fixture(
            root,
            descriptor,
            index,
            adapter,
        )
        fixture_id = fixture["fixtureId"]
        if fixture_id in seen_fixture_ids:
            raise ContractError("fixtureId values must be unique.")
        seen_fixture_ids.add(fixture_id)
        fixtures.append(fixture)
        idempotency_checks.append(idempotency)
        total_words += len(fixture["goldWords"]) + len(fixture["candidateWords"])
        if total_words > MAXIMUM_TOTAL_WORDS:
            raise ContractError("Benchmark exceeds its total word cap.")
        language = fixture["language"]
        language_fixture_counts[language] += 1
        scorable_words = [
            word for word in fixture["goldWords"] if word.get("scorable", True)
        ]
        language_gold_word_counts[language] += len(scorable_words)
        eligible_preview_keys.update(
            (fixture_id, word["wordId"]) for word in scorable_words
        )
    if len(idempotency_checks) > MAXIMUM_IDEMPOTENCY_CHECKS:
        raise ContractError("Benchmark exceeds its idempotency-check cap.")

    preview_reviews = _load_preview_reviews(
        root,
        workspace["previewReviewsPath"],
        eligible_preview_keys,
    )
    resource_runs = _load_resource_runs(
        root,
        workspace["resourceRunsPath"],
    )
    submission = {
        "schemaVersion": BENCHMARK_SUBMISSION_SCHEMA,
        "submissionId": submission_id,
        "runner": {
            "repository": BENCHMARK_RUNNER_REPOSITORY,
            "revision": BENCHMARK_RUNNER_REVISION,
        },
        "benchmark": {
            "corpusVersion": corpus_version,
            "adapter": adapter,
            "fixtures": fixtures,
            "previewReviews": preview_reviews,
            "resourceRuns": resource_runs,
            "idempotencyChecks": idempotency_checks,
            "cleanEnvironmentReproduced": clean_reproduced,
        },
    }
    content = canonical_json_bytes(submission) + b"\n"
    written = write_immutable(output, content, MAXIMUM_BENCHMARK_BYTES)
    return {
        "written": str(written),
        "submissionSha256": sha256_hex(content),
        "submissionBytes": len(content),
        "fixtureCount": len(fixtures),
        "englishFixtureCount": language_fixture_counts["en"],
        "spanishFixtureCount": language_fixture_counts["es"],
        "englishGoldWordCount": language_gold_word_counts["en"],
        "spanishGoldWordCount": language_gold_word_counts["es"],
        "previewReviewCount": len(preview_reviews),
        "resourceRunCount": len(resource_runs),
        "idempotencyCheckCount": len(idempotency_checks),
        "cleanEnvironmentReproduced": clean_reproduced,
    }


def _build_fixture(
    root: Path,
    value: Any,
    index: int,
    adapter: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    field = f"fixtures[{index}]"
    descriptor = _mapping(value, field)
    _exact_keys(
        descriptor,
        {
            "fixtureId",
            "requestPath",
            "resultPath",
            "replayResultPath",
            "goldPath",
            "duplicateBillableJobCreated",
        },
        field,
    )
    fixture_id = _identifier(descriptor["fixtureId"], f"{field}.fixtureId")
    request_path = _input_path(
        root,
        descriptor["requestPath"],
        f"{field}.requestPath",
    )
    request_payload = read_bounded_json(request_path)
    validated = validate_request(request_payload, root, adapter["name"])
    duration_ms = validated.payload["audio"]["durationMs"]
    if not MINIMUM_FIXTURE_DURATION_MS <= duration_ms <= MAXIMUM_FIXTURE_DURATION_MS:
        raise ContractError(
            f"{field} audio duration must be between two and five minutes."
        )
    request_adapter = validated.payload["adapter"]
    for key in ("name", "model", "modelVersion", "settingsVersion"):
        if request_adapter[key] != adapter[key]:
            raise ContractError(f"{field} request adapter identity is not pinned.")

    result_path = _input_path(
        root,
        descriptor["resultPath"],
        f"{field}.resultPath",
    )
    replay_result_path = _input_path(
        root,
        descriptor["replayResultPath"],
        f"{field}.replayResultPath",
    )
    if result_path == replay_result_path:
        raise ContractError(f"{field} result and replay paths must be distinct.")
    result = load_bound_result(
        result_path,
        validated,
        adapter["name"],
        adapter["runnerDigest"],
    )
    replay_result = load_bound_result(
        replay_result_path,
        validated,
        adapter["name"],
        adapter["runnerDigest"],
    )
    if result is None or replay_result is None:
        raise ContractError(f"{field} requires primary and replay result files.")
    _require_adapter_identity(result["manifest"], adapter, f"{field}.result")
    _require_adapter_identity(
        replay_result["manifest"],
        adapter,
        f"{field}.replayResult",
    )

    gold_path = _input_path(
        root,
        descriptor["goldPath"],
        f"{field}.goldPath",
    )
    gold_words = _load_gold_words(
        gold_path,
        fixture_id,
        validated,
        duration_ms,
    )
    primary_candidates = _selected_candidates(
        result["manifest"],
        gold_words,
        duration_ms,
        f"{field}.result",
    )
    replay_candidates = _selected_candidates(
        replay_result["manifest"],
        gold_words,
        duration_ms,
        f"{field}.replayResult",
    )
    semantic_stable, maximum_delta_ms = _compare_replay(
        primary_candidates,
        replay_candidates,
    )
    duplicate_billable = _boolean(
        descriptor["duplicateBillableJobCreated"],
        f"{field}.duplicateBillableJobCreated",
    )
    fixture = {
        "fixtureId": fixture_id,
        "language": validated.payload["language"],
        "audioDurationMs": duration_ms,
        "sourceAudioSha256": validated.payload["audio"]["sha256"],
        "transcriptRevisionSha256": validated.payload["transcript"]["contentSha256"],
        "resultManifestSha256": result["manifestSha256"],
        "goldWords": gold_words,
        "candidateWords": primary_candidates,
    }
    idempotency = {
        "fixtureId": fixture_id,
        "semanticOutputStable": semantic_stable,
        "maximumTimingDeltaMs": maximum_delta_ms,
        "duplicateBillableJobCreated": duplicate_billable,
    }
    return fixture, idempotency


def _load_gold_words(
    path: Path,
    fixture_id: str,
    validated: ValidatedRequest,
    duration_ms: int,
) -> list[dict[str, Any]]:
    payload = read_bounded_json(path)
    _exact_keys(
        payload,
        {"schemaVersion", "fixtureId", "goldWords"},
        f"{fixture_id} gold",
    )
    if payload["schemaVersion"] != BENCHMARK_GOLD_SCHEMA:
        raise ContractError(f"{fixture_id} gold schemaVersion is unsupported.")
    if payload["fixtureId"] != fixture_id:
        raise ContractError(f"{fixture_id} gold fixtureId does not match.")
    inputs = _bounded_array(
        payload["goldWords"],
        f"{fixture_id}.goldWords",
        MAXIMUM_WORDS_PER_FIXTURE,
    )
    if not inputs:
        raise ContractError(f"{fixture_id} goldWords must not be empty.")
    expected_words = {word.word_id: word for word in validated.words}
    expected_positions = {
        word.word_id: index for index, word in enumerate(validated.words)
    }
    seen_word_ids: set[str] = set()
    gold_words: list[dict[str, Any]] = []
    previous_position = -1
    previous_start = -1
    previous_end = -1
    for index, value in enumerate(inputs):
        field = f"{fixture_id}.goldWords[{index}]"
        word = _mapping(value, field)
        _exact_keys(
            word,
            {"wordId", "cueId", "text", "startsAtMs", "endsAtMs"},
            field,
            {"scorable"},
        )
        word_id = _identifier(word["wordId"], f"{field}.wordId")
        if word_id in seen_word_ids:
            raise ContractError(f"{fixture_id} gold word IDs must be unique.")
        seen_word_ids.add(word_id)
        expected = expected_words.get(word_id)
        if expected is None:
            raise ContractError(f"{field} is absent from the reviewed projection.")
        position = expected_positions[word_id]
        if position <= previous_position:
            raise ContractError(
                f"{fixture_id} gold words must preserve projection order."
            )
        previous_position = position
        cue_id = _identifier(word["cueId"], f"{field}.cueId")
        text = _lexical_text(word["text"], f"{field}.text")
        if cue_id != expected.cue_id or text != expected.text:
            raise ContractError(f"{field} does not match the reviewed projection.")
        starts_at_ms = _integer(
            word["startsAtMs"],
            0,
            duration_ms - 1,
            f"{field}.startsAtMs",
        )
        ends_at_ms = _integer(
            word["endsAtMs"],
            starts_at_ms + 1,
            duration_ms,
            f"{field}.endsAtMs",
        )
        if starts_at_ms < previous_start or ends_at_ms < previous_end:
            raise ContractError(f"{fixture_id} gold intervals must be monotonic.")
        previous_start = starts_at_ms
        previous_end = ends_at_ms
        gold = {
            "wordId": word_id,
            "cueId": cue_id,
            "text": text,
            "startsAtMs": starts_at_ms,
            "endsAtMs": ends_at_ms,
        }
        if "scorable" in word:
            gold["scorable"] = _boolean(
                word["scorable"],
                f"{field}.scorable",
            )
        gold_words.append(gold)
    return gold_words


def _selected_candidates(
    manifest: dict[str, Any],
    gold_words: list[dict[str, Any]],
    duration_ms: int,
    field: str,
) -> list[dict[str, Any]]:
    candidates = manifest.get("candidateWords")
    if not isinstance(candidates, list):
        raise ContractError(f"{field}.candidateWords must be an array.")
    indexed: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(candidates):
        candidate = _mapping(value, f"{field}.candidateWords[{index}]")
        word_id = candidate.get("wordId")
        if isinstance(word_id, str):
            indexed[word_id] = candidate
    selected: list[dict[str, Any]] = []
    for gold in gold_words:
        word_id = gold["wordId"]
        candidate = indexed.get(word_id)
        if candidate is None:
            raise ContractError(f"{field} omits gold word {word_id}.")
        candidate_field = f"{field}.candidateWords[{word_id}]"
        _exact_keys(
            candidate,
            {
                "wordId",
                "cueId",
                "text",
                "startsAtMs",
                "endsAtMs",
                "confidence",
                "timingOrigin",
                "unalignedReason",
            },
            candidate_field,
        )
        if (
            candidate["wordId"] != word_id
            or candidate["cueId"] != gold["cueId"]
            or candidate["text"] != gold["text"]
        ):
            raise ContractError(
                f"{candidate_field} does not match the reviewed projection."
            )
        starts_at_ms = _nullable_integer(
            candidate["startsAtMs"],
            -duration_ms,
            duration_ms * 2,
            f"{candidate_field}.startsAtMs",
        )
        ends_at_ms = _nullable_integer(
            candidate["endsAtMs"],
            -duration_ms,
            duration_ms * 2,
            f"{candidate_field}.endsAtMs",
        )
        confidence = _nullable_number(
            candidate["confidence"],
            -1,
            2,
            f"{candidate_field}.confidence",
        )
        timing_origin = candidate["timingOrigin"]
        if timing_origin is not None and timing_origin not in TIMING_ORIGINS:
            raise ContractError(f"{candidate_field}.timingOrigin is invalid.")
        unaligned_reason = candidate["unalignedReason"]
        if unaligned_reason is not None and (
            not isinstance(unaligned_reason, str)
            or not UNALIGNED_REASON.fullmatch(unaligned_reason)
        ):
            raise ContractError(f"{candidate_field}.unalignedReason is invalid.")
        selected.append(
            {
                "wordId": word_id,
                "cueId": gold["cueId"],
                "text": gold["text"],
                "startsAtMs": starts_at_ms,
                "endsAtMs": ends_at_ms,
                "confidence": confidence,
                "timingOrigin": timing_origin,
                "unalignedReason": unaligned_reason,
            }
        )
    return selected


def _compare_replay(
    primary: list[dict[str, Any]],
    replay: list[dict[str, Any]],
) -> tuple[bool, int]:
    semantic_fields = (
        "wordId",
        "cueId",
        "text",
        "timingOrigin",
        "unalignedReason",
    )
    semantic_stable = len(primary) == len(replay) and all(
        all(left[field] == right[field] for field in semantic_fields)
        for left, right in zip(primary, replay, strict=True)
    )
    maximum_delta = 0
    for left, right in zip(primary, replay, strict=False):
        for field in ("startsAtMs", "endsAtMs"):
            left_value = left[field]
            right_value = right[field]
            if left_value is None and right_value is None:
                continue
            if left_value is None or right_value is None:
                maximum_delta = 60_000
                continue
            maximum_delta = max(maximum_delta, abs(left_value - right_value))
    return semantic_stable, min(maximum_delta, 60_000)


def _load_preview_reviews(
    root: Path,
    reference: Any,
    eligible_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    path = _input_path(root, reference, "previewReviewsPath")
    payload = read_bounded_json(path)
    _exact_keys(payload, {"schemaVersion", "reviews"}, "preview reviews")
    if payload["schemaVersion"] != BENCHMARK_PREVIEWS_SCHEMA:
        raise ContractError("Preview review schemaVersion is unsupported.")
    values = _bounded_array(
        payload["reviews"],
        "preview reviews",
        MAXIMUM_PREVIEW_REVIEWS,
    )
    reviews: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, value in enumerate(values):
        field = f"previewReviews[{index}]"
        review = _mapping(value, field)
        _exact_keys(
            review,
            {"fixtureId", "wordId", "acceptedWithoutClipping"},
            field,
        )
        fixture_id = _identifier(review["fixtureId"], f"{field}.fixtureId")
        word_id = _identifier(review["wordId"], f"{field}.wordId")
        key = (fixture_id, word_id)
        if key not in eligible_keys:
            raise ContractError(f"{field} does not identify a scorable gold word.")
        if key in seen:
            raise ContractError("Preview review fixture/word pairs must be unique.")
        seen.add(key)
        reviews.append(
            {
                "fixtureId": fixture_id,
                "wordId": word_id,
                "acceptedWithoutClipping": _boolean(
                    review["acceptedWithoutClipping"],
                    f"{field}.acceptedWithoutClipping",
                ),
            }
        )
    return reviews


def _load_resource_runs(root: Path, reference: Any) -> list[dict[str, Any]]:
    path = _input_path(root, reference, "resourceRunsPath")
    payload = read_bounded_json(path)
    _exact_keys(payload, {"schemaVersion", "runs"}, "resource runs")
    if payload["schemaVersion"] != BENCHMARK_RESOURCES_SCHEMA:
        raise ContractError("Resource run schemaVersion is unsupported.")
    values = _bounded_array(
        payload["runs"],
        "resource runs",
        MAXIMUM_RESOURCE_RUNS,
    )
    runs: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        field = f"resourceRuns[{index}]"
        run = _mapping(value, field)
        _exact_keys(
            run,
            {
                "language",
                "inputDurationMinutes",
                "wallClockMinutes",
                "peakMemoryMb",
                "peakDiskMb",
                "runner",
            },
            field,
        )
        language = run["language"]
        if language not in {"en", "es"}:
            raise ContractError(f"{field}.language must be en or es.")
        runs.append(
            {
                "language": language,
                "inputDurationMinutes": _number(
                    run["inputDurationMinutes"],
                    0,
                    10_000,
                    f"{field}.inputDurationMinutes",
                ),
                "wallClockMinutes": _number(
                    run["wallClockMinutes"],
                    0,
                    10_000,
                    f"{field}.wallClockMinutes",
                ),
                "peakMemoryMb": _number(
                    run["peakMemoryMb"],
                    0,
                    1_000_000,
                    f"{field}.peakMemoryMb",
                ),
                "peakDiskMb": _number(
                    run["peakDiskMb"],
                    0,
                    1_000_000,
                    f"{field}.peakDiskMb",
                ),
                "runner": _safe_text(run["runner"], 200, f"{field}.runner"),
            }
        )
    return runs


def _require_adapter_identity(
    manifest: dict[str, Any],
    expected: dict[str, str],
    field: str,
) -> None:
    adapter = _mapping(manifest.get("adapter"), f"{field}.adapter")
    if adapter != expected:
        raise ContractError(f"{field} adapter identity is not pinned.")


def _input_path(root: Path, reference: Any, field: str) -> Path:
    if isinstance(reference, Path):
        path = reference.expanduser()
    elif isinstance(reference, str) and 0 < len(reference) <= 1_024:
        path = Path(reference)
    else:
        raise ContractError(f"{field} must be a bounded file path.")
    candidate = path if path.is_absolute() else root / path
    if candidate.is_symlink():
        raise ContractError(f"{field} cannot be a symbolic link.")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ContractError(f"{field} escapes input-root.") from error
    if not resolved.is_file():
        raise ContractError(f"{field} must be a regular file.")
    if not 0 < resolved.stat().st_size <= MAX_REQUEST_BYTES:
        raise ContractError(f"{field} exceeds its bounded size.")
    return resolved


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{field} must be an object.")
    return value


def _exact_keys(
    value: dict[str, Any],
    required: set[str],
    field: str,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    keys = set(value)
    if not required <= keys or not keys <= required | optional:
        raise ContractError(f"{field} contains missing or unknown fields.")


def _bounded_array(value: Any, field: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(f"{field} exceeds its bounded array contract.")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not PLAIN_IDENTIFIER.fullmatch(value):
        raise ContractError(f"{field} has an invalid identifier.")
    return value


def _lexical_text(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= 500
        or not value.strip()
        or not SAFE_TEXT.fullmatch(value)
    ):
        raise ContractError(f"{field} must be bounded lexical text.")
    return value


def _safe_text(value: Any, maximum: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= maximum
        or not value.strip()
        or not SAFE_TEXT.fullmatch(value)
    ):
        raise ContractError(f"{field} must be bounded safe text.")
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{field} must be a boolean.")
    return value


def _integer(value: Any, minimum: int, maximum: int, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ContractError(f"{field} is outside its integer bounds.")
    return value


def _nullable_integer(
    value: Any,
    minimum: int,
    maximum: int,
    field: str,
) -> int | None:
    if value is None:
        return None
    return _integer(value, minimum, maximum, field)


def _number(value: Any, minimum: float, maximum: float, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
        or value > maximum
    ):
        raise ContractError(f"{field} is outside its numeric bounds.")
    return value


def _nullable_number(
    value: Any,
    minimum: float,
    maximum: float,
    field: str,
) -> float | None:
    if value is None:
        return None
    return _number(value, minimum, maximum, field)
