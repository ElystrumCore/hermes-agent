from autonomy.brain import Decision
from autonomy.hands import MockHands, Outcome


def test_mock_hands_reports_the_action_as_output():
    d = Decision(target_goal="g1", action="do the thing", rationale="", idle=False, done=False)
    out = MockHands().act(d)
    assert isinstance(out, Outcome)
    assert out.ok is True and out.error is None
    assert "do the thing" in out.output
