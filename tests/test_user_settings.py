"""The oceanval window's settings, kept in the home .oceanvalrc."""

import json

import pytest

from oceanval import user_recipes, user_settings
from test_user_recipes import gridded

# the data store settings, as they are without a data store
NO_STORE = {"data_store": "", "data_store_use": "always"}


def test_the_settings_are_in_the_home_oceanvalrc(oceanvalrc):
    assert user_settings.path() == str(oceanvalrc)


def test_the_defaults_without_a_file(oceanvalrc):
    assert user_settings.load() == {"show_demo": True, **NO_STORE}
    assert not oceanvalrc.exists()


def test_settings_saved_are_loaded(oceanvalrc):
    assert user_settings.save({"show_demo": False, "data_store_use": "fallback"}) == {
        "show_demo": False,
        "data_store": "",
        "data_store_use": "fallback",
    }
    assert user_settings.load() == {"show_demo": False, "data_store": "", "data_store_use": "fallback"}
    data = json.loads(oceanvalrc.read_text())
    assert data["settings"] == {"show_demo": False, "data_store_use": "fallback"}
    assert data["version"] == user_recipes.VERSION


def test_one_setting_can_be_saved_on_its_own(oceanvalrc):
    user_settings.save({"show_demo": False})
    user_settings.save({"data_store_use": "fallback"})
    assert user_settings.load() == {"show_demo": False, "data_store": "", "data_store_use": "fallback"}


def test_saving_the_settings_keeps_the_recipes(oceanvalrc):
    recipes = {"nitrate": {"mysat": {"source": "MySat", "kind": "gridded"}}}
    oceanvalrc.write_text(json.dumps({"version": 1, "recipes": recipes, "other": [1, 2]}))
    user_settings.save({"show_demo": False})
    data = json.loads(oceanvalrc.read_text())
    assert data["recipes"] == recipes
    assert data["other"] == [1, 2]


def test_saving_a_recipe_keeps_the_settings(oceanvalrc, tmp_path):
    user_settings.save({"show_demo": False})
    user_recipes.save(gridded(), "global", str(tmp_path))
    assert user_settings.load() == {"show_demo": False, **NO_STORE}
    assert user_recipes.get("nitrate", "mysat", str(tmp_path)) is not None


@pytest.mark.parametrize(
    "settings",
    [
        {"theme": "dark"},
        {"show_demo": "no"},
        {"show_demo": 0},
        {"colour": "dark"},
        ["show_demo", False],
        {"data_store_use": "sometimes"},
        {"data_store": 3},
    ],
)
def test_settings_that_cannot_be_used_are_refused(oceanvalrc, settings):
    with pytest.raises(ValueError):
        user_settings.save(settings)
    assert not oceanvalrc.exists()


def test_a_file_that_cannot_be_read_is_not_written_over(oceanvalrc):
    oceanvalrc.write_text("{ not json")
    with pytest.raises(ValueError, match="could not be read"):
        user_settings.save({"show_demo": False})
    assert oceanvalrc.read_text() == "{ not json"


@pytest.mark.parametrize(
    "content",
    [
        "{ not json",
        "[1, 2]",
        json.dumps({"settings": "dark"}),
        json.dumps({"settings": {"show_demo": "no"}}),
    ],
)
def test_settings_that_cannot_be_read_are_left_at_their_defaults(oceanvalrc, content):
    oceanvalrc.write_text(content)
    assert user_settings.load() == user_settings.DEFAULTS


def test_only_the_settings_that_cannot_be_used_are_left_at_their_defaults(oceanvalrc):
    oceanvalrc.write_text(json.dumps({"settings": {"data_store_use": "fallback", "show_demo": "no"}}))
    assert user_settings.load() == {"show_demo": True, "data_store": "", "data_store_use": "fallback"}


def test_a_colour_theme_left_in_the_file_is_ignored(oceanvalrc):
    # the window once had colour themes: a file that still names one is read as before
    oceanvalrc.write_text(json.dumps({"settings": {"theme": "dark", "show_demo": False}}))
    assert user_settings.load() == {"show_demo": False, **NO_STORE}


class TestDataStoreFolder:
    def test_a_folder_is_kept_with_the_data_store_made_in_it(self, oceanvalrc, tmp_path):
        saved = user_settings.save({"data_store": f"{tmp_path}/", "data_store_use": "fallback"})
        assert saved["data_store"] == str(tmp_path)
        assert saved["data_store_use"] == "fallback"
        assert (tmp_path / "oceanval_datastore").is_dir()
        # nothing else is put there, not even the file checking it can be written to
        assert sorted(x.name for x in tmp_path.iterdir()) == ["oceanval_datastore"]
        # but the marker saying the folder is OceanVal's
        assert [x.name for x in (tmp_path / "oceanval_datastore").iterdir()] == [".oceanval_datastore"]

    def test_home_is_read_from_a_tilde(self, oceanvalrc, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "data").mkdir()
        assert user_settings.save({"data_store": "~/data"})["data_store"] == str(tmp_path / "data")

    def test_an_empty_folder_is_no_data_store(self, oceanvalrc, tmp_path):
        user_settings.save({"data_store": str(tmp_path)})
        assert user_settings.save({"data_store": "  "})["data_store"] == ""

    @pytest.mark.parametrize("folder", ["data", "./data", "data/store"])
    def test_a_folder_that_is_not_a_full_path_is_refused(self, oceanvalrc, tmp_path, monkeypatch, folder):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        with pytest.raises(ValueError, match="full path"):
            user_settings.save({"data_store": folder})
        assert not (tmp_path / "data" / "oceanval_datastore").exists()
        assert not oceanvalrc.exists()

    def test_a_folder_that_does_not_exist_is_refused_and_not_made(self, oceanvalrc, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            user_settings.save({"data_store": str(tmp_path / "new" / "deeper")})
        assert list(tmp_path.iterdir()) == []

    def test_a_file_is_refused(self, oceanvalrc, tmp_path):
        (tmp_path / "file").write_text("mine")
        with pytest.raises(ValueError, match="a file, not a folder"):
            user_settings.save({"data_store": str(tmp_path / "file")})
        assert (tmp_path / "file").read_text() == "mine"

    def test_a_folder_that_cannot_be_written_to_is_refused(self, oceanvalrc, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            with pytest.raises(ValueError, match="cannot write"):
                user_settings.save({"data_store": str(locked)})
        finally:
            locked.chmod(0o700)
        assert not oceanvalrc.exists()

    def test_the_folder_set_is_not_checked_again(self, oceanvalrc, tmp_path):
        # a disk that is away for now does not stop the other settings being saved
        folder = tmp_path / "disk"
        folder.mkdir()
        user_settings.save({"data_store": str(folder)})
        (folder / "oceanval_datastore" / ".oceanval_datastore").unlink()
        (folder / "oceanval_datastore").rmdir()
        folder.rmdir()
        saved = user_settings.save({"show_demo": False, "data_store": str(folder)})
        assert saved["show_demo"] is False
        assert saved["data_store"] == str(folder)
        assert not folder.exists()

    def test_a_folder_that_is_not_a_full_path_in_the_file_is_not_used(self, oceanvalrc):
        oceanvalrc.write_text(json.dumps({"settings": {"data_store": "data", "data_store_use": "never"}}))
        assert user_settings.load() == user_settings.DEFAULTS
