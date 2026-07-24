from __future__ import annotations

from importlib.metadata import version

from ..contract import ValidatedRequest
from ..projection import AlignedToken


def run(request: ValidatedRequest) -> tuple[list[AlignedToken], str]:
    try:
        import stable_whisper
    except ImportError as error:
        raise RuntimeError(
            "Install the locked stable-ts extra before using this adapter."
        ) from error
    adapter = request.payload["adapter"]
    model = stable_whisper.load_model(adapter["model"])
    reviewed_text = " ".join(word.text for word in request.words)
    result = model.align(
        str(request.audio_path),
        reviewed_text,
        language=request.payload["language"],
    )
    tokens = [
        AlignedToken(
            text=word.word,
            starts_at_ms=_milliseconds(word.start),
            ends_at_ms=_milliseconds(word.end),
            confidence=_confidence(getattr(word, "probability", None)),
        )
        for word in result.all_words()
    ]
    return tokens, version("stable-ts")


def _milliseconds(value: object) -> int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(float(value) * 1_000)


def _confidence(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric if 0 <= numeric <= 1 else None
