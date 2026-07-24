from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contract import TranscriptWord, normalize_lexical_word


@dataclass(frozen=True)
class AlignedToken:
    text: str
    starts_at_ms: int | None
    ends_at_ms: int | None
    confidence: float | None
    timing_origin: str = "forced_alignment"


def project_tokens(
    expected_words: tuple[TranscriptWord, ...],
    aligned_tokens: list[AlignedToken],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    usable = [token for token in aligned_tokens if normalize_lexical_word(token.text)]
    candidates: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    token_index = 0

    for word_index, expected in enumerate(expected_words):
        expected_lexical = normalize_lexical_word(expected.text)
        token = usable[token_index] if token_index < len(usable) else None
        if token and normalize_lexical_word(token.text) == expected_lexical:
            candidates.append(_candidate(expected, token))
            token_index += 1
            continue

        matching_token_index = _lookahead_token(usable, token_index, expected_lexical)
        if matching_token_index is not None:
            for skipped in usable[token_index:matching_token_index]:
                issues.append(
                    {
                        "code": "unexpected_adapter_token",
                        "text": skipped.text,
                        "beforeWordId": expected.word_id,
                    }
                )
            token = usable[matching_token_index]
            candidates.append(_candidate(expected, token))
            token_index = matching_token_index + 1
            continue

        if token and _expected_lookahead_matches(
            expected_words, word_index, normalize_lexical_word(token.text)
        ):
            candidates.append(_unaligned(expected, "adapter_omitted_word"))
            issues.append(
                {
                    "code": "adapter_omitted_word",
                    "wordId": expected.word_id,
                }
            )
            continue

        candidates.append(_unaligned(expected, "lexical_projection_mismatch"))
        issues.append(
            {
                "code": "lexical_projection_mismatch",
                "wordId": expected.word_id,
                "adapterText": token.text if token else None,
            }
        )
        if token:
            token_index += 1

    for token in usable[token_index:]:
        issues.append(
            {
                "code": "unexpected_adapter_token",
                "text": token.text,
                "beforeWordId": None,
            }
        )
    return candidates, issues


def _candidate(
    expected: TranscriptWord,
    token: AlignedToken,
) -> dict[str, Any]:
    valid_interval = (
        token.starts_at_ms is not None
        and token.ends_at_ms is not None
        and token.starts_at_ms >= 0
        and token.ends_at_ms > token.starts_at_ms
    )
    if not valid_interval:
        return _unaligned(expected, "adapter_timing_missing")
    return {
        "wordId": expected.word_id,
        "cueId": expected.cue_id,
        "text": expected.text,
        "startsAtMs": token.starts_at_ms,
        "endsAtMs": token.ends_at_ms,
        "confidence": token.confidence,
        "timingOrigin": token.timing_origin,
        "unalignedReason": None,
    }


def _unaligned(expected: TranscriptWord, reason: str) -> dict[str, Any]:
    return {
        "wordId": expected.word_id,
        "cueId": expected.cue_id,
        "text": expected.text,
        "startsAtMs": None,
        "endsAtMs": None,
        "confidence": None,
        "timingOrigin": None,
        "unalignedReason": reason,
    }


def _lookahead_token(
    tokens: list[AlignedToken],
    starts_at: int,
    expected_lexical: str,
) -> int | None:
    for index in range(starts_at + 1, min(len(tokens), starts_at + 5)):
        if normalize_lexical_word(tokens[index].text) == expected_lexical:
            return index
    return None


def _expected_lookahead_matches(
    words: tuple[TranscriptWord, ...],
    starts_at: int,
    token_lexical: str,
) -> bool:
    for index in range(starts_at + 1, min(len(words), starts_at + 5)):
        if normalize_lexical_word(words[index].text) == token_lexical:
            return True
    return False
