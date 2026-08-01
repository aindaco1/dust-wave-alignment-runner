from __future__ import annotations

from pathlib import Path
from typing import Any

from .benchmark import (
    BENCHMARK_ADAPTERS,
    BENCHMARK_RUNNER_REPOSITORY,
    BENCHMARK_RUNNER_REVISION,
    MAXIMUM_FIXTURE_DURATION_MS,
    MAXIMUM_FIXTURES,
    MINIMUM_FIXTURE_DURATION_MS,
    _boolean,
    _bounded_array,
    _exact_keys,
    _identifier,
    _input_path,
    _integer,
    _lexical_text,
    _mapping,
    _require_adapter_identity,
    _validated_candidates,
)
from .contract import (
    SHA256,
    ContractError,
    canonical_json_bytes,
    file_sha256,
    read_bounded_json,
    sha256_hex,
    validate_request,
)
from .result_contract import load_bound_result, write_immutable

BENCHMARK_REVIEW_WORKSPACE_SCHEMA = "alignment-benchmark-review-workspace-v1"
BENCHMARK_REVIEW_PACKET_SCHEMA = "alignment-benchmark-review-packet-v1"
BENCHMARK_REVIEW_COMPLETION_SCHEMA = "alignment-benchmark-review-completion-v1"
BENCHMARK_REVIEW_MATERIALIZATION_SCHEMA = (
    "alignment-benchmark-review-materialization-v1"
)

MAXIMUM_REVIEW_PACKET_BYTES = 8 * 1024 * 1024
GOLD_REVIEW_TARGET_PER_LANGUAGE = 500
PREVIEW_REVIEW_TARGET_PER_LANGUAGE = 60


def build_benchmark_review_packet(
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
        {"schemaVersion", "adapter", "fixtures"},
        "benchmark review workspace",
    )
    if workspace["schemaVersion"] != BENCHMARK_REVIEW_WORKSPACE_SCHEMA:
        raise ContractError("Unsupported benchmark review workspace schemaVersion.")
    adapter_name = workspace["adapter"]
    if adapter_name not in BENCHMARK_ADAPTERS:
        raise ContractError("adapter must be whisperx or stable-ts.")
    adapter = dict(BENCHMARK_ADAPTERS[adapter_name])
    descriptors = _bounded_array(
        workspace["fixtures"],
        "fixtures",
        MAXIMUM_FIXTURES,
    )
    if not descriptors:
        raise ContractError("fixtures must contain at least one fixture.")

    fixtures: list[dict[str, Any]] = []
    eligible_by_fixture: list[list[dict[str, Any]]] = []
    seen_fixture_ids: set[str] = set()
    for index, value in enumerate(descriptors):
        field = f"fixtures[{index}]"
        descriptor = _mapping(value, field)
        _exact_keys(
            descriptor,
            {"fixtureId", "requestPath", "resultPath"},
            field,
        )
        fixture_id = _identifier(descriptor["fixtureId"], f"{field}.fixtureId")
        if fixture_id in seen_fixture_ids:
            raise ContractError("fixtureId values must be unique.")
        seen_fixture_ids.add(fixture_id)
        request_path = _input_path(
            root,
            descriptor["requestPath"],
            f"{field}.requestPath",
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
        result = load_bound_result(
            result_path,
            validated,
            adapter_name,
            adapter["runnerDigest"],
        )
        if result is None:
            raise ContractError(f"{field} requires a primary result file.")
        _require_adapter_identity(result["manifest"], adapter, f"{field}.result")
        candidates = _validated_candidates(
            result["manifest"],
            validated,
            duration_ms,
            f"{field}.result",
        )
        eligible = [
            candidate
            for candidate in candidates
            if _eligible_review_candidate(candidate, duration_ms)
        ]
        if not eligible:
            raise ContractError(f"{field} has no eligible aligned review words.")
        fixtures.append(
            {
                "fixtureId": fixture_id,
                "language": validated.payload["language"],
                "audioDurationMs": duration_ms,
                "sourceAudioSha256": validated.payload["audio"]["sha256"],
                "transcriptContentSha256": validated.payload["transcript"][
                    "contentSha256"
                ],
                "transcriptProjectionSha256": validated.payload["transcript"][
                    "projectionSha256"
                ],
                "resultManifestSha256": result["manifestSha256"],
                "reviewWords": [],
            }
        )
        eligible_by_fixture.append(eligible)

    selected_indexes: dict[int, list[int]] = {}
    for language in ("en", "es"):
        language_indexes = [
            index
            for index, fixture in enumerate(fixtures)
            if fixture["language"] == language
        ]
        selected_indexes.update(
            _balanced_selection(
                eligible_by_fixture,
                language_indexes,
                GOLD_REVIEW_TARGET_PER_LANGUAGE,
            )
        )

    selected_by_fixture: list[list[dict[str, Any]]] = []
    for fixture_index, fixture in enumerate(fixtures):
        selected = [
            eligible_by_fixture[fixture_index][candidate_index]
            for candidate_index in selected_indexes.get(fixture_index, [])
        ]
        selected_by_fixture.append(selected)
        fixture["reviewWords"] = [
            _review_word(candidate, preview_required=False) for candidate in selected
        ]

    preview_indexes: dict[int, list[int]] = {}
    for language in ("en", "es"):
        language_indexes = [
            index
            for index, fixture in enumerate(fixtures)
            if fixture["language"] == language
        ]
        preview_indexes.update(
            _balanced_selection(
                selected_by_fixture,
                language_indexes,
                PREVIEW_REVIEW_TARGET_PER_LANGUAGE,
            )
        )
    for fixture_index, indexes in preview_indexes.items():
        required = set(indexes)
        fixture_words = fixtures[fixture_index]["reviewWords"]
        for word_index, word in enumerate(fixture_words):
            word["previewReviewRequired"] = word_index in required

    packet = {
        "schemaVersion": BENCHMARK_REVIEW_PACKET_SCHEMA,
        "runner": {
            "repository": BENCHMARK_RUNNER_REPOSITORY,
            "revision": BENCHMARK_RUNNER_REVISION,
        },
        "adapter": adapter,
        "selectionPolicy": {
            "goldTargetPerLanguage": GOLD_REVIEW_TARGET_PER_LANGUAGE,
            "previewTargetPerLanguage": PREVIEW_REVIEW_TARGET_PER_LANGUAGE,
        },
        "fixtures": fixtures,
    }
    content = canonical_json_bytes(packet) + b"\n"
    written = write_immutable(output, content, MAXIMUM_REVIEW_PACKET_BYTES)
    language_counts = _language_review_counts(fixtures)
    preview_counts = _language_preview_counts(fixtures)
    return {
        "written": str(written),
        "packetSha256": sha256_hex(content),
        "packetBytes": len(content),
        "fixtureCount": len(fixtures),
        "englishReviewWordCount": language_counts["en"],
        "spanishReviewWordCount": language_counts["es"],
        "englishPreviewReviewCount": preview_counts["en"],
        "spanishPreviewReviewCount": preview_counts["es"],
        "englishReviewWordShortfall": max(
            0, GOLD_REVIEW_TARGET_PER_LANGUAGE - language_counts["en"]
        ),
        "spanishReviewWordShortfall": max(
            0, GOLD_REVIEW_TARGET_PER_LANGUAGE - language_counts["es"]
        ),
    }


def materialize_benchmark_review(
    packet_path: Path,
    completion_path: Path,
    input_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    root = input_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ContractError("input-root must be a directory.")
    packet_file = _input_path(root, packet_path, "packet")
    completion_file = _input_path(root, completion_path, "completion")
    packet = read_bounded_json(packet_file)
    completion = read_bounded_json(completion_file)
    fixtures = _validated_review_packet(packet)
    _exact_keys(
        completion,
        {"schemaVersion", "packetSha256", "reviews"},
        "benchmark review completion",
    )
    if completion["schemaVersion"] != BENCHMARK_REVIEW_COMPLETION_SCHEMA:
        raise ContractError("Unsupported benchmark review completion schemaVersion.")
    packet_sha256 = file_sha256(packet_file)
    if completion["packetSha256"] != packet_sha256:
        raise ContractError("Review completion is bound to a different packet.")

    expected: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    for fixture in fixtures:
        for word in fixture["reviewWords"]:
            expected[(fixture["fixtureId"], word["wordId"])] = (fixture, word)
    values = _bounded_array(
        completion["reviews"],
        "reviews",
        len(expected),
    )
    if len(values) != len(expected):
        raise ContractError(
            "Review completion must decide every packet word exactly once."
        )

    reviews: dict[tuple[str, str], dict[str, Any]] = {}
    for index, value in enumerate(values):
        field = f"reviews[{index}]"
        review = _mapping(value, field)
        _exact_keys(
            review,
            {
                "fixtureId",
                "wordId",
                "startsAtMs",
                "endsAtMs",
                "scorable",
                "acceptedWithoutClipping",
            },
            field,
        )
        fixture_id = _identifier(review["fixtureId"], f"{field}.fixtureId")
        word_id = _identifier(review["wordId"], f"{field}.wordId")
        key = (fixture_id, word_id)
        if key not in expected:
            raise ContractError(f"{field} is absent from the review packet.")
        if key in reviews:
            raise ContractError("Review completion fixture/word pairs must be unique.")
        fixture, word = expected[key]
        starts_at_ms = _integer(
            review["startsAtMs"],
            0,
            fixture["audioDurationMs"] - 1,
            f"{field}.startsAtMs",
        )
        ends_at_ms = _integer(
            review["endsAtMs"],
            starts_at_ms + 1,
            fixture["audioDurationMs"],
            f"{field}.endsAtMs",
        )
        scorable = _boolean(review["scorable"], f"{field}.scorable")
        accepted = review["acceptedWithoutClipping"]
        if word["previewReviewRequired"] and scorable:
            accepted = _boolean(accepted, f"{field}.acceptedWithoutClipping")
        elif accepted is not None:
            raise ContractError(
                f"{field}.acceptedWithoutClipping must be null when not applicable."
            )
        reviews[key] = {
            "startsAtMs": starts_at_ms,
            "endsAtMs": ends_at_ms,
            "scorable": scorable,
            "acceptedWithoutClipping": accepted,
        }

    gold_payloads: list[tuple[dict[str, Any], dict[str, Any]]] = []
    preview_reviews: list[dict[str, Any]] = []
    scorable_counts = {"en": 0, "es": 0}
    previous_intervals: dict[str, tuple[int, int]] = {}
    for fixture in fixtures:
        gold_words: list[dict[str, Any]] = []
        for word in fixture["reviewWords"]:
            decision = reviews[(fixture["fixtureId"], word["wordId"])]
            previous_start, previous_end = previous_intervals.get(
                fixture["fixtureId"], (-1, -1)
            )
            if (
                decision["startsAtMs"] < previous_start
                or decision["endsAtMs"] < previous_end
            ):
                raise ContractError(
                    f"{fixture['fixtureId']} review intervals must be monotonic."
                )
            previous_intervals[fixture["fixtureId"]] = (
                decision["startsAtMs"],
                decision["endsAtMs"],
            )
            gold_words.append(
                {
                    "wordId": word["wordId"],
                    "cueId": word["cueId"],
                    "text": word["text"],
                    "startsAtMs": decision["startsAtMs"],
                    "endsAtMs": decision["endsAtMs"],
                    "scorable": decision["scorable"],
                }
            )
            if decision["scorable"]:
                scorable_counts[fixture["language"]] += 1
            if word["previewReviewRequired"] and decision["scorable"]:
                preview_reviews.append(
                    {
                        "fixtureId": fixture["fixtureId"],
                        "wordId": word["wordId"],
                        "acceptedWithoutClipping": decision["acceptedWithoutClipping"],
                    }
                )
        gold_payloads.append(
            (
                fixture,
                {
                    "schemaVersion": "alignment-benchmark-gold-v1",
                    "fixtureId": fixture["fixtureId"],
                    "goldWords": gold_words,
                },
            )
        )

    destination = _private_output_root(root, output_root)
    written_gold: list[dict[str, Any]] = []
    for fixture, payload in gold_payloads:
        content = canonical_json_bytes(payload) + b"\n"
        path = write_immutable(
            destination / "gold" / f"{fixture['fixtureId']}.json",
            content,
            MAXIMUM_REVIEW_PACKET_BYTES,
        )
        written_gold.append(
            {
                "fixtureId": fixture["fixtureId"],
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_hex(content),
            }
        )
    preview_payload = {
        "schemaVersion": "alignment-benchmark-previews-v1",
        "reviews": preview_reviews,
    }
    preview_content = canonical_json_bytes(preview_payload) + b"\n"
    preview_path = write_immutable(
        destination / "reviews" / "previews.json",
        preview_content,
        MAXIMUM_REVIEW_PACKET_BYTES,
    )
    materialization = {
        "schemaVersion": BENCHMARK_REVIEW_MATERIALIZATION_SCHEMA,
        "packetSha256": packet_sha256,
        "goldFiles": written_gold,
        "previewReviewsPath": preview_path.relative_to(root).as_posix(),
        "previewReviewsSha256": sha256_hex(preview_content),
    }
    materialization_content = canonical_json_bytes(materialization) + b"\n"
    materialization_path = write_immutable(
        destination / "materialization.json",
        materialization_content,
        MAXIMUM_REVIEW_PACKET_BYTES,
    )
    accepted_count = sum(
        1 for review in preview_reviews if review["acceptedWithoutClipping"]
    )
    return {
        "written": str(materialization_path),
        "materializationSha256": sha256_hex(materialization_content),
        "goldFileCount": len(written_gold),
        "englishScorableGoldWordCount": scorable_counts["en"],
        "spanishScorableGoldWordCount": scorable_counts["es"],
        "previewReviewCount": len(preview_reviews),
        "previewAcceptedWithoutClippingCount": accepted_count,
    }


def _eligible_review_candidate(candidate: dict[str, Any], duration_ms: int) -> bool:
    starts_at_ms = candidate["startsAtMs"]
    ends_at_ms = candidate["endsAtMs"]
    return (
        isinstance(starts_at_ms, int)
        and not isinstance(starts_at_ms, bool)
        and isinstance(ends_at_ms, int)
        and not isinstance(ends_at_ms, bool)
        and 0 <= starts_at_ms < ends_at_ms <= duration_ms
        and candidate["timingOrigin"] in {"forced_alignment", "model", "editor"}
        and candidate["unalignedReason"] is None
    )


def _balanced_selection(
    candidates_by_fixture: list[list[dict[str, Any]]],
    fixture_indexes: list[int],
    target: int,
) -> dict[int, list[int]]:
    allocations = {index: 0 for index in fixture_indexes}
    remaining = min(
        target,
        sum(len(candidates_by_fixture[index]) for index in fixture_indexes),
    )
    while remaining:
        progressed = False
        for fixture_index in fixture_indexes:
            if allocations[fixture_index] >= len(candidates_by_fixture[fixture_index]):
                continue
            allocations[fixture_index] += 1
            remaining -= 1
            progressed = True
            if not remaining:
                break
        if not progressed:
            break
    return {
        fixture_index: _evenly_spaced_indexes(
            len(candidates_by_fixture[fixture_index]), count
        )
        for fixture_index, count in allocations.items()
    }


def _evenly_spaced_indexes(length: int, count: int) -> list[int]:
    if count <= 0:
        return []
    if count >= length:
        return list(range(length))
    if count == 1:
        return [length // 2]
    return [index * (length - 1) // (count - 1) for index in range(count)]


def _review_word(
    candidate: dict[str, Any],
    *,
    preview_required: bool,
) -> dict[str, Any]:
    return {
        "wordId": candidate["wordId"],
        "cueId": candidate["cueId"],
        "text": candidate["text"],
        "candidateStartsAtMs": candidate["startsAtMs"],
        "candidateEndsAtMs": candidate["endsAtMs"],
        "confidence": candidate["confidence"],
        "timingOrigin": candidate["timingOrigin"],
        "previewReviewRequired": preview_required,
    }


def _validated_review_packet(packet: dict[str, Any]) -> list[dict[str, Any]]:
    _exact_keys(
        packet,
        {"schemaVersion", "runner", "adapter", "selectionPolicy", "fixtures"},
        "benchmark review packet",
    )
    if packet["schemaVersion"] != BENCHMARK_REVIEW_PACKET_SCHEMA:
        raise ContractError("Unsupported benchmark review packet schemaVersion.")
    runner = _mapping(packet["runner"], "runner")
    _exact_keys(runner, {"repository", "revision"}, "runner")
    if runner != {
        "repository": BENCHMARK_RUNNER_REPOSITORY,
        "revision": BENCHMARK_RUNNER_REVISION,
    }:
        raise ContractError("Review packet runner identity is not pinned.")
    if packet["adapter"] not in BENCHMARK_ADAPTERS.values():
        raise ContractError("Review packet adapter identity is not pinned.")
    policy = _mapping(packet["selectionPolicy"], "selectionPolicy")
    _exact_keys(
        policy,
        {"goldTargetPerLanguage", "previewTargetPerLanguage"},
        "selectionPolicy",
    )
    if policy != {
        "goldTargetPerLanguage": GOLD_REVIEW_TARGET_PER_LANGUAGE,
        "previewTargetPerLanguage": PREVIEW_REVIEW_TARGET_PER_LANGUAGE,
    }:
        raise ContractError("Review packet selection policy is not pinned.")
    fixtures = _bounded_array(packet["fixtures"], "fixtures", MAXIMUM_FIXTURES)
    if not fixtures:
        raise ContractError("Review packet fixtures must not be empty.")
    seen_fixtures: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for fixture_index, value in enumerate(fixtures):
        field = f"fixtures[{fixture_index}]"
        fixture = _mapping(value, field)
        _exact_keys(
            fixture,
            {
                "fixtureId",
                "language",
                "audioDurationMs",
                "sourceAudioSha256",
                "transcriptContentSha256",
                "transcriptProjectionSha256",
                "resultManifestSha256",
                "reviewWords",
            },
            field,
        )
        fixture_id = _identifier(fixture["fixtureId"], f"{field}.fixtureId")
        if fixture_id in seen_fixtures:
            raise ContractError("Review packet fixtureId values must be unique.")
        seen_fixtures.add(fixture_id)
        if fixture["language"] not in {"en", "es"}:
            raise ContractError(f"{field}.language must be en or es.")
        duration_ms = _integer(
            fixture["audioDurationMs"],
            MINIMUM_FIXTURE_DURATION_MS,
            MAXIMUM_FIXTURE_DURATION_MS,
            f"{field}.audioDurationMs",
        )
        for digest_field in (
            "sourceAudioSha256",
            "transcriptContentSha256",
            "transcriptProjectionSha256",
            "resultManifestSha256",
        ):
            digest = fixture[digest_field]
            if not isinstance(digest, str) or not SHA256.fullmatch(digest):
                raise ContractError(f"{field}.{digest_field} is invalid.")
        words = _bounded_array(
            fixture["reviewWords"],
            f"{field}.reviewWords",
            GOLD_REVIEW_TARGET_PER_LANGUAGE,
        )
        if not words:
            raise ContractError(f"{field}.reviewWords must not be empty.")
        seen_words: set[str] = set()
        normalized_words: list[dict[str, Any]] = []
        for word_index, word_value in enumerate(words):
            word_field = f"{field}.reviewWords[{word_index}]"
            word = _mapping(word_value, word_field)
            _exact_keys(
                word,
                {
                    "wordId",
                    "cueId",
                    "text",
                    "candidateStartsAtMs",
                    "candidateEndsAtMs",
                    "confidence",
                    "timingOrigin",
                    "previewReviewRequired",
                },
                word_field,
            )
            word_id = _identifier(word["wordId"], f"{word_field}.wordId")
            if word_id in seen_words:
                raise ContractError(f"{field} review word IDs must be unique.")
            seen_words.add(word_id)
            cue_id = _identifier(word["cueId"], f"{word_field}.cueId")
            text = _lexical_text(word["text"], f"{word_field}.text")
            starts_at_ms = _integer(
                word["candidateStartsAtMs"],
                0,
                duration_ms - 1,
                f"{word_field}.candidateStartsAtMs",
            )
            ends_at_ms = _integer(
                word["candidateEndsAtMs"],
                starts_at_ms + 1,
                duration_ms,
                f"{word_field}.candidateEndsAtMs",
            )
            if word["timingOrigin"] not in {"forced_alignment", "model", "editor"}:
                raise ContractError(f"{word_field}.timingOrigin is invalid.")
            confidence = word["confidence"]
            if confidence is not None and (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not -1 <= confidence <= 2
            ):
                raise ContractError(f"{word_field}.confidence is invalid.")
            normalized_words.append(
                {
                    "wordId": word_id,
                    "cueId": cue_id,
                    "text": text,
                    "candidateStartsAtMs": starts_at_ms,
                    "candidateEndsAtMs": ends_at_ms,
                    "confidence": confidence,
                    "timingOrigin": word["timingOrigin"],
                    "previewReviewRequired": _boolean(
                        word["previewReviewRequired"],
                        f"{word_field}.previewReviewRequired",
                    ),
                }
            )
        normalized.append(
            {
                **fixture,
                "fixtureId": fixture_id,
                "audioDurationMs": duration_ms,
                "reviewWords": normalized_words,
            }
        )
    return normalized


def _private_output_root(root: Path, output_root: Path) -> Path:
    candidate = output_root.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ContractError("output-root escapes input-root.") from error
    if resolved.is_symlink():
        raise ContractError("output-root cannot be a symbolic link.")
    return resolved


def _language_review_counts(fixtures: list[dict[str, Any]]) -> dict[str, int]:
    return {
        language: sum(
            len(fixture["reviewWords"])
            for fixture in fixtures
            if fixture["language"] == language
        )
        for language in ("en", "es")
    }


def _language_preview_counts(fixtures: list[dict[str, Any]]) -> dict[str, int]:
    return {
        language: sum(
            1
            for fixture in fixtures
            if fixture["language"] == language
            for word in fixture["reviewWords"]
            if word["previewReviewRequired"]
        )
        for language in ("en", "es")
    }
