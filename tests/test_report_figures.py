"""The gridded page's figures from the gap report (options 1, 3, 4, 8, 9, 13 and
the peak month of 10) and the point page's mean profiles, through a real
matchup and two validations of it: one with every figure, whose numbers are
checked against xarray (and xCDAT's area weights) worked out independently of
OceanVal, and one that leaves every new figure out (validate's omit).

The observations are the example model's own monthly means, changed in ways
whose effect is known: temperature's are 1.1 times the model's plus 0.5, month
by month for 2000 and 2001 (so the matchup pairs 24 months, and the model peaks
when the observations do), and "chlorophyll" (the model's nitrate) is twice the
model's monthly climatology a month later (so the observations peak a month
after the model). "oxygen" is the same as chlorophyll, so its annual low, which
is what oxygen's page times, is also a month after the model's.
"""

import glob
import html
import re

import nctoolkit as nc
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from figure_code_checks import check_every_figure_has_its_code, check_no_figure_code, check_the_code_draws_each_figure

NEW_GRIDDED = [
    "density_scatter", "peak_month", "zonal_lines", "hovmoller_clim", "hovmoller_years",
    "mean_profiles", "depth_maps",
]


def text_of(path):
    text = open(path).read()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


def numbers(text, word):
    return [int(x) for x in re.findall(rf"{word} (\d+) ?:", text)]


def page(out_dir, name):
    found = glob.glob(str(out_dir / f"oceanval_report/_build/html/notebooks/*{name}.html"))
    assert len(found) == 1, found
    return text_of(found[0])


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    folder = tmp_path_factory.mktemp("figures")
    # temperature: every month of 2000 and 2001, 1.1 times the model's plus 0.5
    obs = folder / "obs_temp"
    obs.mkdir()
    for year in (2000, 2001):
        ds = nc.open_data(sorted(glob.glob(f"data/example/{year}/*/*grid_T.nc")), checks=False)
        ds.subset(variables="votemper")
        ds.merge("time")
        ds.tmean(["year", "month"])
        ds * 1.1
        ds + 0.5
        ds.to_nc(str(obs / f"obs_{year}.nc"), overwrite=True)
    # "chlorophyll": twice the model's nitrate climatology of the years matched up, a month later
    obs = folder / "obs_chl"
    obs.mkdir()
    ds = nc.open_data(sorted(glob.glob("data/example/200[01]/*/*ptrc_T.nc")), checks=False)
    ds.subset(variables="N3_n")
    ds.merge("time")
    ds.tmean("month")
    ds * 2
    ds.to_nc(str(folder / "chl_clim.nc"), overwrite=True)
    with xr.open_dataset(folder / "chl_clim.nc") as clim:
        shifted = clim.load()
    time_name = [x for x in shifted["N3_n"].dims if "time" in x][0]
    shifted["N3_n"].values = np.roll(shifted["N3_n"].values, 1, axis=shifted["N3_n"].dims.index(time_name))
    shifted.to_netcdf(obs / "obs_2000.nc")

    oceanval.reset()
    oceanval.add_gridded_comparison(
        name="temperature", obs_path=str(folder / "obs_temp"), source="foo",
        model_variable="votemper", obs_variable="votemper", climatology=False,
        vertical=True, start=2000, end=2001,
    )
    oceanval.add_gridded_comparison(
        name="chlorophyll", obs_path=str(folder / "obs_chl"), source="bar",
        model_variable="N3_n", obs_variable="N3_n", climatology=True, start=2000, end=2001,
    )
    oceanval.add_gridded_comparison(
        name="oxygen", obs_path=str(folder / "obs_chl"), source="qux",
        model_variable="N3_n", obs_variable="N3_n", climatology=True, start=2000, end=2001,
    )
    oceanval.add_point_comparison(
        name="temperature", obs_path="data/evaldata/point/nws/all/temperature", source="baz",
        model_variable="votemper", vertical=True, obs_adder=273.15,
    )
    oceanval.matchup(
        sim_dir="data/example", start=2000, end=2001, ask=False, cores=1,
        thickness="data/example/e3t.nc", out_dir=str(folder),
    )
    detailed = folder / "detailed"
    # with figure code: every figure has the button that copies the code that draws it
    oceanval.validate(
        data_dir=str(folder), out_dir=str(detailed), concise=False, subregions="cmems",
        map_depths=[20, 5000], test=True, figure_code=True,
    )
    # the concise report, which leaves every one of them out (the same as omit=NEW_GRIDDED): cheaper
    omitted = folder / "omitted"
    oceanval.validate(
        data_dir=str(folder), out_dir=str(omitted), concise=True, omit=["point_profiles"], test=True,
    )
    yield folder, detailed, omitted
    oceanval.reset()


def test_every_figure_has_its_picture(reports):
    # each "Figure N" caption has an image between it and the caption before it: a figure that was
    # drawn but not shown (plotnine's draw() closes its figure, so a plt.show() after it showed
    # nothing) leaves its caption alone
    _, detailed, _ = reports
    pages = glob.glob(str(detailed / "oceanval_report/_build/html/notebooks/*.html"))
    alone = []
    for path in pages:
        text = open(path).read()
        tokens = re.finditer(r"<img\b|<strong>(Figure|Table) (\d+)</strong>", text)
        pictures = 0
        for token in tokens:
            if token.group(0) == "<img":
                pictures += 1
            elif token.group(1):
                if token.group(1) == "Figure" and pictures == 0:
                    alone.append(f"{path.split('/')[-1]}: Figure {token.group(2)}")
                pictures = 0
    assert pages and not alone, alone


def test_every_figure_has_the_code_that_draws_it_again(reports, tmp_path):
    # figure_code=True: each figure's button copies code that, run on its own, draws the
    # figure the page shows, from the data saved in oceanval_results/figure_data
    _, detailed, _ = reports
    html_dir = str(detailed / "oceanval_report/_build/html/notebooks")
    shown = check_every_figure_has_its_code(html_dir, str(detailed / "oceanval_results"))
    # the point page and the summary as well as the gridded pages
    assert all(shown[page][0] > 0 for page in shown if "methods" not in page), shown
    assert check_the_code_draws_each_figure(html_dir, str(tmp_path)) == sum(n for n, _ in shown.values())


def test_without_figure_code_nothing_is_saved_for_it(reports):
    _, _, omitted = reports
    check_no_figure_code(str(omitted / "oceanval_report/_build/html"), str(omitted / "oceanval_results"))


def _surface(folder, variable, source):
    return folder / f"oceanval_matchups/gridded/{variable}/{source}_{variable}_surface.nc"


def _climatology(path):
    """The monthly climatology of a surface matchup's model and observation,
    worked out by xarray (each month's mean over the years, ignoring what is
    missing, as the page's tmean("month") does), with lat and lon last."""
    ds = xr.open_dataset(path)
    time_name = [x for x in ds["model"].dims if "time" in x][0]
    clim = ds[["model", "observation"]].groupby(f"{time_name}.month").mean(skipna=True)
    return clim["model"].values, clim["observation"].values


def test_every_page_ran_and_numbers_its_figures_without_a_gap(reports):
    _, detailed, omitted = reports
    for out_dir in (detailed, omitted):
        for name in ("foo_temperature", "bar_chlorophyll", "baz_all_temperature", "summary"):
            text = page(out_dir, name)
            for word in ("Figure", "Table"):
                found = numbers(text, word)
                assert found == list(range(1, len(found) + 1)), (out_dir.name, name, word, found)
        assert "This is getting to the end!" in page(out_dir, "foo_temperature")


CAPTIONS = {
    "density_scatter": "for every grid cell and month matched up (left) and for the annual means of each grid cell (right)",
    "peak_month": "Month of the maximum of the monthly climatology",
    "zonal_lines": "Zonal mean of the annual mean",
    "hovmoller_clim": "in each month of the climatology, by latitude",
    "hovmoller_years": "in every month matched up, by latitude",
    "mean_profiles": "Mean profiles of",
    "depth_maps": "(the observations' level nearest 20 m)",
}


def test_each_new_figure_is_shown_and_left_out_by_omit(reports):
    _, detailed, omitted = reports
    temperature = page(detailed, "foo_temperature")
    for item, caption in CAPTIONS.items():
        assert caption in temperature, item
        assert caption not in page(omitted, "foo_temperature"), item
    # the depth with no level near it is not mapped
    assert "nearest 5000 m" not in temperature
    # 12 months of chlorophyll: no figure of every month, and nothing through the water column
    chlorophyll = page(detailed, "bar_chlorophyll")
    assert CAPTIONS["hovmoller_clim"] in chlorophyll
    assert CAPTIONS["hovmoller_years"] not in chlorophyll
    assert CAPTIONS["mean_profiles"] not in chlorophyll
    assert "(log10)" in chlorophyll
    point = page(detailed, "baz_all_temperature")
    assert "in each depth range, over every observation matched up in it" in point
    assert "in each depth range, over every observation matched up in it" not in page(omitted, "baz_all_temperature")


def test_the_zonal_means_agree_with_xarray(reports):
    folder, detailed, _ = reports
    for variable, source, log in (("temperature", "foo", False), ("chlorophyll", "bar", True)):
        csv = pd.read_csv(detailed / f"oceanval_results/zonal_mean/zonalmean_{variable}_{source}.csv")
        model, obs = _climatology(_surface(folder, variable, source))
        with _quiet(), np.errstate(invalid="ignore", divide="ignore"):
            model, obs = np.nanmean(model, axis=0), np.nanmean(obs, axis=0)
            if log:
                model = np.where(model > 0, np.log10(model), np.nan)
                obs = np.where(obs > 0, np.log10(obs), np.nan)
        both = np.isfinite(model) & np.isfinite(obs)
        model, obs = np.where(both, model, np.nan), np.where(both, obs, np.nan)
        # each row of a regular grid has cells of one area, so the zonal mean is the plain mean
        with _quiet():
            want_obs, want_model = np.nanmean(obs, axis=-1), np.nanmean(model, axis=-1)
        lat = xr.open_dataset(_surface(folder, variable, source))["lat"].values
        want = pd.DataFrame({"lat": np.round(lat, 4), "observation": want_obs, "model": want_model}).dropna()
        got = csv.assign(lat=csv.lat.round(4)).merge(want, on="lat", suffixes=("", "_want"))
        assert len(got) == len(want) > 0, variable
        assert np.allclose(got.observation, got.observation_want, rtol=1e-4), variable
        assert np.allclose(got.model, got.model_want, rtol=1e-4), variable
        assert bool(csv.log10.iloc[0]) is log
        # the observations' interquartile range across longitude: CDO's zonpctl takes the value at
        # the rank, as numpy's inverted_cdf does (a skewed row's mean can be outside it)
        rows = [row for row in obs if np.isfinite(row).any()]
        for q in (25, 75):
            want_q = [np.nanpercentile(row, q, method="inverted_cdf") for row in rows]
            got_q = csv[f"observation_q{q}"].dropna().values
            assert np.allclose(got_q, want_q, rtol=1e-5), (variable, q)


class _quiet:
    def __enter__(self):
        import warnings

        self.catch = warnings.catch_warnings()
        self.catch.__enter__()
        warnings.simplefilter("ignore", RuntimeWarning)

    def __exit__(self, *args):
        self.catch.__exit__(*args)


def _peaks(model, obs):
    """The peak month of each cell (1-12) of the paired climatology, the last month
    with the maximum (as the page's nctoolkit does), with what the page masks."""
    both = np.isfinite(model) & np.isfinite(obs)
    model, obs = np.where(both, model, np.nan), np.where(both, obs, np.nan)
    with _quiet():
        n = both.sum(axis=0)
        amplitude = (np.nanmax(obs, axis=0) - np.nanmin(obs, axis=0)) / np.abs(np.nanmean(obs, axis=0))
    ok = (n >= 12) & (amplitude >= 0.25)

    def last_max(x):
        filled = np.where(np.isfinite(x), x, -np.inf)
        return 12 - np.argmax(filled[::-1], axis=0)

    peak_model, peak_obs = last_max(model), last_max(obs)
    lag = peak_model - peak_obs
    lag = np.where(lag > 6, lag - 12, np.where(lag <= -6, lag + 12, lag))
    return ok, peak_model, peak_obs, lag


def _lows(model, obs):
    """The month of the annual low of each cell: _peaks of the values' negatives."""
    return _peaks(-model, -obs)


def test_the_peak_month_agrees_with_xarray(reports):
    folder, detailed, _ = reports
    for variable, source, lag_wanted in (("temperature", "foo", 0), ("chlorophyll", "bar", -1)):
        model, obs = _climatology(_surface(folder, variable, source))
        ok, peak_model, peak_obs, lag = _peaks(model, obs)
        assert ok.any(), variable
        got = xr.open_dataset(detailed / f"oceanval_results/peak_month/peakmonth_{variable}_{source}.nc")
        got_lag = np.squeeze(got["lag"].values)
        assert np.array_equal(np.isfinite(got_lag), ok), variable
        assert np.array_equal(np.squeeze(got["peak_obs"].values)[ok], peak_obs[ok]), variable
        assert np.array_equal(np.squeeze(got["peak_model"].values)[ok], peak_model[ok]), variable
        assert np.array_equal(got_lag[ok], lag[ok]), variable
        # the observations are the model a month later (chlorophyll), or the model itself
        assert np.all(lag[ok] == lag_wanted), variable


def test_the_maps_at_depth_are_at_the_nearest_level(reports):
    folder, detailed, _ = reports
    vertical = xr.open_dataset(folder / "oceanval_matchups/gridded/temperature/foo_temperature_vertical.nc")
    depth_name = [x for x in vertical["observation"].dims if "dep" in x][0]
    levels = vertical[depth_name].values
    nearest = levels[np.argmin(np.abs(levels - 20))]
    assert f"at {nearest:g} m (the observations' level nearest 20 m)" in page(detailed, "foo_temperature")


def test_the_point_depth_summary_has_the_means_of_the_profiles(reports):
    _, detailed, _ = reports
    summary = pd.read_csv(glob.glob(str(detailed / "oceanval_results/temperature_baz_depth_summary.csv"))[0])
    assert {"Model mean", "Observation mean"} <= set(summary.columns)
    # the bias is the model's mean minus the observations', over the same values
    assert np.allclose(summary.Bias, summary["Model mean"] - summary["Observation mean"], atol=1e-6)


def test_oxygen_times_its_annual_low_and_agrees_with_xarray(reports):
    folder, detailed, _ = reports
    model, obs = _climatology(_surface(folder, "oxygen", "qux"))
    # the amplitude test is of the observations' range over their mean, the same for the low
    ok, _, _, _ = _peaks(model, obs)
    _, low_model, low_obs, lag = _lows(model, obs)
    assert ok.any()
    got = xr.open_dataset(detailed / "oceanval_results/peak_month/peakmonth_oxygen_qux.nc")
    assert got.attrs["timing"] == "low"
    got_lag = np.squeeze(got["lag"].values)
    assert np.array_equal(np.isfinite(got_lag), ok)
    assert np.array_equal(np.squeeze(got["peak_obs"].values)[ok], low_obs[ok])
    assert np.array_equal(np.squeeze(got["peak_model"].values)[ok], low_model[ok])
    assert np.all(got_lag[ok] == -1)
    text = page(detailed, "qux_oxygen")
    # the variable registered here is named oxygen (the built-in recipes' is dissolved oxygen)
    assert "When does sea surface oxygen reach its annual low?" in text
    assert "Month of the minimum of the monthly climatology" in text
    assert " peak?" not in text
    # the others time their peak
    peak = xr.open_dataset(detailed / "oceanval_results/peak_month/peakmonth_chlorophyll_bar.nc")
    assert peak.attrs["timing"] == "high"


def _h1(out_dir, name):
    found = glob.glob(str(out_dir / f"oceanval_report/_build/html/notebooks/*{name}.html"))
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", open(found[0]).read(), re.S).group(1)
    return html.unescape(re.sub(r"<[^>]+>", "", heading)).replace("¶", "").strip()


def test_a_page_through_the_water_column_is_not_titled_sea_surface(reports):
    _, detailed, _ = reports
    assert not _h1(detailed, "foo_temperature").lower().startswith("sea surface")
    assert "validation using gridded observations from FOO" in _h1(detailed, "foo_temperature")
    assert _h1(detailed, "bar_chlorophyll").lower().startswith("sea surface")


def test_the_depth_table_has_at_most_ten_levels_from_the_surface_to_the_deepest(reports):
    from oceanval import depths

    folder, detailed, _ = reports
    found = glob.glob(str(detailed / "oceanval_report/_build/html/notebooks/*foo_temperature.html"))
    tables = [t for t in re.findall(r"<table.*?</table>", open(found[0]).read(), re.S) if "Depth (m)" in t]
    assert len(tables) == 1
    body = tables[0][tables[0].index("<tbody"):]
    # the first cell of each row of the table body is its depth
    shown = [float(re.sub(r"<[^>]+>", "", row).split()[0]) for row in re.findall(r"<td[^>]*>.*?</td>", body, re.S)[::8]]
    vertical = xr.open_dataset(folder / "oceanval_matchups/gridded/temperature/foo_temperature_vertical.nc")
    depth_name = [x for x in vertical["observation"].dims if "dep" in x][0]
    both = vertical["observation"].notnull() & vertical["model"].notnull()
    has_data = both.any([x for x in both.dims if x != depth_name])
    levels = vertical[depth_name].values[has_data.values]
    assert 1 < len(shown) <= 10
    # the table shows the depths to 2 decimal places
    assert np.allclose(shown, depths.table_levels(levels), atol=0.006)
    assert np.isclose(shown[-1], levels.max(), atol=0.006)


def test_each_month_has_its_density_scatter_and_the_spatial_patterns_follow_it(reports):
    _, detailed, _ = reports
    for name in ("foo_temperature", "bar_chlorophyll"):
        text = page(detailed, name)
        assert "for every grid cell matched up in each month of the year" in text, name
        density = text.index(CAPTIONS["density_scatter"])
        patterns = text.index("Can the model reproduce spatial patterns of")
        seasonality = text.index("Can the model reproduce seasonality of")
        assert density < patterns < seasonality, name
