import os

import netCDF4
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval.fvcom import _sigma_to_z, default_levels, fvcom_matchup_files, matchup_depths
from oceanval.gridded import _obs_resolution


def uniform_siglay(n_layer, n_node):
    # layer centres of a uniform sigma grid, 0 at the surface, -1 at the seabed
    centres = -(np.arange(n_layer) + 0.5) / n_layer
    return np.repeat(centres[:, None], n_node, axis=1)


def test_linear_profile_is_reproduced():
    siglay = uniform_siglay(10, 3)
    h = np.array([20.0, 50.0, 100.0])
    zeta = np.zeros((1, 3))
    depths = -siglay * h
    # value equals depth, so linear interpolation should return the level itself
    values = depths[None, :, :]
    levels = [5, 10, 17.5]
    out = _sigma_to_z(values, siglay, h, zeta, levels)
    assert out.shape == (1, 3, 3)
    np.testing.assert_allclose(out[0], np.repeat(np.array(levels)[:, None], 3, axis=1), rtol=1e-6)


def test_top_bottom_and_below_seabed():
    siglay = uniform_siglay(4, 1)
    h = np.array([40.0])
    zeta = np.zeros((1, 1))
    # layer centres are at 5, 15, 25, 35 m
    values = np.array([[[1.0], [2.0], [3.0], [4.0]]])
    out = _sigma_to_z(values, siglay, h, zeta, [0, 2, 38, 40, 45])
    np.testing.assert_allclose(out[0, :4, 0], [1.0, 1.0, 4.0, 4.0])
    assert np.isnan(out[0, 4, 0])


def test_level_on_layer_centre():
    siglay = uniform_siglay(4, 1)
    h = np.array([40.0])
    zeta = np.zeros((1, 1))
    values = np.array([[[1.0], [2.0], [3.0], [4.0]]])
    out = _sigma_to_z(values, siglay, h, zeta, [5, 15, 20, 35])
    np.testing.assert_allclose(out[0, :, 0], [1.0, 2.0, 2.5, 4.0])


def test_non_uniform_sigma_and_free_surface():
    # the two nodes have different sigma layers, and zeta varies in time
    siglay = np.array([[-0.1, -0.25], [-0.5, -0.5], [-0.9, -0.75]])
    h = np.array([10.0, 20.0])
    zeta = np.array([[0.0, 0.0], [2.0, -4.0]])
    values = np.array(
        [
            [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]],
            [[4.0, 40.0], [5.0, 50.0], [6.0, 60.0]],
        ]
    )
    levels = [3, 11, 17]
    out = _sigma_to_z(values, siglay, h, zeta, levels)

    for tt in range(2):
        total = h + zeta[tt]
        for nn in range(2):
            depths = -siglay[:, nn] * total[nn]
            expected = np.interp(levels, depths, values[tt, :, nn])
            expected[np.array(levels) > total[nn]] = np.nan
            np.testing.assert_allclose(out[tt, :, nn], expected, rtol=1e-6)

    # node 0 is 10 m deep at t=0 and 12 m deep at t=1, so 11 m is only wet at t=1
    assert np.isnan(out[0, 1, 0])
    assert np.isfinite(out[1, 1, 0])


def test_nan_values_propagate():
    siglay = uniform_siglay(3, 1)
    h = np.array([30.0])
    zeta = np.zeros((1, 1))
    values = np.array([[[1.0], [np.nan], [3.0]]])
    out = _sigma_to_z(values, siglay, h, zeta, [0, 12, 28])
    assert out[0, 0, 0] == 1.0
    assert np.isnan(out[0, 1, 0])
    assert out[0, 2, 0] == 3.0


def test_default_levels():
    levels = default_levels(246.2)
    assert levels[0] == 0
    assert levels[-1] == 250
    assert len(levels) == 27
    assert default_levels(100)[-1] == 100


@pytest.mark.parametrize(
    "levels, error",
    [
        ([0, 10, 5], ValueError),
        ([-5, 0, 10], ValueError),
        ([], ValueError),
        (["a", "b"], TypeError),
    ],
)
def test_bad_levels(tmp_path, levels, error):
    with pytest.raises(error):
        oceanval.fvcom_preprocess(
            variables="salinity", paths=["dummy.nc"], out_dir=str(tmp_path), levels=levels
        )


def test_bad_z_level(tmp_path):
    with pytest.raises(TypeError):
        oceanval.fvcom_preprocess(
            variables="salinity", paths=["dummy.nc"], out_dir=str(tmp_path), z_level="yes"
        )


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


def test_mesh_mask(fvcom_file, tmp_path):
    oceanval.fvcom_preprocess(
        variables="salinity", paths=[fvcom_file], lon_lim=[-5, -4], lat_lim=[50, 51],
        res=0.05, out_dir=str(tmp_path),
    )
    ds = xr.open_dataset(tmp_path / "fvcom_values.nc", decode_times=False)
    values = ds.salinity.isel(time=0).values
    glon, glat = np.meshgrid(ds.lon.values, ds.lat.values)
    # stay clear of cells on the edge of the mesh, which could go either way
    outside = (glon > -4.475) & (glat > 50.525)
    interior = (glon > -4.975) & (glon < -4.025) & (glat > 50.025) & (glat < 50.975)
    inside = interior & ((glon < -4.525) | (glat < 50.475))
    assert np.isnan(values[outside]).all()
    assert np.isfinite(values[inside]).all()


def test_obs_resolution(tmp_path):
    lon = np.arange(-10, 0, 0.25)
    lat = np.arange(40, 60, 0.5)
    ds = xr.Dataset(
        {"sst": (("lat", "lon"), np.zeros((len(lat), len(lon))))},
        coords={"lon": lon, "lat": lat},
    )
    ds.to_netcdf(tmp_path / "obs.nc")
    comparison = {"obs_path": str(tmp_path / "obs.nc"), "thredds": False}
    assert _obs_resolution(comparison) == pytest.approx([0.25, 0.5])

    # a folder of files with 2D coordinates
    glon, glat = np.meshgrid(lon, lat)
    ds = xr.Dataset(
        {"sst": (("y", "x"), np.zeros(glon.shape))},
        coords={"longitude": (("y", "x"), glon), "latitude": (("y", "x"), glat)},
    )
    os.makedirs(tmp_path / "folder")
    ds.to_netcdf(tmp_path / "folder" / "obs_2000.nc")
    comparison = {"obs_path": str(tmp_path / "folder"), "thredds": False}
    assert _obs_resolution(comparison) == pytest.approx([0.25, 0.5])


# observation locations are nodes of the synthetic mesh, away from its edges
SURFACE_POINTS = [(-4.8, 50.2), (-4.6, 50.7), (-4.2, 50.3), (-4.9, 50.9)]
PROFILE_POINTS = [
    (-4.8, 50.2, 2),  # above the top layer centre
    (-4.8, 50.2, 30),
    (-4.6, 50.7, 50),
    (-4.2, 50.3, 120),
]
DROPPED_POINTS = [
    (-4.2, 50.3, 160),  # deeper than 150 m
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
        PROFILE_POINTS + DROPPED_POINTS, columns=["lon", "lat", "depth"]
    ).assign(observation=30.0, **date)
    for source, df in [("surf", surface), ("prof", profile)]:
        os.makedirs(obs_root / source)
        df.to_csv(obs_root / source / "obs.csv", index=False)

    oceanval.reset()
    oceanval.add_point_comparison(
        name="salinity", source="surf", model_variable="salinity",
        obs_path=str(obs_root / "surf"),
    )
    oceanval.add_point_comparison(
        name="salinity", source="prof", model_variable="salinity",
        obs_path=str(obs_root / "prof"), vertical=True,
    )
    oceanval.matchup(
        sim_dir=os.path.dirname(fvcom_file),
        start=2012,
        end=2012,
        n_dirs_down=0,
        ask=False,
        cores=1,
        out_dir=out_dir,
        fvcom=True,
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
    assert found == sorted(PROFILE_POINTS)
    np.testing.assert_allclose(df.model, _salinity(df.lon, df.depth, 1), atol=1e-3)


def test_fvcom_matchup_removes_regridded_files(fvcom_matchups):
    assert not os.path.exists(f"{fvcom_matchups}/oceanval_matchups/fvcom_tmp")


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_fvcom_matchup_closes_regridded_files(fvcom_matchups):
    # on NFS, removing a directory with an open file fails with "Device or resource busy"
    fvcom_dir = f"{fvcom_matchups}/oceanval_matchups/fvcom_tmp"
    open_files = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            open_files.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            pass
    assert [x for x in open_files if x.startswith(fvcom_dir)] == []


def test_matchup_depths():
    assert matchup_depths() == list(range(0, 155, 5))
    assert matchup_depths(10, 50, 20) == [10, 30, 50]
    # the last depth is the last one within the maximum
    assert matchup_depths(0, 100, 30) == [0, 30, 60, 90]
    assert matchup_depths(0, 1, 0.1)[-1] == 1.0
    assert len(matchup_depths(0, 1, 0.1)) == 11


@pytest.mark.parametrize(
    "arguments, error",
    [
        (dict(fvcom_res=0), ValueError),
        (dict(fvcom_res=-1), ValueError),
        (dict(fvcom_res="0.1"), TypeError),
        (dict(fvcom_dz=0), ValueError),
        (dict(fvcom_dz=True), TypeError),
        (dict(fvcom_min_depth=-5), ValueError),
        (dict(fvcom_min_depth=100, fvcom_max_depth=50), ValueError),
        (dict(fvcom_max_depth=float("nan")), ValueError),
    ],
)
def test_bad_fvcom_settings(arguments, error):
    with pytest.raises(error):
        oceanval.matchup(sim_dir=".", start=2012, end=2012, fvcom=True, **arguments)


def test_fvcom_settings_of_your_own(fvcom_file, tmp_path):
    """Depths and resolution can be chosen: deeper observations than the maximum are dropped"""
    obs = tmp_path / "obs"
    os.makedirs(obs)
    date = {"year": 2012, "month": 1, "day": 2}
    pd.DataFrame(PROFILE_POINTS, columns=["lon", "lat", "depth"]).assign(
        observation=30.0, **date
    ).to_csv(obs / "obs.csv", index=False)

    oceanval.reset()
    oceanval.add_point_comparison(
        name="salinity", source="own", model_variable="salinity",
        obs_path=str(obs), vertical=True,
    )
    out_dir = str(tmp_path / "out")
    oceanval.matchup(
        sim_dir=os.path.dirname(fvcom_file), start=2012, end=2012, n_dirs_down=0,
        ask=False, cores=1, out_dir=out_dir, fvcom=True,
        fvcom_res=0.1, fvcom_min_depth=0, fvcom_max_depth=60, fvcom_dz=10,
    )
    df = _read_point(out_dir, "all", "own")
    oceanval.reset()
    found = sorted(zip(df.lon.round(3), df.lat.round(3), df.depth))
    assert found == sorted(x for x in PROFILE_POINTS if x[2] <= 60)
    # a depth between the levels is interpolated, so only those on a level are exact
    on_level = df.depth % 10 == 0
    assert on_level.sum() == 2
    np.testing.assert_allclose(
        df.model[on_level], _salinity(df.lon, df.depth, 1)[on_level], atol=1e-3
    )


def test_the_cache_of_regridded_files_depends_on_the_depths(fvcom_file, tmp_path):
    kwargs = dict(
        paths=[fvcom_file], variables=["salinity"], out_dir=str(tmp_path), vertical=True,
        res=0.1, lon_lim=[-5, -4], lat_lim=[50, 51],
    )
    first = fvcom_matchup_files(**kwargs, levels=[0, 10, 20])
    second = fvcom_matchup_files(**kwargs, levels=[0, 10, 20, 30])
    assert first != second
    with xr.open_dataset(second[fvcom_file], decode_times=False) as ds:
        assert list(ds.depth.values if "depth" in ds else ds.lev.values) == [0, 10, 20, 30]


def test_bad_fvcom():
    with pytest.raises(TypeError):
        oceanval.matchup(sim_dir=".", start=2012, end=2012, fvcom="yes")
