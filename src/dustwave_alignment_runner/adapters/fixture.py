from __future__ import annotations

import os
from importlib.metadata import version

from ..contract import ValidatedRequest
from ..projection import AlignedToken


def run(request: ValidatedRequest) -> tuple[list[AlignedToken], str]:
    if os.environ.get("DUSTWAVE_ALLOW_FIXTURE_ADAPTER") != "1":
        raise RuntimeError("The fixture adapter is disabled outside explicit tests.")
    tokens: list[AlignedToken] = []
    for cue in request.payload["transcript"]["cues"]:
        duration = cue["endsAtMs"] - cue["startsAtMs"]
        words = cue["words"]
        for index, word in enumerate(words):
            starts_at = cue["startsAtMs"] + round(duration * index / len(words))
            ends_at = cue["startsAtMs"] + round(duration * (index + 1) / len(words))
            tokens.append(
                AlignedToken(
                    text=word["text"],
                    starts_at_ms=starts_at,
                    ends_at_ms=ends_at,
                    confidence=None,
                    timing_origin="interpolated",
                )
            )
    return tokens, version("dustwave-alignment-runner")
