"""The North Sea Benthos Survey's point recipe (oceanval.nsbs): its Darwin Core archive
read without the network, the recipe, how its model variable is found from the
long_names, its units, and a matchup of it with a model's benthic (2D) variables."""

import glob
import importlib
import os
import zipfile

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval import matchall, nsbs, unit_conversion, variable_identify
from oceanval.parsers import find_recipe

from test_data_store import store  # noqa: F401 - the fixture

create_recipes = importlib.import_module("oceanval.create_recipes")

EVENT_COLUMNS = [
    "id", "type", "eventID", "parentEventID", "eventDate", "minimumDepthInMeters",
    "maximumDepthInMeters", "decimalLatitude", "decimalLongitude",
]
FACT_COLUMNS = [
    "id", "occurrenceID", "measurementType", "measurementTypeID", "measurementValue",
    "measurementValueID", "measurementUnit", "measurementUnitID",
]


def _event(event, date, lat, lon, kind="sample"):
    return [event, kind, event, "", date, "30", "30", str(lat), str(lon)]


def _fact(event, kind, value, unit, aphia="883"):
    return [event, f"{event}-aphiaID:{aphia}-", kind, "", str(value), "", unit, ""]


def _archive(path, events, facts):
    """A Darwin Core archive as VLIZ's IPT writes the survey's: tab-separated text."""
    def table(columns, rows):
        return "\n".join("\t".join(row) for row in [columns] + rows) + "\n"

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("event.txt", table(EVENT_COLUMNS, events))
        archive.writestr("extendedmeasurementorfact.txt", table(FACT_COLUMNS, facts))
        archive.writestr("meta.xml", "<archive/>")
    return path


@pytest.fixture
def archive(tmp_path):
    a = "cruise:17-station:705-date:1986-04-25-sample:787-replic:1"
    b = "cruise:11-station:677-date:1986-04-01/1986-05-20-sample:967-replic:1"
    maff = "cruise:3-station:12-date:1985-01-01-sample:5-replic:1"
    bad = "cruise:17-station:9-date:1986/04-sample:9-replic:1"
    events = [
        _event(a, "1986-04-25", 54.0, 8.0),
        _event(b, "1986-04-01/1986-05-20", 56.5, -2.0),
        # MAFF's samples are all stamped 1 January 1985
        _event(maff, "1985-01-01", 55.0, 2.0),
        # a date that cannot be read
        _event(bad, "1986/04", 55.5, 3.0),
        # a station visit, which has no biomass of its own
        _event("cruise:17-station:705", "1986-04-25", 54.0, 8.0, kind="stationVisit"),
    ]
    facts = [
        # the groups of a sample are summed: polychaetes, molluscs and the other animals
        _fact(a, "Biomass", 2.0, "g/m2", "883"),
        _fact(a, "Biomass", 1.5, "g/m2", "51"),
        _fact(a, "Biomass", 0.5, "g/m2", "2"),
        # counts and the sediment are not biomass
        _fact(a, "Density", 120, "#/m2"),
        _fact(a, "sand", 80, "%"),
        _fact(b, "Biomass", 3.0, "g/m2"),
        _fact(maff, "Biomass", 9.0, "g/m2"),
        _fact(bad, "Biomass", 9.0, "g/m2"),
        _fact("cruise:17-station:705", "Vessel", "Victor Hensen", ""),
    ]
    return _archive(tmp_path / "nsbs.zip", events, facts)


class TestTheArchive:
    def test_the_biomass_of_each_sample_is_its_groups_summed_as_carbon(self, archive):
        df = nsbs.extract(archive)["macrobenthos"]
        assert list(df.columns) == nsbs.POINT_COLUMNS
        # g AFDW/m2 x 0.5 g C/g x 1000 mg/g
        by_place = df.set_index(["lon", "lat"]).observation
        assert by_place[(8.0, 54.0)] == pytest.approx((2.0 + 1.5 + 0.5) * 500)
        assert by_place[(-2.0, 56.5)] == pytest.approx(3.0 * 500)

    def test_only_the_1986_survey_is_kept(self, archive):
        df = nsbs.extract(archive)["macrobenthos"]
        # not MAFF's, dated only 1 January 1985, nor the date that cannot be read
        assert len(df) == 2 and (df.year == 1986).all()

    def test_a_span_of_days_is_dated_at_its_middle(self, archive):
        df = nsbs.extract(archive)["macrobenthos"]
        spanned = df[df.lon == -2.0].iloc[0]
        # 1 April to 20 May 1986: 49 days, so the middle is 25 April
        assert (spanned.year, spanned.month, spanned.day) == (1986, 4, 25)
        single = df[df.lon == 8.0].iloc[0]
        assert (single.month, single.day) == (4, 25)

    def test_an_archive_without_its_tables_or_columns_is_refused(self, tmp_path):
        path = tmp_path / "empty.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("event.txt", "id\teventID\n")
        with pytest.raises(ValueError, match="no extendedmeasurementorfact"):
            nsbs.extract(path)
        with zipfile.ZipFile(tmp_path / "columns.zip", "w") as archive:
            archive.writestr("event.txt", "eventID\teventDate\nx\t1986-04-01\n")
            archive.writestr("extendedmeasurementorfact.txt", "\t".join(FACT_COLUMNS) + "\n")
        with pytest.raises(ValueError, match="no column decimalLatitude"):
            nsbs.extract(tmp_path / "columns.zip")

    def test_download_point_data_cuts_the_box_and_years(self, monkeypatch, archive):
        frames = nsbs.extract(archive)
        monkeypatch.setattr(nsbs, "_without_store", lambda variable: frames[variable])
        monkeypatch.setattr(nsbs.data_store, "nsbs", lambda variables, download: None)
        recipe = {"kind": "nsbs", "variable": "macrobenthos"}
        assert len(nsbs.download_point_data(recipe, 1986, 1986, None, None)) == 2
        boxed = nsbs.download_point_data(recipe, 1900, 2030, (0, 10), (50, 60))
        assert list(boxed.lon) == [8.0]
        assert nsbs.download_point_data(recipe, 2000, 2010, None, None).empty
        assert nsbs.download_point_data(recipe, 1990, 1980, None, None).empty

    def test_the_store_keeps_the_archive_once(self, store, archive, monkeypatch):  # noqa: F811
        downloads = []

        def download(variables=None):
            downloads.append(variables)
            return nsbs.extract(archive), nsbs.ARCHIVE_URL

        monkeypatch.setattr(nsbs, "download_all", download)
        store("always")
        recipe = {"kind": "nsbs", "variable": "macrobenthos"}
        first = nsbs.download_point_data(recipe, 1986, 1986, None, None)
        second = nsbs.download_point_data(recipe, 1986, 1986, None, None)
        assert len(downloads) == 1
        assert sorted(first.observation) == pytest.approx(sorted(second.observation))

    def test_the_store_knows_the_survey(self):
        from oceanval import data_store

        assert "nsbs" in data_store.POINT_KINDS
        assert "ipt.vliz.be" in data_store.BUILTIN_HOSTS
        assert data_store.PIECE_NAME.match("nsbs/" + "0" * 32 + ".csv.gz")


class TestTheRecipe:
    def test_it_is_a_point_recipe_matched_by_month(self):
        found = find_recipe({"macrobenthos": "nsbs"}, cwd="/nonexistent")
        assert found["point"] is True and found["vertical"] is False
        assert found["builtin"] == "nsbs" and found["source"] == "NSBS"
        assert found["point_time_res"] == ["month"]
        assert "marineinfo.org/doc/dataset/67" in found["source_info"]
        assert found["short_title"] == "Macrobenthos"

    def test_it_is_only_for_the_macrobenthos(self):
        with pytest.raises(ValueError):
            find_recipe({"nitrate": "nsbs"}, cwd="/nonexistent")

    def test_it_is_always_matched_by_month(self):
        oceanval.reset()
        try:
            oceanval.add_point_comparison(name="macrobenthos", model_variable="Y2_c+Y3_c",
                                          recipe={"macrobenthos": "nsbs"})
            comparison = oceanval.definitions["macrobenthos"].point_comparisons["NSBS"]
            assert comparison["recipe"] == {"kind": "nsbs", "variable": "macrobenthos"}
            assert comparison["point_time_res"] == ["month"]
            assert comparison["vertical"] is False
        finally:
            oceanval.reset()

    @pytest.mark.parametrize("point_time_res", [["year", "month"], "month", ["month", "day"]])
    def test_point_time_res_cannot_be_given(self, point_time_res):
        oceanval.reset()
        try:
            with pytest.raises(ValueError, match="always matched by month"):
                oceanval.add_point_comparison(name="macrobenthos", model_variable="Y2_c+Y3_c",
                                              recipe={"macrobenthos": "nsbs"}, point_time_res=point_time_res)
        finally:
            oceanval.reset()

    def test_the_window_shows_how_it_is_matched_and_does_not_limit_its_years(self):
        from oceanval import recipes_gui

        entry = [e for e in create_recipes.POINT_RECIPE_CATALOGUE if e["recipe"] == "nsbs"][0]
        assert entry["fixed_point_time_res"] == ("month",)
        assert recipes_gui._time_res_label(entry["fixed_point_time_res"]) == "Month"
        # only the North Sea Benthos Survey, and stratification (by the day of each cast), are matched without a choice
        assert [(e["variable"], e["recipe"]) for e in create_recipes.POINT_RECIPE_CATALOGUE if e.get("fixed_point_time_res")] == [
            ("stratification", "ices"), ("stratification", "en4"), ("macrobenthos", "nsbs")]

    def test_its_observations_are_downloaded_for_the_survey_whatever_the_model_years(self):
        assert matchall.recipe_years({"kind": "nsbs", "variable": "macrobenthos"}, "macrobenthos", 2000, 2010) == (
            1986, 1986)

    def test_it_is_in_the_catalogue(self):
        entry = [e for e in create_recipes.POINT_RECIPE_CATALOGUE if e["recipe"] == "nsbs"]
        assert len(entry) == 1 and entry[0]["variable"] == "macrobenthos"
        assert entry[0]["units"] == "mg C/m2"
        assert not create_recipes._has_vertical_option(entry[0])


# what ERSEM writes in its output (FABM), as its long_names and units give them
ERSEM_CONTENTS = pd.DataFrame(
    [
        ("Y2_c", "benthic deposit feeder carbon", "mg C/m^2", 1),
        ("Y3_c", "benthic suspension feeder carbon", "mg C/m^2", 1),
        ("Y4_c", "benthic meiofauna carbon", "mg C/m^2", 1),
        ("H1_c", "benthic aerobic bacteria carbon", "mg C/m^2", 1),
        ("Q6_c", "benthic particulate organic matter carbon", "mg C/m^2", 1),
        ("Y2_faerbacc_result", "benthic deposit feeders predation of aerobic bacteria carbon", "mg C/m^2/d", 1),
        ("Y3_fphytoc_result", "benthic suspension feeders grazing of phytoplankton carbon", "mg C/m^2/d", 1),
        ("Y4_fmeioc_result", "benthic meiofauna predation of meiofauna carbon", "mg C/m^2/d", 1),
        ("Z4_c", "mesozooplankton carbon", "mg C/m^3", 20),
        ("N3_n", "nitrate nitrogen", "mmol N/m^3", 20),
    ],
    columns=["variable", "long_name", "unit", "nlevels"],
)


class TestTheModelVariable:
    def test_ersems_deposit_and_suspension_feeders_are_summed(self):
        mapping = create_recipes.contents_mapping(ERSEM_CONTENTS)[0]
        assert mapping["macrobenthos"] == "Y2_c+Y3_c"

    def test_the_rule_reads_the_long_names_not_the_variable_names(self):
        renamed = ERSEM_CONTENTS.assign(variable=[f"v{i}" for i in range(len(ERSEM_CONTENTS))])
        assert create_recipes.contents_mapping(renamed)[0]["macrobenthos"] == "v0+v1"
        # and names alone, without long_names, find nothing
        unnamed = ERSEM_CONTENTS.assign(long_name="")
        assert create_recipes.contents_mapping(unnamed)[0]["macrobenthos"] is None

    @pytest.mark.parametrize("long_name", [
        "macrobenthos carbon biomass", "Macrofauna carbon", "macrozoobenthos biomass", "filter feeder carbon",
    ])
    def test_other_wordings_of_the_macrofauna(self, long_name):
        contents = pd.DataFrame([("x", long_name, "g C m-2", 1)], columns=["variable", "long_name", "unit", "nlevels"])
        assert create_recipes.contents_mapping(contents)[0]["macrobenthos"] == "x"

    @pytest.mark.parametrize("long_name, units", [
        ("benthic meiofauna carbon", "mg C/m^2"),
        ("benthic deposit feeder nitrogen", "mmol N/m^2"),
        ("benthic deposit feeders respiration", "mg C/m^2/d"),
        # a flux with an innocent long_name is ruled out by its units
        ("deposit feeder carbon", "mg C/m^2/d"),
    ])
    def test_what_is_not_the_macrobenthos(self, long_name, units):
        contents = pd.DataFrame([("x", long_name, units, 1)], columns=["variable", "long_name", "unit", "nlevels"])
        assert create_recipes.contents_mapping(contents)[0]["macrobenthos"] is None

    def test_an_amount_per_area_fits_and_a_rate_does_not(self):
        assert variable_identify.units_fit("macrobenthos", "mg C/m^2")
        assert variable_identify.units_fit("macrobenthos", "mmol C m-2")
        assert not variable_identify.units_fit("macrobenthos", "mg C/m^2/d")
        assert not variable_identify.units_fit("macrobenthos", "mg C m-2 s-1")


class TestTheUnits:
    def test_the_same_units_written_two_ways(self):
        assert unit_conversion.suggest("macrobenthos", "mg C/m2", "mg C/m^2")["status"] == "same"

    def test_carbon_per_area_into_moles_and_grams(self):
        moles = unit_conversion.suggest("macrobenthos", "mg C/m2", "mmol C/m^2")
        assert moles["status"] == "convert" and moles["multiplier"] == pytest.approx(1 / 12.011, rel=1e-4)
        grams = unit_conversion.suggest("macrobenthos", "mg C/m2", "g C m-2")
        assert grams["multiplier"] == pytest.approx(1e-3)

    def test_a_rate_or_a_concentration_is_not_converted(self):
        assert unit_conversion.suggest("macrobenthos", "mg C/m2", "mg C/m^2/d")["status"] == "unknown"
        assert unit_conversion.suggest("macrobenthos", "mg C/m2", "mg C/m^3")["status"] == "unknown"

    def test_a_mass_of_something_else_is_not_turned_into_carbon(self):
        assert unit_conversion.suggest("macrobenthos", "mg N/m2", "mmol C/m^2")["status"] == "unknown"


# a short simulation of benthic (2D) variables: the first 2 days of two months in two years
SIM_MONTHS = [(2000, 1), (2000, 2), (2001, 1), (2001, 2)]
CELLS = [(1, 1), (1, 4), (2, 2), (2, 6)]
TOL = 1e-3


@pytest.fixture(scope="module")
def benthic_simulation(tmp_path_factory):
    """data/example's bottom temperature, written as ERSEM's deposit and suspension
    feeders: 2D, with their long_names and units."""
    sim_dir = tmp_path_factory.mktemp("benthic")
    frames = []
    for year, month in SIM_MONTHS:
        src = glob.glob(f"data/example/{year}/{month:02d}/*grid_T.nc")[0]
        with xr.open_dataset(src) as ds:
            bottom = ds.votemper.isel(time_counter=slice(0, 2), deptht=0).drop_vars("deptht").load()
        out = xr.Dataset({
            "Y2_c": (bottom * 100).assign_attrs(long_name="benthic deposit feeder carbon", units="mg C/m^2"),
            "Y3_c": (bottom * 50).assign_attrs(long_name="benthic suspension feeder carbon", units="mg C/m^2"),
        })
        folder = sim_dir / str(year) / f"{month:02d}"
        folder.mkdir(parents=True)
        out.to_netcdf(folder / f"amm7_1d_{year}{month:02d}01_ptrc_T.nc")
        frames.append(out)
    model = xr.concat(frames, dim="time_counter")
    total = (model.Y2_c + model.Y3_c).astype("float64")
    lon = xr.DataArray([model.lon.values[x] for x, _ in CELLS], dims="point")
    lat = xr.DataArray([model.lat.values[y] for _, y in CELLS], dims="point")
    at = total.sel(lon=lon, lat=lat).to_dataframe(name="value").reset_index()
    at = at.assign(month=at.time_counter.dt.month)
    # the model's own values, as the survey's: one each month at each cell, dated 1986
    expected = at.groupby(["lon", "lat", "month"]).value.mean().reset_index().rename(columns={"value": "observation"})
    return str(sim_dir), expected


def test_a_matchup_with_a_models_benthic_variables(benthic_simulation, tmp_path, monkeypatch):
    """The survey's observations (here the model's own values, dated 1986) are matched by
    month with every year of the model, at the sea bed, so the model gives them back:
    matchup's own point_time_res (year, month and day by default) is not used for them."""
    sim_dir, expected = benthic_simulation
    observations = expected.assign(year=1986, day=25)[nsbs.POINT_COLUMNS]
    monkeypatch.setattr(nsbs.data_store, "nsbs", lambda variables, download: None)
    monkeypatch.setattr(nsbs, "_without_store", lambda variable: observations)

    oceanval.reset()
    try:
        oceanval.add_point_comparison(name="macrobenthos", model_variable="Y2_c+Y3_c",
                                      recipe={"macrobenthos": "nsbs"})
        oceanval.matchup(sim_dir=sim_dir, start=2000, end=2001, ask=False, cores=1, out_dir=str(tmp_path))
    finally:
        oceanval.reset()

    path = tmp_path / "oceanval_matchups" / "point" / "surface" / "macrobenthos" / "NSBS" / "NSBS_surface_macrobenthos.csv"
    df = pd.read_csv(path)
    assert sorted(df.columns) == ["lat", "lon", "model", "month", "observation"]
    merged = df.round({"lon": 6, "lat": 6}).merge(
        expected.round({"lon": 6, "lat": 6}), on=["lon", "lat", "month"], suffixes=("", "_expected")
    )
    assert len(merged) == len(expected) == len(df)
    assert np.abs(merged.model - merged.observation_expected).max() < TOL
    assert np.abs(merged.observation - merged.observation_expected).max() < TOL
