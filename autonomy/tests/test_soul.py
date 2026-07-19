from autonomy.soul import Goal, Soul


def _write(tmp_path, soul="I am a test agent.", goals="# Goals\n- [ ] First goal\n- [x] Done goal\n- [ ] Second goal\n"):
    (tmp_path / "SOUL.md").write_text(soul, encoding="utf-8")
    (tmp_path / "GOALS.md").write_text(goals, encoding="utf-8")
    return Soul(soul_path=str(tmp_path / "SOUL.md"), goals_path=str(tmp_path / "GOALS.md"))


def test_parses_goals_checklist(tmp_path):
    soul = _write(tmp_path)
    all_goals = soul.all_goals()
    assert [g.id for g in all_goals] == ["g1", "g2", "g3"]
    assert [g.text for g in all_goals] == ["First goal", "Done goal", "Second goal"]
    assert [g.done for g in all_goals] == [False, True, False]


def test_active_goals_excludes_done(tmp_path):
    soul = _write(tmp_path)
    active = soul.active_goals()
    assert [g.id for g in active] == ["g1", "g3"]        # done goal excluded, order preserved


def test_render_includes_soul_identity(tmp_path):
    soul = _write(tmp_path, soul="I am HERMES-1, safety-first.")
    rendered = soul.render()
    assert "HERMES-1" in rendered


def test_remaining_goals_excludes_memory_completed(tmp_path):
    from autonomy.soul import remaining_goals
    soul = _write(tmp_path, goals="# Goals\n- [ ] First\n- [ ] Second\n")

    class _Mem:
        def completed_goals(self):
            return {"g1"}

    assert [g.id for g in remaining_goals(soul, _Mem())] == ["g2"]
