"""Analyze with raw FVCOM output (see oceanval.fvcom_climatology,
oceanval.mesh_means and oceanval.mesh_maps): the climatology (at the surface,
the bottom and through the water column), the phenology, the long-term and
seasonal series and the map changes are worked out on the mesh itself, the
spatial means weighted by the areas of its cells, and the transects are
interpolated from the mesh onto a strip along each line, which nctoolkit's
to_transect extracts them from (see oceanval.fvcom_transects)."""

import ast
import datetime
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
import xarray as xr  # noqa: E402

import oceanval  # noqa: E402
from oceanval import (  # noqa: E402
    analysis, analysis_forms, fvcom_climatology, fvcom_transects, mesh_maps, mesh_means, missing_probe,
)
from oceanval.app import App  # noqa: E402
from test_app import get_json, leftover_dir, post, wait_for  # noqa: E402, F401 (leftover_dir is autouse)
from viewer_simulations import fvcom_file, fvcom_mesh, fvcom_months, fvcom_simulation  # noqa: E402

MONTHS = ((2012, 1), (2012, 2), (2012, 3), (2012, 4), (2013, 1), (2013, 2))
SURFACE = {"start": 2012, "end": 2013, "surface": True}


@pytest.fixture
def fvcom_sim(tmp_path):
    return fvcom_months(str(tmp_path / "fvcom_sim"), MONTHS)


def days_of(year, month):
    """The days since 2012-01-01 of each day of a month, as fvcom_file dates them."""
    first = datetime.date(year, month, 1)
    after = datetime.date(year + month // 12, month % 12 + 1, 1)
    return np.arange((first - datetime.date(2012, 1, 1)).days, (after - datetime.date(2012, 1, 1)).days)


def expected_temperature(lon, months=MONTHS):
    """The monthly climatology of temp at the surface (10 + lon + the day)
    and its annual mean, by hand: each year's monthly mean, then the mean of
    each month of the year over the years."""
    by_month = {}
    for year, month in months:
        by_month.setdefault(month, []).append(10 + lon + days_of(year, month).mean())
    climatology = np.array([np.mean(by_month[month], axis=0) for month in sorted(by_month)])
    return sorted(by_month), climatology, climatology.mean(axis=0)


def analyze(sim, out, **arguments):
    arguments.setdefault("variables", {"Temperature": "temp"})
    arguments.setdefault("climatology", {name: SURFACE for name in arguments["variables"]})
    return oceanval.analyze(sim_dir=sim, n_dirs_down=1, cores=1, view=False, out_dir=str(out), **arguments)


def results_of(out):
    return os.path.join(str(out), analysis.FOLDER, analysis.RESULTS)


class TestClimatology:
    def test_the_surface_climatology_is_worked_out_on_the_mesh(self, fvcom_sim, tmp_path, no_report):
        analyze(fvcom_sim, tmp_path / "out")
        info = no_report[-1]
        assert info["fvcom"] is True
        entry = info["variables"][0]
        layer = entry["climatology"]["layers"]["surface"]
        assert layer["mesh"] == "node" and layer["months"] == 4 and layer["units"] == "degrees_C"
        assert entry["levels"] == 5 and entry["time_res_text"] == "daily"
        lon, _, _ = fvcom_mesh()
        months, climatology, annual = expected_temperature(lon)
        results = results_of(tmp_path / "out")
        with xr.open_dataset(os.path.join(results, layer["monthly"])) as ds:
            assert ds["temperature"].dims == ("time", "node")
            assert list(ds["month"].values) == months
            assert np.allclose(ds["temperature"].values, climatology, atol=1e-4)
            # the mesh is kept, so the maps are drawn of the results alone
            assert {"lon", "lat", "nv"} <= set(ds.variables)
            assert ds["temperature"].attrs["long_name"] == "Temperature, surface"
        with xr.open_dataset(os.path.join(results, layer["annual"])) as ds:
            assert np.allclose(ds["temperature"].values[0], annual, atol=1e-4)

    def test_values_on_the_elements_and_sums(self, fvcom_sim, tmp_path, no_report):
        analyze(fvcom_sim, tmp_path / "out", variables={"Velocity": "u", "Plus": "temp+zeta"})
        info = no_report[-1]
        velocity, plus = info["variables"]
        assert velocity["climatology"]["layers"]["surface"]["mesh"] == "nele"
        results = results_of(tmp_path / "out")
        with xr.open_dataset(os.path.join(results, velocity["climatology"]["layers"]["surface"]["annual"])) as ds:
            # u is each element's number, at every time
            assert np.allclose(ds["velocity"].values[0], np.arange(ds.sizes["nele"]))
        lon, _, _ = fvcom_mesh()
        with xr.open_dataset(os.path.join(results, plus["climatology"]["layers"]["surface"]["monthly"])) as ds:
            assert np.allclose(ds["plus"].values, expected_temperature(lon)[1] + 0.5, atol=1e-4)

    def test_the_years_and_what_is_missing(self, fvcom_sim, tmp_path, no_report):
        analyze(
            fvcom_sim, tmp_path / "out", variables={"Velocity": "u", "Temperature": "temp"},
            climatology={"Velocity": SURFACE, "Temperature": {"start": 2013, "end": 2013, "surface": True}},
            as_missing=[4.5, 7.5],
        )
        velocity, temperature = no_report[-1]["variables"]
        results = results_of(tmp_path / "out")
        with xr.open_dataset(os.path.join(results, velocity["climatology"]["layers"]["surface"]["annual"])) as ds:
            values = ds["velocity"].values[0]
        assert np.isnan(values[5:8]).all() and np.isfinite(np.delete(values, [5, 6, 7])).all()
        # 2013 only: January and February
        lon, _, _ = fvcom_mesh()
        months, climatology, _ = expected_temperature(lon, MONTHS[4:])
        with xr.open_dataset(os.path.join(results, temperature["climatology"]["layers"]["surface"]["monthly"])) as ds:
            assert list(ds["month"].values) == months == [1, 2]
            assert np.allclose(ds["temperature"].values, climatology, atol=1e-4)

    def test_the_times_are_shifted(self, tmp_path):
        path = fvcom_file(str(tmp_path / "one.nc"), first_day=31, n_times=3)
        unshifted = fvcom_climatology.file_sums(path, ["temp"], [2012, 2012])
        shifted = fvcom_climatology.file_sums(path, ["temp"], [2012, 2012], time_shift=-1)
        assert set(unshifted) == {(2012, 2)} and set(shifted) == {(2012, 1), (2012, 2)}
        assert list(shifted[(2012, 1)][1][:1]) == [1] and list(shifted[(2012, 2)][1][:1]) == [2]

    def test_more_cores_give_the_same(self, fvcom_sim):
        files = sorted(os.path.join(root, name) for root, _, names in os.walk(fvcom_sim) for name in names)
        one = fvcom_climatology.monthly_sums(files, ["temp"], [2012, 2013], cores=1)
        two = fvcom_climatology.monthly_sums(files, ["temp"], [2012, 2013], cores=2)
        assert sorted(one) == sorted(two) == sorted(MONTHS)
        for month in one:
            assert np.allclose(one[month][0], two[month][0]) and np.array_equal(one[month][1], two[month][1])

    def test_parts_on_the_nodes_and_the_elements_cannot_be_summed(self, fvcom_sim, tmp_path, no_report):
        with pytest.raises(ValueError, match="not all on the same part of the FVCOM mesh"):
            analyze(fvcom_sim, tmp_path / "out", variables={"Both": "temp+u"})
        # before anything is worked out
        assert not os.path.exists(os.path.join(str(tmp_path / "out"), analysis.FOLDER))

    def test_the_streams_hold_the_mesh_s_variables_only(self, fvcom_sim):
        found = analysis.streams(fvcom_sim, 1)
        assert len(found) == 1 and found[0]["fvcom"] is True
        variables = found[0]["variables"]
        assert set(variables) == {"temp", "u", "zeta"}
        assert variables["temp"] == {"long_name": "temperature", "units": "degrees_C", "levels": 5}
        assert variables["zeta"]["levels"] == 1


def simulation_files(sim):
    return sorted(os.path.join(root, name) for root, _, names in os.walk(sim) for name in names)


def depth_of_water():
    """h + zeta of fvcom_file on its nodes, and on its elements (the mean of their nodes')."""
    lon, _, nv = fvcom_mesh()
    depth = (20 + 200 * (lon + 5)).astype("float32").astype("float64") + 0.5
    return depth, depth[nv - 1].mean(axis=0)


def mean_of(found, month):
    sums, counts = found[month]
    with np.errstate(invalid="ignore"):
        return np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)


class TestBottomAndWaterColumn:
    """fvcom_file's temp is 10 + lon + the layer + the day, on five layers of
    the same thickness, h is 20 + 200 (lon + 5) and zeta 0.5."""

    def test_the_bottom_and_water_column_with_no_thickness(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2013}
        analyze(
            fvcom_sim, tmp_path / "out", variables={"Temperature": "temp", "Heat": "temp", "Velocity": "u"},
            climatology={
                "Temperature": dict(years, bottom=True, water_column="mean"),
                "Heat": dict(years, water_column="integral"),
                "Velocity": dict(years, bottom=True, water_column="integral"),
            },
        )
        temperature, heat, velocity = no_report[-1]["variables"]
        results = results_of(tmp_path / "out")
        lon, _, _ = fvcom_mesh()
        _, climatology, _ = expected_temperature(lon)
        on_nodes, on_elements = depth_of_water()

        def values(entry, layer, key):
            saved = entry["climatology"]["layers"][layer]
            with xr.open_dataset(os.path.join(results, saved["monthly"])) as ds:
                return saved, ds[key].values, ds[key].attrs["long_name"]

        saved, bottom, long_name = values(temperature, "bottom", "temperature")
        assert saved["mesh"] == "node" and saved["title"] == "Bottom" and long_name == "Temperature, bottom"
        assert np.allclose(bottom, climatology + 4, atol=1e-4)
        saved, mean, _ = values(temperature, "mean", "temperature")
        assert saved["units"] == "degrees_C" and np.allclose(mean, climatology + 2, atol=1e-4)
        saved, integral, long_name = values(heat, "integral", "heat")
        assert saved["units"] == analysis.integral_units("degrees_C") == "degrees_C m"
        assert long_name == "Heat, water column: depth integral"
        assert np.allclose(integral, (climatology + 2) * on_nodes, rtol=1e-5)
        # u is each element's number in every layer
        saved, integral, _ = values(velocity, "integral", "velocity")
        assert saved["mesh"] == "nele" and saved["units"] == "meters s-1 m"
        assert np.allclose(integral, np.arange(len(on_elements)) * on_elements, rtol=1e-5)
        assert np.allclose(values(velocity, "bottom", "velocity")[1], np.arange(len(on_elements)))

    def test_each_layer_is_weighted_by_its_thickness_at_each_time(self, tmp_path):
        import netCDF4

        path = fvcom_file(str(tmp_path / "uneven.nc"), n_times=3)
        levels = np.array([0.0, -0.1, -0.3, -0.6, -0.8, -1.0])
        zeta = np.array([0.0, 1.0, 5.0])
        with netCDF4.Dataset(path, "a") as ds:
            nodes = ds.dimensions["node"].size
            ds.variables["siglev"][:] = np.repeat(levels[:, None], nodes, axis=1)
            ds.variables["zeta"][:] = np.repeat(zeta[:, None], nodes, axis=1)
            h = ds.variables["h"][:].astype("float64")
            temp = ds.variables["temp"][:].astype("float64")
        shares = -np.diff(levels)
        thickness = shares[None, :, None] * (h[None, None, :] + zeta[:, None, None])
        found = {
            layer: mean_of(fvcom_climatology.file_sums(path, ["temp"], [2012, 2012], layer=layer), (2012, 1))
            for layer in ("mean", "integral")
        }
        # the mean of each time's column: the thickness changes with zeta, but not the shares
        assert np.allclose(found["mean"], (temp * shares[None, :, None]).sum(axis=1).mean(axis=0), rtol=1e-6)
        assert np.allclose(found["integral"], (temp * thickness).sum(axis=1).mean(axis=0), rtol=1e-6)
        # not the even split
        assert not np.allclose(found["mean"], temp.mean(axis=(0, 1)))

    def test_what_is_missing_is_left_out(self, tmp_path):
        import netCDF4

        path = fvcom_file(str(tmp_path / "gaps.nc"), n_times=2)
        with netCDF4.Dataset(path, "a") as ds:
            temp = ds.variables["temp"][:]
            temp[:, 0, 0] = np.ma.masked  # the top layer of node 0
            temp[:, :, 1] = np.ma.masked  # every layer of node 1
            temp[:, 4, 2] = np.ma.masked  # the bottom layer of node 2
            ds.variables["temp"][:] = temp
            h = ds.variables["h"][:].astype("float64")
            full = ds.variables["temp"][:].data.astype("float64")
        lon, _, _ = fvcom_mesh()

        def layer(name):
            return mean_of(fvcom_climatology.file_sums(path, ["temp"], [2012, 2012], layer=name), (2012, 1))

        top = 10 + lon + 0.5  # the mean of days 0 and 1
        mean, integral, bottom = layer("mean"), layer("integral"), layer("bottom")
        # node 0: the four layers below the top, each a fifth of the depth
        assert np.isclose(mean[0], top[0] + 2.5) and np.isclose(integral[0], (top[0] + 2.5) * 0.8 * (h[0] + 0.5))
        assert np.isnan(mean[1]) and np.isnan(integral[1]) and np.isnan(bottom[1])
        # node 2: the deepest layer with a value
        assert np.isclose(bottom[2], top[2] + 3) and np.isclose(bottom[3], top[3] + 4)
        assert np.isclose(mean[3], top[3] + 2) and np.isfinite(full).all()

    def test_more_cores_give_the_same(self, fvcom_sim):
        files = simulation_files(fvcom_sim)
        for layer in ("bottom", "integral"):
            one = fvcom_climatology.monthly_sums(files, ["temp"], [2012, 2013], cores=1, layer=layer)
            two = fvcom_climatology.monthly_sums(files, ["temp"], [2012, 2013], cores=2, layer=layer)
            assert sorted(one) == sorted(two) == sorted(MONTHS)
            for month in one:
                assert np.allclose(one[month][0], two[month][0]) and np.array_equal(one[month][1], two[month][1])

    def test_a_sum_with_a_surface_variable(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2013}
        # the bottom of each part, added, as CDO takes it
        analyze(fvcom_sim, tmp_path / "out", variables={"Plus": "temp+zeta"},
                climatology={"Plus": dict(years, bottom=True)})
        saved = no_report[-1]["variables"][0]["climatology"]["layers"]["bottom"]
        lon, _, _ = fvcom_mesh()
        with xr.open_dataset(os.path.join(results_of(tmp_path / "out"), saved["monthly"])) as ds:
            assert np.allclose(ds["plus"].values, expected_temperature(lon)[1] + 4.5, atol=1e-4)
        # but zeta has no water column
        with pytest.raises(ValueError, match="the water column needs every part of it on the sigma layers"):
            analyze(fvcom_sim, tmp_path / "again", variables={"Plus": "temp+zeta"},
                    climatology={"Plus": dict(years, water_column="mean")})

    def test_what_has_no_water_column_is_refused(self, fvcom_sim, tmp_path, no_report):
        import netCDF4

        for path in simulation_files(fvcom_sim):
            with netCDF4.Dataset(path, "a") as ds:
                km = ds.createVariable("km", "f4", ("time", "siglev", "node"))
                km[:] = 1.0
                km.units = "m2 s-1"
        years = {"start": 2012, "end": 2013}
        with pytest.raises(ValueError, match=r"km is not \(on the sigma levels, siglev"):
            analyze(fvcom_sim, tmp_path / "out", variables={"Viscosity": "km"},
                    climatology={"Viscosity": dict(years, water_column="integral")})
        with pytest.raises(ValueError, match="has only one vertical level"):
            analyze(fvcom_sim, tmp_path / "out", variables={"Elevation": "zeta"},
                    climatology={"Elevation": dict(years, bottom=True)})
        assert not os.path.exists(os.path.join(str(tmp_path / "out"), analysis.FOLDER))
        # the bottom of what is on the sigma levels is the seabed's
        analyze(fvcom_sim, tmp_path / "out", variables={"Viscosity": "km"},
                climatology={"Viscosity": dict(years, bottom=True)})
        assert no_report[-1]["variables"][0]["climatology"]["layers"]["bottom"]["mesh"] == "node"


EARTH_M = 6_371_000.0


def plane_areas(lon, lat, nv):
    """The area of each triangle of the mesh in square metres, by hand: flat,
    a degree of longitude cos(latitude) of one of latitude, which is close
    enough for triangles a tenth of a degree across."""
    corners_lon, corners_lat = lon[nv - 1], lat[nv - 1]
    scale = np.radians(1) * EARTH_M
    x = (corners_lon - corners_lon[0]) * scale * np.cos(np.radians(corners_lat.mean(axis=0)))
    y = (corners_lat - corners_lat[0]) * scale
    return 0.5 * np.abs(x[1] * y[2] - x[2] * y[1])


def hand_node_areas(lon, lat, nv):
    areas = np.zeros(len(lon))
    for corner in range(3):
        np.add.at(areas, nv[corner] - 1, plane_areas(lon, lat, nv) / 3)
    return areas


def stored(values):
    """values as fvcom_file stores them, as float32."""
    return np.asarray(values, dtype="float32").astype("float64")


def series_of(out, key, layer, kind):
    import pandas as pd

    return pd.read_csv(os.path.join(results_of(out), f"{key}_{layer}_{kind}.csv"))


def write_regions(path):
    """A regions file on a grid of 0.3 degree cells whose sides are clear of
    the mesh's nodes: "West", where the cells' centres are west of 4.5W, and
    "Nowhere", out of the mesh."""
    lon = -5.95 + 0.3 * np.arange(10)
    lat = 48.95 + 0.3 * np.arange(10)
    west = np.where(lon[None, :] < -4.5, 1.0, np.nan) * np.ones((len(lat), 1))
    nowhere = np.full((len(lat), len(lon)), np.nan)
    nowhere[0, 0] = 1.0
    xr.Dataset(
        {"west": (("lat", "lon"), west, {"long_name": "West"}),
         "nowhere": (("lat", "lon"), nowhere, {"long_name": "Nowhere"})},
        coords={"lat": ("lat", lat, {"units": "degrees_north"}), "lon": ("lon", lon, {"units": "degrees_east"})},
    ).to_netcdf(path)
    return path


class TestAreas:
    def test_a_triangle_in_the_plane_and_on_the_sphere(self):
        assert np.isclose(mesh_means.triangle_areas([0, 3, 0], [0, 0, 4], np.array([[0, 1, 2]]), cartesian=True)[0], 6)
        # an eighth of the globe
        octant = mesh_means.triangle_areas([0, 90, 0], [0, 0, 90], np.array([[0, 1, 2]]))[0]
        assert np.isclose(octant, 4 * np.pi * EARTH_M**2 / 8)

    def test_the_mesh_s_areas(self):
        lon, lat, nv = fvcom_mesh()
        areas = mesh_means.triangle_areas(lon, lat, nv.T - 1)
        assert np.allclose(areas, plane_areas(lon, lat, nv), rtol=1e-3)
        nodes = mesh_means.node_areas(areas, nv.T - 1, len(lon))
        # the control volumes of the nodes cover the mesh, as FVCOM's art1 does
        assert np.isclose(nodes.sum(), areas.sum()) and np.allclose(nodes, hand_node_areas(lon, lat, nv), rtol=1e-3)
        # a node on the edge of the mesh has less of it than one inside
        assert nodes.min() < 0.5 * nodes.max()

    def test_the_cells_of_a_file(self, tmp_path):
        path = fvcom_file(str(tmp_path / "one.nc"), n_times=1)
        lon, lat, nv = fvcom_mesh()
        nodes, elements = mesh_means.MeshCells(path, "node"), mesh_means.MeshCells(path, "nele")
        assert np.isclose(nodes.areas.sum(), elements.areas.sum())
        assert np.allclose(elements.lon, stored(lon)[nv - 1].mean(axis=0), atol=1e-5)
        # the sides of the triangles, about 7 and 11 km, and their diagonals
        assert 5 < np.nanmin(nodes.size) and np.nanmax(nodes.size) < 15
        flat = mesh_means.MeshCells(fvcom_file(str(tmp_path / "flat.nc"), n_times=1, cartesian=True), "node")
        assert flat.cartesian and np.isclose(flat.areas.sum(), 0.1 * 70000 * 0.1 * 111000 * (100 - 25), rtol=1e-4)

    def test_a_weighted_mean(self):
        fields = np.array([[1.0, 2.0, np.nan], [np.nan, np.nan, np.nan]])
        assert np.allclose(mesh_means.weighted_mean(fields, np.array([1.0, 3.0, 5.0]))[0], 1.75)
        assert np.isnan(mesh_means.weighted_mean(fields, np.array([1.0, 3.0, 5.0]))[1])


class TestSpatialMeans:
    @pytest.fixture
    def cells(self, tmp_path):
        return mesh_means.MeshCells(fvcom_file(str(tmp_path / "one.nc"), n_times=1), "node")

    def test_the_domain_and_a_box(self, cells):
        lon, lat, nv = fvcom_mesh()
        weights = hand_node_areas(lon, lat, nv)
        series = mesh_means.Series(cells, lon_lim=[-5.0, -4.65], lat_lim=[50.0, 51.0])
        series.add((2012, 1), cells.lon)
        (row,), notes = series.region_rows()
        west = lon < -4.65
        assert row[:4] == ("domain", "Full Domain", 2012, 1) and notes == []
        assert np.isclose(row[4], (weights * lon)[west].sum() / weights[west].sum(), atol=1e-5)
        whole = mesh_means.Series(cells)
        whole.add((2012, 1), cells.lon)
        value = whole.region_rows()[0][0][4]
        assert np.isclose(value, (weights * lon).sum() / weights.sum(), atol=1e-5)
        # weighted by area, not the plain mean
        assert not np.isclose(value, lon.mean(), atol=1e-4)

    def test_regions(self, cells, tmp_path):
        lon, lat, nv = fvcom_mesh()
        weights = hand_node_areas(lon, lat, nv)
        series = mesh_means.Series(cells, region_file=write_regions(str(tmp_path / "regions.nc")))
        series.add((2012, 1), cells.lon)
        rows, notes = series.region_rows()
        west = lon < -4.6
        assert [row[:2] for row in rows] == [("domain", "Full Domain"), ("region", "West")]
        assert np.isclose(rows[1][4], (weights * lon)[west].sum() / weights[west].sum(), atol=1e-5)
        assert notes == ["Nowhere has no cells in the model's domain, so it is left out."]

    def test_stations(self, cells):
        series = mesh_means.Series(cells, stations={"Near": [-4.52, 50.31]})
        series.add((2012, 1), cells.lon)
        rows, found = series.station_rows()
        assert found["Near"]["lon"] == pytest.approx(-4.5, abs=1e-5) and found["Near"]["lat"] == pytest.approx(50.3, abs=1e-5)
        assert rows == [("station", "Near", 2012, 1, pytest.approx(-4.5, abs=1e-5))]
        # where the mesh is cut out, and far from it
        for where in ([-4.1, 50.9], [0.0, 40.0]):
            far = mesh_means.Series(cells, stations={"Far": where})
            far.add((2012, 1), cells.lon)
            with pytest.raises(ValueError, match="Far is .* km from the nearest cell of the FVCOM mesh with values"):
                far.station_rows()

    def test_a_station_is_in_the_nearest_cell_with_values(self, cells):
        series = mesh_means.Series(cells, stations={"Near": [-4.52, 50.31]})
        field = cells.lon.copy()
        field[np.isclose(cells.lon, -4.5) & np.isclose(cells.lat, 50.3)] = np.nan
        series.add((2012, 1), field)
        _, found = series.station_rows()
        assert found["Near"]["distance"] > 2


class TestSeries:
    """fvcom_sim's surface temp is 10 + lon + the days since 2012-01-01."""

    def expected(self, weights, lon):
        return {(year, month): 10 + (weights * stored(lon)).sum() / weights.sum() + days_of(year, month).mean()
                for year, month in MONTHS}

    def test_the_domain_series_are_weighted_by_area(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2013}
        analyze(fvcom_sim, tmp_path / "out", climatology=None, long_term={"Temperature": dict(years, surface=True)},
                seasonal={"Temperature": dict(years, surface=True, water_column="integral")})
        entry = no_report[-1]["variables"][0]
        assert entry["seasonal"]["layers"]["integral"]["units"] == "degrees_C m"
        lon, lat, nv = fvcom_mesh()
        expected = self.expected(hand_node_areas(lon, lat, nv), lon)
        monthly = series_of(tmp_path / "out", "temperature", "surface", "seasonal")
        assert set(monthly.kind) == {"domain"} and len(monthly) == 24
        for row in monthly.itertuples():
            if (row.year, row.month) in expected:
                assert np.isclose(row.value, expected[(row.year, row.month)], atol=1e-3)
            else:
                assert np.isnan(row.value)
        annual = series_of(tmp_path / "out", "temperature", "surface", "long_term")
        assert list(annual.year) == [2012, 2013] and list(annual.months) == [4, 2]
        assert np.isclose(annual.value[0], np.mean([expected[(2012, m)] for m in (1, 2, 3, 4)]), atol=1e-3)
        assert entry["long_term"]["layers"]["surface"]["incomplete"] == [2012, 2013]
        # the depth integral: the mean temperature of the column (2 above the surface's) times its depth
        on_nodes, _ = depth_of_water()
        integral = series_of(tmp_path / "out", "temperature", "integral", "seasonal").dropna()
        weights = hand_node_areas(lon, lat, nv)
        first = (weights * (10 + stored(lon) + days_of(2012, 1).mean() + 2) * on_nodes).sum() / weights.sum()
        assert np.isclose(integral.value.iloc[0], first, rtol=1e-4)

    def test_regions_and_stations(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2013}
        analyze(fvcom_sim, tmp_path / "out", climatology=None, variables={"Velocity": "u"},
                long_term={"Velocity": dict(years, surface=True)}, subregions=write_regions(str(tmp_path / "r.nc")),
                stations={"Near": [-4.52, 50.31]})
        entry = no_report[-1]["variables"][0]["long_term"]
        assert entry["regions"] == ["West"] and set(entry["stations"]) == {"Near"}
        assert any("Nowhere has no cells" in note for note in entry["notes"])
        annual = series_of(tmp_path / "out", "velocity", "surface", "long_term")
        assert set(annual.kind) == {"domain", "region", "station"}
        # u is each element's number: the station's is that of the element nearest it
        lon, lat, nv = fvcom_mesh()
        centres = mesh_means._distance_km(-4.52, 50.31, lon[nv - 1].mean(axis=0), lat[nv - 1].mean(axis=0))
        assert annual[annual.kind == "station"].value.iloc[0] == pytest.approx(float(np.argmin(centres)))

    def test_more_cores_give_the_same(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2013}
        found = []
        for cores in (1, 2):
            out = tmp_path / f"out{cores}"
            oceanval.analyze(sim_dir=fvcom_sim, n_dirs_down=1, cores=cores, view=False, out_dir=str(out),
                             variables={"Temperature": "temp"}, seasonal={"Temperature": dict(years, bottom=True)})
            found.append(series_of(out, "temperature", "bottom", "seasonal"))
        assert found[0].equals(found[1])

    def test_a_mesh_in_metres(self, tmp_path, no_report):
        sim = fvcom_simulation(str(tmp_path / "flat_sim"), cartesian=True)
        arguments = dict(sim_dir=sim, n_dirs_down=0, cores=1, view=False, variables={"Temperature": "temp"},
                         long_term={"Temperature": {"start": 2012, "end": 2012, "surface": True}})
        with pytest.raises(ValueError, match="is in metres, with no longitudes and latitudes"):
            oceanval.analyze(out_dir=str(tmp_path / "out"), stations={"Near": [-4.5, 50.3]}, **arguments)
        oceanval.analyze(out_dir=str(tmp_path / "out"), **arguments)
        lon, _, nv = fvcom_mesh()
        # every triangle has the same area in the plane, so a node's weight is how many it is a corner of
        corners = np.bincount((nv - 1).ravel(), minlength=len(lon))
        value = series_of(tmp_path / "out", "temperature", "surface", "long_term").value.iloc[0]
        assert np.isclose(value, 10 + (corners * stored(lon)).sum() / corners.sum() + 5.5, atol=1e-3)


def write_peaks(sim):
    """Make the surface temp of the daily files of sim peak on a day of the
    year of its own at each node: minus how many days the day is from it,
    its day being 20 + the node's number % 60, the days counted as 2012 has
    them (a leap year), so each day of the year is the same in every year.
    Returns the days of the peaks."""
    import netCDF4

    peaks = None
    for path in simulation_files(sim):
        with netCDF4.Dataset(path, "a") as ds:
            nodes = ds.dimensions["node"].size
            peaks = 20 + np.arange(nodes) % 60
            days = []
            for day in ds.variables["time"][:]:
                date = datetime.date(1858, 11, 17) + datetime.timedelta(days=int(day))
                days.append((datetime.date(2012, date.month, date.day) - datetime.date(2012, 1, 1)).days + 1)
            temp = ds.variables["temp"][:]
            temp[:, 0, :] = -np.abs(np.array(days)[:, None] - peaks[None, :])
            ds.variables["temp"][:] = temp
    return peaks


class TestPhenology:
    def test_the_peak_of_each_node(self, fvcom_sim, tmp_path, no_report):
        from oceanval import analysis_plots as ap

        peaks = write_peaks(fvcom_sim)
        analyze(fvcom_sim, tmp_path / "out", climatology=None, phenology={"Temperature": {"start": 2012, "end": 2013}})
        entry = no_report[-1]["variables"][0]["phenology"]
        # January to April, 29 February too
        assert entry["mesh"] == "node" and entry["days"] == 121
        results = results_of(tmp_path / "out")
        with xr.open_dataset(os.path.join(results, entry["file"])) as ds:
            assert ds.attrs["mesh"] == "node" and {"lon", "lat", "nv"} <= set(ds.variables)
            assert np.array_equal(ds[analysis.PEAK_DAY].values, peaks)
            assert np.allclose(ds[analysis.PEAK_VALUE].values, 0)
        # the daily cycle's mean over the domain, weighted by the nodes' areas
        times, values, note = ap.domain_mean(os.path.join(results, entry["daily"]), "temperature")
        lon, lat, nv = fvcom_mesh()
        weights = hand_node_areas(lon, lat, nv)
        first = (weights * -np.abs(1 - peaks)).sum() / weights.sum()
        assert len(times) == 121 and note == "" and np.isclose(values[0], first, rtol=1e-3)


class TestMapChanges:
    def test_the_change_between_two_periods(self, fvcom_sim, tmp_path, no_report):
        periods = {"historical": {"start": 2012, "end": 2012}, "future": [{"start": 2013, "end": 2013}],
                   "plot": "absolute_changes", "surface": True}
        analyze(fvcom_sim, tmp_path / "out", climatology=None, variables={"Temperature": "temp", "Percent": "temp"},
                map_changes={"Temperature": dict(periods, change="absolute"),
                             "Percent": dict(periods, change="percentage", water_column="integral")})
        temperature, percent = no_report[-1]["variables"]
        saved = temperature["map_changes"]["layers"]["surface"]
        assert saved["mesh"] == "node"
        lon, _, _ = fvcom_mesh()
        historical = 10 + stored(lon) + np.mean([days_of(2012, m).mean() for m in (1, 2, 3, 4)])
        future = 10 + stored(lon) + np.mean([days_of(2013, m).mean() for m in (1, 2)])
        results = results_of(tmp_path / "out")

        def read(name, key):
            with xr.open_dataset(os.path.join(results, name)) as ds:
                return ds[key].values[0]

        assert np.allclose(read(saved["historical"], "temperature"), historical, atol=1e-3)
        assert np.allclose(read(saved["futures"][0], "temperature"), future, atol=1e-3)
        assert np.allclose(read(saved["changes"][0], "temperature"), future - historical, atol=1e-3)
        shown = percent["map_changes"]["layers"]["surface"]
        assert shown["change_units"] == "%"
        assert np.allclose(read(shown["changes"][0], "percent"), 100 * (future - historical) / historical, rtol=1e-4)
        assert percent["map_changes"]["layers"]["integral"]["units"] == "degrees_C m"


def analyzed(built, sim, out, **arguments):
    """analyze(), and the info it saved (built is no_report)."""
    analyze(sim, out, **arguments)
    return built[-1]


def transect_csv(out, key, number, option):
    import pandas as pd

    return pd.read_csv(os.path.join(results_of(out), f"{key}_transect_{number}_{option}.csv"))


def inside_mesh(lons, lats):
    """Which of the points are inside the mesh of fvcom_mesh, and the element (from 0) each is in."""
    from matplotlib.tri import Triangulation

    lon, lat, nv = fvcom_mesh()
    found = Triangulation(lon, lat, nv.T - 1).get_trifinder()(np.asarray(lons, float), np.asarray(lats, float))
    return found >= 0, found


def elements_at(lon, lat):
    """The elements (from 0) of fvcom_mesh the point is in, or on a side or corner of."""
    mesh_lon, mesh_lat, nv = fvcom_mesh()
    x, y = mesh_lon[nv - 1], mesh_lat[nv - 1]
    det = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
    first = ((y[1] - y[2]) * (lon - x[2]) + (x[2] - x[1]) * (lat - y[2])) / det
    second = ((y[2] - y[0]) * (lon - x[2]) + (x[0] - x[2]) * (lat - y[2])) / det
    near = -1e-9
    return set(np.flatnonzero((first >= near) & (second >= near) & (1 - first - second >= near)).tolist())


def water_depth(lon):
    """The depth of the water of fvcom_file: h + zeta."""
    return 20 + 200 * (lon + 5) + 0.5


class TestWeights:
    def mesh(self):
        from oceanval.viewer_mesh import Mesh

        lon, lat, nv = fvcom_mesh()
        return Mesh(lon, lat, nv.T - 1)

    def test_a_value_on_the_nodes_is_linear_within_its_triangle(self):
        mesh = self.mesh()
        lons, lats = np.array([-5.0, -4.93, -4.5, -3.5]), np.array([50.0, 50.07, 50.55, 50.5])
        cells, weights = fvcom_transects.weights(mesh, "node", lons, lats)
        # a linear field is interpolated exactly
        field = 3 * mesh.x - 2 * mesh.y
        values = fvcom_transects.interpolate(field, cells, weights)
        assert np.allclose(values[:3], 3 * lons[:3] - 2 * lats[:3]) and np.isnan(values[3])
        # at a node, all of the weight is on it
        assert np.isclose(np.nanmax(weights[0]), 1)
        # a missing value at one corner leaves out the triangles it is a corner of
        field[cells[1, 0]] = np.nan
        assert np.isnan(fvcom_transects.interpolate(field, cells, weights)[1])

    def test_a_value_on_the_elements_fills_its_triangle(self):
        mesh = self.mesh()
        lons, lats = np.array([-4.93, -4.47, -3.5]), np.array([50.02, 50.31, 50.5])
        cells, weights = fvcom_transects.weights(mesh, "nele", lons, lats)
        values = fvcom_transects.interpolate(np.arange(mesh.elements, dtype=float), cells, weights)
        _, found = inside_mesh(lons, lats)
        assert values[:2].tolist() == found[:2].tolist() and np.isnan(values[2])

    def test_the_strip_holds_each_point_in_a_cell_of_its_own(self):
        line = {"start": [-4.5, 50.9], "end": [-4.5, 50.1]}
        lons, lats, (point_lons, point_lats) = fvcom_transects.strip(line, 9)
        assert len(lons) == 2 and np.allclose(lons.mean(), -4.5)
        # two rows round each point, in order from the south
        assert np.allclose(point_lats, np.linspace(50.1, 50.9, 9)) and np.allclose(point_lons, -4.5)
        assert np.allclose(lats.reshape(-1, 2).mean(axis=1), point_lats) and np.all(np.diff(lats) > 0)


class TestTransects:
    def test_the_surface_climatology_along_a_line(self, fvcom_sim, tmp_path, no_report):
        # an east-west line that runs off the mesh at both ends
        years = {"start": 2012, "end": 2013}
        info = analyzed(no_report, fvcom_sim, tmp_path / "out", climatology=None, transects={"Temperature": [
            {"start": [-5.2, 50.25], "end": [-3.8, 50.25], "climatology": years}]})
        line = info["variables"][0]["transects"]["lines"][0]
        assert info["variables"][0]["transects"]["mesh"] == "node" and line["steps"] > 2
        saved = line["options"]["climatology"]
        assert saved["months"] == 4 and not saved["empty"]
        frame = transect_csv(tmp_path / "out", "temperature", 1, "climatology")
        positions = np.linspace(-5.2, -3.8, line["steps"])
        assert np.allclose(np.sort(frame.position.unique()), positions)
        inside, _ = inside_mesh(positions, np.full(positions.size, 50.25))
        months, climatology, _ = expected_temperature(positions)
        for number, month in enumerate(months):
            values = frame[frame.month == month].sort_values("position").value.to_numpy()
            # 10 + lon + the day is linear on the mesh, so it is interpolated exactly
            assert np.allclose(values[inside], climatology[number][inside], rtol=1e-6)
            assert np.isnan(values[~inside]).all()

    def test_the_section_through_the_water_column(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2013}
        info = analyzed(no_report, fvcom_sim, tmp_path / "out", climatology=None, transects={"Temperature": [
            {"start": [-5.0, 50.25], "end": [-4.0, 50.25], "vertical": years}]})
        saved = info["variables"][0]["transects"]["lines"][0]["options"]["vertical"]
        assert saved["depths"] == "sigma" and not saved["empty"]
        frame = transect_csv(tmp_path / "out", "temperature", 1, "vertical").dropna()
        _, _, annual = expected_temperature(frame.position.to_numpy())
        depth = water_depth(frame.position.to_numpy())
        # temp is the surface's + the layer, and layer k's middle is (k + 0.5) / 5 of the depth of the water,
        # so between the middles of the top and bottom layers it is the surface's + 5 x depth / water - 0.5
        assert np.allclose(frame.value, annual + 5 * frame.depth / depth - 0.5, rtol=1e-5)
        assert np.isclose(frame.depth.min(), 0.1 * water_depth(-5.0), rtol=1e-4)
        assert np.isclose(frame.depth.max(), 0.9 * water_depth(-4.0), rtol=1e-3)

    def test_values_on_the_elements(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2012}
        info = analyzed(no_report, fvcom_sim, tmp_path / "out", variables={"Velocity": "u"}, climatology=None, transects={
            "Velocity": [{"start": [-4.47, 50.0], "end": [-4.47, 51.0], "climatology": years}]})
        line = info["variables"][0]["transects"]["lines"][0]
        assert info["variables"][0]["transects"]["mesh"] == "nele"
        frame = transect_csv(tmp_path / "out", "velocity", 1, "climatology")
        january = frame[frame.month == 1].sort_values("position")
        lats = np.linspace(50.0, 51.0, line["steps"])
        inside, _ = inside_mesh(np.full(lats.size, -4.47), lats)
        values = january.value.to_numpy()
        # u is the number of the element, which fills its triangle (a point on a side is in either)
        for lat, value in zip(lats[inside], values[inside]):
            assert value in elements_at(-4.47, lat)
        assert np.isnan(values[~inside]).all() and (~inside).any()

    def test_the_changes_over_the_years(self, tmp_path, no_report):
        sim = fvcom_months(str(tmp_path / "sim"), [(year, month) for year in (2012, 2013) for month in range(1, 13)])
        years = {"start": 2012, "end": 2013}
        info = analyzed(no_report, sim, tmp_path / "out", climatology=None, transects={"Temperature": [
            {"start": [-5.0, 50.25], "end": [-4.0, 50.25], "trends": dict(years, how="absolute_change"),
             "vertical_trends": dict(years, how="absolute")}]})
        options = info["variables"][0]["transects"]["lines"][0]["options"]
        assert options["trends"]["years"] == [2012, 2013] and options["trends"]["notes"] == []
        surface = transect_csv(tmp_path / "out", "temperature", 1, "trends")
        # each year's mean is the mean of its months' means, and 10 + lon + the day changes only by the days
        change = np.mean([days_of(2013, m).mean() - days_of(2012, m).mean() for m in range(1, 13)])
        later = surface[surface.year == 2013].dropna()
        assert len(later) and np.allclose(later.value, change, rtol=1e-5)
        assert np.allclose(surface[surface.year == 2012].dropna().value, 0)
        vertical = transect_csv(tmp_path / "out", "temperature", 1, "vertical_trends")
        assert {"position", "depth", "year", "value"} <= set(vertical) and vertical.value.notna().any()
        first = vertical[vertical.year == 2012].dropna()
        annual = 10 + first.position + np.mean([days_of(2012, m).mean() for m in range(1, 13)])
        assert np.allclose(first.value, annual + 5 * first.depth / water_depth(first.position) - 0.5, rtol=1e-5)

    def test_more_cores_give_the_same(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2013}
        lines = {"Temperature": [{"start": [-4.55, 50.0], "end": [-4.55, 51.0], "climatology": years, "vertical": years}]}
        analyze(fvcom_sim, tmp_path / "one", climatology=None, transects=lines)
        oceanval.analyze(sim_dir=fvcom_sim, n_dirs_down=1, cores=2, view=False, out_dir=str(tmp_path / "two"),
                         variables={"Temperature": "temp"}, transects=lines)
        for option in ("climatology", "vertical"):
            one = transect_csv(tmp_path / "one", "temperature", 1, option)
            two = transect_csv(tmp_path / "two", "temperature", 1, option)
            assert np.allclose(one.value, two.value, equal_nan=True)

    def test_what_has_no_section_is_refused(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2013}
        with pytest.raises(ValueError, match="a section through the water column needs every part of it on the sigma layers"):
            analyze(fvcom_sim, tmp_path / "out", variables={"Plus": "temp+zeta"}, climatology=None, transects={
                "Plus": [{"start": [-5.0, 50.25], "end": [-4.0, 50.25], "vertical": years}]})
        # the surface is fine
        analyze(fvcom_sim, tmp_path / "out", variables={"Plus": "temp+zeta"}, climatology=None, transects={
            "Plus": [{"start": [-5.0, 50.25], "end": [-4.0, 50.25], "climatology": years}]})

    def test_a_mesh_in_metres_has_no_transects(self, tmp_path, no_report):
        sim = fvcom_simulation(str(tmp_path / "sim"), cartesian=True)
        with pytest.raises(ValueError, match="the FVCOM mesh is in metres, with no longitudes and latitudes, so it has no transects"):
            oceanval.analyze(sim_dir=sim, n_dirs_down=0, cores=1, view=False, out_dir=str(tmp_path / "out"),
                             variables={"Temperature": "temp"}, transects={"Temperature": [
                                 {"start": [-5.0, 50.25], "end": [-4.0, 50.25], "climatology": {"start": 2012, "end": 2012}}]})


class TestWhatIsRefused:
    def test_the_water_column_of_the_series_needs_the_sigma_layers(self, fvcom_sim, tmp_path, no_report):
        years = {"start": 2012, "end": 2013}
        with pytest.raises(ValueError, match="the water column needs every part of it on the sigma layers"):
            analyze(fvcom_sim, tmp_path / "out", variables={"Plus": "temp+zeta"}, climatology=None,
                    long_term={"Plus": dict(years, water_column="mean")})


class TestMaps:
    @pytest.fixture
    def results(self, fvcom_sim, tmp_path, no_report):
        analyze(fvcom_sim, tmp_path / "out", variables={"Temperature": "temp", "Velocity": "u"})
        return results_of(tmp_path / "out")

    def test_the_maps_are_drawn_on_the_mesh(self, results):
        from oceanval import analysis_plots as ap

        for function in (ap.annual_map, ap.monthly_maps):
            for key in ("temperature", "velocity"):
                drawn = function(results, key, "surface")
                assert not drawn.regridded and "to_latlon" not in drawn.code and "pub_plot" not in drawn.code
                assert "from oceanval.mesh_maps import" in drawn.code
                assert any(ax.collections for ax in plt.gcf().axes)
                plt.close("all")
                ast.parse(drawn.code)

    def test_a_triangle_with_a_value_missing_is_not_drawn(self, tmp_path):
        path = fvcom_file(str(tmp_path / "one.nc"), n_times=1)
        whole = mesh_maps.mesh_plot(path, "temp")
        drawn = [c for c in whole.collections if hasattr(c, "get_array") and c.get_array() is not None]
        plt.close("all")
        import netCDF4

        with netCDF4.Dataset(path, "a") as ds:
            values = ds.variables["temp"][:]
            values[0, 0, 0] = np.ma.masked
            ds.variables["temp"][:] = values
        ax = mesh_maps.mesh_plot(path, "temp")
        mask = next(c for c in ax.collections if hasattr(c, "_triangulation"))._triangulation.mask
        plt.close("all")
        _, _, nv = fvcom_mesh()
        assert drawn and mask is not None and mask.sum() == ((nv - 1) == 0).any(axis=0).sum()

    def test_a_mesh_in_metres_is_drawn_on_plain_axes(self, tmp_path):
        path = fvcom_file(str(tmp_path / "flat.nc"), n_times=1, cartesian=True)
        ax = mesh_maps.mesh_plot(path, "u", title="Flat")
        assert ax.get_xlabel() == "x (m)" and not hasattr(ax, "projection")
        plt.close("all")
        fig = mesh_maps.mesh_panel_plot(path, "temp", ["One"], ncol=2)
        assert len([ax for ax in fig.axes if ax.get_title()]) == 1
        plt.close("all")


class TestMissingProbe:
    def test_the_surface_of_fvcom_output_is_read(self, fvcom_sim):
        files = sorted(os.path.join(root, name) for root, _, names in os.walk(fvcom_sim) for name in names)
        slices = missing_probe._slices(files[:2], "temp", 2)
        lon, _, _ = fvcom_mesh()
        assert len(slices) == 2 and slices[0].shape == lon.shape
        assert np.allclose(slices[0], 10 + lon, atol=1e-4)


# ---- the window ----


@pytest.fixture
def app(tmp_path):
    app = App(cwd=str(tmp_path))
    app.start()
    yield app
    app.close()


def to_options(app):
    assert post(app, "/api/choose", {"action": "analyze"})[0] == 200
    form = dict(app.analyze_setup_form, simdir="fvcom_sim", ndown="1")
    assert post(app, "/api/setup", {"form": form}) == (200, {"ok": True})
    wait_for(lambda: app.analyze_found["status"] != "reading")
    assert app.analyze_found["status"] == "done", app.analyze_found["error"]
    rows = [{"name": "Temperature", "model_variable": "temp"}]
    assert post(app, "/api/analyze_variables", {"rows": rows}) == (200, {"ok": True})
    wait_for(lambda: app.analyze_check is not None and app.analyze_check.status != "running", timeout=60)
    assert post(app, "/api/analyze_files") == (200, {"ok": True})
    return get_json(app, "/api/state")[1]["analyze"]


class TestWindow:
    def test_everything_can_be_chosen(self, app, fvcom_sim, monkeypatch):
        started = []
        monkeypatch.setattr(app, "start_run", lambda args, label: started.append(args))
        state = to_options(app)
        assert state["fvcom"] is True and "transects are interpolated from the mesh" in state["fvcom_note"]
        # the dates are checked as matchup(fvcom=True) reads them
        assert app.analyze_check.request["fvcom"] is True and app.analyze_check.status == "done"
        years = {"use": True, "start": "2012", "end": "2013"}
        # the transects, with a section through the water column and no thickness asked for
        line = dict(analysis_forms.blank_transect(), start_lon="-5", start_lat="50.5", end_lon="-4", end_lat="50.5",
                    climatology=True, climatology_start="2012", climatology_end="2012",
                    vertical=True, vertical_start="2012", vertical_end="2013")
        assert post(app, "/api/analyze_option", {"kind": "transects", "form": {
            "rows": {"Temperature": {"use": True, "transects": [line]}}, "thickness": ""}}) == (200, {"ok": True})
        # the phenology, of daily output
        assert post(app, "/api/analyze_option", {"kind": "phenology", "form": {"rows": {"Temperature": years}}}) == (
            200, {"ok": True})
        # the bottom and the water column, with no thickness asked for: it is worked out from the mesh
        column = {"rows": {"Temperature": dict(years, surface=True, bottom=True, water_column=True, how="mean")},
                  "thickness": ""}
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": column}) == (200, {"ok": True})
        changes = {"rows": {"Temperature": {
            "use": True, "historical_start": "2012", "historical_end": "2012", "future1_start": "2013",
            "future1_end": "2013", "future2_start": "", "future2_end": "", "plot": "absolute", "change": "absolute",
            "surface": False, "bottom": False, "water_column": True, "how": "integral"}}, "thickness": ""}
        assert post(app, "/api/analyze_option", {"kind": "map_changes", "form": changes}) == (200, {"ok": True})
        for kind, form in (("cores", {"cores": "1"}), ("missing", {"from": "", "to": ""}), ("figure_code", {"choice": "no"})):
            assert post(app, "/api/analyze_option", {"kind": kind, "form": form})[0] == 200
        assert post(app, "/api/analyze_run") == (200, {"ok": True})
        script = os.path.join(app.cwd, "analysis.py")
        call = ast.parse(open(script).read()).body[-1].value
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        assert given["climatology"] == {"Temperature": {"start": 2012, "end": 2013, "surface": True, "bottom": True,
                                                        "water_column": "mean"}}
        assert given["phenology"] == {"Temperature": {"start": 2012, "end": 2013}}
        assert given["map_changes"]["Temperature"]["water_column"] == "integral"
        assert given["transects"]["Temperature"][0]["vertical"] == {"start": 2012, "end": 2013}
        assert given["thickness"] is None and started


def test_the_thickness_is_not_asked_for_in_a_browser(browser, tmp_path):
    fvcom_months(str(tmp_path / "fvcom_sim"), MONTHS[:2])
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        to_options(app)
        page.reload()
        page.wait_for_selector("#view-analyze-options:not([hidden])")
        assert page.is_visible("#ao-fvcom-note") and "FVCOM" in page.inner_text("#ao-fvcom-note")
        for kind in ("climatology", "phenology", "long_term", "seasonal", "map_changes", "transects"):
            assert page.is_enabled(f"#ao-{kind}")
        assert "Not yet available" not in page.inner_text("#view-analyze-options")
        page.click("#ao-climatology")
        page.wait_for_selector("#clim-dialog:not([hidden])")
        cells = page.locator("#clim-body tr").first.locator("td")
        # the bottom and the water column can be chosen, and the thickness is not asked for
        bottom, water = (cells.nth(n).locator("input[type=checkbox]").first for n in (4, 5))
        assert bottom.is_enabled() and water.is_enabled()
        assert "Not yet available" not in cells.nth(4).inner_text() + cells.nth(5).inner_text()
        water.check()
        assert page.is_hidden("#clim-thickness-row") and page.is_visible("#clim-fvcom-thickness")
        assert "siglev" in page.inner_text("#clim-fvcom-thickness")
        page.click("#clim-cancel")
        page.wait_for_selector("#clim-dialog", state="hidden")
        # a vertical transect does not ask for it either
        page.click("#ao-transects")
        page.wait_for_selector("#trans-dialog:not([hidden])")
        # a variable is unticked to start with
        page.locator("#trans-body .transect-var__head input[type=checkbox]").first.check()
        card = page.locator("#trans-body .transect-card").first
        card.locator("input[type=checkbox]").nth(1).check()
        assert page.is_hidden("#trans-thickness-row") and page.is_visible("#trans-fvcom-thickness")
        assert "siglev" in page.inner_text("#trans-fvcom-thickness")
        card.locator("input[type=checkbox]").nth(1).uncheck()
        assert page.is_hidden("#trans-fvcom-thickness")
        assert errors == []
    finally:
        app.close()
