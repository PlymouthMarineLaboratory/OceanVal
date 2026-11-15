"""compare()'s figures from the gap report (options 6, 12 and 13): the Taylor
diagram and the skill heat map of every simulation and variable, and the zonal
means of each simulation, through two real validations and a real comparison.

The two simulations are the example model and the same model with its
temperature and nitrate 1.2 times as large; the observations are the first
one's own monthly means (temperature) and twice its nitrate ("chlorophyll").
The statistics the figures show are checked against xarray, with xCDAT's area
weights, on the cells both simulations have.
"""

import glob
import html
import os
import re
import shutil

import nctoolkit as nc
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval


def text_of(path):
    text = open(path).read()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


@pytest.fixture(scope="module")
def comparison(tmp_path_factory):
    folder = tmp_path_factory.mktemp("compare")
    # the second simulation: the example model's files, temperature and nitrate times 1.2
    scaled = folder / "scaled"
    for path in sorted(glob.glob("data/example/2000/*/*.nc")):
        out = scaled / os.path.relpath(path, "data/example")
        out.parent.mkdir(parents=True, exist_ok=True)
        variable = "votemper" if path.endswith("grid_T.nc") else "N3_n"
        ds = nc.open_data(path, checks=False)
        ds.subset(variables=variable)
        ds * 1.2
        ds.to_nc(str(out), overwrite=True)
    shutil.copy("data/example/e3t.nc", scaled / "e3t.nc")

    obs_temp, obs_chl = folder / "obs_temp", folder / "obs_chl"
    obs_temp.mkdir()
    obs_chl.mkdir()
    for files, variable, times, obs in (("*grid_T.nc", "votemper", 1, obs_temp), ("*ptrc_T.nc", "N3_n", 2, obs_chl)):
        ds = nc.open_data(sorted(glob.glob(f"data/example/2000/*/{files}")), checks=False)
        ds.subset(variables=variable)
        ds.merge("time")
        ds.tmean("month")
        ds * times
        ds.to_nc(str(obs / "obs_2000.nc"), overwrite=True)

    validations = {}
    for name, sim_dir in (("example", "data/example"), ("scaled", str(scaled))):
        oceanval.reset()
        oceanval.add_gridded_comparison(
            name="temperature", obs_path=str(obs_temp), source="foo", model_variable="votemper",
            obs_variable="votemper", climatology=True, start=2000, end=2000,
        )
        oceanval.add_gridded_comparison(
            name="chlorophyll", obs_path=str(obs_chl), source="bar", model_variable="N3_n",
            obs_variable="N3_n", climatology=True, start=2000, end=2000,
        )
        run = folder / name
        oceanval.matchup(sim_dir=sim_dir, start=2000, end=2000, ask=False, cores=1, out_dir=str(run))
        oceanval.validate(data_dir=str(run), out_dir=str(run), test=True)
        validations[name] = str(run)
    oceanval.compare(model_dict=validations, out_dir=str(folder), view=False, ask=False)
    pages = glob.glob(str(folder / "oceanval_comparison/**/_build/html/notebooks/comparison_spatial.html"), recursive=True)
    assert len(pages) == 1, pages
    yield folder, validations, text_of(pages[0]), open(pages[0]).read()
    oceanval.reset()


def test_the_spatial_page_has_the_new_figures_in_order(comparison):
    _, _, text, _ = comparison
    for words in ("Taylor diagram of the annual mean sea surface values", "Skill of the annual mean sea surface values",
                  "Zonal mean of the annual mean sea surface temperature", "Zonal mean of the annual mean sea surface chlorophyll (log10)"):
        assert words in text, words
    found = [int(x) for x in re.findall(r"Figure (\d+) ?:", text)]
    assert found == list(range(1, len(found) + 1))


def test_the_best_value_is_bold(comparison):
    *_, raw = comparison
    # html, not markdown, as the table is html: no "**" left over
    assert re.search(r"<b>[-0-9.e]+</b>", raw)
    assert not re.search(r"\d\*\*", raw)


def _areas(path, shape):
    xcdat = pytest.importorskip("xcdat")
    ds = xcdat.open_dataset(path, add_bounds=["X", "Y"], decode_times=False)
    weights = ds.spatial.get_weights(axis=["X", "Y"], data_var="observation").transpose("lat", "lon").values
    return np.broadcast_to(weights, shape)


def test_the_skill_agrees_with_xarray(comparison):
    folder, validations, _, _ = comparison
    skill_csv = glob.glob(str(folder / "oceanval_comparison/**/compare_skill.csv"), recursive=True)
    assert len(skill_csv) == 1, skill_csv
    got = pd.read_csv(skill_csv[0])
    for variable, source, log in (("temperature", "foo", False), ("chlorophyll", "bar", True)):
        files = {name: os.path.join(path, f"oceanval_results/annual_mean/annualmean_{variable}_{source}.nc")
                 for name, path in validations.items()}
        data = {name: xr.open_dataset(path) for name, path in files.items()}
        values = {name: (np.squeeze(d.model.values).astype("f8"), np.squeeze(d.observation.values).astype("f8"))
                  for name, d in data.items()}
        # the cells every simulation has (both are on one grid here)
        common = np.ones(values["example"][0].shape, bool)
        for model, obs in values.values():
            common &= np.isfinite(model) & np.isfinite(obs)
            if log:
                common &= (model > 0) & (obs > 0)
        w = np.where(common, _areas(files["example"], common.shape), 0)
        w = w / w.sum()
        for name, (model, obs) in values.items():
            m, o = (np.log10(np.where(common, model, 1)), np.log10(np.where(common, obs, 1))) if log else (model, obs)
            m, o = np.where(common, m, 0), np.where(common, o, 0)
            mm, om = np.sum(w * m), np.sum(w * o)
            msd, osd = np.sqrt(np.sum(w * (m - mm) ** 2)), np.sqrt(np.sum(w * (o - om) ** 2))
            r = np.sum(w * (m - mm) * (o - om)) / (msd * osd)
            row = got[(got.simulation == name) & got.variable.str.lower().str.startswith(variable)].iloc[0]
            assert row.r == pytest.approx(r, abs=1e-4), (name, variable)
            assert row.nsd == pytest.approx(msd / osd, rel=1e-4), (name, variable)
            assert row.bias_n == pytest.approx(abs(mm - om) / osd, rel=1e-3, abs=1e-6), (name, variable)
        # the scaled temperature varies 1.2 times as much in space
        if not log:
            nsd = got[got.variable.str.lower().str.startswith("temperature")].set_index("simulation").nsd
            assert nsd["scaled"] == pytest.approx(1.2 * nsd["example"], rel=1e-3)
