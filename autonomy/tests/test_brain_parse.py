from autonomy.brain import _extract_json, _parse_yes, _strip_think


def test_strip_think_removes_thinking_blocks():
    assert _strip_think("<think>reasoning</think>answer").strip() == "answer"


def test_extract_json_after_think_and_trailing_prose():
    text = '<think>let me decide</think>\nSure: {"a": 1, "b": {"c": 2}} and that is my answer.'
    assert _extract_json(text) == '{"a": 1, "b": {"c": 2}}'


def test_extract_json_none_when_absent():
    assert _extract_json("no json here at all") is None


def test_extract_json_balanced_not_greedy():
    # A stray closing brace in trailing prose must not extend the match.
    assert _extract_json('{"x": 1} then } junk') == '{"x": 1}'


def test_parse_yes():
    assert _parse_yes("yes") is True
    assert _parse_yes("No.") is False
    assert _parse_yes("<think>hmm</think>\nYes, it is done.") is True
    assert _parse_yes("maybe, unclear") is False        # fail-closed
    assert _parse_yes("no, but yes later") is False      # first standalone word wins
