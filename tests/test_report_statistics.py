"""The report's statistics tables, through a real validation: the summary's
normalised standard deviation and centred RMSD, the log10 statistics of
chlorophyll and kd490, the CMEMS-style regional table, a built-in set of
regions, and the statistics at each depth of a gridded matchup through the
water column.

The observations are the example model's own monthly means, changed in a way
whose statistics are known exactly: temperature's are 1.1 times the model's plus
0.5 (so r is 1, the model's standard deviation is 1/1.1 of theirs and its bias is
-(0.1 model mean + 0.5)), and "chlorophyll" (the model's nitrate) twice the
model's (so on log10 values the bias is -log10(2) and r is 1).
"""

import glob
import html
import os
import re

import nctoolkit as nc
import numpy as np
import pandas as pd
import pytest

import oceanval


def text_of(path):
    text = open(path).read()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


def numbers(text, word):
    return [int(x) for x in re.findall(rf"{word} (\d+) ?:", text)]


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    folder = tmp_path_factory.mktemp("statistics")
    for name, files, variable, times, plus in (
        ("temp", "*grid_T.nc", "votemper", 1.1, 0.5),
        ("chl", "*ptrc_T.nc", "N3_n", 2, 0),
    ):
        obs = folder / f"obs_{name}"
        obs.mkdir()
        ds = nc.open_data(sorted(glob.glob(f"data/example/2000/*/{files}")), checks=False)
        ds.subset(variables=variable)
        ds.merge("time")
        ds.tmean("month")
        ds * times
        if plus:
            ds + plus
        ds.to_nc(str(obs / "obs_2000.nc"), overwrite=True)

    oceanval.reset()
    oceanval.add_gridded_comparison(
        name="temperature", obs_path=str(folder / "obs_temp"), source="foo",
        model_variable="votemper", obs_variable="votemper", climatology=True,
        vertical=True, start=2000, end=2000,
    )
    oceanval.add_gridded_comparison(
        name="chlorophyll", obs_path=str(folder / "obs_chl"), source="bar",
        model_variable="N3_n", obs_variable="N3_n", climatology=True, start=2000, end=2000,
    )
    oceanval.matchup(
        sim_dir="data/example", start=2000, end=2000, ask=False, cores=1,
        thickness="data/example/e3t.nc", out_dir=str(folder),
    )
    out_dir = folder / "report"
    oceanval.validate(
        data_dir=str(folder), out_dir=str(out_dir), concise=False, subregions="cmems", test=True
    )
    yield folder, out_dir
    oceanval.reset()


def _areas(path, shape):
    """The area weights of a file's cells, worked out independently of OceanVal by
    xCDAT (from the latitude and longitude bounds it adds), broadcast to the grid
    of shape."""
    xcdat = pytest.importorskip("xcdat")

    ds = xcdat.open_dataset(path, add_bounds=["X", "Y"], decode_times=False)
    weights = ds.spatial.get_weights(axis=["X", "Y"], data_var="observation").transpose("lat", "lon").values
    return np.broadcast_to(weights, shape[-2:])


def _xarray_stats(model, obs, weights):
    """The statistics worked out independently of OceanVal, with numpy on what xarray reads."""
    ok = np.isfinite(model) & np.isfinite(obs)
    w = np.broadcast_to(weights, model.shape)[ok]
    m, o = model[ok].astype("f8"), obs[ok].astype("f8")
    w = w / w.sum()
    mm, om = np.sum(w * m), np.sum(w * o)
    msd, osd = np.sqrt(np.sum(w * (m - mm) ** 2)), np.sqrt(np.sum(w * (o - om) ** 2))
    return {
        "model_mean": mm, "obs_mean": om, "model_sd": msd, "obs_sd": osd, "bias": mm - om,
        "rmsd": np.sqrt(np.sum(w * (m - o) ** 2)),
        "r": np.sum(w * (m - mm) * (o - om)) / (msd * osd),
    }


def page(out_dir, name):
    found = glob.glob(str(out_dir / f"oceanval_report/_build/html/notebooks/*{name}.html"))
    assert len(found) == 1, found
    return text_of(found[0])


def test_every_page_ran_and_numbers_its_tables_without_a_gap(report):
    _, out_dir = report
    for name in ("foo_temperature", "bar_chlorophyll", "summary"):
        text = page(out_dir, name)
        for word in ("Figure", "Table"):
            found = numbers(text, word)
            assert found == list(range(1, len(found) + 1)), (name, word, found)
    assert "This is getting to the end!" in page(out_dir, "foo_temperature")


def test_the_summary_gives_the_taylor_numbers(report):
    _, out_dir = report
    text = page(out_dir, "summary")
    assert "Normalised standard deviation (σm/σo)" in text
    assert "Centred RMSD/σo" in text
    assert "Chlorophyll (log10)" in text or "chlorophyll (log10)" in text.lower()


def test_the_taylor_numbers_are_right(report):
    from oceanval import skill

    _, out_dir = report
    ff = glob.glob(str(out_dir / "oceanval_results/annual_mean/annualmean_temperature_foo.nc"))[0]
    s = skill.file_stats(ff)
    assert s["r"] == pytest.approx(1, abs=1e-5)
    assert s["nsd"] == pytest.approx(1 / 1.1, rel=1e-4)
    assert s["crmsd_n"] == pytest.approx(0.1 / 1.1, rel=1e-3)
    assert s["bias"] == pytest.approx(-(0.1 * s["model_mean"] + 0.5), rel=1e-4)
    ff = glob.glob(str(out_dir / "oceanval_results/annual_mean/annualmean_chlorophyll_bar.nc"))[0]
    s = skill.file_stats(ff, log=True)
    assert s["bias"] == pytest.approx(-np.log10(2), abs=1e-5)
    # and against xarray, from the annual mean file
    import xarray as xr

    ff = glob.glob(str(out_dir / "oceanval_results/annual_mean/annualmean_temperature_foo.nc"))[0]
    ds = xr.open_dataset(ff)
    model, obs = np.squeeze(ds.model.values), np.squeeze(ds.observation.values)
    want = _xarray_stats(model, obs, _areas(ff, model.shape))
    got = skill.file_stats(ff)
    for key in ("model_mean", "obs_mean", "model_sd", "obs_sd", "bias", "rmsd", "r"):
        assert got[key] == pytest.approx(want[key], rel=1e-4, abs=1e-7), key


def test_the_regional_table_is_cmems_style(report):
    _, out_dir = report
    text = page(out_dir, "foo_temperature")
    for column in ("Observed mean", "Modelled mean", "Observed SD", "Modelled SD"):
        assert column in text
    assert "North Sea (NORDSEA)" in text
    assert "Fay" not in text and "GLO-QUID-001-028" in text
    stats = pd.read_csv(glob.glob(str(out_dir / "oceanval_results/regionals/foo_temperature_regional_stats.csv"))[0])
    full = stats.query("region == 'Full Domain' and scale == 'linear'").iloc[0]
    assert full.model_sd / full.obs_sd == pytest.approx(1 / 1.1, rel=1e-3)
    assert full.bias == pytest.approx(-(0.1 * full.model_mean + 0.5), rel=1e-4)
    assert not (stats.scale == "log10").any()
    # and against xarray: every cell and month of the matchup's monthly climatology, by area
    import xarray as xr

    folder, _ = report
    surface = folder / "oceanval_matchups/gridded/temperature/foo_temperature_surface.nc"
    ds = xr.open_dataset(surface)
    model = ds.model.values
    obs = ds.observation.values
    want = _xarray_stats(model, obs, _areas(surface, model.shape))
    for key in ("obs_mean", "model_mean", "obs_sd", "model_sd", "bias", "rmsd"):
        assert full[key] == pytest.approx(want[key], rel=1e-4), key


def test_chlorophyll_is_also_given_on_log10_values(report):
    _, out_dir = report
    text = page(out_dir, "bar_chlorophyll")
    assert "on the log10 of" in text
    stats = pd.read_csv(glob.glob(str(out_dir / "oceanval_results/regionals/bar_chlorophyll_regional_stats.csv"))[0])
    log = stats.query("region == 'Full Domain' and scale == 'log10'").iloc[0]
    assert log.bias == pytest.approx(-np.log10(2), abs=1e-5)
    assert log.model_sd == pytest.approx(log.obs_sd, rel=1e-4)


def test_the_statistics_at_each_depth(report):
    _, out_dir = report
    text = page(out_dir, "foo_temperature")
    assert "at each depth level ofthe" not in text
    assert "Statistics of modelled against observed" in text
    bins = pd.read_csv(glob.glob(str(out_dir / "oceanval_results/temperature_foo_gridded_depth_summary.csv"))[0])
    assert list(bins.Depth)[0] == "0-10m"
    # the bins against numpy, from the matchup itself (below the surface the matchup's model is
    # interpolated to the observations' depths by the layers' thickness, so it is not the model
    # the observations were made from): the annual mean, each level weighted by its cells' areas
    # (xCDAT's) and the thickness of its layer
    import xarray as xr
    from oceanval import skill

    folder, _ = report
    ds = xr.open_dataset(folder / "oceanval_matchups/gridded/temperature/foo_temperature_vertical.nc")
    model = np.nanmean(ds.model.values, axis=0)
    obs = np.nanmean(ds.observation.values, axis=0)
    depths = ds.deptht.values
    thickness = skill.layer_thickness(depths)
    area = _areas(folder / "oceanval_matchups/gridded/temperature/foo_temperature_surface.nc", obs.shape)
    for label, low, high in (("0-10m", 0, 10), ("10-30m", 10, 30)):
        inside = (depths > low) & (depths <= high) if low > 0 else (depths >= low) & (depths <= high)
        m, o = model[inside], obs[inside]
        w = thickness[inside][:, None, None] * area[None]
        ok = np.isfinite(m) & np.isfinite(o)
        w = np.where(ok, w, 0)
        bias = np.sum(w * np.nan_to_num(m - o)) / w.sum()
        mm, om = np.sum(w * np.nan_to_num(m)) / w.sum(), np.sum(w * np.nan_to_num(o)) / w.sum()
        r = np.sum(w * np.nan_to_num((m - mm) * (o - om))) / np.sqrt(
            np.sum(w * np.nan_to_num((m - mm) ** 2)) * np.sum(w * np.nan_to_num((o - om) ** 2))
        )
        row = bins.query("Depth == @label").iloc[0]
        assert row.Bias == pytest.approx(bias, rel=1e-3), label
        assert row.Correlation == pytest.approx(r, abs=1e-3), label
    summary = page(out_dir, "summary")
    assert "of model across depths" in summary
    # df_display title-cases the row labels
    assert "(foo, gridded)" in summary.lower()
