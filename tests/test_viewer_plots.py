"""The viewer's maps (oceanval.viewer_plots, ported from ncviewer's tests):
drawn as ncplot draws them, an image or a quadmesh, by the grid's own indices
where its longitudes and latitudes cannot place the cells."""

import re

import numpy as np
import pytest
import xarray as xr
from viewer_simulations import axes_visible, regular_file

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


def test_curvilinear_land_at_zero_is_drawn_on_longitudes_and_latitudes(found):
    # the land cells piled up at 0, 0 are left out (oceanval.viewer_grid), the rest drawn where they are
    data, _ = found.field(0, "votemper", 0, 0)
    item, notes, clim = plotting.map_plot(data, found.about(0, "votemper"), "votemper")
    assert "Image" in _kinds(item)
    assert not any("indices" in note for note in notes)
    assert any("regular" in note for note in notes)
    assert item["aspect"] is not None
    assert clim[0] < clim[1]


def test_cells_the_coordinates_cannot_place_are_drawn_by_index():
    # every cell with a value at 0, 0: nothing can be placed
    data = xr.DataArray(
        np.random.default_rng(0).random((8, 6)), dims=("y", "x"),
        coords={"lon": (("y", "x"), np.zeros((8, 6))), "lat": (("y", "x"), np.zeros((8, 6)))},
    )
    about = {"horizontal": ["y", "x"], "lon": "lon", "lat": "lat", "units": "K", "long_name": "t"}
    item, notes, clim = plotting.map_plot(data, about, "t")
    assert "Image" in _kinds(item)
    assert any("indices" in note for note in notes)
    assert item["aspect"] is None


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
    assert "Image" in _kinds(item)


def _regular(tmp_path):
    regular_file(str(tmp_path / "r.nc"))
    found = catalogue.scan(str(tmp_path), 0)
    data, _ = found.field(0, "sst", 1)
    return data, found.about(0, "sst"), "sst"


def _curvilinear(tmp_path):
    y, x = np.meshgrid(np.arange(8), np.arange(6), indexing="ij")
    data = xr.DataArray(
        np.random.default_rng(0).random((8, 6)), dims=("y", "x"),
        coords={"lon": (("y", "x"), -10 + x + 0.1 * y), "lat": (("y", "x"), 50 + y + 0.1 * x)},
    )
    return data, {"horizontal": ["y", "x"], "lon": "lon", "lat": "lat", "units": "K", "long_name": "t"}, "t"


def _by_index(tmp_path):
    data = xr.DataArray(
        np.ones((5, 4)), dims=("y", "x"),
        coords={"lon": (("y", "x"), np.full((5, 4), np.nan)), "lat": (("y", "x"), np.full((5, 4), np.nan))},
    )
    return data, {"horizontal": ["y", "x"], "lon": "lon", "lat": "lat", "units": "K", "long_name": "t"}, "t"


@pytest.mark.parametrize("make", [_regular, _curvilinear, _by_index], ids=["image", "quadmesh", "indices"])
def test_a_map_has_no_title_colour_bar_label_or_axes(make, tmp_path):
    """The page says what the map is (variable, time, units), so the figure has
    nothing else: no title, nothing beside the colour bar (hvplot would name it
    after the variable), and no axis labels, numbers or ticks (both axes are
    there, not visible), whatever the kind of map."""
    data, about, name = make(tmp_path)
    item, _, _ = plotting.map_plot(data, about, name)
    assert _titles(item) == []
    assert name not in re.findall(r"'title': '([^']*)'", str(item))
    assert axes_visible(item) == ["False", "False"]


def test_uneven_latitudes_are_a_quadmesh():
    lat = np.array([40, 41, 43, 47, 55.0])
    data = xr.DataArray(np.ones((5, 4)), dims=("lat", "lon"), coords={"lat": lat, "lon": np.arange(4.0)})
    about = {"horizontal": ["lat", "lon"], "lon": "lon", "lat": "lat", "units": "", "long_name": ""}
    item, notes, clim = plotting.map_plot(data, about, "v")
    assert "QuadMesh" in _kinds(item)


def _box(lat0, lat1, lon0, lon1, n=(11, 21)):
    lat, lon = np.linspace(lat0, lat1, n[0]), np.linspace(lon0, lon1, n[1])
    data = xr.DataArray(np.random.default_rng(0).random(n), dims=("lat", "lon"), coords={"lat": lat, "lon": lon})
    about = {"horizontal": ["lat", "lon"], "lon": "lon", "lat": "lat", "units": "", "long_name": ""}
    return data, about


@pytest.mark.parametrize("coast", [False, True])
def test_the_frame_keeps_distances_on_one_scale(coast):
    """A map's frame is as tall for its width as the area's north-south
    distance is for its east-west one: 10 degrees of latitude by 20 of
    longitude about 55N is nearly as tall as it is wide."""
    if coast and not plotting.has_cartopy():
        pytest.skip("cartopy is not installed")
    data, about = _box(50, 60, -10, 10)
    item, _, _ = plotting.map_plot(data, about, "v", coast=coast)
    # the image's cells reach half a cell past the outer points
    yspan, xspan = 10 + 1, 20 + 1
    assert item["aspect"] == pytest.approx(yspan / (xspan * np.cos(np.radians(55))), rel=1e-6)


def test_a_map_of_indices_fills_its_box():
    # longitudes and latitudes that place no cell: the map is of the grid's indices
    data = xr.DataArray(
        np.ones((5, 4)), dims=("y", "x"),
        coords={"lon": (("y", "x"), np.full((5, 4), np.nan)), "lat": (("y", "x"), np.full((5, 4), np.nan))},
    )
    about = {"horizontal": ["y", "x"], "lon": "lon", "lat": "lat", "units": "K", "long_name": "t"}
    item, _, _ = plotting.map_plot(data, about, "t")
    assert item["aspect"] is None


def test_frame_aspect():
    assert plotting.frame_aspect((0, 2), (0, 1), "metres") == 0.5
    assert plotting.frame_aspect((0, 2), (59, 61), "lonlat") == pytest.approx(2 / (2 * 0.5), rel=1e-3)
    assert plotting.frame_aspect((0, 2), (0, 1), None) is None
    assert plotting.frame_aspect((0, 0), (0, 1), "lonlat") is None


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


def test_every_map_has_its_coastline_drawn_over_it(monkeypatch):
    """The coastline is drawn over each map in its own axes (coastline_overlay), the
    next map's too: geoviews' coastline, once one map was drawn, refused the next."""
    if not plotting.has_cartopy():
        pytest.skip("cartopy is not installed")
    asked = []

    def coastline(x_range, y_range, scale):
        asked.append((x_range, y_range))
        return np.array([x_range[0], x_range[1]]), np.array([y_range[0], y_range[1]])

    monkeypatch.setattr(plotting, "natural_earth_coastline", coastline)
    image, about = _box(50, 60, -10, 10)
    j, i = np.meshgrid(np.arange(6), np.arange(8), indexing="ij")
    curvilinear = xr.DataArray(
        np.random.default_rng(1).random((6, 8)), dims=("y", "x"),
        coords={"lon": (("y", "x"), -5 + i * 0.5 + j * 0.1), "lat": (("y", "x"), 50 + j * 0.5)},
    )
    for data, about_it in ((image, about), (curvilinear, dict(about, horizontal=["y", "x"])), (image, about)):
        item, notes, _ = plotting.map_plot(data, about_it, "v", coast=True)
        assert "'MultiLine'" in str(item)
        assert not any("coast" in note.lower() for note in notes)
    # to the map's edges: an image's cells reach half a cell past its outer points
    assert asked[0] == ((-10.5, 10.5), (49.5, 60.5))


def test_a_thinned_map_says_every_nth_cell():
    assert [plotting._ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "111th"]
