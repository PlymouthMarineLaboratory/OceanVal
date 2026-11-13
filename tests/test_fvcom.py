"""Raw FVCOM output in matchups: found from its files (oceanval.fvcom.is_fvcom),
opened by nctoolkit's open_fvcom, and matched up on its own mesh, with the
depths of its sigma layers worked out from its files."""

import os

import netCDF4
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval import fvcom


# 2012-01-01 in FVCOM's modified julian days
MJD_2012 = 55927
N_TIMES = 3


def _make_fvcom(path):
    """
    A small FVCOM file: a 0.1 degree mesh over 5-4W, 50-51N with its north-east
    corner cut out, and salinity equal to 30 + 0.02 * depth + 0.1 * time step
    """
    lons = np.round(np.arange(-5.0, -3.95, 0.1), 6)
    lats = np.round(np.arange(50.0, 51.05, 0.1), 6)
    glon, glat = np.meshgrid(lons, lats)
    keep = ~((glon > -4.45) & (glat > 50.55))
    index = np.full(glon.shape, -1)
    index[keep] = np.arange(keep.sum())
    triangles = []
    for j in range(len(lats) - 1):
        for i in range(len(lons) - 1):
            a, b = index[j, i], index[j, i + 1]
            c, d = index[j + 1, i], index[j + 1, i + 1]
            if min(a, b, c, d) >= 0:
                triangles += [[a, b, d], [a, d, c]]
    nv = np.array(triangles).T + 1
    lon, lat = glon[keep], glat[keep]
    n_node = len(lon)

    siglay = np.repeat((-(np.arange(5) + 0.5) / 5)[:, None], n_node, axis=1)
    h = 20 + 200 * (lon + 5)
    steps = np.arange(N_TIMES)
    depth = -siglay[None, :, :] * h[None, None, :]
    salinity = 30 + 0.02 * depth + 0.1 * steps[:, None, None]

    # written with netCDF4, as xarray will not create a 2D variable named after its dimension
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("node", n_node)
        ds.createDimension("nele", nv.shape[1])
        ds.createDimension("three", 3)
        ds.createDimension("siglay", 5)
        ds.createDimension("time", None)

        def add(name, dims, values, dtype="f4", **attrs):
            var = ds.createVariable(name, dtype, dims)
            var[:] = values
            var.setncatts(attrs)

        add("lon", ("node",), lon, units="degrees_east", long_name="nodal longitude")
        add("lat", ("node",), lat, units="degrees_north", long_name="nodal latitude")
        add("nv", ("three", "nele"), nv, dtype="i4")
        add("siglay", ("siglay", "node"), siglay, long_name="Sigma Layers", positive="up")
        add("h", ("node",), h, units="m", long_name="Bathymetry")
        add("time", ("time",), MJD_2012 + steps, units="days since 1858-11-17 00:00:00")
        # FVCOM's integer time has units xarray cannot decode
        add("Itime2", ("time",), np.zeros(N_TIMES), dtype="i4", units="msec since 00:00:00")
        add(
            "zeta", ("time", "node"), np.zeros((N_TIMES, n_node)),
            units="meters", long_name="Water Surface Elevation", coordinates="lon lat",
        )
        add(
            "salinity", ("time", "siglay", "node"), salinity,
            units="1e-3", long_name="salinity", coordinates="lon lat",
        )
    return path


def _salinity(lon, depth, step):
    """Salinity at a node of the synthetic mesh, with depth limited to its layer centres"""
    h = 20 + 200 * (np.asarray(lon) + 5)
    depth = np.clip(depth, 0.1 * h, 0.9 * h)
    return 30 + 0.02 * depth + 0.1 * step


@pytest.fixture(scope="module")
def fvcom_file(tmp_path_factory):
    sim_dir = tmp_path_factory.mktemp("fvcom_sim")
    return _make_fvcom(str(sim_dir / "fvcom_test_0001.nc"))


class TestTellingFVCOMApart:
    def test_raw_fvcom_output_is_found(self, fvcom_file):
        assert fvcom.is_fvcom(fvcom_file)

    def test_other_files_are_not_taken_for_it(self, tmp_path):
        path = str(tmp_path / "grid.nc")
        xr.Dataset({"t": (("lat", "lon"), np.zeros((2, 2)))}, coords={"lat": [0, 1], "lon": [0, 1]}).to_netcdf(path)
        assert not fvcom.is_fvcom(path)
        assert not fvcom.is_fvcom(str(tmp_path / "missing.nc"))

    def test_the_mesh_is_what_tells(self):
        assert fvcom.has_mesh(["time", "node", "nele", "siglay"], ["nv", "temp"])
        # regridded FVCOM output keeps its attributes but not its mesh
        assert not fvcom.has_mesh(["time", "node", "nele"], ["temp"])
        assert not fvcom.has_mesh(["time", "lat", "lon"], ["nv"])

    def test_it_is_opened_on_its_mesh_with_only_the_variables_needed(self, fvcom_file):
        ds = fvcom.open_model(fvcom_file, ["salinity"])
        assert ds.fvcom
        # open_fvcom converts every value it opens: zeta and u are not, nor the thickness unless asked for
        assert ds.variables == ["salinity"]
        assert len(ds.levels) == 5
        ds = fvcom.open_model(fvcom_file, ["salinity"], thickness=True)
        assert sorted(ds.variables) == sorted(["salinity", fvcom.THICKNESS])

    def test_the_thickness_of_its_layers(self, fvcom_file):
        ds = fvcom.thickness_dataset(fvcom_file, "salinity")
        assert ds.variables == [fvcom.THICKNESS]
        with xr.open_dataset(ds[0]) as found:
            values = found[fvcom.THICKNESS].values.squeeze()
            lon = found.lon.values
        # five even layers of h = 20 + 200 * (lon + 5), with no free surface
        np.testing.assert_allclose(values, np.repeat(((20 + 200 * (lon + 5)) / 5)[None, :], 5, axis=0), rtol=1e-5)


# observation locations are nodes of the synthetic mesh, away from its edges
SURFACE_POINTS = [(-4.8, 50.2), (-4.6, 50.7), (-4.2, 50.3), (-4.9, 50.9)]
PROFILE_POINTS = [
    (-4.8, 50.2, 2),  # above the top layer centre
    (-4.8, 50.2, 30),
    (-4.6, 50.7, 50),
    (-4.2, 50.3, 120),
]
# no longer dropped: there is no deepest depth to interpolate to (it was 150 m)
DEEP_POINTS = [(-4.2, 50.3, 160)]
DROPPED_POINTS = [
    (-4.9, 50.9, 45),  # below the 40 m seabed
    (-4.3, 50.8, 10),  # outside the mesh
]


@pytest.fixture(scope="module")
def fvcom_matchups(fvcom_file, tmp_path_factory):
    obs_root = tmp_path_factory.mktemp("fvcom_obs")
    out_dir = str(tmp_path_factory.mktemp("fvcom_out"))
    # observations on 2012-01-02, the second time step
    date = {"year": 2012, "month": 1, "day": 2}

    surface = pd.DataFrame(SURFACE_POINTS, columns=["lon", "lat"]).assign(
        observation=30.0, **date
    )
    profile = pd.DataFrame(
        PROFILE_POINTS + DEEP_POINTS + DROPPED_POINTS, columns=["lon", "lat", "depth"]
    ).assign(observation=30.0, **date)
    for source, df in [("surf", surface), ("prof", profile)]:
        os.makedirs(obs_root / source)
        df.to_csv(obs_root / source / "obs.csv", index=False)

    # a gridded product at 0, 10 and 50 m on a 0.25 degree grid, for January 2012
    lon = np.arange(-4.875, -4.0, 0.25)
    lat = np.arange(50.125, 51, 0.25)
    gridded = xr.Dataset(
        {"sal": (("time", "depth", "lat", "lon"), np.full((1, 3, len(lat), len(lon)), 30.0, dtype="f4"))},
        coords={
            "time": pd.to_datetime(["2012-01-15"]),
            "depth": ("depth", [0.0, 10.0, 50.0], {"positive": "down", "units": "m", "axis": "Z"}),
            "lat": ("lat", lat, {"units": "degrees_north"}),
            "lon": ("lon", lon, {"units": "degrees_east"}),
        },
    )
    os.makedirs(obs_root / "grid")
    gridded.to_netcdf(obs_root / "grid" / "sal_2012.nc")

    oceanval.reset()
    oceanval.add_point_comparison(
        name="salinity", source="surf", model_variable="salinity",
        obs_path=str(obs_root / "surf"),
    )
    oceanval.add_point_comparison(
        name="salinity", source="prof", model_variable="salinity",
        obs_path=str(obs_root / "prof"), vertical=True,
    )
    oceanval.add_gridded_comparison(
        name="salinity", source="grid", model_variable="salinity", obs_path=str(obs_root / "grid"),
        obs_variable="sal", climatology=False, vertical=True,
    )
    # nothing is said of FVCOM: matchup finds it
    oceanval.matchup(
        sim_dir=os.path.dirname(fvcom_file),
        start=2012,
        end=2012,
        n_dirs_down=0,
        ask=False,
        cores=1,
        out_dir=out_dir,
    )
    yield out_dir

    oceanval.reset()


def _read_point(out_dir, layer, source):
    return pd.read_csv(
        f"{out_dir}/oceanval_matchups/point/{layer}/salinity/{source}/{source}_{layer}_salinity.csv"
    )


def test_fvcom_surface_matchup(fvcom_matchups):
    df = _read_point(fvcom_matchups, "surface", "surf")
    assert len(df) == len(SURFACE_POINTS)
    np.testing.assert_allclose(df.model, _salinity(df.lon, 0, 1), atol=1e-3)


def test_fvcom_profile_matchup(fvcom_matchups):
    df = _read_point(fvcom_matchups, "all", "prof")
    found = sorted(zip(df.lon.round(3), df.lat.round(3), df.depth))
    # the one above the top layer's centre is in the top layer; those below the
    # seabed or outside the mesh are not matched
    assert found == sorted(PROFILE_POINTS + DEEP_POINTS)
    np.testing.assert_allclose(df.model, _salinity(df.lon, df.depth, 1), atol=1e-3)


def _gridded(out_dir, kind):
    with xr.open_dataset(f"{out_dir}/oceanval_matchups/gridded/salinity/grid_salinity_{kind}.nc") as ds:
        return ds.load()


def _expected_grid(lon, lat, depth):
    """The monthly mean, of time steps 0-2, at each cell centre, missing below
    the seabed and in the corner cut out of the mesh"""
    h = 20 + 200 * (lon + 5)
    expected = _salinity(lon, np.clip(depth, 0.1 * h, None), 1.0)
    expected = np.where(depth > 0.9 * h, np.nan, expected)
    return np.where((lon > -4.45) & (lat > 50.55), np.nan, expected)


def test_fvcom_gridded_surface_matchup(fvcom_matchups):
    ds = _gridded(fvcom_matchups, "surface")
    glon, glat = np.meshgrid(ds.lon.values, ds.lat.values)
    np.testing.assert_allclose(ds.model.isel(time=0).values, _expected_grid(glon, glat, 0.0), atol=1e-3)


def test_fvcom_gridded_vertical_matchup(fvcom_matchups):
    ds = _gridded(fvcom_matchups, "vertical")
    glon, glat = np.meshgrid(ds.lon.values, ds.lat.values)
    # the model's own depth dimension, beside the observations'
    vertical = [x for x in ds.model.dims if x.startswith("depth")][0]
    np.testing.assert_allclose(ds[vertical].values, [0.0, 10.0, 50.0])
    for k, depth in enumerate([0.0, 10.0, 50.0]):
        np.testing.assert_allclose(
            ds.model.isel({"time": 0, vertical: k}).values, _expected_grid(glon, glat, depth), atol=1e-3
        )


@pytest.mark.parametrize("name", ["fvcom", "fvcom_res", "fvcom_min_depth", "fvcom_max_depth", "fvcom_dz"])
def test_the_old_fvcom_arguments_say_they_are_gone(name):
    with pytest.raises(TypeError, match=f"no longer takes {name}"):
        oceanval.matchup(sim_dir=".", start=2012, end=2012, **{name: True})


def test_other_unknown_arguments_are_still_refused():
    with pytest.raises(TypeError, match="unexpected keyword argument 'colour'"):
        oceanval.matchup(sim_dir=".", start=2012, end=2012, colour="red")
