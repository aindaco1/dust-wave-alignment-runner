from dustwave_alignment_runner.contract import TranscriptWord
from dustwave_alignment_runner.projection import AlignedToken, project_tokens


def test_projects_spanish_punctuation_to_stable_ids() -> None:
    expected = (
        TranscriptWord("word_1", "cue_1", "¡Ópera!"),
        TranscriptWord("word_2", "cue_1", "wave"),
    )
    candidates, issues = project_tokens(
        expected,
        [
            AlignedToken("opera", 100, 500, 0.95),
            AlignedToken("wave.", 520, 900, 0.96),
        ],
    )

    assert issues == []
    assert [word["wordId"] for word in candidates] == ["word_1", "word_2"]
    assert candidates[0]["startsAtMs"] == 100


def test_retains_explicit_unaligned_records_and_adapter_extras() -> None:
    expected = (
        TranscriptWord("word_1", "cue_1", "one"),
        TranscriptWord("word_2", "cue_1", "two"),
    )
    candidates, issues = project_tokens(
        expected,
        [
            AlignedToken("extra", 0, 20, 0.5),
            AlignedToken("one", 30, 80, 0.9),
        ],
    )

    assert candidates[0]["startsAtMs"] == 30
    assert candidates[1]["startsAtMs"] is None
    assert candidates[1]["unalignedReason"] == "lexical_projection_mismatch"
    assert {issue["code"] for issue in issues} == {
        "unexpected_adapter_token",
        "lexical_projection_mismatch",
    }
