from __future__ import annotations

from importlib.metadata import version

from ..contract import ValidatedRequest
from ..projection import AlignedToken


def run(request: ValidatedRequest) -> tuple[list[AlignedToken], str]:
    try:
        import whisperx
    except ImportError as error:
        raise RuntimeError(
            "Install the locked whisperx extra before using this adapter."
        ) from error
    adapter = request.payload["adapter"]
    device = "cpu"
    audio = whisperx.load_audio(str(request.audio_path))
    model, metadata = whisperx.load_align_model(
        language_code=request.payload["language"],
        device=device,
        model_name=adapter["model"] if adapter["model"] != "default" else None,
    )
    segments = [
        {
            "start": cue["startsAtMs"] / 1_000,
            "end": cue["endsAtMs"] / 1_000,
            "text": " ".join(word["text"] for word in cue["words"]),
        }
        for cue in request.payload["transcript"]["cues"]
    ]
    result = whisperx.align(
        segments,
        model,
        metadata,
        audio,
        device,
        interpolate_method="ignore",
        return_char_alignments=False,
    )
    tokens = [
        AlignedToken(
            text=word["word"],
            starts_at_ms=_milliseconds(word.get("start")),
            ends_at_ms=_milliseconds(word.get("end")),
            confidence=_confidence(word.get("score")),
        )
        for word in result["word_segments"]
    ]
    return tokens, version("whisperx")


def _milliseconds(value: object) -> int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(float(value) * 1_000)


def _confidence(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric if 0 <= numeric <= 1 else None
