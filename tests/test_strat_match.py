"""Stratification matchups (oceanval.strat_match and matchall): the rows of a
cast matched with the model, the model's top and bottom cells, the measures
written, and the temperature and salinity matchups that come with them.

The matchups are of a small model (strat_model) whose temperature and salinity
are linear in depth, so casts sampled from it give the model back.
"""

import pickle

import numpy as np
import pandas as pd
import pytest

import oceanval
from oceanval import ices, strat_match, stratification

from strat_model import (CASTS, DAYS, DEPTHS, LATS, LONS, WET_LEVELS, _casts, _expected, _s_levels, _z_levels,
                         salinity, temperature)


def _run(sim_dir, out_dir, observations, monkeypatch, thickness, temperature_too=False):
    monkeypatch.setattr(ices, "download_point_data", lambda recipe, start, end, lon, lat: observations.copy())
    oceanval.reset()
    try:
        oceanval.add_point_comparison(
            name="stratification", model_variable="votemper", salinity_variable="vosaline",
            recipe={"stratification": "ices"}, start=2012, end=2012,
        )
        if temperature_too:
            oceanval.add_point_comparison(
                name="temperature", model_variable="votemper", recipe={"temperature": "ices"},
                vertical=True, start=2012, end=2012,
            )
        oceanval.matchup(
            sim_dir=str(sim_dir), start=2012, end=2012, ask=False, cores=1, out_dir=str(out_dir),
            thickness=thickness,
        )
    finally:
        oceanval.reset()
    return out_dir / "oceanval_matchups" / "point" / "all"


def _close(found, expected, keys):
    merged = found.round({"lon": 4, "lat": 4}).merge(expected.round({"lon": 4, "lat": 4}), on=keys)
    assert len(merged) == len(expected)
    return merged


@pytest.fixture(scope="module")
def s_level_run(tmp_path_factory):
    """One stratification matchup of the s-level model, with a temperature matchup
    through the water column ticked too, matched the same way."""
    root = tmp_path_factory.mktemp("s_levels")
    centres = _s_levels(root / "sim")
    thickness = DEPTHS / WET_LEVELS
    top = lambda i, j: centres[0, j, i]
    bottom = lambda i, j: centres[WET_LEVELS - 1, j, i]
    water = lambda i, j: DEPTHS[j, i]
    observations = _casts(top, bottom)
    with pytest.MonkeyPatch.context() as monkeypatch:
        printed = []
        monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a)))
        folder = _run(root / "sim", root / "out", observations, monkeypatch, "e3t", temperature_too=True)
    return folder, observations, (top, bottom, water), printed, thickness


class TestTheRowsOfACast:
    places = pd.DataFrame({"lon": [1.0], "lat": [2.0], "surface_depth": [1.5], "bottom_depth": [27.0], "water_depth": [30.0]})

    def frame(self):
        return pd.DataFrame({
            "lon": 1.0, "lat": 2.0, "year": 2012, "month": 7, "day": 5,
            "depth": [0.0, 10.0, 29.0], "observation": [15.0, 12.0, 10.0], "salinity": [35.0, 35.1, np.nan],
        })

    def test_observations_outside_the_cell_centres_are_matched_in_the_top_and_bottom_cells(self):
        rows = strat_match.with_cell_rows(self.frame(), self.places)
        cast = rows.loc[rows.role == "cast"]
        assert cast.depth.tolist() == pytest.approx([1.5 + strat_match.OFFSET, 10.0, 27.0 - strat_match.OFFSET])
        assert cast.obs_depth.tolist() == [0.0, 10.0, 29.0]

    def test_each_cast_has_a_row_at_its_top_and_bottom_cell_centres(self):
        rows = strat_match.with_cell_rows(self.frame(), self.places)
        assert rows.role.value_counts().to_dict() == {"cast": 3, "surface": 1, "bottom": 1}
        assert rows.loc[rows.role == "surface"].depth.item() == pytest.approx(1.5 + strat_match.OFFSET)
        assert rows.loc[rows.role == "bottom"].depth.item() == pytest.approx(27.0 - strat_match.OFFSET)
        assert rows.loc[rows.role != "cast"].observation.isna().all()

    def test_a_cast_where_the_model_has_no_sea_is_left_out(self):
        rows = strat_match.with_cell_rows(self.frame().assign(lon=9.0), self.places)
        assert len(rows) == 0

    def test_cells_leave_out_land_and_take_one_top_depth_for_z_levels(self):
        bottom = pd.DataFrame({"lon": [1.0, 3.0], "lat": [2.0, 2.0], "bottom_depth": [27.0, 0.0], "water_depth": [30.0, 0.0]})
        found = strat_match.cells(bottom.loc[:, ["lon", "lat"]], 2.5, bottom)
        assert found.lon.tolist() == [1.0]
        assert found.surface_depth.tolist() == [2.5]

    def test_the_matched_rows_split_into_profiles_and_casts_in_degrees_c(self):
        rows = strat_match.with_cell_rows(self.frame(), self.places)
        keys = ["lon", "lat", "year", "month", "day", "depth"]
        matched = rows.loc[:, keys].assign(
            **{strat_match.TEMPERATURE: 273.15 + 20 - rows.depth / 10, strat_match.SALINITY: 35.0}
        )
        profiles, casts, kelvin = strat_match.split(matched, rows, self.places)
        assert kelvin
        assert profiles.depth.tolist() == [0.0, 10.0, 29.0]
        assert profiles.model.tolist() == pytest.approx([20 - (1.5 + strat_match.OFFSET) / 10, 19.0, 20 - (27 - strat_match.OFFSET) / 10])
        assert list(casts.columns) == ["lon", "lat", "year", "month", "day", *stratification.CAST_COLUMNS]
        assert casts.model_bottom.item() == pytest.approx(20 - (27 - strat_match.OFFSET) / 10)
        frames = strat_match.reused_frames(profiles, kelvin)
        # a temperature matchup in the model's units: Kelvin
        assert frames["temperature"].observation.tolist() == pytest.approx([288.15, 285.15, 283.15])
        # salinity only where it was observed
        assert len(frames["salinity"]) == 2

    def test_the_profiles_are_kept_where_the_report_does_not_take_them_for_a_matchup(self):
        assert not strat_match.profile_path("a/ICES_all_stratification.csv").endswith(".csv")
        assert not strat_match.casts_path("a/ICES_all_stratification.csv").endswith(".csv")

    def test_stratification_is_matched_first(self):
        found = strat_match.first([("salinity", "ICES"), ("temperature", "EN4"), ("stratification", "ICES")])
        assert found[0] == ("stratification", "ICES")


class TestReuse:
    def setup_method(self):
        oceanval.reset()

    def teardown_method(self):
        oceanval.reset()

    def _stratification(self, **extra):
        oceanval.add_point_comparison(
            name="stratification", model_variable="votemper", recipe={"stratification": "ices"},
            start=2010, end=2012, **extra,
        )

    def test_temperature_and_salinity_are_added_when_not_asked_for(self):
        self._stratification(salinity_variable="vosaline")
        point = [("stratification", "ICES")]
        reused = strat_match.add_reused(oceanval.definitions, point, ["year", "month", "day"])
        assert reused == {("temperature", "ICES"): "ICES", ("salinity", "ICES"): "ICES"}
        assert point == [("stratification", "ICES"), ("temperature", "ICES"), ("salinity", "ICES")]
        added = oceanval.definitions["salinity"]
        assert added.model_variable == "vosaline"
        comparison = added.point_comparisons["ICES"]
        assert comparison["vertical"] and comparison["recipe"]["parameter"] == "PSALPR01"
        assert (comparison["start"], comparison["end"]) == (2010, 2012)
        assert comparison["point_time_res"] == ["year", "month", "day"]

    def test_without_salinity_only_temperature_comes_with_it(self):
        self._stratification()
        reused = strat_match.add_reused(oceanval.definitions, [("stratification", "ICES")], ["year", "month", "day"])
        assert list(reused) == [("temperature", "ICES")]

    def test_one_asked_for_and_matched_the_same_way_is_given(self):
        self._stratification()
        oceanval.add_point_comparison(name="temperature", model_variable="votemper", recipe={"temperature": "ices"},
                                      vertical=True, start=2010, end=2012)
        point = [("stratification", "ICES"), ("temperature", "ICES")]
        assert strat_match.add_reused(oceanval.definitions, point, ["year", "month", "day"]) == {("temperature", "ICES"): "ICES"}
        assert len(point) == 2

    @pytest.mark.parametrize("extra", [
        dict(point_time_res=["year", "month"]), dict(start=2011), dict(vertical=False),
    ])
    def test_one_matched_otherwise_is_matched_on_its_own(self, extra):
        self._stratification()
        given = dict(vertical=True, start=2010, end=2012)
        given.update(extra)
        oceanval.add_point_comparison(name="temperature", model_variable="votemper", recipe={"temperature": "ices"}, **given)
        assert strat_match.add_reused(oceanval.definitions, [("stratification", "ICES")], ["year", "month", "day"]) == {}

    def test_one_of_another_model_variable_is_not_given(self):
        self._stratification()
        oceanval.add_point_comparison(name="temperature", model_variable="thetao", recipe={"temperature": "ices"},
                                      vertical=True, start=2010, end=2012)
        assert strat_match.add_reused(oceanval.definitions, [("stratification", "ICES")], ["year", "month", "day"]) == {}


class TestAnSLevelMatchup:
    def test_the_measures_of_the_casts_are_the_models(self, s_level_run):
        folder, _, (top, bottom, water), _, _ = s_level_run
        found = pd.read_csv(folder / "stratification" / "ICES" / "ICES_all_stratification.csv")
        assert list(found.columns) == ["lon", "lat", "year", "month", "day", "measure", "observation", "model", "water_depth", "n_samples"]
        assert set(found.measure) == set(stratification.MEASURES)
        merged = _close(found, _expected(top, bottom, water), ["lon", "lat", "day", "measure"])
        assert np.abs(merged.observation - merged.expected).max() < 1e-3
        assert np.abs(merged.model - merged.expected).max() < 1e-3
        dt = merged.loc[merged.measure == "dt"]
        assert (dt.model > 2).all()
        # 0.2 degrees C below the 10 m reference, cooling by 0.1 degrees C a metre
        assert merged.loc[merged.measure == "mld"].model.tolist() == pytest.approx([12.0] * len(CASTS), abs=1e-3)

    def test_the_bottom_is_the_deepest_wet_cell_not_the_level_under_the_sea_bed(self, s_level_run):
        folder, _, (top, bottom, water), _, _ = s_level_run
        casts = pd.read_csv(strat_match.casts_path(str(folder / "stratification" / "ICES" / "ICES_all_stratification.csv")))
        for i, j, day in CASTS:
            row = casts.loc[np.isclose(casts.lon, LONS[i]) & np.isclose(casts.lat, LATS[j]) & (casts.day == day)].iloc[0]
            assert row.water_depth == pytest.approx(water(i, j), abs=1e-3)
            assert row.bottom_depth == pytest.approx(bottom(i, j), abs=1e-3)
            assert row.surface_depth == pytest.approx(top(i, j), abs=1e-3)
            assert row.model_bottom == pytest.approx(temperature(bottom(i, j), day), abs=1e-3)
            assert row.model_bottom_salinity == pytest.approx(salinity(bottom(i, j)), abs=1e-3)

    def test_it_gives_the_temperature_and_salinity_matchups_without_matching_them_again(self, s_level_run):
        folder, observations, _, printed, _ = s_level_run
        for name, column in [("temperature", "observation"), ("salinity", "salinity")]:
            df = pd.read_csv(folder / name / "ICES" / f"ICES_all_{name}.csv")
            assert sorted(df.columns) == ["day", "depth", "lat", "lon", "model", "month", "observation", "year"]
            assert len(df) == len(observations)
            assert np.abs(df.model - df.observation).max() < 1e-3
            with open(folder / name / "ICES" / "matchup_dict.pkl", "rb") as f:
                settings = pickle.load(f)
            assert settings["point_time_res"] == ["year", "month", "day"]
            assert settings["model_variable"] == ("votemper" if name == "temperature" else "vosaline")
        # the temperature matchup asked for, matched the same way, was not matched again
        assert any("temperature (ICES) matchup was made with the stratification matchup" in x for x in printed)
        assert not any("all-depths sea water temperature with ICES" in x.lower() for x in printed)
        assert not any("Matching up model output of all sea water temperature" in x for x in printed)


def test_a_z_level_matchup_finds_the_deepest_wet_level(tmp_path, monkeypatch):
    levels, wet = _z_levels(tmp_path / "sim")
    deepest = lambda i, j: levels[wet[:, j, i]].max()
    top = lambda i, j: levels[0]
    water = lambda i, j: deepest(i, j) + 2.5
    observations = _casts(top, deepest)
    folder = _run(tmp_path / "sim", tmp_path / "out", observations, monkeypatch, "z_level")
    path = folder / "stratification" / "ICES" / "ICES_all_stratification.csv"
    casts = pd.read_csv(strat_match.casts_path(str(path)))
    for i, j, day in CASTS:
        row = casts.loc[np.isclose(casts.lon, LONS[i]) & np.isclose(casts.lat, LATS[j]) & (casts.day == day)].iloc[0]
        assert row.bottom_depth == pytest.approx(deepest(i, j))
        assert row.water_depth == pytest.approx(water(i, j))
    found = pd.read_csv(path)
    merged = _close(found, _expected(top, deepest, water), ["lon", "lat", "day", "measure"])
    assert np.abs(merged.model - merged.expected).max() < 1e-3
    # every cast, though the sea bed shelves between them: each is compared with
    # its nearest water column, whose neighbours' missing cells are not used
    assert merged.groupby(["lon", "lat", "day"]).ngroups == len(CASTS)
    temperature_matchup = pd.read_csv(folder / "temperature" / "ICES" / "ICES_all_temperature.csv")
    assert len(temperature_matchup) == len(observations)


def test_an_en4_matchup_gives_its_own_temperature_and_salinity_matchups(tmp_path, monkeypatch):
    from oceanval import en4

    centres = _s_levels(tmp_path / "sim")
    top = lambda i, j: centres[0, j, i]
    bottom = lambda i, j: centres[WET_LEVELS - 1, j, i]
    observations = _casts(top, bottom)[en4.COLUMNS]
    asked = []

    def download(recipe, start, end, lon, lat):
        asked.append(recipe)
        return observations.copy()

    monkeypatch.setattr(en4, "download_point_data", download)
    oceanval.reset()
    try:
        oceanval.add_point_comparison(
            name="stratification", model_variable="votemper", salinity_variable="vosaline",
            recipe={"stratification": "en4"}, start=2012, end=2012,
        )
        oceanval.matchup(sim_dir=str(tmp_path / "sim"), start=2012, end=2012, ask=False, cores=1,
                         out_dir=str(tmp_path / "out"), thickness="e3t")
    finally:
        oceanval.reset()
    folder = tmp_path / "out" / "oceanval_matchups" / "point" / "all"
    assert asked == [{"kind": "en4", "variable": "stratification"}]
    assert len(pd.read_csv(folder / "stratification" / "EN4" / "EN4_all_stratification.csv")) > 0
    for name in ("temperature", "salinity"):
        assert len(pd.read_csv(folder / name / "EN4" / f"EN4_all_{name}.csv")) == len(observations)
