"""FVCOM output in the viewer: told from other output, its variables on the
mesh listed, and the mesh drawn as an image (oceanval.viewer_mesh, ported
from ncviewer's tests)."""

import json

import netCDF4
import numpy as np
import pytest
from viewer_simulations import fvcom_file, fvcom_mesh, fvcom_simulation

from oceanval import viewer_catalogue as catalogue
from oceanval import viewer_mesh as fvcom
from oceanval import viewer_plots as plotting
from oceanval.app import App


@pytest.fixture
def simulation(viewer_simulation):
    return viewer_simulation


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    return fvcom_simulation(str(tmp_path_factory.mktemp("fvcom")))


@pytest.fixture
def found(output):
    found = catalogue.scan(output, 0)
    yield found
    found.close()


def test_it_is_told_from_other_output(output, simulation):
    with netCDF4.Dataset(f"{output}/estuary_0001.nc") as ds:
        assert fvcom.looks_like_fvcom(ds)
    with catalogue.open_dataset(f"{simulation}/2000/01/amm7_1m_20000101_20000128_grid_T.nc") as ds:
        assert not fvcom.looks_like_fvcom(ds)


def test_variables_and_times(found):
    summary = found.summary()
    stream = summary["streams"][0]
    assert stream["fvcom"] is True
    # four files of three days, from FVCOM's modified julian days
    assert stream["steps"] == 12 and stream["first"] == "2012-01-01 00:00:00" and stream["last"] == "2012-01-12 00:00:00"
    # the mesh's own variables are not listed
    assert list(summary["variables"]) == ["h", "temp", "u", "zeta"]
    temp = summary["variables"]["temp"]["streams"][0]
    assert temp["mesh"] == "node" and temp["vertical"] == "siglay" and temp["levels"] == 5
    assert temp["level_labels"] == ["Layer 1 (surface)", "Layer 2", "Layer 3", "Layer 4", "Layer 5 (bottom)"]
    assert summary["variables"]["u"]["streams"][0]["mesh"] == "nele"
    assert summary["variables"]["h"]["streams"][0]["time"] is False


def test_a_field_is_a_slice_of_the_mesh(found):
    lon, _, _ = fvcom_mesh()
    data, when = found.field(0, ["temp"], step=4, level=2)
    assert when == "2012-01-05 00:00:00"
    np.testing.assert_allclose(data.values, 10 + lon + 2 + 4, rtol=1e-6)


def test_the_mesh(found):
    lon, lat, nv = fvcom_mesh()
    mesh = found.mesh(0)
    assert found.mesh(0) is mesh
    assert mesh.nodes == len(lon) and mesh.elements == nv.shape[1] and not mesh.cartesian
    np.testing.assert_array_equal(mesh.triangles, nv.T - 1)


def test_the_raster(found):
    mesh = found.mesh(0)
    raster = mesh.raster()
    assert mesh.raster() is raster
    assert max(raster.shape) == fvcom.MAX_SIDE
    x, y = np.meshgrid(raster.x_axis, raster.y_axis)
    # a field linear in longitude is drawn exactly: shaded linearly across each triangle
    image = raster.image(mesh.x * 2 + 1)
    inside = np.isfinite(image)
    np.testing.assert_allclose(image[inside], 2 * x[inside] + 1, atol=1e-9)
    # the corner cut out of the mesh (each square with a node gone) is missing, and the rest is drawn
    assert np.isnan(image[(x > -4.495) & (y > 50.505)]).all()
    assert inside[(x < -4.505) | (y < 50.495)].all()
    # a value on the elements fills its triangle
    elements = raster.image(np.arange(mesh.elements, dtype="float64"), "nele")
    assert set(np.unique(elements[inside]).astype(int)) == set(range(mesh.elements))
    # a missing node leaves its triangles blank, and no more
    values = mesh.x.copy()
    values[0] = np.nan
    blank = np.isnan(raster.image(values)) & inside
    assert blank.any() and blank.sum() < inside.sum() / 50


def test_a_large_mesh_is_quick(monkeypatch):
    n = 300
    x, y = np.meshgrid(np.linspace(0, 10, n), np.linspace(50, 60, n))
    index = np.arange(n * n).reshape(n, n)
    a, b, c, d = (part.ravel() for part in (index[:-1, :-1], index[:-1, 1:], index[1:, :-1], index[1:, 1:]))
    triangles = np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])
    # a few pairs at a time, so the pieces are put together right
    monkeypatch.setattr(fvcom, "PAIRS_AT_ONCE", 100_000)
    raster = fvcom.Mesh(x.ravel(), y.ravel(), triangles).raster()
    image = raster.image(y.ravel())
    grid_x, grid_y = np.meshgrid(raster.x_axis, raster.y_axis)
    np.testing.assert_allclose(image, grid_y, atol=1e-9)


def test_a_mesh_in_metres(tmp_path):
    root = fvcom_simulation(str(tmp_path / "metres"), files=1, cartesian=True)
    found = catalogue.scan(root, 0)
    try:
        mesh = found.mesh(0)
        assert mesh.cartesian and mesh.x.max() == pytest.approx(70000)
        data, _ = found.field(0, ["temp"], 0, 0)
        item, notes, clim = plotting.map_plot(data, found.combined(0, ["temp"]), "temp", coast=True, mesh=mesh)
        assert "x (m)" in str(item)
        # metres east and north are drawn on one scale
        span = (mesh.y.max() - mesh.y.min()) / (mesh.x.max() - mesh.x.min())
        assert item["aspect"] == pytest.approx(span, rel=0.05)
        # Natural Earth's coastline cannot be placed on metres
        assert any("no coastline" in note for note in notes)
        assert _lines(item) == 0
    finally:
        found.close()


def test_maps_are_drawn(output, tmp_path):
    import time

    app = App(cwd=str(tmp_path))
    try:
        assert app.choose("viewer")
        assert app.setup({"simdir": output, "ndown": "0", "exclude": ""}) == (200, {"ok": True})
        for _ in range(600):
            if app.view == "viewer":
                break
            time.sleep(0.05)
        for name, level in (("temp", 2), ("u", 0), ("zeta", 0), ("h", 0)):
            reply = app.viewer_plot({"stream": "0", "vars": f'["{name}"]', "step": "4", "level": str(level)})
            assert reply["ok"], reply
            assert "FVCOM's mesh of 96 nodes and 150 triangles" in reply["notes"][0]
        temp = app.viewer_plot({"stream": "0", "vars": '["temp"]', "step": "4", "level": "2"})
        assert temp["clim"] == [pytest.approx(11, abs=0.01), pytest.approx(12, abs=0.01)]
        # the user's missing values blank the triangles of the nodes they are at
        app.viewer_missing = [(0, 11.5)]
        blanked = app.viewer_plot({"stream": "0", "vars": '["temp"]', "step": "4", "level": "2"})
        assert blanked["ok"] and blanked["clim"][0] > 11.5
        app.viewer_missing = []
        # Play's colour scale
        limits = app.viewer_limits({"stream": "0", "vars": '["temp"]', "level": "0"})
        assert (limits["vmin"], limits["vmax"]) == (pytest.approx(5), pytest.approx(17))
    finally:
        app.close()


def test_one_file(tmp_path):
    path = fvcom_file(str(tmp_path / "one.nc"))
    with catalogue.open_dataset(path) as ds:
        # (xarray before 2023 refused siglay and siglev, which are then left out)
        assert "temp" in ds.data_vars


# ---- coastlines ----

def _lines(item):
    """How many lines (holoviews Paths) a map has over its image."""
    return json.dumps(item).count('"glyph": {"type": "object", "name": "MultiLine"')


def _temp_map(found, **options):
    data, _ = found.field(0, ["temp"], 0, 0)
    return plotting.map_plot(data, found.combined(0, ["temp"]), "temp", mesh=found.mesh(0), **options)


def test_the_coastline_on_a_mesh(found, monkeypatch):
    # Natural Earth's coastline as cartopy would give it, without the internet
    asked = []

    def coastline(x_range, y_range, scale):
        asked.append((x_range, y_range, scale))
        return np.array([-4.9, -4.1, np.nan, -4.8, -4.2]), np.array([50.3, 50.4, np.nan, 50.9, 50.8])

    monkeypatch.setattr(plotting, "has_cartopy", lambda: True)
    monkeypatch.setattr(plotting, "natural_earth_coastline", coastline)
    item, notes, _ = _temp_map(found)
    assert _lines(item) == 0 and not asked
    item, notes, _ = _temp_map(found, coast=True)
    # Natural Earth's coastline alone (no line round the mesh), in the map's own longitude and latitude
    assert _lines(item) == 1
    (x0, x1), (y0, y1), scale = asked[0]
    assert x0 < -5 and x1 > -4 and y0 < 50 and y1 > 51 and scale == "10m"
    assert "Longitude" in str(item)
    # without cartopy, none
    monkeypatch.setattr(plotting, "has_cartopy", lambda: False)
    item, notes, _ = _temp_map(found, coast=True)
    assert _lines(item) == 0 and any("need cartopy" in note for note in notes)


def test_a_coastline_that_cannot_be_fetched(found, monkeypatch):
    def offline(*args):
        raise OSError("no internet")

    monkeypatch.setattr(plotting, "has_cartopy", lambda: True)
    monkeypatch.setattr(plotting, "natural_earth_coastline", offline)
    item, notes, _ = _temp_map(found, coast=True)
    # the map is still drawn, and the user is told
    assert _lines(item) == 0
    assert any(note.startswith(plotting.COAST_FAILED) and "no internet" in note for note in notes)


def test_the_natural_earth_coastline():
    pytest.importorskip("cartopy")
    pytest.importorskip("shapely")
    try:
        x, y = plotting.natural_earth_coastline((-5.2, -3.8), (49.8, 51.2), "50m")
    except Exception as error:  # noqa: BLE001 - not downloaded, and no internet
        pytest.skip(f"Natural Earth's coastline cannot be fetched here: {error}")
    finite = np.isfinite(x)
    # Cornwall and Devon, cut to the box
    assert finite.sum() > 10
    assert (x[finite] >= -5.2).all() and (x[finite] <= -3.8).all() and (y[finite] >= 49.8).all() and (y[finite] <= 51.2).all()
