"""What each built-in variable and dataset can show (oceanval.option_profiles),
so that the oceanval window offers only the report options that apply."""

import importlib

import pytest

from oceanval import option_profiles, report_items, skill, totals

create_recipes = importlib.import_module("oceanval.create_recipes")


def matchup(kind, variable, source, layer=None, vertical=False):
    return {
        "kind": kind, "variable": variable, "source": source, "layer": layer, "vertical": vertical,
        "title": variable.title(), "key": f"{variable}/{source}" + (f"/{layer}" if layer else ""),
    }


def gridded(variable, source, vertical=False):
    return matchup("gridded", variable, source, vertical=vertical)


def point(variable, source, layer="surface"):
    return matchup("point", variable, source, layer=layer, vertical=layer == "all")


GRIDDED_ENTRIES = [(e["variable"], e["recipe"], e) for e in create_recipes.RECIPE_CATALOGUE]
POINT_ENTRIES = [(e["variable"], e["recipe"], e) for e in create_recipes.POINT_RECIPE_CATALOGUE]


class TestProfilesAgreeWithTheCatalogues:
    def test_every_recipe_has_a_profile_and_every_profile_a_recipe(self):
        expected = {("gridded", v, r) for v, r, _ in GRIDDED_ENTRIES} | {("point", v, r) for v, r, _ in POINT_ENTRIES}
        assert set(option_profiles.PROFILES) == expected

    @pytest.mark.parametrize("variable,recipe,entry", GRIDDED_ENTRIES + POINT_ENTRIES)
    def test_what_the_catalogue_can_say_is_the_profiles(self, variable, recipe, entry):
        kind = "point" if entry in [e for _, _, e in POINT_ENTRIES] else "gridded"
        found = option_profiles.PROFILES[(kind, variable, recipe)]
        # through the water column: by choice, or always (stratification, worked out from whole casts)
        always = variable == "stratification"
        assert (found["depth"] == "profile") == (create_recipes._has_vertical_option(entry) or always)
        # log10 statistics
        assert found["log"] == skill.is_log(variable)
        # the region
        assert (found["region"] == "global") == (entry["region"] == "Global")
        if kind == "gridded":
            units = create_recipes._GRIDDED_UNITS[(variable, recipe)]
            assert found["total"] == totals.kind_of(units, variable)
            # a monthly climatology is not a series, and one annual value has no months
            climatology = "climatology=True," in entry["arguments"]
            annual = entry["coverage"]["temporal"].startswith("Annual")
            assert found["months"] == ("annual" if annual else "climatology" if climatology else "series")
        else:
            assert found["months"] == "dated" and found["total"] is None


class TestWhatAMatchupCanShow:
    def test_only_a_flux_has_a_total_over_the_area(self):
        assert "global_series" in option_profiles.items_for(gridded("co2flux", "mpisom"))
        for other in (gridded("temperature", "woa23", True), gridded("pco2", "mpisom"), gridded("chlorophyll", "occci")):
            assert "global_series" not in option_profiles.items_for(other)

    def test_one_annual_value_has_no_seasonal_figures(self):
        items = option_profiles.items_for(gridded("alkalinity", "glodap", vertical=True))
        for gone in ("seasonal_cor_map", "monthly_maps", "peak_month", "hovmoller_clim", "hovmoller_years",
                     "regional_cycle", "transect_surface", "temporal_maps", "temporal_table"):
            assert gone not in items
        # and what an annual climatology does have
        for kept in ("clim_map", "bias_map", "density_scatter", "zonal_lines", "zonal_sections", "mean_profiles"):
            assert kept in items

    def test_a_monthly_climatology_has_them_but_not_latitude_by_time(self):
        items = option_profiles.items_for(gridded("temperature", "woa23", vertical=True))
        assert {"seasonal_cor_map", "monthly_maps", "peak_month", "hovmoller_clim"} <= set(items)
        assert "hovmoller_years" not in items

    def test_a_series_has_latitude_by_time(self):
        assert "hovmoller_years" in option_profiles.items_for(gridded("chlorophyll", "occci"))

    def test_a_surface_matchup_has_no_figures_through_the_water_column(self):
        items = option_profiles.items_for(gridded("temperature", "oisst"))
        for gone in ("zonal_sections", "depth_level_table", "mean_profiles", "depth_maps", "transect_section",
                     "depth_performance"):
            assert gone not in items

    def test_a_point_matchup_has_the_depth_figures_only_through_the_water_column(self):
        surface = option_profiles.items_for(point("nitrate", "ices"))
        every = option_profiles.items_for(point("nitrate", "ices", "all"))
        assert "scatter" in surface and "scatter" in every
        assert not {"depth_locations_map", "depth_table", "point_profiles", "depth_performance"} & set(surface)
        assert {"depth_locations_map", "depth_table", "point_profiles", "depth_performance"} <= set(every)
        # none of a gridded matchup's, and none of the summary's other than the depths
        assert not {item.id for item in report_items.ITEMS if item.page == "gridded"} & set(every)
        assert not {"taylor", "bias_table"} & set(every)

    def test_the_summary_is_what_the_matchups_feed(self):
        summary = {item.id for item in report_items.ITEMS if item.page == "summary"}
        assert summary & set(option_profiles.items_for(gridded("temperature", "cobe2"))) == {
            "taylor", "bias_table", "spatial_table", "temporal_maps", "temporal_table"}
        # a total is of a flux, or of a concentration through the water column
        assert "global_totals" in option_profiles.items_for(gridded("co2flux", "mpisom"))
        assert "global_totals" in option_profiles.items_for(gridded("nitrate", "woa23", vertical=True))
        assert "global_totals" not in option_profiles.items_for(gridded("nitrate", "woa23"))

    def test_each_item_is_only_on_its_own_page(self):
        for one in (gridded("temperature", "woa23", True), point("nitrate", "ices", "all")):
            for found in option_profiles.items_for(one):
                page = report_items.BY_ID[found].page
                assert page in (one["kind"], "summary")

    def test_the_names_are_not_case_sensitive(self):
        assert option_profiles.profile_for(gridded("Temperature", "WOA23")) is option_profiles.profile_for(
            gridded("temperature", "woa23"))

    def test_the_users_own_data_shows_everything_its_kind_and_layer_allow(self):
        own = gridded("mine", "myobs", vertical=False)
        assert option_profiles.profile_for(own) is None
        items = option_profiles.items_for(own)
        for kept in ("monthly_maps", "hovmoller_years", "global_series", "regional_cycle", "global_totals"):
            assert kept in items
        for gone in ("zonal_sections", "depth_maps"):
            assert gone not in items
        assert option_profiles.default_sections(own)


class TestTheReportOptions:
    def test_a_regional_dataset_has_no_default_sections(self):
        assert option_profiles.default_sections(gridded("nitrate", "woa23", True))
        assert not option_profiles.default_sections(gridded("nitrate", "nsbc", True))

    def test_what_each_option_is_for(self):
        applies = option_profiles.overall_options
        assert not any(applies([point("nitrate", "ices")]).values())
        assert applies([point("nitrate", "ices", "all")]) == {
            "subregions": False, "transect": False, "map_depths": False, "fixed_scale": False, "depth_bins": True}
        assert applies([gridded("temperature", "oisst")]) == {
            "subregions": True, "transect": True, "map_depths": False, "fixed_scale": True, "depth_bins": False}
        # a gridded matchup through the water column also takes the depth bins
        assert applies([gridded("temperature", "woa23", True)])["depth_bins"]
        assert applies([gridded("temperature", "woa23", True)])["map_depths"]

    def test_the_fixed_scale_is_of_the_monthly_maps(self):
        assert not option_profiles.overall_options([gridded("alkalinity", "glodap", True)])["fixed_scale"]
        assert option_profiles.overall_options(
            [gridded("alkalinity", "glodap", True), gridded("nitrate", "woa23", True)])["fixed_scale"]

    def test_every_option_has_boxes(self):
        assert set(option_profiles.OPTION_BOXES) == set(option_profiles.overall_options([]))


class TestAnnotate:
    def test_each_matchup_carries_what_the_window_needs(self):
        found = option_profiles.annotate([gridded("co2flux", "mpisom"), gridded("mine", "myobs")])
        assert found[0]["known"] and not found[1]["known"]
        assert "global_series" in found[0]["items"]
        assert found[0]["options"] == ["subregions", "transect", "fixed_scale"]
        assert found[0]["default_sections"] is True
        # the matchup is not changed
        assert "items" not in gridded("co2flux", "mpisom")

    def test_none_stays_none(self):
        assert option_profiles.annotate(None) is None


class TestTheTotalOfTheUsersOwnData:
    """The total over the area matched is of a flux per area: a gridded matchup of
    the user's own data is judged by its model's units, read from its file."""

    def _write(self, data_dir, variable, source, units):
        import numpy as np
        import xarray as xr

        folder = data_dir / "oceanval_matchups" / "gridded" / variable
        folder.mkdir(parents=True, exist_ok=True)
        values = np.ones((1, 2, 2))
        ds = xr.Dataset(
            {"model": (("time", "lat", "lon"), values), "observation": (("time", "lat", "lon"), values)},
            coords={"time": [0.0], "lat": [50.0, 51.0], "lon": [-4.0, -3.0]},
        )
        ds["model"].attrs["units"] = units
        ds.to_netcdf(folder / f"{source}_{variable}_surface.nc")

    def test_a_flux_has_the_total_and_a_concentration_has_not(self, tmp_path):
        self._write(tmp_path, "myflux", "mine", "mol m-2 yr-1")
        self._write(tmp_path, "mynitrate", "mine", "mmol/m**3")
        found = option_profiles.annotate([gridded("myflux", "mine"), gridded("mynitrate", "mine")], str(tmp_path))
        assert "global_series" in found[0]["items"]
        assert "global_series" not in found[1]["items"]
        # a concentration through the water column still has its inventory on the summary
        vertical = option_profiles.annotate([gridded("mynitrate", "mine", vertical=True)], str(tmp_path))[0]
        assert "global_totals" in vertical["items"] and "global_series" not in vertical["items"]

    def test_without_its_units_nothing_is_hidden(self, tmp_path):
        found = option_profiles.annotate([gridded("mine", "myobs")], str(tmp_path))[0]
        assert found["units"] is None
        assert "global_series" in found["items"]
        assert "global_series" in option_profiles.annotate([gridded("mine", "myobs")])[0]["items"]

    def test_a_built_in_matchup_is_judged_by_its_profile(self, tmp_path):
        self._write(tmp_path, "nitrate", "woa23", "mol m-2 yr-1")
        found = option_profiles.annotate([gridded("nitrate", "woa23")], str(tmp_path))[0]
        assert "units" not in found
        assert "global_series" not in found["items"]
