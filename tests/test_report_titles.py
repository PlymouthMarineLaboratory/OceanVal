"""The title of a gridded page: sea surface only for a matchup of the sea surface
alone (oceanval._gridded_notebook_text), and the annual low of oxygen."""

import json

import oceanval
from oceanval import skill


def _title(data_dir, vertical, title="Nitrate"):
    folder = data_dir / "oceanval_matchups" / "gridded" / "nitrate"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "woa23_nitrate_surface.nc").write_text("")
    if vertical:
        (folder / "woa23_nitrate_vertical.nc").write_text("")
    text = oceanval._gridded_notebook_text(
        "woa23", "nitrate", title, True, str(data_dir), None, None, None,
    )
    return "".join(json.loads(text)["cells"][0]["source"])


def test_a_matchup_through_the_water_column_is_not_titled_sea_surface(tmp_path):
    assert _title(tmp_path, vertical=True) == "# Nitrate validation using gridded observations from WOA23"


def test_the_title_starts_with_a_capital(tmp_path):
    assert _title(tmp_path, vertical=True, title="nitrate").startswith("# Nitrate validation")


def test_a_matchup_of_the_surface_alone_is(tmp_path):
    assert _title(tmp_path, vertical=False) == "# Sea surface Nitrate validation using gridded observations from WOA23"


def test_oxygen_times_its_annual_low_and_the_rest_their_peak():
    assert skill.times_the_low("oxygen") and skill.times_the_low("Oxygen")
    assert not any(skill.times_the_low(x) for x in ("nitrate", "temperature", "chlorophyll", "o2sat", "aou"))
