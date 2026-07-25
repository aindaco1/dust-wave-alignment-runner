from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 4 * 1024 * 1024 * 1024
MAX_AUDIO_DURATION_MS = 24 * 60 * 60 * 1000
MAX_CUES = 20_000
MAX_WORDS = 25_000
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
RUNNER_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
MODEL_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class ContractError(ValueError):
    """Raised when untrusted runner input violates the bounded contract."""


@dataclass(frozen=True)
class TranscriptWord:
    word_id: str
    cue_id: str
    text: str


@dataclass(frozen=True)
class ValidatedRequest:
    payload: dict[str, Any]
    audio_path: Path
    words: tuple[TranscriptWord, ...]


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_lexical_word(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value))
    return "".join(
        character.lower()
        for character in decomposed
        if not unicodedata.combining(character)
        and unicodedata.category(character)[0] in {"L", "N"}
    )


def read_bounded_json(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    size = resolved.stat().st_size
    if size <= 0 or size > MAX_REQUEST_BYTES:
        raise ContractError("Request JSON exceeds its bounded size.")
    try:
        parsed = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError("Request JSON is unreadable or invalid.") from error
    if not isinstance(parsed, dict):
        raise ContractError("Request JSON must be an object.")
    return parsed


def validate_request(
    payload: dict[str, Any],
    input_root: Path,
    adapter_name: str,
) -> ValidatedRequest:
    _exact_keys(
        payload,
        {
            "schemaVersion",
            "jobId",
            "alignmentRevisionId",
            "language",
            "audio",
            "transcript",
            "adapter",
        },
        "request",
    )
    if payload.get("schemaVersion") != "2":
        raise ContractError("Unsupported request schemaVersion.")
    _identifier(payload.get("jobId"), "jobId")
    _identifier(payload.get("alignmentRevisionId"), "alignmentRevisionId")
    if payload.get("language") not in {"en", "es"}:
        raise ContractError("language must be en or es.")

    audio = _mapping(payload.get("audio"), "audio")
    _exact_keys(audio, {"path", "sha256", "durationMs"}, "audio")
    raw_audio_path = audio.get("path")
    if not isinstance(raw_audio_path, str) or not raw_audio_path.strip():
        raise ContractError("audio.path is required.")
    if len(raw_audio_path) > 1_024:
        raise ContractError("audio.path is too long.")
    root = input_root.resolve(strict=True)
    audio_path = (root / raw_audio_path).resolve(strict=True)
    try:
        audio_path.relative_to(root)
    except ValueError as error:
        raise ContractError("audio.path escapes input-root.") from error
    if not audio_path.is_file():
        raise ContractError("audio.path must resolve to a regular file.")
    audio_size = audio_path.stat().st_size
    if audio_size <= 0 or audio_size > MAX_AUDIO_BYTES:
        raise ContractError("Audio size is outside the supported bounds.")
    audio_sha256 = audio.get("sha256")
    if not isinstance(audio_sha256, str) or not SHA256.fullmatch(audio_sha256):
        raise ContractError("audio.sha256 must be lowercase SHA-256.")
    if file_sha256(audio_path) != audio_sha256:
        raise ContractError("Audio SHA-256 does not match the request.")
    duration_ms = _positive_integer(audio.get("durationMs"), "audio.durationMs")
    if duration_ms > MAX_AUDIO_DURATION_MS:
        raise ContractError("Audio duration exceeds 24 hours.")

    transcript = _mapping(payload.get("transcript"), "transcript")
    _exact_keys(
        transcript,
        {"contentSha256", "projectionSha256", "cues"},
        "transcript",
    )
    content_sha256 = transcript.get("contentSha256")
    if not isinstance(content_sha256, str) or not SHA256.fullmatch(content_sha256):
        raise ContractError("transcript.contentSha256 must be lowercase SHA-256.")
    projection_sha256 = transcript.get("projectionSha256")
    if not isinstance(projection_sha256, str) or not SHA256.fullmatch(
        projection_sha256
    ):
        raise ContractError("transcript.projectionSha256 must be lowercase SHA-256.")
    cues = transcript.get("cues")
    if not isinstance(cues, list) or not 1 <= len(cues) <= MAX_CUES:
        raise ContractError(f"transcript.cues must contain 1-{MAX_CUES} cues.")
    if sha256_hex(canonical_json_bytes(cues)) != projection_sha256:
        raise ContractError(
            "Transcript projection SHA-256 does not match canonical cues."
        )

    cue_ids: set[str] = set()
    word_ids: set[str] = set()
    words: list[TranscriptWord] = []
    prior_cue_start = -1
    for cue in cues:
        cue_value = _mapping(cue, "cue")
        _exact_keys(cue_value, {"cueId", "startsAtMs", "endsAtMs", "words"}, "cue")
        cue_id = _identifier(cue_value.get("cueId"), "cue.cueId")
        if cue_id in cue_ids:
            raise ContractError("cueId values must be unique.")
        cue_ids.add(cue_id)
        starts_at = _non_negative_integer(cue_value.get("startsAtMs"), "cue.startsAtMs")
        ends_at = _positive_integer(cue_value.get("endsAtMs"), "cue.endsAtMs")
        if starts_at < prior_cue_start or ends_at <= starts_at or ends_at > duration_ms:
            raise ContractError("Cue intervals must be monotonic and inside audio.")
        prior_cue_start = starts_at
        cue_words = cue_value.get("words")
        if not isinstance(cue_words, list) or not cue_words:
            raise ContractError("Every cue must contain at least one word.")
        for word in cue_words:
            word_value = _mapping(word, "word")
            _exact_keys(word_value, {"wordId", "text"}, "word")
            word_id = _identifier(word_value.get("wordId"), "word.wordId")
            if word_id in word_ids:
                raise ContractError("wordId values must be unique.")
            word_ids.add(word_id)
            text = word_value.get("text")
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text) > 500
                or not normalize_lexical_word(text)
            ):
                raise ContractError("Every word requires bounded lexical text.")
            words.append(TranscriptWord(word_id=word_id, cue_id=cue_id, text=text))
            if len(words) > MAX_WORDS:
                raise ContractError(f"Transcript exceeds {MAX_WORDS} words.")

    adapter = _mapping(payload.get("adapter"), "adapter")
    _exact_keys(
        adapter,
        {"name", "model", "modelVersion", "settingsVersion"},
        "adapter",
    )
    if adapter.get("name") != adapter_name:
        raise ContractError("CLI adapter does not match request adapter.name.")
    for key in ("name", "model", "modelVersion", "settingsVersion"):
        value = adapter.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ContractError(f"adapter.{key} is required and bounded.")
    model = adapter["model"]
    if (
        not MODEL_REFERENCE.fullmatch(model)
        or model.startswith("/")
        or ".." in model.split("/")
    ):
        raise ContractError("adapter.model must be a safe package or model reference.")

    return ValidatedRequest(
        payload=payload,
        audio_path=audio_path,
        words=tuple(words),
    )


def validate_runner_digest(value: str) -> str:
    if not RUNNER_DIGEST.fullmatch(str(value)):
        raise ContractError("runner-digest must be sha256 followed by 64 hex digits.")
    return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be an object.")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ContractError(f"{name} contains missing or unknown fields.")


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ContractError(f"{name} has an invalid identifier.")
    return value


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{name} must be a positive integer.")
    return value


def _non_negative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{name} must be a non-negative integer.")
    return value
