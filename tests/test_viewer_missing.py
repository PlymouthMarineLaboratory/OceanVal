"""What the viewer's setup page assumes to be missing values (see oceanval.viewer_missing)."""

import pytest
from viewer_simulations import land_simulation, nemo_simulation

from oceanval import recipes_gui, viewer_missing


@pytest.mark.parametrize("land, found", [(0.0, "0"), (1e20, "1e+20"), (-999.0, "-999")])
def test_a_value_many_cells_share_in_every_file_is_found(tmp_path, land, found):
    root = land_simulation(str(tmp_path / "sim"), land=land)
    assert viewer_missing.guess(root, 0) == {"value": found, "ersem": False}


def test_land_that_is_already_missing_gives_nothing(tmp_path):
    root = nemo_simulation(str(tmp_path / "sim"), years=(2000,))
    assert viewer_missing.guess(root, 2) == {"value": None, "ersem": False}


def test_the_file_filters_are_used(tmp_path):
    root = land_simulation(str(tmp_path / "sim"), files=3)
    # one file is not enough to tell
    assert viewer_missing.guess(root, 0, exclude="land_01 land_02")["value"] is None
    assert viewer_missing.guess(root, 0, require="land_0")["value"] == "0"


def test_an_ersem_simulation_is_told(tmp_path, monkeypatch):
    root = nemo_simulation(str(tmp_path / "sim"), years=(2000,))
    seen = []
    monkeypatch.setattr(recipes_gui, "is_likely_nemo_ersem", lambda names: seen.append(set(names)) or True)
    assert viewer_missing.guess(root, 2) == {"value": None, "ersem": True}
    # the variables of every file type are looked at, not only the one that was probed
    assert {"votemper", "chl"} <= seen[0]


def test_what_cannot_be_read_gives_nothing(tmp_path):
    assert viewer_missing.guess(str(tmp_path / "nowhere"), 0) == {"value": None, "ersem": False}
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "broken.nc").write_text("not netcdf")
    assert viewer_missing.guess(str(empty), 0) == {"value": None, "ersem": False}
