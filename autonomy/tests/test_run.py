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
