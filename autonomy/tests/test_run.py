from autonomy.run import main


def test_harness_drives_mock_loop_to_completion(tmp_path, capsys):
    (tmp_path / "SOUL.md").write_text("test agent, safety-first", encoding="utf-8")
    (tmp_path / "GOALS.md").write_text("# Goals\n- [ ] Alpha\n- [ ] Beta\n", encoding="utf-8")
    rc = main([
        "--soul", str(tmp_path / "SOUL.md"),
        "--goals", str(tmp_path / "GOALS.md"),
        "--memory-dir", str(tmp_path / "mem"),
        "--ticks", "10",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "all goals done" in out
    assert "Alpha" in out and "Beta" in out          # both goals were worked
    assert (tmp_path / "mem" / "memory.jsonl").exists()


def test_write_back_ticks_by_id_not_text(tmp_path):
    from autonomy.run import _write_back
    goals = tmp_path / "GOALS.md"
    goals.write_text("# Goals\n- [ ] same text\n- [ ] same text\n- [ ] other\n", encoding="utf-8")
    _write_back(str(goals), {"g1", "g3"})            # complete g1 and g3, NOT g2
    out = goals.read_text(encoding="utf-8").splitlines()
    ticked = [ln for ln in out if ln.strip().startswith("- [x]")]
    unticked = [ln for ln in out if ln.strip().startswith("- [ ]")]
    assert len(ticked) == 2 and len(unticked) == 1   # g1+g3 ticked, g2 (dup text) NOT double-ticked
