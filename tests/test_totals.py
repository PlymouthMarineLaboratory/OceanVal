"""Totals over the matched cells (oceanval/totals.py): the unit factors, the
published ranges, and nctoolkit's sums, checked against xarray with xCDAT's
area weights."""

import numpy as np
import pytest
import xarray as xr

from oceanval import totals
from oceanval.quiet_nc import nc

EARTH_RADIUS = 6371000.0


def test_the_unit_factors():
    # a mole of carbon is 12.011 g, and a Pg 1e15 g
    assert totals.factor("mol m-2 yr-1", "co2flux", "Pg C yr-1") == pytest.approx(12.011e-15)
    # a year is 365.25 days, as unit_conversion takes it
    assert totals.factor("mg C m-2 d-1", "npp", "Pg C yr-1") == pytest.approx(365.25e-18)
    # per kilogram at OceanVal's density of seawater, 1025 kg m-3
    assert totals.factor("umol/kg", "alkalinity", "Pmol") == pytest.approx(1.025e-18)
    assert totals.factor("mmol/m^3", "nitrate", "Pmol") == pytest.approx(1e-18)
    assert totals.factor("degC", "temperature", "Pmol") is None


def test_what_can_be_totalled():
    assert totals.kind_of("mol m-2 yr-1", "co2flux") == totals.FLUX
    assert totals.kind_of("mmol/m^3", "oxygen") == totals.INVENTORY
    assert totals.kind_of("degC", "temperature") is None
    assert totals.kind_of("nonsense units", "nitrate") is None


def test_the_published_ranges_are_quoted_with_their_paper():
    rows = totals.references("co2flux")
    assert {row["period"] for row in rows} == {"2022", "2009-2018"}
    for row in totals._table():
        assert row["low"] <= row["high"]
        assert row["quote"] and row["citation"] and row["paper"].endswith(".pdf") and row["doi"].startswith("10.")
        assert row["units"] in totals.TARGETS
    assert totals.target_for("co2flux", totals.FLUX) == "Pg C yr-1"
    assert totals.target_for("oxygen", totals.INVENTORY) == "Pmol"
    # outside both (2.4-3.2 and 1.9-3.1), and inside one
    assert totals.outside(1.5, rows) and not totals.outside(2.0, rows)
    assert not totals.outside(5.0, [])


def _write(path, model, obs, lat, lon, depth=None, units="mol m-2 yr-1"):
    dims = ("time", "lat", "lon") if depth is None else ("time", "depth", "lat", "lon")
    coords = {"time": np.array(["2000-%02d-15" % (k + 1) for k in range(model.shape[0])], dtype="datetime64[ns]"),
              "lat": lat, "lon": lon}
    if depth is not None:
        coords["depth"] = depth
    ds = xr.Dataset({"model": (dims, model.astype("f4")), "observation": (dims, obs.astype("f4"))}, coords=coords)
    ds.lat.attrs = {"units": "degrees_north", "standard_name": "latitude"}
    ds.lon.attrs = {"units": "degrees_east", "standard_name": "longitude"}
    ds.model.attrs["units"] = units
    if depth is not None:
        ds.depth.attrs = {"units": "m", "positive": "down", "axis": "Z"}
    ds.to_netcdf(path, encoding={"model": {"_FillValue": 1e20}, "observation": {"_FillValue": 1e20}})
    return str(path)


def _areas(path, shape):
    """Each cell's area in m2, from xCDAT's weights (in square degrees' sines),
    on the sphere CDO's gridarea uses."""
    xcdat = pytest.importorskip("xcdat")
    ds = xcdat.open_dataset(path, add_bounds=["X", "Y"], decode_times=False)
    weights = ds.spatial.get_weights(axis=["X", "Y"], data_var="observation").transpose("lat", "lon").values
    # xCDAT's weights are proportional to the area: scale them to the sphere's
    lat_b = ds.lat_bnds.values
    lon_b = ds.lon_bnds.values
    area = (np.radians(lon_b[:, 1] - lon_b[:, 0])[None, :]
            * (np.sin(np.radians(lat_b[:, 1])) - np.sin(np.radians(lat_b[:, 0])))[:, None]) * EARTH_RADIUS ** 2
    assert np.allclose(weights / weights.sum(), area / area.sum())
    return np.broadcast_to(area, shape)


def test_the_flux_series_agrees_with_xarray(tmp_path):
    rng = np.random.default_rng(4)
    lat = np.arange(-58.0, 60.0, 4.0)
    lon = np.arange(0.0, 360.0, 6.0)
    obs = rng.normal(0.5, 1.0, (12, lat.size, lon.size))
    model = obs + 0.2
    obs[:, :3, :] = np.nan  # no observations in the south: those cells are not counted
    model[5, 10, :] = np.nan
    path = _write(tmp_path / "flux.nc", model, obs, lat, lon)
    series, target = totals.flux_series(nc.open_data(path, checks=False), "mol m-2 yr-1", "co2flux")
    assert target == "Pg C yr-1"
    area = _areas(path, obs.shape)
    ok = np.isfinite(model) & np.isfinite(obs)
    factor = 12.011e-15
    want_model = np.nansum(np.where(ok, model * area, 0), axis=(1, 2)) * factor
    want_obs = np.nansum(np.where(ok, obs * area, 0), axis=(1, 2)) * factor
    assert np.allclose(series.model, want_model, rtol=2e-3)
    assert np.allclose(series.observation, want_obs, rtol=2e-3)
    assert np.allclose(series.area, np.sum(np.where(ok, area, 0), axis=(1, 2)), rtol=2e-3)


def test_the_inventory_weights_each_level_by_its_layer(tmp_path):
    lat = np.arange(-20.0, 21.0, 10.0)
    lon = np.arange(0.0, 50.0, 10.0)
    depth = np.array([5.0, 20.0, 60.0, 150.0])
    obs = np.ones((1, depth.size, lat.size, lon.size)) * 2300.0
    model = obs * 1.1
    obs[0, 3, 0, :] = np.nan
    path = _write(tmp_path / "column.nc", model, obs, lat, lon, depth, units="umol/kg")
    got_model, got_obs, volume, target = totals.inventory(nc.open_data(path, checks=False), "umol/kg", "alkalinity")
    assert target == "Pmol"
    area = _areas(path, obs.shape[-2:])
    thickness = np.array([12.5, 27.5, 65.0, 90.0])  # skill.layer_thickness of the levels
    ok = np.isfinite(obs[0]) & np.isfinite(model[0])
    want_volume = np.sum(np.where(ok, area[None] * thickness[:, None, None], 0))
    # CDO's gridarea bounds a cell by great circles, xCDAT by lines of latitude: 0.2% apart on
    # this 10 degree grid
    assert volume == pytest.approx(want_volume, rel=5e-3)
    assert got_obs == pytest.approx(2300.0 * 1e-6 * 1025 * volume * 1e-15, rel=1e-5)
    assert got_model == pytest.approx(1.1 * got_obs, rel=1e-5)


def test_only_carbon_is_turned_into_carbon():
    # a flux of nitrogen is in moles, never Pg of carbon
    assert totals.target_for("nitrate_flux", totals.FLUX, "mmol N m-2 d-1") == "Tmol yr-1"
    assert totals.factor("mmol N m-2 d-1", "nitrate_flux", "Pg C yr-1") is None
    assert totals.factor("mmol N m-2 d-1", "nitrate_flux", "Tmol yr-1") == pytest.approx(365.25e-15)
    # primary production is carbon by its published range, even with no C in its units
    assert totals.factor("mmol m-2 d-1", "npp", "Pg C yr-1") == pytest.approx(365.25 * 12.011e-18)


def test_model_and_observations_on_depth_axes_of_their_own(tmp_path):
    # as a vertical matchup has them: the same levels, named depth and deptht
    lat = np.arange(-20.0, 21.0, 10.0)
    lon = np.arange(0.0, 50.0, 10.0)
    depth = np.array([5.0, 20.0, 60.0, 150.0])
    values = np.ones((1, depth.size, lat.size, lon.size)) * 30.0
    ds = xr.Dataset(
        {"model": (("time", "depth", "lat", "lon"), values.astype("f4")),
         "observation": (("time", "deptht", "lat", "lon"), values.astype("f4"))},
        coords={"time": np.array(["2000-01-15"], dtype="datetime64[ns]"), "depth": depth, "deptht": depth,
                "lat": lat, "lon": lon},
    )
    ds.lat.attrs = {"units": "degrees_north", "standard_name": "latitude"}
    ds.lon.attrs = {"units": "degrees_east", "standard_name": "longitude"}
    for name in ("depth", "deptht"):
        ds[name].attrs = {"units": "m", "positive": "down", "axis": "Z"}
    path = str(tmp_path / "two_axes.nc")
    ds.to_netcdf(path)
    model, obs, volume, _ = totals.inventory(nc.open_data(path, checks=False), "mmol/m^3", "nitrate")
    area = _areas(path, (lat.size, lon.size)).sum()
    assert volume == pytest.approx(area * 195.0, rel=5e-3)  # the layers reach 195 m
    assert model == pytest.approx(obs) and model == pytest.approx(30e-3 * volume * 1e-15, rel=1e-5)
