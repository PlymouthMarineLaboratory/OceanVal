"""The shared skill statistics (oceanval/skill.py): worked out by nctoolkit, and
checked here against numpy (the gap report's area-weighted statistics)."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import skill
from oceanval.quiet_nc import nc


def _wstats(m, o, w):
    # the gap report's area-weighted statistics (research/report/oceanval_gap_report.ipynb)
    k = np.isfinite(m) & np.isfinite(o)
    m, o, w = m[k].astype("f8"), o[k].astype("f8"), w[k].astype("f8")
    w = w / w.sum()
    mm, mo = np.sum(w * m), np.sum(w * o)
    sm, so = np.sqrt(np.sum(w * (m - mm) ** 2)), np.sqrt(np.sum(w * (o - mo) ** 2))
    return dict(
        obs_mean=mo, model_mean=mm, obs_sd=so, model_sd=sm, bias=mm - mo,
        rmsd=np.sqrt(np.sum(w * (m - o) ** 2)),
        crmsd=np.sqrt(np.sum(w * ((m - mm) - (o - mo)) ** 2)),
        r=np.sum(w * (m - mm) * (o - mo)) / (sm * so), mae=np.sum(w * np.abs(m - o)), nsd=sm / so,
    )


def _write(path, model, obs, lat, lon, depth=None):
    """A file of model and observation, as OceanVal's matchups are: finite fill values."""
    dims = ("time", "lat", "lon") if depth is None else ("time", "depth", "lat", "lon")
    coords = {"time": np.array(["2000-%02d-15" % (k + 1) for k in range(model.shape[0])], dtype="datetime64[ns]"),
              "lat": lat, "lon": lon}
    if depth is not None:
        coords["depth"] = depth
    ds = xr.Dataset({"model": (dims, model.astype("f4")), "observation": (dims, obs.astype("f4"))}, coords=coords)
    ds.lat.attrs = {"units": "degrees_north", "standard_name": "latitude"}
    ds.lon.attrs = {"units": "degrees_east", "standard_name": "longitude"}
    if depth is not None:
        ds.depth.attrs = {"units": "m", "positive": "down", "axis": "Z"}
    ds.to_netcdf(path, encoding={"model": {"_FillValue": 1e20}, "observation": {"_FillValue": 1e20}})
    return str(path)


def _areas(path, shape):
    """The area weights of a file's cells, worked out independently of OceanVal by
    xCDAT (from the latitude and longitude bounds it adds), broadcast to shape."""
    xcdat = pytest.importorskip("xcdat")

    ds = xcdat.open_dataset(path, add_bounds=["X", "Y"], decode_times=False)
    weights = ds.spatial.get_weights(axis=["X", "Y"], data_var="observation").transpose("lat", "lon").values
    return np.broadcast_to(weights, shape)


@pytest.fixture
def fields(tmp_path):
    rng = np.random.default_rng(1)
    lat = np.linspace(-60, 60, 13)
    lon = np.arange(0.0, 40.0, 2.0)
    obs = rng.lognormal(0, 1, (12, 13, 20))
    model = obs * rng.lognormal(0.1, 0.4, obs.shape)
    obs[:, 3, :5] = np.nan
    model[2, 8, :] = np.nan
    model[4, 9, 2] = 0.0
    path = _write(tmp_path / "m.nc", model, obs, lat, lon)
    return path, model, obs, lat, lon


def test_the_statistics_agree_with_the_gap_report(fields):
    path, model, obs, lat, lon = fields
    got = skill.file_stats(path)
    want = _wstats(model, obs, np.array(_areas(path, obs.shape)))
    for key, value in want.items():
        assert got[key] == pytest.approx(value, rel=1e-4, abs=1e-6), key
    assert got["n"] == int((np.isfinite(model) & np.isfinite(obs)).sum())
    assert got["crmsd_n"] == pytest.approx(got["crmsd"] / got["obs_sd"])
    assert got["pbias"] == pytest.approx(100 * got["bias"] / got["obs_mean"])


def test_the_taylor_identity_holds(fields):
    s = skill.file_stats(fields[0])
    lhs = s["crmsd"] ** 2
    rhs = s["obs_sd"] ** 2 + s["model_sd"] ** 2 - 2 * s["obs_sd"] * s["model_sd"] * s["r"]
    assert lhs == pytest.approx(rhs, rel=1e-6)


def test_log10_leaves_out_values_that_are_not_positive(fields):
    path, model, obs, lat, lon = fields
    got = skill.file_stats(path, log=True)
    ok = (model > 0) & (obs > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        lm = np.where(ok, np.log10(model), np.nan)
        lo = np.where(ok, np.log10(obs), np.nan)
    want = _wstats(lm, lo, np.array(_areas(path, obs.shape)))
    assert got["n"] == int((np.isfinite(lm) & np.isfinite(lo)).sum())
    for key in ("bias", "rmsd", "r", "nsd", "obs_mean"):
        assert got[key] == pytest.approx(want[key], rel=1e-4, abs=1e-6), key
    assert np.isnan(got["pbias"])


def test_the_dataset_is_not_changed(fields):
    ds = nc.open_data(fields[0], checks=False)
    skill.dataset_stats(ds)
    assert sorted(ds.variables) == ["model", "observation"]


def test_paired_masks_both_and_takes_logs(tmp_path):
    lat = np.array([0.0, 1.0])
    lon = np.array([0.0, 1.0, 2.0])
    model = np.ones((1, 2, 3))
    obs = np.ones((1, 2, 3)) * 10
    model[0, 0, 0] = 0.0  # no log
    obs[0, 0, 1] = np.nan  # the model is missing here too, though it is 1
    model[0, 1, 2] = np.nan
    ds = nc.open_data(_write(tmp_path / "p.nc", model, obs, lat, lon), checks=False)
    skill.paired(ds)
    values = ds.to_xarray()
    assert np.isnan(values.model.values[0, 0, 1]) and np.isnan(values.observation.values[0, 1, 2])
    assert values.model.values[0, 0, 0] == 0.0
    ds = nc.open_data(str(tmp_path / "p.nc"), checks=False)
    skill.paired(ds, log=True)
    values = ds.to_xarray()
    assert np.isnan(values.model.values[0, 0, 0]) and np.isnan(values.observation.values[0, 0, 0])
    assert values.observation.values[0, 1, 0] == pytest.approx(1.0)


def test_levels_and_bins(tmp_path):
    rng = np.random.default_rng(3)
    lat = np.array([10.0, 11.0, 12.0])
    lon = np.array([0.0, 1.0, 2.0, 3.0])
    depth = np.array([0.0, 10.0, 20.0, 50.0, 100.0])
    obs = rng.normal(5, 1, (1, 5, 3, 4))
    model = obs + np.arange(5)[None, :, None, None]  # a bias of k at level k
    path = _write(tmp_path / "v.nc", model, obs, lat, lon, depth)
    depths, rows, frame = skill.level_stats(nc.open_data(path, checks=False))
    assert list(depths) == list(depth)
    assert [round(x["bias"], 4) for x in rows] == [0, 1, 2, 3, 4]
    bins = skill.bin_stats(depths, frame, [(0, 10), (10, 30), (30, 60), (200, None)])
    # 0 and 10 m are in 0-10m, 20 m in 10-30m, 50 m in 30-60m, and nothing is below 200 m
    assert [x["depth"] for x in bins] == ["0-10m", "10-30m", "30-60m"]
    # 0-10m pools levels 0 (5 m thick, bias 0) and 10 m (10 m thick, bias 1)
    assert bins[0]["bias"] == pytest.approx(10 / 15, rel=1e-4)
    assert bins[1]["bias"] == pytest.approx(2, rel=1e-4)


def test_layer_thickness():
    thickness = skill.layer_thickness([0, 10, 20, 50])
    assert list(thickness) == [5, 10, 20, 30]


def test_table_statistics_for_point_data():
    model = np.array([1.0, 2.0, 4.0, np.nan])
    obs = np.array([1.0, 3.0, 3.0, 2.0])
    s = skill.weighted_stats(model, obs)
    assert s["n"] == 3
    assert s["bias"] == pytest.approx(0.0, abs=1e-12)
    assert s["mae"] == pytest.approx(2 / 3)
    assert s["rmsd"] == pytest.approx(np.sqrt(2 / 3))
    assert s["r"] == pytest.approx(np.corrcoef(model[:3], obs[:3])[0, 1])
    s = skill.log_stats([1.0, 10.0, 0.0, 100.0], [10.0, 10.0, 5.0, 100.0])
    assert s["n"] == 3 and s["bias"] == pytest.approx(-1 / 3)


def test_no_pairs_gives_nan():
    frame = pd.DataFrame({name: [0.0] for name in skill.SUMS})
    s = skill.stats_from_sums(frame)
    assert s["n"] == 0
    assert all(np.isnan(s[k]) for k in skill.NAMES if k != "n")


def test_log_variables():
    assert skill.is_log("chlorophyll") and skill.is_log("KD490")
    assert not skill.is_log("nitrate")


def test_best():
    assert skill.best([0.3, -0.1, 0.2], "abs") == 1
    assert skill.best([1.4, 0.8, 1.1], "one") == 2
    assert skill.best([np.nan, np.nan], "min") is None
