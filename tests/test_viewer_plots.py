"""The viewer's maps (oceanval.viewer_plots, ported from ncviewer's tests):
drawn as ncplot draws them, an image or a quadmesh, by the grid's own indices
where its longitudes and latitudes cannot place the cells."""

import re

import numpy as np
import pytest
import xarray as xr
from viewer_simulations import regular_file

from oceanval import viewer_catalogue as catalogue
from oceanval import viewer_plots as plotting


def _titles(item):
    """The text of each title a json_item's figures have."""
    return re.findall(r"'name': 'Title', 'id': '[a-z0-9]+', 'attributes': \{'text': '([^']*)'", str(item))


def _kinds(item):
    """The kinds of glyph a json_item draws (a quadmesh of a curvilinear grid
    is drawn as Patches, one of a rectilinear one as Quads)."""
    names = set(re.findall(r"'name': '([A-Za-z]+)'", str(item)))
    found = names & {"Image"}
    if names & {"QuadMesh", "Patches", "Quad"}:
        found.add("QuadMesh")
    return found


@pytest.fixture
def simulation(viewer_simulation):
    return viewer_simulation


@pytest.fixture
def found(simulation):
    return catalogue.scan(simulation, 2)


def test_curvilinear_land_at_zero_is_drawn_by_index(found):
    data, _ = found.field(0, "votemper", 0, 0)
    item, notes, clim = plotting.map_plot(data, found.about(0, "votemper"), "votemper")
    assert "Image" in _kinds(item)
    assert any("indices" in note for note in notes)
    assert clim[0] < clim[1]


def test_curvilinear_grid_is_a_quadmesh():
    y, x = np.meshgrid(np.arange(8), np.arange(6), indexing="ij")
    data = xr.DataArray(
        np.random.default_rng(0).random((8, 6)), dims=("y", "x"),
        coords={"lon": (("y", "x"), -10 + x + 0.1 * y), "lat": (("y", "x"), 50 + y + 0.1 * x)},
    )
    about = {"horizontal": ["y", "x"], "lon": "lon", "lat": "lat", "units": "K", "long_name": "t"}
    item, notes, clim = plotting.map_plot(data, about, "t")
    assert "indices" not in " ".join(notes)
    assert "QuadMesh" in _kinds(item)


def test_regular_grid_is_an_image(tmp_path):
    regular_file(str(tmp_path / "r.nc"))
    found = catalogue.scan(str(tmp_path), 0)
    data, _ = found.field(0, "sst", 1)
    item, notes, clim = plotting.map_plot(data, found.about(0, "sst"), "sst")
    assert notes == []
    # the map has no title: the page and the colour bar say what it is
    assert _titles(item) == []
    assert "Image" in _kinds(item)


def test_uneven_latitudes_are_a_quadmesh():
    lat = np.array([40, 41, 43, 47, 55.0])
    data = xr.DataArray(np.ones((5, 4)), dims=("lat", "lon"), coords={"lat": lat, "lon": np.arange(4.0)})
    about = {"horizontal": ["lat", "lon"], "lon": "lon", "lat": "lat", "units": "", "long_name": ""}
    item, notes, clim = plotting.map_plot(data, about, "v")
    assert "QuadMesh" in _kinds(item)


def test_colour_limits():
    assert plotting.colour_limits([-1, 3]) == ((-3, 3), True)
    assert plotting.colour_limits([1, 3]) == ((1, 3), False)
    assert plotting.colour_limits([1, 3], vmin=0, vmax=10) == ((0, 10), False)
    assert plotting.colour_limits([np.nan, 2, 4]) == ((2, 4), False)
    low, high = plotting.colour_limits([2, 2])[0]
    assert low < 2 < high


def test_the_window_imports_no_plotting_library():
    """holoviews, hvplot and bokeh take seconds to import: they are imported
    with the first map, not with the window."""
    import subprocess
    import sys

    code = ("import sys, oceanval.app, oceanval.viewer_plots, oceanval.viewer_catalogue; "
            "print(sorted(n for n in ('holoviews', 'hvplot', 'bokeh') if n in sys.modules))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert done.stdout.strip().splitlines()[-1] == "[]", done.stderr
