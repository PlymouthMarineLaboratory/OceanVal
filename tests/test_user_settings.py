"""The oceanval window's settings, kept in the home .oceanvalrc."""

import json

import pytest

from oceanval import recipes_gui, user_recipes, user_settings
from test_user_recipes import gridded


def test_the_settings_are_in_the_home_oceanvalrc(oceanvalrc):
    assert user_settings.path() == str(oceanvalrc)


def test_the_defaults_without_a_file(oceanvalrc):
    assert user_settings.load() == {"theme": "default", "show_demo": True}
    assert not oceanvalrc.exists()


def test_settings_saved_are_loaded(oceanvalrc):
    assert user_settings.save({"theme": "dark", "show_demo": False}) == {
        "theme": "dark",
        "show_demo": False,
    }
    assert user_settings.load() == {"theme": "dark", "show_demo": False}
    data = json.loads(oceanvalrc.read_text())
    assert data["settings"] == {"theme": "dark", "show_demo": False}
    assert data["version"] == user_recipes.VERSION


def test_one_setting_can_be_saved_on_its_own(oceanvalrc):
    user_settings.save({"theme": "blue", "show_demo": False})
    user_settings.save({"theme": "silver"})
    assert user_settings.load() == {"theme": "silver", "show_demo": False}


def test_saving_the_settings_keeps_the_recipes(oceanvalrc):
    recipes = {"nitrate": {"mysat": {"source": "MySat", "kind": "gridded"}}}
    oceanvalrc.write_text(json.dumps({"version": 1, "recipes": recipes, "other": [1, 2]}))
    user_settings.save({"theme": "dark"})
    data = json.loads(oceanvalrc.read_text())
    assert data["recipes"] == recipes
    assert data["other"] == [1, 2]


def test_saving_a_recipe_keeps_the_settings(oceanvalrc, tmp_path):
    user_settings.save({"theme": "silver", "show_demo": False})
    user_recipes.save(gridded(), "global", str(tmp_path))
    assert user_settings.load() == {"theme": "silver", "show_demo": False}
    assert user_recipes.get("nitrate", "mysat", str(tmp_path)) is not None


@pytest.mark.parametrize(
    "settings",
    [
        {"theme": "purple"},
        {"theme": None},
        {"show_demo": "no"},
        {"show_demo": 0},
        {"colour": "dark"},
        ["theme", "dark"],
    ],
)
def test_settings_that_cannot_be_used_are_refused(oceanvalrc, settings):
    with pytest.raises(ValueError):
        user_settings.save(settings)
    assert not oceanvalrc.exists()


def test_a_file_that_cannot_be_read_is_not_written_over(oceanvalrc):
    oceanvalrc.write_text("{ not json")
    with pytest.raises(ValueError, match="could not be read"):
        user_settings.save({"theme": "dark"})
    assert oceanvalrc.read_text() == "{ not json"


@pytest.mark.parametrize(
    "content",
    [
        "{ not json",
        "[1, 2]",
        json.dumps({"settings": "dark"}),
        json.dumps({"settings": {"theme": "purple", "show_demo": "no"}}),
    ],
)
def test_settings_that_cannot_be_read_are_left_at_their_defaults(oceanvalrc, content):
    oceanvalrc.write_text(content)
    assert user_settings.load() == user_settings.DEFAULTS


def test_only_the_settings_that_cannot_be_used_are_left_at_their_defaults(oceanvalrc):
    oceanvalrc.write_text(json.dumps({"settings": {"theme": "blue", "show_demo": "no"}}))
    assert user_settings.load() == {"theme": "blue", "show_demo": True}


@pytest.mark.parametrize("theme", sorted(user_settings.THEMES))
def test_a_page_is_in_the_colours_of_the_settings(oceanvalrc, theme):
    user_settings.save({"theme": theme})
    page = recipes_gui.render_page("oceanval_app.html", {})
    if theme == "default":
        assert '<html lang="en">' in page
        assert "data-theme=" not in page.split("<head>")[0]
    else:
        assert f'<html lang="en" data-theme="{theme}">' in page
        # and the stylesheet has its colours
        assert f'[data-theme="{theme}"] {{' in page
