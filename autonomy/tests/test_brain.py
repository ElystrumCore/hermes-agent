from autonomy.brain import Decision, MockBrain
from autonomy.soul import Goal


def test_mock_brain_targets_top_goal_and_completes_in_one_tick():
    brain = MockBrain(ticks_per_goal=1)
    g = [Goal("g1", "First", False), Goal("g2", "Second", False)]
    d = brain.decide("id", g, [])
    assert d.target_goal == "g1"
    assert d.action == "[mock] work on First"
    assert d.idle is False and d.done is True     # completes in one tick


def test_mock_brain_takes_multiple_ticks_when_configured():
    brain = MockBrain(ticks_per_goal=2)
    g = [Goal("g1", "First", False)]
    assert brain.decide("id", g, []).done is False   # tick 1: not done
    assert brain.decide("id", g, []).done is True    # tick 2: done


def test_mock_brain_idle_when_no_active_goals():
    d = MockBrain().decide("id", [], [])
    assert d.idle is True and d.action == "" and d.target_goal is None
