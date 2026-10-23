"""What OceanVal remembers in ~/.oceanvalcache."""

import json
import os

from oceanval import user_cache


def test_the_file_is_in_the_home_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("OCEANVALCACHE")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert user_cache.path() == str(tmp_path / ".oceanvalcache")


def test_the_file_can_be_named(monkeypatch, tmp_path):
    monkeypatch.setenv("OCEANVALCACHE", str(tmp_path / "elsewhere.json"))
    assert user_cache.path() == str(tmp_path / "elsewhere.json")


def test_nothing_is_remembered_before_anything_is_recorded(oceanvalcache):
    assert user_cache.recent_sim_dirs() == []
    assert not oceanvalcache.exists()


def test_simulations_are_remembered_latest_first_without_repeats(oceanvalcache, tmp_path):
    for name in ("a", "b", "a", "c"):
        assert user_cache.record_sim_dir(str(tmp_path / name))
    assert user_cache.recent_sim_dirs() == [str(tmp_path / name) for name in ("c", "a", "b")]
    data = json.loads(oceanvalcache.read_text())
    assert data["version"] == 1
    assert {"path", "last_used"} == set(data["sim_dirs"][0])


def test_a_relative_path_is_remembered_in_full(oceanvalcache, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    user_cache.record_sim_dir("sim")
    assert user_cache.recent_sim_dirs() == [str(tmp_path / "sim")]


def test_only_the_latest_are_kept(oceanvalcache, tmp_path):
    for number in range(user_cache.MAX_SIM_DIRS + 5):
        user_cache.record_sim_dir(str(tmp_path / str(number)))
    found = user_cache.recent_sim_dirs()
    assert len(found) == user_cache.MAX_SIM_DIRS
    assert found[0] == str(tmp_path / str(user_cache.MAX_SIM_DIRS + 4))


def test_what_is_already_in_the_file_is_kept(oceanvalcache, tmp_path):
    oceanvalcache.write_text(json.dumps({
        "version": 1,
        "sim_dirs": [{"path": "/old/sim", "last_used": "2020-01-01T00:00:00", "note": "mine"}],
        "something_else": {"kept": True},
    }))
    user_cache.record_sim_dir(str(tmp_path / "new"))
    data = json.loads(oceanvalcache.read_text())
    assert data["something_else"] == {"kept": True}
    assert data["sim_dirs"][1] == {"path": "/old/sim", "last_used": "2020-01-01T00:00:00", "note": "mine"}
    assert user_cache.recent_sim_dirs() == [str(tmp_path / "new"), "/old/sim"]


def test_saving_leaves_no_temporary_files(oceanvalcache, tmp_path):
    user_cache.record_sim_dir(str(tmp_path / "a"))
    assert os.listdir(oceanvalcache.parent) == [".oceanvalcache"]


def test_a_file_that_cannot_be_read_is_ignored_and_not_written_over(oceanvalcache, tmp_path):
    oceanvalcache.write_text("{ not json")
    assert user_cache.recent_sim_dirs() == []
    assert not user_cache.record_sim_dir(str(tmp_path / "a"))
    assert oceanvalcache.read_text() == "{ not json"


def test_a_file_from_a_newer_oceanval_is_not_written_over(oceanvalcache, tmp_path):
    text = json.dumps({"version": 99, "sim_dirs": [{"path": "/x"}], "new": 1})
    oceanvalcache.write_text(text)
    assert not user_cache.record_sim_dir(str(tmp_path / "a"))
    assert oceanvalcache.read_text() == text


def test_a_place_that_cannot_be_written_does_not_raise(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("OCEANVALCACHE", str(blocker / "sub" / ".oceanvalcache"))
    assert not user_cache.record_sim_dir(str(tmp_path / "a"))
    assert user_cache.recent_sim_dirs() == []


def test_one_simulation_can_be_forgotten(oceanvalcache, tmp_path):
    for name in ("a", "b", "c"):
        user_cache.record_sim_dir(str(tmp_path / name))
    assert user_cache.forget_sim_dir(str(tmp_path / "b"))
    assert user_cache.recent_sim_dirs() == [str(tmp_path / "c"), str(tmp_path / "a")]
    assert os.listdir(oceanvalcache.parent) == [".oceanvalcache"]


def test_forgetting_keeps_what_else_is_in_the_file(oceanvalcache):
    oceanvalcache.write_text(json.dumps({
        "version": 1,
        "sim_dirs": [
            {"path": "/old/sim", "last_used": "2020-01-01T00:00:00", "note": "mine"},
            "/plain/sim",
            {"path": "/other/sim"},
        ],
        "something_else": {"kept": True},
    }))
    assert user_cache.forget_sim_dir("/plain/sim")
    data = json.loads(oceanvalcache.read_text())
    assert data["something_else"] == {"kept": True}
    assert data["sim_dirs"] == [
        {"path": "/old/sim", "last_used": "2020-01-01T00:00:00", "note": "mine"},
        {"path": "/other/sim"},
    ]


def test_forgetting_a_relative_path(oceanvalcache, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    user_cache.record_sim_dir("sim")
    assert user_cache.forget_sim_dir("sim")
    assert user_cache.recent_sim_dirs() == []


def test_forgetting_what_is_not_there_writes_nothing(oceanvalcache, tmp_path):
    assert user_cache.forget_sim_dir(str(tmp_path / "a"))
    assert not oceanvalcache.exists()
    user_cache.record_sim_dir(str(tmp_path / "a"))
    before = oceanvalcache.stat().st_mtime_ns
    assert user_cache.forget_sim_dir(str(tmp_path / "b"))
    assert oceanvalcache.stat().st_mtime_ns == before


def test_a_file_that_cannot_be_used_is_not_written_over_by_forgetting(oceanvalcache):
    oceanvalcache.write_text("{ not json")
    assert not user_cache.forget_sim_dir("/x")
    assert oceanvalcache.read_text() == "{ not json"
    text = json.dumps({"version": 99, "sim_dirs": [{"path": "/x"}]})
    oceanvalcache.write_text(text)
    assert not user_cache.forget_sim_dir("/x")
    assert oceanvalcache.read_text() == text


def test_a_place_that_cannot_be_written_does_not_raise_when_forgetting(oceanvalcache, monkeypatch):
    user_cache.record_sim_dir("/x")

    def refuse(where, data):
        raise OSError("read-only")

    monkeypatch.setattr(user_cache, "_write", refuse)
    assert not user_cache.forget_sim_dir("/x")
