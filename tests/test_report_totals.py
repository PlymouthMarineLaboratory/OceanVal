"""Totals over the cells matched up (the gap report's options 5 and 34), through a
real matchup and validation: a flux summed over the area each month, beside the
published ranges of the ocean's CO2 sink, and an inventory through the water
column, each checked against xarray with xCDAT's area weights.

The example model has no flux, so the simulation here is its nitrate and a
"flux" made from its surface nitrate (mol m-2 yr-1), validated as co2flux. The
observations are the model's own monthly means, the flux's 0.9 times.
"""

import glob
import html
import os
import re

import nctoolkit as nc
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from figure_code_checks import check_every_figure_has_its_code, check_the_code_draws_each_figure
from oceanval import skill


def text_of(path):
    text = open(path).read()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    folder = tmp_path_factory.mktemp("totals")
    sim = folder / "sim"
    for path in sorted(glob.glob("data/example/2000/*/*ptrc_T.nc")):
        out = sim / os.path.relpath(path, "data/example")
        out.parent.mkdir(parents=True, exist_ok=True)
        ds = nc.open_data(path, checks=False)
        ds.subset(variables="N3_n")
        ds_flux = ds.copy()
        ds_flux.top()
        ds_flux.cdo_command("expr,'flux=N3_n*0.01;'")
        ds_flux.set_units({"flux": "mol m-2 yr-1"})
        ds.append(ds_flux)
        ds.merge("variables")
        ds.to_nc(str(out), overwrite=True)
    obs_flux, obs_n3 = folder / "obs_flux", folder / "obs_n3"
    obs_flux.mkdir()
    obs_n3.mkdir()
    ds = nc.open_data(sorted(glob.glob(str(sim / "2000/*/*ptrc_T.nc"))), checks=False)
    ds.merge("time")
    ds.tmean("month")
    ds_flux = ds.copy()
    ds_flux.subset(variables="flux")
    ds_flux * 0.9
    ds_flux.to_nc(str(obs_flux / "obs_2000.nc"), overwrite=True)
    ds.subset(variables="N3_n")
    ds.to_nc(str(obs_n3 / "obs_2000.nc"), overwrite=True)

    oceanval.reset()
    oceanval.add_gridded_comparison(
        name="co2flux", obs_path=str(obs_flux), source="foo", model_variable="flux",
        obs_variable="flux", climatology=True, start=2000, end=2000,
    )
    oceanval.add_gridded_comparison(
        name="nitrate", obs_path=str(obs_n3), source="bar", model_variable="N3_n",
        obs_variable="N3_n", climatology=True, vertical=True, start=2000, end=2000,
    )
    oceanval.matchup(
        sim_dir=str(sim), start=2000, end=2000, ask=False, cores=1,
        thickness="data/example/e3t.nc", out_dir=str(folder),
    )
    out_dir = folder / "report"
    oceanval.validate(data_dir=str(folder), out_dir=str(out_dir), concise=False, test=True, figure_code=True)
    yield folder, out_dir
    oceanval.reset()


def page(out_dir, name):
    found = glob.glob(str(out_dir / f"oceanval_report/_build/html/notebooks/*{name}.html"))
    assert len(found) == 1, found
    return text_of(found[0])


def _areas(path, shape):
    xcdat = pytest.importorskip("xcdat")
    ds = xcdat.open_dataset(path, add_bounds=["X", "Y"], decode_times=False)
    lat_b, lon_b = ds.lat_bnds.values, ds.lon_bnds.values
    area = (np.radians(lon_b[:, 1] - lon_b[:, 0])[None, :]
            * (np.sin(np.radians(lat_b[:, 1])) - np.sin(np.radians(lat_b[:, 0])))[:, None]) * 6371000.0 ** 2
    weights = ds.spatial.get_weights(axis=["X", "Y"], data_var="observation").transpose("lat", "lon").values
    assert np.allclose(weights / weights.sum(), area / area.sum())
    return np.broadcast_to(area, shape)


def test_the_pages_have_the_totals_numbered_without_a_gap(report):
    _, out_dir = report
    flux = page(out_dir, "foo_co2flux")
    assert "summed over the grid cells with both model and observational data in each month" in flux
    assert "Friedlingstein et al. (2023), Global Carbon Budget 2023" in flux
    summary = page(out_dir, "summary")
    assert "Totals over the area matched up" in summary
    for text in (flux, page(out_dir, "bar_nitrate"), summary):
        for word in ("Figure", "Table"):
            found = [int(x) for x in re.findall(rf"{word} (\d+) ?:", text)]
            assert found == list(range(1, len(found) + 1))


def test_the_code_of_the_totals_draws_them_again(report, tmp_path):
    _, out_dir = report
    html_dir = str(out_dir / "oceanval_report/_build/html/notebooks")
    check_every_figure_has_its_code(html_dir, str(out_dir / "oceanval_results"))
    check_the_code_draws_each_figure(html_dir, str(tmp_path))
    assert "gridded_plots.total_series(" in "".join(open(p).read() for p in glob.glob(f"{tmp_path}/*.py"))


def test_the_flux_total_agrees_with_xarray(report):
    folder, out_dir = report
    got = pd.read_csv(out_dir / "oceanval_results/totals/co2flux_foo_totals.csv").iloc[0]
    assert got.units == "Pg C yr-1"
    path = folder / "oceanval_matchups/gridded/co2flux/foo_co2flux_surface.nc"
    ds = xr.open_dataset(path)
    model, obs = ds.model.values.astype("f8"), ds.observation.values.astype("f8")
    ok = np.isfinite(model) & np.isfinite(obs)
    area = _areas(path, model.shape)
    factor = 12.011e-15
    # every axis but time (the surface matchup has a depth axis of one level)
    others = tuple(range(1, model.ndim))
    steps = ok.reshape(ok.shape[0], -1).any(axis=1)
    want_model = (np.sum(np.where(ok, model * area, 0), axis=others) * factor)[steps].mean()
    want_obs = (np.sum(np.where(ok, obs * area, 0), axis=others) * factor)[steps].mean()
    # CDO's gridarea bounds a cell by great circles: a little different from xCDAT's
    assert got.model == pytest.approx(want_model, rel=5e-3)
    assert got.observation == pytest.approx(want_obs, rel=5e-3)
    assert got.observation == pytest.approx(0.9 * got.model, rel=1e-4)


def test_the_inventory_agrees_with_xarray(report):
    folder, out_dir = report
    got = pd.read_csv(out_dir / "oceanval_results/totals/nitrate_bar_totals.csv").iloc[0]
    assert got.units == "Pmol"
    path = folder / "oceanval_matchups/gridded/nitrate/bar_nitrate_vertical.nc"
    ds = xr.open_dataset(path)
    depth_name = [x for x in ds.observation.dims if "dep" in x][0]
    # the annual mean of the monthly climatology, by month first as the page does
    time_name = [x for x in ds.model.dims if "time" in x][0]
    model = ds.model.groupby(f"{time_name}.month").mean().mean("month").values.astype("f8")
    obs = ds.observation.groupby(f"{time_name}.month").mean().mean("month").values.astype("f8")
    ok = np.isfinite(model) & np.isfinite(obs)
    area = _areas(folder / "oceanval_matchups/gridded/nitrate/bar_nitrate_surface.nc", model.shape[-2:])
    thickness = skill.layer_thickness(ds[depth_name].values)
    volume = np.where(ok, area[None] * thickness[:, None, None], 0)
    # mmol m-3 times m3 is mmol: 1e-3 mol, and a Pmol is 1e15 mol
    want_model = np.sum(np.where(ok, model, 0) * volume) * 1e-18
    want_obs = np.sum(np.where(ok, obs, 0) * volume) * 1e-18
    assert got.model == pytest.approx(want_model, rel=5e-3)
    assert got.observation == pytest.approx(want_obs, rel=5e-3)
    assert got.volume_km3 == pytest.approx(volume.sum() / 1e9, rel=5e-3)
