import ast
import os

import numpy as np
import pytest
import xarray as xr

import oceanval
from oceanval.create_recipes import (
    POINT_RECIPE_CATALOGUE,
    RECIPE_CATALOGUE,
    RECIPE_VARIABLES,
    build_recipe_script,
    default_selection,
    matchup_defaults,
    validate_defaults,
    extract_recipe_variable_mapping,
    generate_recipe_mapping,
    simulation_files,
    simulation_years,
)


def write_netcdf(path, variables, nz=5):
    """A model output file, with one long_name per variable.

    A long_name prefixed "SURF:" is written as a surface field, which is how
    a model reports fluxes and other two dimensional diagnostics.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dataset = xr.Dataset()
    for name, long_name in variables.items():
        surface = long_name.startswith("SURF:")
        attributes = {"long_name": long_name.replace("SURF:", ""), "units": "1"}
        if surface:
            values = np.random.rand(1, 4, 4).astype("f4")
            dims = ("time", "y", "x")
        else:
            values = np.random.rand(1, nz, 4, 4).astype("f4")
            dims = ("time", "deptht", "y", "x")
        dataset[name] = xr.DataArray(values, dims=dims, attrs=attributes)
    dataset["time"] = ("time", np.array([0.0]))
    dataset.time.attrs = {"units": "days since 2011-01-01", "calendar": "standard"}
    dataset.to_netcdf(path)


def write_fvcom(path, variables):
    """A raw FVCOM output file, with one long_name per variable.

    Every variable is on the sigma layers of an unstructured mesh of four
    nodes and two triangles.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dataset = xr.Dataset(
        {
            "nv": (("three", "nele"), np.array([[1, 1], [2, 4], [4, 3]], dtype="i4")),
            "lon": ("node", np.array([-5.0, -4.9, -5.0, -4.9], dtype="f4")),
            "lat": ("node", np.array([50.0, 50.0, 50.1, 50.1], dtype="f4")),
            "h": ("node", np.full(4, 20.0, dtype="f4")),
        }
    )
    for name, long_name in variables.items():
        dataset[name] = xr.DataArray(
            np.random.rand(1, 5, 4).astype("f4"),
            dims=("time", "siglay", "node"),
            attrs={"long_name": long_name, "units": "1"},
        )
    dataset["time"] = ("time", np.array([55927.0]))
    dataset.time.attrs = {"units": "days since 1858-11-17 00:00:00"}
    dataset.to_netcdf(path)


GRID_VARIABLES = {
    "thetao": "sea water potential temperature",
    "tair": "SURF:air temperature at 2m",
    "so": "sea water salinity",
}

TRACER_VARIABLES = {
    "P1_Chl": "diatoms chlorophyll",
    "P2_Chl": "nanophytoplankton chlorophyll",
    "Chl_tot": "total chlorophyll",
    "O2_o": "oxygen",
    "O2_sat": "oxygen saturation",
    "O2_flux": "SURF:air-sea oxygen flux",
    "Y2_o": "benthic oxygen",
    "N3_n": "nitrate nitrogen",
    "N4_n": "ammonium nitrogen",
    "N1_p": "phosphate phosphorus",
    "N5_s": "silicate silicate",
    "O3_pH": "pH",
    "ben_pH": "benthic pH",
    "O3_TA": "total alkalinity",
    "xEPS": "attenuation coefficient",
    "R1_n": "river nitrate nitrogen",
}

FVCOM_VARIABLES = {
    "temp": "temperature",
    "salinity": "salinity",
    "N3_n": "nitrate nitrogen",
}


def write_simulation(root):
    """A two year simulation, filed as root/<year>/<month>/<files>."""
    for year, month in (("2011", "01"), ("2012", "06")):
        stem = f"nemo_1m_{year}{month}01_{year}{month}28"
        write_netcdf(str(root / year / month / f"{stem}_grid_T.nc"), GRID_VARIABLES)
        write_netcdf(str(root / year / month / f"{stem}_ptrc_T.nc"), TRACER_VARIABLES)
    return str(root)


@pytest.fixture
def simulation(tmp_path):
    """A two year simulation, filed as sim/<year>/<month>/<files>."""
    return write_simulation(tmp_path / "sim")


@pytest.fixture(scope="module")
def scripts(tmp_path_factory):
    """The script create_recipes writes from the two year simulation for
    2011-2012, for each domain, which the tests that only read it share."""
    folder = tmp_path_factory.mktemp("scripts")
    simdir = write_simulation(folder / "sim")
    written = {}
    # conftest.py's oceanvalrc fixture is not set up yet for a fixture of the
    # module, so keep the user's own recipes out of the scripts here
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("OCEANVALRC", str(folder / ".oceanvalrc"))
        for domain in ("global", "nwes"):
            out = str(folder / f"{domain}.py")
            oceanval.create_recipes(
                simdir=simdir, ndown=2, out=out, domain=domain, start=2011, end=2012,
                ask=False, gui=False,
            )
            written[domain] = open(out).read()
    return written


@pytest.fixture
def fvcom_simulation(tmp_path):
    """A month of FVCOM output, filed as fvcom/<year>/<month>/<file>."""
    root = tmp_path / "fvcom"
    write_fvcom(str(root / "2012" / "01" / "run_avg_0001.nc"), FVCOM_VARIABLES)
    return str(root)


class TestArguments:
    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({}, "Please provide simdir"),
            ({"simdir": "."}, "Please provide ndown"),
            ({"simdir": ".", "ndown": 1}, "Please provide out"),
            (
                {"simdir": ".", "ndown": 1, "out": "x"},
                "Please provide domain",
            ),
            (
                {"simdir": ".", "ndown": 1, "out": "x", "domain": "global"},
                "Please provide start",
            ),
            (
                {
                    "simdir": ".",
                    "ndown": 1,
                    "out": "x",
                    "domain": "global",
                    "start": 2000,
                },
                "Please provide end",
            ),
        ],
    )
    def test_every_argument_is_required(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            oceanval.create_recipes(gui=False, **kwargs)

    def test_ndown_must_be_an_integer(self, tmp_path):
        with pytest.raises(TypeError, match="ndown must be an integer"):
            oceanval.create_recipes(
                simdir=str(tmp_path),
                ndown="2",
                out=str(tmp_path / "out.py"),
                domain="global",
                start=2011,
                end=2012,
                ask=False,
                gui=False,
            )

    def test_ndown_must_not_be_negative(self, tmp_path):
        with pytest.raises(ValueError, match="ndown must be a positive integer"):
            oceanval.create_recipes(
                simdir=str(tmp_path),
                ndown=-1,
                out=str(tmp_path / "out.py"),
                domain="global",
                start=2011,
                end=2012,
                ask=False,
                gui=False,
            )

    def test_simdir_must_exist(self, tmp_path):
        missing = str(tmp_path / "nope")
        with pytest.raises(ValueError, match="is not a directory"):
            oceanval.create_recipes(
                simdir=missing,
                ndown=1,
                out=str(tmp_path / "out.py"),
                domain="global",
                start=2011,
                end=2012,
                ask=False,
                gui=False,
            )

    def test_a_simulation_with_no_files_says_which_argument_to_check(self, tmp_path):
        with pytest.raises(ValueError, match="Check the ndown argument"):
            oceanval.create_recipes(
                simdir=str(tmp_path),
                ndown=2,
                out=str(tmp_path / "out.py"),
                domain="global",
                start=2011,
                end=2012,
                ask=False,
                gui=False,
            )

    def test_domain_must_be_a_string(self, tmp_path):
        with pytest.raises(TypeError, match="domain must be a string"):
            oceanval.create_recipes(
                simdir=str(tmp_path),
                ndown=1,
                out=str(tmp_path / "out.py"),
                domain=1,
                start=2011,
                end=2012,
                ask=False,
                gui=False,
            )

    @pytest.mark.parametrize("domain", ["europe", "local", "", "GLOBALLY"])
    def test_domain_must_be_global_or_nwes(self, tmp_path, domain):
        with pytest.raises(
            ValueError, match='domain must be either "global" or "nwes"'
        ):
            oceanval.create_recipes(
                simdir=str(tmp_path),
                ndown=1,
                out=str(tmp_path / "out.py"),
                domain=domain,
                start=2011,
                end=2012,
                ask=False,
                gui=False,
            )

    def test_domain_is_case_insensitive(self, tmp_path):
        write_netcdf(
            str(tmp_path / "sim" / "output.nc"),
            {"thetao": "sea water potential temperature"},
        )
        out = str(tmp_path / "out.py")

        oceanval.create_recipes(
            simdir=str(tmp_path / "sim"), ndown=0, out=out, domain="NWES",
            start=2011, end=2012,
            ask=False, gui=False,
        )

        assert 'recipe={"temperature": "nsbc"}' in open(out).read()

    def test_start_must_be_an_integer(self, tmp_path):
        with pytest.raises(TypeError, match="start must be an integer"):
            oceanval.create_recipes(
                simdir=str(tmp_path),
                ndown=1,
                out=str(tmp_path / "out.py"),
                domain="global",
                start="2011",
                end=2012,
                ask=False,
                gui=False,
            )

    def test_end_must_be_an_integer(self, tmp_path):
        with pytest.raises(TypeError, match="end must be an integer"):
            oceanval.create_recipes(
                simdir=str(tmp_path),
                ndown=1,
                out=str(tmp_path / "out.py"),
                domain="global",
                start=2011,
                end="2012",
                ask=False,
                gui=False,
            )

    def test_end_must_not_be_before_start(self, tmp_path):
        with pytest.raises(ValueError, match="end must not be before start"):
            oceanval.create_recipes(
                simdir=str(tmp_path),
                ndown=1,
                out=str(tmp_path / "out.py"),
                domain="global",
                start=2012,
                end=2011,
                ask=False,
                gui=False,
            )

    def test_a_single_year_simulation_is_allowed(self, tmp_path):
        write_netcdf(
            str(tmp_path / "sim" / "output.nc"),
            {"thetao": "sea water potential temperature"},
        )
        out = str(tmp_path / "out.py")

        oceanval.create_recipes(
            simdir=str(tmp_path / "sim"), ndown=0, out=out, domain="global",
            start=2011, end=2011,
            ask=False, gui=False,
        )

        assert "start=2011," in open(out).read()


class TestDeprecation:
    def test_create_recipes_warns_that_it_is_deprecated(self):
        with pytest.warns(FutureWarning, match="create_recipes is deprecated"):
            with pytest.raises(ValueError, match="simdir"):
                oceanval.create_recipes()

    def test_the_warning_points_at_the_caller(self, recwarn):
        with pytest.raises(ValueError):
            oceanval.create_recipes()
        assert recwarn.pop(FutureWarning).filename == __file__

    def test_the_signature_and_docstring_are_kept(self):
        import inspect

        assert "simdir" in inspect.signature(oceanval.create_recipes).parameters
        assert "Deprecated" in oceanval.create_recipes.__doc__

    def test_the_oceanval_app_does_not_call_the_deprecated_function(self):
        from oceanval import app

        assert app._create_recipes is not oceanval.create_recipes


class TestVariableIdentification:
    def test_model_variables_are_found_from_their_long_names(self, simulation):
        mapping = extract_recipe_variable_mapping(simulation, 2)

        assert mapping["temperature"] == "thetao"
        assert mapping["salinity"] == "so"
        assert mapping["nitrate"] == "N3_n"
        assert mapping["ammonium"] == "N4_n"
        assert mapping["phosphate"] == "N1_p"
        assert mapping["silicate"] == "N5_s"
        assert mapping["alkalinity"] == "O3_TA"
        assert mapping["kd490"] == "xEPS"

    def test_decoy_variables_are_rejected(self, simulation):
        mapping = extract_recipe_variable_mapping(simulation, 2)

        # air temperature, a benthic pH, and oxygen saturation and flux are
        # all named after the quantity the recipe wants but are not it
        assert mapping["temperature"] == "thetao"
        assert mapping["ph"] == "O3_pH"
        assert mapping["oxygen"] == "O2_o"
        # a river nitrate must not outvote the water column one
        assert mapping["nitrate"] == "N3_n"

    def test_chlorophyll_sums_the_phytoplankton_types(self, simulation):
        mapping = extract_recipe_variable_mapping(simulation, 2)

        # the model reports a total as well as its types, and counting both
        # would double the chlorophyll
        assert mapping["chlorophyll"] == "P1_Chl+P2_Chl"

    def test_an_ambiguous_variable_is_left_unmatched(self, tmp_path):
        path = str(tmp_path / "sim" / "ambiguous.nc")
        write_netcdf(
            path,
            {
                "sal_a": "sea water salinity",
                "sal_b": "sea water salinity anomaly reference",
            },
        )
        mapping = generate_recipe_mapping(path)

        # there is no way to tell which one the recipe wants
        assert mapping["salinity"] is None

    def test_nemo_names_are_matched_without_a_long_name(self, tmp_path):
        path = str(tmp_path / "sim" / "nemo.nc")
        write_netcdf(path, {"votemper": "unknown", "vosaline": "unknown"})

        mapping = generate_recipe_mapping(path)

        assert mapping["temperature"] == "votemper"
        assert mapping["salinity"] == "vosaline"

    def test_a_bare_ph_variable_is_matched_by_name(self, tmp_path):
        path = str(tmp_path / "sim" / "bare.nc")
        # pH is often written with no long_name worth matching on, and the
        # one it has need not be unique
        write_netcdf(path, {"ph": "unknown", "junk": "unknown"})

        assert generate_recipe_mapping(path)["ph"] == "ph"

    def test_a_surface_only_variable_is_found_beside_3d_ones(self, tmp_path):
        path = str(tmp_path / "sim" / "mixed.nc")
        write_netcdf(
            path,
            {
                "thetao": "sea water potential temperature",
                # a model can report the light attenuation as a 2D diagnostic
                # in the same file as its 3D tracers
                "xEPS": "SURF:attenuation coefficient",
            },
        )
        mapping = generate_recipe_mapping(path)

        assert mapping["temperature"] == "thetao"
        assert mapping["kd490"] == "xEPS"

    def test_a_3d_field_wins_over_a_surface_diagnostic(self, tmp_path):
        path = str(tmp_path / "sim" / "both.nc")
        write_netcdf(
            path,
            {
                "thetao": "sea water potential temperature",
                "tos": "SURF:sea surface temperature",
            },
        )
        mapping = generate_recipe_mapping(path)

        # searching the two together would make the pair look ambiguous and
        # lose temperature altogether
        assert mapping["temperature"] == "thetao"

    def test_a_3d_field_wins_a_tie_with_a_surface_field_in_another_stream(self, tmp_path):
        # as GETM writes its sea surface temperature in one stream and its
        # temperature through the water column in another
        write_netcdf(str(tmp_path / "sim" / "2000" / "north_sea_3d_200001.nc"),
                     {"sst": "SURF:sea surface temperature"})
        write_netcdf(str(tmp_path / "sim" / "2000" / "north_sea_3dm_200001.nc"),
                     {"temp": "conservative temperature"})

        mapping = extract_recipe_variable_mapping(str(tmp_path / "sim"), 1)

        # sst comes first alphabetically, but cannot be matched at depth
        assert mapping["temperature"] == "temp"

    def test_sea_ice_salinity_and_temperature_are_not_the_oceans(self, tmp_path):
        # as NEMO-SI3 writes them, in an icemod stream beside grid_T, with a
        # level for each category of ice, so they look like the water column
        write_netcdf(str(tmp_path / "sim" / "1970" / "eORCA1_1m_197001_grid_T.nc"),
                     {"so_abs": "absolute salinity", "thetao_con": "conservative temperature"})
        write_netcdf(str(tmp_path / "sim" / "1970" / "eORCA1_1m_197001_icemod.nc"),
                     {"sisalcat": "Sea-Ice Bulk salinity per category",
                      "sitemcat": "Ice temperature per category",
                      "sisali": "SURF:Sea ice salinity"})

        mapping = extract_recipe_variable_mapping(str(tmp_path / "sim"), 1)

        # each ties with the ocean's, and comes first alphabetically
        assert mapping["salinity"] == "so_abs"
        assert mapping["temperature"] == "thetao_con"

    def test_only_one_file_per_output_stream_is_read(self, simulation):
        files = simulation_files(simulation, 2)

        # two streams, written twice each - reading one of each is enough
        assert len(files) == 2
        assert sorted(os.path.basename(f).split("_")[-2] for f in files) == [
            "grid",
            "ptrc",
        ]

    def test_restart_files_are_left_to_the_user(self, simulation, tmp_path):
        write_netcdf(
            str(tmp_path / "sim" / "2011" / "01" / "nemo_restart.nc"),
            {"junk": "sea water salinity"},
        )

        # nothing is left out unasked: a restart file is a stream like any other
        names = [os.path.basename(f) for f in simulation_files(simulation, 2)]
        assert "nemo_restart.nc" in names
        # until the file filters leave it out
        names = [os.path.basename(f) for f in simulation_files(simulation, 2, exclude=["restart"])]
        assert not [name for name in names if "restart" in name]
        # and no default of OceanVal's leaves it out
        assert not hasattr(oceanval.create_recipes, "DEFAULT_EXCLUDE")


class TestGeneratedScript:
    def test_the_script_is_valid_python(self, scripts):
        for script in scripts.values():
            ast.parse(script)

    def test_matched_recipes_carry_the_model_variable(self, scripts):
        script = scripts["global"]

        assert 'model_variable="thetao"' in script
        assert 'recipe={"temperature": "cobe2"},' in script
        # and the block is live, not commented out
        assert "\noceanval.add_gridded_comparison(\n    name=\"temperature\"," in script

    def test_unmatched_recipes_are_commented_out(self, tmp_path):
        # a simulation holding nothing a recipe covers
        write_netcdf(str(tmp_path / "sim" / "empty.nc"), {"uo": "eastward velocity"})
        out = str(tmp_path / "matchup.py")
        with pytest.warns(UserWarning, match="No model variables could be identified"):
            oceanval.create_recipes(
                simdir=str(tmp_path / "sim"),
                ndown=0,
                out=out,
                domain="global",
                start=2011,
                end=2012,
                ask=False,
                gui=False,
            )
        script = open(out).read()

        # every recipe is still there, so nothing is silently dropped
        assert script.count("add_gridded_comparison(") == len(RECIPE_CATALOGUE)
        assert "\noceanval.add_gridded_comparison(" not in script
        assert "# No alkalinity variable was found in the model output." in script
        # the placeholder from the published example is left to be filled in
        assert '#     model_variable="talk",' in script

    def test_one_recipe_stays_live_per_observational_variable(self, scripts):
        script = scripts["global"]

        # by default only one recipe per variable is left live - the others
        # are written out commented, to be added as further sources
        live = [
            line
            for line in script.splitlines()
            if line.startswith("    name=") and not line.startswith("#")
        ]
        assert len(live) == len(set(live))
        assert "# The Global recipe for temperature is registered instead." in script

    def test_domain_prefers_the_matching_regions_recipe(self, scripts):
        script = scripts["nwes"]

        # temperature has a recipe in both regions; nwes must win, and the
        # global ones (cobe2 and woa23) become the commented alternatives
        assert 'recipe={"temperature": "nsbc"},' in script
        assert '\noceanval.add_gridded_comparison(\n    name="temperature",' in script
        assert (
            "The Northwest European Shelf recipe for temperature is registered "
            "instead." in script
        )
        # only one live *gridded* block for the variable, same as with
        # domain="global" - the ICES point block for temperature is also
        # live at the same time, which is fine, since add_point_comparison
        # and add_gridded_comparison register separately, so it's excluded
        # from this check by only looking at the script before that section
        gridded_script = script.split("ICES point observations")[0]
        live = [
            line
            for line in gridded_script.splitlines()
            if line.startswith("    name=") and not line.startswith("#")
        ]
        assert len(live) == len(set(live))

    def test_domain_falls_back_when_the_region_has_no_recipe(self, scripts):
        # nitrate is identified (N3_n) but NWES has no nitrate recipe of its
        # own to prefer, so the global woa23 one stays live either way
        script = scripts["nwes"]

        assert 'name="nitrate",' in script
        assert 'recipe={"nitrate": "woa23"},' in script
        assert '\noceanval.add_gridded_comparison(\n    name="nitrate",' in script

    def test_the_matchup_call_uses_the_simulation_and_the_given_years(
        self, simulation, tmp_path
    ):
        out = str(tmp_path / "matchup.py")
        oceanval.create_recipes(
            simdir=simulation, ndown=2, out=out, domain="global", start=2009, end=2013,
            ask=False, gui=False,
        )
        script = open(out).read()

        # start/end are passed straight through to matchup(), not inferred
        # from the simulation's file paths (which are 2011-2012 here)
        assert f'sim_dir="{os.path.abspath(simulation)}",' in script
        assert "n_dirs_down=2," in script
        assert "start=2009," in script
        assert "end=2013," in script
        assert "oceanval.validate()" in script

    def test_woa23_recipes_get_the_decadal_period_covering_the_given_years(self, scripts):
        script = scripts["global"]

        # WOA23 temperature and salinity are published per decade, and
        # find_recipe rejects a start/end that straddles two of them
        assert "start=2005, end=2014," in script

    def test_a_simulation_outside_the_woa23_periods_is_flagged(self):
        script = build_recipe_script("/sim", 2, {"salinity": "so"}, years=(1901, 1903))

        assert "# No decadal WOA23 period covers 1901-1903" in script

    def test_woa23_recipes_say_what_to_do_when_the_years_are_unknown(self):
        script = build_recipe_script("/sim", 0, {"salinity": "so"}, years=None)

        # the years are not known, so the note cannot claim no period covers them
        assert "# WOA23 publishes this per decade" in script
        assert "No decadal WOA23 period covers" not in script

    def test_unreadable_years_leave_the_matchup_years_to_be_filled_in(self):
        script = build_recipe_script("/sim", 0, {"salinity": "so"}, years=None)

        assert "set start and end on the matchup call" in script
        assert "start=1995,  # set to your simulation's years" in script

    def test_years_come_from_below_the_simulation_directory(self, tmp_path):
        # a 1999 in the path the user keeps their simulations in is not a
        # year of the simulation
        root = tmp_path / "archive1999" / "sim"
        write_netcdf(str(root / "2011" / "output.nc"), GRID_VARIABLES)

        paths = [str(root / "2011" / "output.nc")]
        assert simulation_years(str(root), paths) == (2011, 2011)

    def test_years_are_read_from_dated_file_names(self, tmp_path):
        paths = ["/sim/nemo_1m_20030101_20031231_grid_T.nc"]

        assert simulation_years("/sim", paths) == (2003, 2003)


class TestPointRecipes:
    def test_ices_recipes_are_included_when_domain_is_nwes(self, scripts):
        script = scripts["nwes"]

        assert script.count("add_point_comparison(") == len(POINT_RECIPE_CATALOGUE)
        assert 'recipe={"temperature": "ices"}' in script
        # the simulation fixture holds a model variable for every ICES
        # variable, so every point block should be live, not commented
        assert '\noceanval.add_point_comparison(\n    name="temperature",' in script
        assert "No temperature variable was found" not in script

    def test_ices_recipes_are_excluded_when_domain_is_global(self, scripts):
        script = scripts["global"]

        assert "add_point_comparison(" not in script
        assert '"ices"' not in script

    def test_unmatched_ices_recipes_are_commented_out(self, tmp_path):
        write_netcdf(str(tmp_path / "sim" / "empty.nc"), {"uo": "eastward velocity"})
        out = str(tmp_path / "matchup.py")
        with pytest.warns(UserWarning, match="No model variables could be identified"):
            oceanval.create_recipes(
                simdir=str(tmp_path / "sim"),
                ndown=0,
                out=out,
                domain="nwes",
                start=2011,
                end=2012,
                ask=False,
                gui=False,
            )
        script = open(out).read()

        # every point recipe is still there, so nothing is silently dropped
        assert script.count("add_point_comparison(") == len(POINT_RECIPE_CATALOGUE)
        assert '\noceanval.add_point_comparison(' not in script
        assert "# No temperature variable was found in the model output." in script


def test_the_catalogue_matches_the_published_example():
    """The generated script offers the same recipes as the website's."""
    example = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs-site",
        "recipe-examples.py",
    )
    if not os.path.exists(example):
        pytest.skip("the docs-site example is not present")

    published = []
    for node in ast.walk(ast.parse(open(example).read())):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords}
        if "recipe" not in keywords:
            continue
        recipe = keywords["recipe"]
        published.append((recipe.keys[0].value, recipe.values[0].value))

    catalogue = [
        (entry["variable"], entry["recipe"])
        for entry in RECIPE_CATALOGUE + POINT_RECIPE_CATALOGUE
    ]
    assert catalogue == published


def test_every_catalogue_variable_can_be_identified():
    """Nothing in the catalogue is a recipe the generator can never match."""
    assert set(RECIPE_VARIABLES) == {entry["variable"] for entry in RECIPE_CATALOGUE}


def test_every_point_catalogue_variable_can_be_identified():
    """Nothing in the ICES catalogue is a recipe the generator can never match."""
    point_variables = {entry["variable"] for entry in POINT_RECIPE_CATALOGUE}
    assert point_variables <= set(RECIPE_VARIABLES)


class TestAskingForMissingVariables:
    def _run(self, tmp_path, monkeypatch, answers, tty=True):
        write_netcdf(
            str(tmp_path / "sim" / "a.nc"),
            {"thetao": "sea water potential temperature", "mystery": "unknown thing"},
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: tty, raising=False)
        asked = []

        def fake_input(prompt=""):
            asked.append(prompt)
            return next(answers, "")

        monkeypatch.setattr("builtins.input", fake_input)
        out = str(tmp_path / "matchup.py")
        oceanval.create_recipes(
            simdir=str(tmp_path / "sim"), ndown=0, out=out,
            domain="global", start=2011, end=2012, gui=False,
        )
        return open(out).read(), asked

    def test_valid_name_is_used(self, tmp_path, monkeypatch, capsys):
        answers = iter(["mystery"])
        script, asked = self._run(tmp_path, monkeypatch, answers)
        assert "mystery" in script
        assert "could not be identified in the model output" in capsys.readouterr().out
        assert asked

    def test_unknown_name_is_asked_again(self, tmp_path, monkeypatch, capsys):
        answers = iter(["nonsense", ""])
        _, asked = self._run(tmp_path, monkeypatch, answers)
        assert "nonsense not found" in capsys.readouterr().out
        assert len(asked) > 1

    def test_not_asked_without_a_terminal(self, tmp_path, monkeypatch):
        _, asked = self._run(tmp_path, monkeypatch, iter([]), tty=False)
        assert asked == []


def _from_a_terminal(monkeypatch, answers):
    """Run as if from a terminal, answering each question from answers.

    Once the answers run out, input fails as it does when stdin is closed.
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    asked = []

    def fake_input(prompt=""):
        asked.append(prompt)
        try:
            return next(answers)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


class TestFvcom:
    QUESTION = (
        "The output in {simdir} looks like raw FVCOM output "
        "(an unstructured mesh with node, nele and nv).\n"
        "Is this FVCOM output? (y/n) "
    )

    def _create(self, simdir, tmp_path, **kwargs):
        out = str(tmp_path / "matchup.py")
        oceanval.create_recipes(
            simdir=simdir, ndown=2, out=out,
            domain="global", start=2012, end=2012, gui=False, **kwargs,
        )
        return open(out).read()

    def test_variables_on_the_mesh_are_identified(self, fvcom_simulation):
        path = simulation_files(fvcom_simulation, 2)[0]
        mapping = generate_recipe_mapping(path, fvcom=True)
        assert mapping["temperature"] == "temp"
        assert mapping["salinity"] == "salinity"
        assert mapping["nitrate"] == "N3_n"

    def test_fvcom_is_assumed_when_it_cannot_ask(
        self, fvcom_simulation, tmp_path, capsys
    ):
        script = self._create(fvcom_simulation, tmp_path, ask=False)
        assert "looks like raw FVCOM output" in capsys.readouterr().out
        assert "    fvcom=True,\n" in script
        assert 'model_variable="temp"' in script
        ast.parse(script)

    def test_other_output_is_not_taken_for_fvcom(self, simulation, tmp_path, capsys):
        script = self._create(simulation, tmp_path, ask=False)
        assert "fvcom=True" not in script
        assert "FVCOM" not in capsys.readouterr().out

    def test_confirmed_fvcom(self, fvcom_simulation, tmp_path, monkeypatch):
        asked = _from_a_terminal(monkeypatch, iter(["y"]))
        script = self._create(fvcom_simulation, tmp_path)
        assert asked[0] == self.QUESTION.format(simdir=fvcom_simulation)
        assert "    fvcom=True,\n" in script

    def test_declined_fvcom(self, fvcom_simulation, tmp_path, monkeypatch):
        asked = _from_a_terminal(monkeypatch, iter(["n"]))
        script = self._create(fvcom_simulation, tmp_path)
        assert asked[0] == self.QUESTION.format(simdir=fvcom_simulation)
        assert "fvcom=True" not in script

    def test_anything_but_yes_or_no_is_asked_again(
        self, fvcom_simulation, tmp_path, monkeypatch, capsys
    ):
        asked = _from_a_terminal(monkeypatch, iter(["maybe", "y"]))
        script = self._create(fvcom_simulation, tmp_path)
        assert asked[:2] == [self.QUESTION.format(simdir=fvcom_simulation)] * 2
        assert "Provide y or n" in capsys.readouterr().out
        assert "    fvcom=True,\n" in script

    def test_the_fvcom_argument_skips_the_question(
        self, fvcom_simulation, tmp_path, monkeypatch
    ):
        asked = _from_a_terminal(monkeypatch, iter([]))
        script = self._create(fvcom_simulation, tmp_path, fvcom=False)
        assert not any('FVCOM output?' in question for question in asked)
        assert "fvcom=True" not in script

    def test_fvcom_true_is_not_second_guessed(self, simulation, tmp_path):
        script = self._create(simulation, tmp_path, ask=False, fvcom=True)
        assert "    fvcom=True,\n" in script

    def test_fvcom_must_be_a_boolean(self, fvcom_simulation, tmp_path):
        with pytest.raises(TypeError, match="fvcom"):
            self._create(fvcom_simulation, tmp_path, ask=False, fvcom="yes")


class TestSelection:
    MAPPING = {"temperature": "thetao", "salinity": "so", "chlorophyll": "chl"}

    @pytest.mark.parametrize("domain", ["global", "nwes"])
    def test_the_default_selection_is_what_is_left_live(self, domain):
        chosen = build_recipe_script(
            "/sim", 2, self.MAPPING, (2011, 2012), domain,
            selection=default_selection(self.MAPPING, domain),
        )
        assert chosen == build_recipe_script(
            "/sim", 2, self.MAPPING, (2011, 2012), domain
        )

    def test_the_default_is_one_gridded_recipe_per_variable(self):
        assert default_selection(self.MAPPING, "global") == {
            ("temperature", "cobe2"),
            ("salinity", "woa23"),
            ("chlorophyll", "occci"),
        }
        # nwes also gets the ICES point recipes
        assert default_selection(self.MAPPING, "nwes") == {
            ("temperature", "nsbc"),
            ("salinity", "nsbc"),
            ("chlorophyll", "nsbc"),
            ("temperature", "ices"),
            ("salinity", "ices"),
            ("chlorophyll", "ices"),
        }

    def test_a_variable_can_be_validated_against_several_sources(self):
        script = build_recipe_script(
            "/sim", 2, {"temperature": "thetao"}, (2011, 2012),
            selection={("temperature", "cobe2"), ("temperature", "woa23")},
        )
        ast.parse(script)
        for recipe in ("cobe2", "woa23"):
            assert (
                '\noceanval.add_gridded_comparison(\n    name="temperature",\n'
                '    model_variable="thetao",\n'
                f'    recipe={{"temperature": "{recipe}"}},'
            ) in script
        # the one left out names what is registered instead
        assert "# The Global recipes for temperature are registered instead." in script
        assert '#     recipe={"temperature": "nsbc"},' in script

    def test_an_alternative_can_be_added_as_well(self):
        script = build_recipe_script("/sim", 2, self.MAPPING, (2011, 2012), "nwes")

        # sources are registered side by side, so nothing needs commenting out
        assert "# To validate against this source as well, uncomment this block." in script
        assert "would replace it" not in script
        assert "replaces the first" not in script

    def test_a_variable_with_nothing_selected_is_commented_out(self):
        script = build_recipe_script(
            "/sim", 2, {"temperature": "thetao"}, (2011, 2012), selection=set()
        )

        assert "\noceanval.add_gridded_comparison(" not in script
        # cobe2, woa23 and nsbc
        assert script.count("# Not selected when this script was generated.") == 3

    def test_an_ices_recipe_can_be_selected_for_the_global_domain(self):
        mapping = {"temperature": "thetao", "salinity": "so"}
        selection = default_selection(mapping, "global") | {("temperature", "ices")}
        script = build_recipe_script(
            "/sim", 2, mapping, (2011, 2012), "global", selection=selection
        )
        ast.parse(script)

        assert script.count("add_point_comparison(") == len(POINT_RECIPE_CATALOGUE)
        assert '\noceanval.add_point_comparison(\n    name="temperature",' in script
        # salinity has a model variable, but its ICES recipe was not selected
        assert '\noceanval.add_point_comparison(\n    name="salinity",' not in script


class TestGui:
    def _create(self, simulation, tmp_path, **kwargs):
        out = str(tmp_path / "matchup.py")
        returned = oceanval.create_recipes(
            simdir=simulation, ndown=2, out=out, domain="nwes",
            start=2011, end=2012, gui=True, **kwargs,
        )
        return out, returned

    def test_the_window_is_shown_by_default(self, simulation, tmp_path, monkeypatch):
        shown = []
        monkeypatch.setattr(
            "oceanval.recipes_gui.choose_recipes", lambda *args: shown.append(args)
        )
        oceanval.create_recipes(
            simdir=simulation, ndown=2, out=str(tmp_path / "matchup.py"),
            domain="global", start=2011, end=2012, ask=False,
        )

        assert len(shown) == 1

    def test_gui_must_be_a_boolean(self, simulation, tmp_path):
        with pytest.raises(TypeError, match="gui must be True or False"):
            oceanval.create_recipes(
                simdir=simulation, ndown=2, out=str(tmp_path / "out.py"),
                domain="global", start=2011, end=2012, ask=False, gui="yes",
            )

    def test_the_window_is_shown_what_was_identified(
        self, simulation, tmp_path, monkeypatch
    ):
        shown = {}

        def window(mapping, domain, available, context, write):
            shown.update(mapping=mapping, domain=domain, available=available)
            shown.update(context=context)
            return None

        monkeypatch.setattr("oceanval.recipes_gui.choose_recipes", window)
        self._create(simulation, tmp_path, ask=False)

        assert shown["mapping"]["temperature"] == "thetao"
        assert shown["domain"] == "nwes"
        # any variable in the output can be picked, not only the matched ones
        assert {"thetao", "tair", "R1_n"} <= set(shown["available"])
        assert shown["context"]["region"] == "Northwest European Shelf"
        assert (shown["context"]["start"], shown["context"]["end"]) == (2011, 2012)
        assert shown["context"]["out"] == str(tmp_path / "matchup.py")
        assert shown["context"]["own_data"] == {}

    def test_the_window_is_given_your_own_data(self, simulation, tmp_path, monkeypatch):
        shown = {}

        def window(mapping, domain, available, context, write):
            shown.update(context=context)
            return None

        monkeypatch.setattr("oceanval.recipes_gui.choose_recipes", window)
        own = {"point": [{"name": "temperature", "source": "pygetm", "model_variable": "thetao",
                          "obs_path": str(tmp_path)}], "gridded": []}
        self._create(simulation, tmp_path, ask=False, own_data=own)

        # to show it in its variable's row (see recipes_gui.recipe_rows)
        assert shown["context"]["own_data"] == own

    def test_what_is_chosen_in_the_window_is_written(
        self, simulation, tmp_path, monkeypatch, capsys
    ):
        def window(mapping, domain, available, context, write):
            mapping = dict(mapping, temperature="tair")
            selection = {
                ("temperature", "cobe2"),
                ("temperature", "woa23"),
                ("salinity", "ices"),
            }
            write(mapping, selection, None, None, None)
            return mapping, selection, None, None, None

        monkeypatch.setattr("oceanval.recipes_gui.choose_recipes", window)
        out, returned = self._create(simulation, tmp_path, ask=False)
        script = open(out).read()
        ast.parse(script)

        assert returned == out
        for recipe in ("cobe2", "woa23"):
            assert (
                '\noceanval.add_gridded_comparison(\n    name="temperature",\n'
                '    model_variable="tair",\n'
                f'    recipe={{"temperature": "{recipe}"}},'
            ) in script
        assert '\noceanval.add_point_comparison(\n    name="salinity",' in script
        # nwes would have left salinity's NSBC recipe live, but it was not chosen
        assert '\noceanval.add_gridded_comparison(\n    name="salinity",' not in script
        assert "commented out (no dataset selected): alkalinity" in capsys.readouterr().out

    def test_the_window_replaces_the_terminal_questions(self, tmp_path, monkeypatch):
        write_netcdf(
            str(tmp_path / "sim" / "a.nc"),
            {"thetao": "sea water potential temperature", "mystery": "unknown thing"},
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr(
            "builtins.input", lambda prompt="": pytest.fail(f"asked {prompt!r}")
        )

        def window(mapping, domain, available, context, write):
            mapping = dict(mapping, salinity="mystery")
            selection = default_selection(mapping, domain)
            write(mapping, selection, None, None, None)
            return mapping, selection, None, None, None

        monkeypatch.setattr("oceanval.recipes_gui.choose_recipes", window)
        out = str(tmp_path / "matchup.py")
        oceanval.create_recipes(
            simdir=str(tmp_path / "sim"), ndown=0, out=out,
            domain="global", start=2011, end=2012, gui=True,
        )

        assert 'model_variable="mystery"' in open(out).read()

    def test_cancelling_the_window_writes_nothing(
        self, simulation, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            "oceanval.recipes_gui.choose_recipes", lambda *args: None
        )
        out, returned = self._create(simulation, tmp_path, ask=False)

        assert returned is None
        assert not os.path.exists(out)
        assert "nothing was written" in capsys.readouterr().out



def settings(**changes):
    """The window's settings: matchup's and validate's defaults, changed."""
    values = dict(matchup_defaults(), **validate_defaults(), start=2011, end=2012)
    values.update(changes)
    return values


def matchup_call(script):
    return script[script.index("oceanval.matchup(") :]


class TestSettings:
    MAPPING = {"temperature": "thetao", "salinity": "so"}

    def build(self, domain="nwes", **kwargs):
        return build_recipe_script(
            "/sim", 2, self.MAPPING, (2011, 2012), domain, **kwargs
        )

    def test_unchanged_settings_write_nothing_more(self):
        # so writing straight away from the window gives the gui=False script
        assert self.build(settings=settings()) == self.build()
        assert "oceanval.validate()\n" in self.build(settings=settings())

    def test_changed_settings_are_written_in_matchups_order(self):
        script = self.build(
            settings=settings(
                lon_lim=[-20.0, 10.5], lat_lim=[40.0, 65.0], cores=12,
                ask=False, exclude=["restart", "5d"],
            )
        )
        ast.parse(script)

        assert (
            '    n_dirs_down=2,\n'
            '    lon_lim=[-20, 10.5],\n'
            '    lat_lim=[40, 65],\n'
            '    cores=12,\n'
            '    ask=False,\n'
            '    exclude=["restart", "5d"],\n'
            ')'
        ) in matchup_call(script)
        # left at their defaults, so not written
        assert "overwrite=" not in script
        assert "point_time_res=" not in matchup_call(script)

    def test_fvcom_settings_are_written_only_when_changed(self):
        script = self.build(
            settings=settings(fvcom_res=0.1, fvcom_max_depth=200),
            fvcom=True,
        )
        ast.parse(script)
        call = matchup_call(script)
        assert "    fvcom=True,\n" in call
        assert "    fvcom_res=0.1,\n" in call
        assert "    fvcom_max_depth=200,\n" in call
        assert "fvcom_min_depth" not in call
        assert "fvcom_dz" not in call
        assert "fvcom_res" not in self.build(settings=settings(), fvcom=True)

    def test_a_time_shift_is_written_when_given(self):
        # the oceanval window writes the answer to matchup's question about
        # the model's times; it has no box of its own
        script = self.build(settings=settings(time_shift=-1))
        ast.parse(script)
        assert "    time_shift=-1,\n" in matchup_call(script)
        assert "    time_shift=0,\n" in matchup_call(self.build(settings=settings(time_shift=0)))
        assert "time_shift" not in self.build(settings=settings())

    def test_validate_reads_the_matchups_from_out_dir(self):
        script = self.build(settings=settings(out_dir="/scratch/run 1"))

        assert '    out_dir="/scratch/run 1",\n)' in matchup_call(script)
        assert (
            'oceanval.validate(\n'
            '    data_dir="/scratch/run 1",\n'
            '    out_dir="/scratch/run 1",\n'
            ')'
        ) in script

    def test_report_settings_are_passed_to_validate(self):
        script = self.build(settings=settings(pdf=True, word=True, subregions="nwes"))

        assert (
            'oceanval.validate(\n    subregions="nwes",\n    pdf=True,\n    word=True,\n)'
        ) in script

    def test_report_options_chosen_later_replace_the_settings(self):
        # chosen in the oceanval window once the matchups are checked, with
        # validate's own subset rather than matchup's
        script = self.build(
            settings=settings(out_dir="/run", lon_lim=[-5.0, 5.0], pdf=True),
            report={"lon_lim": [-20.0, 10.0], "lat_lim": [40.0, 65.0], "zip": True, "concise": False},
        )
        ast.parse(script)

        assert "    lon_lim=[-5, 5],\n" in matchup_call(script).split("oceanval.validate")[0]
        assert (
            'oceanval.validate(\n'
            '    data_dir="/run",\n'
            '    out_dir="/run",\n'
            '    lon_lim=[-20, 10],\n'
            '    lat_lim=[40, 65],\n'
            '    zip=True,\n'
            '    concise=False,\n'
            ')'
        ) in script
        assert "pdf=True" not in script.split("oceanval.validate(")[-1]
        # the report's defaults
        assert "oceanval.validate()\n" in self.build(settings=settings(pdf=True), report={})

    def test_the_interim_report_has_the_report_options_chosen_later(self, monkeypatch):
        # matchup builds it as the matchups are made (see oceanval.live), and
        # it is HTML only, so it takes no pdf, word or zip
        script = self.build(
            settings=settings(out_dir="/run"),
            report={"lon_lim": [-20.0, 10.0], "pdf": True, "zip": True, "concise": False},
        )
        ast.parse(script)

        assert (
            '    out_dir="/run",\n'
            '    live_validation={"lon_lim": [-20, 10], "concise": False},\n'
            ")"
        ) in matchup_call(script)
        assert "live_validation builds an interim HTML report" in script
        # the report's defaults
        assert "    live_validation=True,\n)" in matchup_call(
            self.build(settings=settings(), report={"word": True})
        )
        # without the window's report options, there is none
        assert "live_validation" not in self.build(settings=settings(pdf=True))

    def test_the_matchups_chosen_to_overwrite_and_validate_are_written(self):
        # chosen in the oceanval window's simulation step, of those there already
        script = self.build(
            settings=settings(overwrite=["temperature/COBE2"]),
            report={"previous": ["nitrate/ICES"], "concise": False},
        )
        ast.parse(script)

        assert '    overwrite=["temperature/COBE2"],\n' in matchup_call(script)
        assert (
            'oceanval.validate(\n'
            '    previous=["nitrate/ICES"],\n'
            '    concise=False,\n'
            ')'
        ) in script
        # the interim report only ever has the run's own matchups
        assert '    live_validation={"concise": False},\n' in matchup_call(script)
        assert "    overwrite=False,\n" in matchup_call(self.build(settings=settings(overwrite=False)))

    def test_a_transect_is_written_into_validate_and_the_interim_report(self, monkeypatch):
        # chosen in the oceanval window, with the report options
        report = {
            "transect": {"start": [-30.0, 0.0], "end": [-30.0, 65.0]},
            "pdf": True,
        }
        script = self.build(settings=settings(out_dir="/run"), report=report)
        ast.parse(script)

        assert (
            'oceanval.validate(\n'
            '    data_dir="/run",\n'
            '    out_dir="/run",\n'
            '    transect={"start": [-30, 0], "end": [-30, 65]},\n'
            '    pdf=True,\n'
            ')'
        ) in script
        # the interim report is HTML only, but draws the transect
        assert (
            '    live_validation={"transect": {"start": [-30, 0], "end": [-30, 65]}},\n'
        ) in matchup_call(script)
        # validate's argument is a literal (live_validation's is a dict key)
        found = [
            ast.literal_eval(node.value)
            for node in ast.walk(ast.parse(script))
            if isinstance(node, ast.keyword) and node.arg == "transect"
        ]
        assert found == [{"start": [-30, 0], "end": [-30, 65]}]

    def test_a_full_report_is_asked_for_with_concise_false(self):
        assert "concise" not in self.build(settings=settings()).split("oceanval.validate")[-1]

        script = self.build(settings=settings(concise=False))

        assert "oceanval.validate(\n    concise=False,\n)" in script

    @pytest.mark.parametrize(
        "value, written", [(0.0, "as_missing=0,"), ([0.0, 1e20], "as_missing=[0, 1e+20],")]
    )
    def test_as_missing_is_a_value_or_a_range(self, value, written):
        assert written in matchup_call(self.build(settings=settings(as_missing=value)))

    def test_a_point_dataset_gets_its_own_options(self):
        script = self.build(
            point_options={
                ("temperature", "ices"): {"start": 2011, "end": None, "point_time_res": ["month"]}
            }
        )
        ast.parse(script)

        assert (
            '    recipe={"temperature": "ices"},\n'
            '    start=2011,\n'
            '    point_time_res=["month"],\n'
            '    vertical=False,'
        ) in script
        # the other ICES recipes are left as they were
        assert '    recipe={"salinity": "ices"},\n    vertical=False,' in script

    def test_a_vertical_point_dataset_is_written_as_vertical(self):
        script = self.build(
            point_options={("temperature", "ices"): {"vertical": True}}
        )

        assert '    recipe={"temperature": "ices"},\n    vertical=True,' in script
        # the others stay surface-only
        assert '    recipe={"salinity": "ices"},\n    vertical=False,' in script

    def test_a_gridded_dataset_gets_its_own_years(self):
        script = self.build(
            domain="global",
            gridded_options={
                ("temperature", "cobe2"): {"start": 2012, "end": None, "vertical": None}
            },
        )
        ast.parse(script)

        assert (
            '    recipe={"temperature": "cobe2"},\n'
            '    start=2012,\n'
            '    climatology=False,\n)'
        ) in script

    def test_a_vertical_gridded_dataset_is_written_as_vertical(self):
        script = self.build(
            gridded_options={("salinity", "nsbc"): {"vertical": True}}
        )

        assert (
            '    recipe={"salinity": "nsbc"},\n'
            '    climatology=True,\n'
            '    vertical=True,  # the full water column, so matchup also needs thickness\n'
        ) in script
        # the others stay surface-only
        assert (
            '    recipe={"temperature": "nsbc"},\n    climatology=True,\n    vertical=False,'
        ) in script

    def test_fvcom_needs_no_thickness_for_vertical(self):
        script = build_recipe_script(
            "/sim", 2, self.MAPPING, (2011, 2012), "nwes", fvcom=True,
            gridded_options={("salinity", "nsbc"): {"vertical": True}},
            point_options={("salinity", "ices"): {"vertical": True}},
        )

        assert script.count("    vertical=True,  # the full water column\n") == 2
        assert "so matchup also needs thickness" not in script

    def test_a_woa23_decade_can_be_chosen(self):
        # 2011-2012 sits inside 2005-2014, but the years chosen replace it
        script = self.build(
            domain="global",
            selection={("temperature", "woa23")},
            gridded_options={
                ("temperature", "woa23"): {"start": 2015, "end": 2022, "vertical": None}
            },
        )

        assert (
            '    recipe={"temperature": "woa23"},\n'
            '    start=2015,\n'
            '    end=2022,\n'
            '    climatology=True,\n'
        ) in script
        # salinity was given none, so keeps the decade covering the simulation
        assert (
            '#     recipe={"salinity": "woa23"},\n'
            '#     start=2005, end=2014,\n'
        ) in script

    def test_climatologies_are_given_the_years_for_climatologies(self):
        # also where the recipe is not ticked, so is commented out
        script = self.build(
            domain="global",
            selection={("temperature", "woa23")},
            settings=settings(clim_years=(2006, 2012)),
        )

        assert (
            '    recipe={"temperature": "woa23"},\n'
            '    start=2006,\n'
            '    end=2012,\n'
            '    climatology=True,\n'
        ) in script
        assert (
            '#     recipe={"salinity": "woa23"},\n'
            '#     start=2006,\n'
            '#     end=2012,\n'
        ) in script
        # a time series is left as it is
        cobe2 = script.split('recipe={"temperature": "cobe2"},\n')[1].split(")\n")[0]
        assert "start=" not in cobe2
        # and the years are not matchup's
        assert "clim_years" not in script

    def test_woa23_years_are_used_where_none_of_the_years_fit_a_decade(self):
        script = build_recipe_script(
            "/sim", 2, dict(self.MAPPING, nitrate="no3"), (2011, 2012), "global",
            selection={("temperature", "woa23"), ("nitrate", "woa23")},
            settings=settings(clim_years=(1990, 2020), woa_years=(2005, 2014)),
        )

        # nitrate is the all-years file, so keeps the years for climatologies
        assert (
            '    recipe={"nitrate": "woa23"},\n'
            '    start=1990,\n'
            '    end=2020,\n'
        ) in script
        assert (
            '    recipe={"temperature": "woa23"},\n'
            '    start=2005,\n'
            '    end=2014,\n'
        ) in script

    def test_validate_can_be_left_for_later(self, simulation, tmp_path):
        out = str(tmp_path / "matchup.py")
        oceanval.create_recipes(
            simdir=simulation, ndown=2, out=out, domain="global",
            start=2011, end=2012, ask=False, gui=False, validate=False,
        )
        script = open(out).read()
        ast.parse(script)

        assert "\noceanval.matchup(\n" in script
        assert "\n# oceanval.validate()\n" in script
        assert "\noceanval.validate(" not in script

    @pytest.mark.parametrize(
        "filters, written",
        [
            ({"exclude": "ptrc"}, '    exclude=["ptrc"],\n'),
            ({"require": ["grid_T"]}, '    require=["grid_T"],\n'),
        ],
    )
    def test_the_file_filters_are_used_and_passed_on(
        self, simulation, tmp_path, filters, written
    ):
        out = str(tmp_path / "matchup.py")
        oceanval.create_recipes(
            simdir=simulation, ndown=2, out=out, domain="global",
            start=2011, end=2012, ask=False, gui=False, **filters,
        )
        script = open(out).read()
        ast.parse(script)

        # the tracers are only in the ptrc files, which are left out
        assert 'model_variable="thetao"' in script
        assert 'model_variable="N3_n"' not in script
        assert written in matchup_call(script)

    def test_file_filters_must_be_strings(self, simulation, tmp_path):
        with pytest.raises(TypeError, match="exclude must be a string or a list"):
            oceanval.create_recipes(
                simdir=simulation, ndown=2, out=str(tmp_path / "out.py"),
                domain="global", start=2011, end=2012, ask=False, gui=False,
                exclude=5,
            )

    def test_validate_must_be_a_boolean(self, simulation, tmp_path):
        with pytest.raises(TypeError, match="validate must be True or False"):
            oceanval.create_recipes(
                simdir=simulation, ndown=2, out=str(tmp_path / "out.py"),
                domain="global", start=2011, end=2012, ask=False, gui=False,
                validate="no",
            )

    def test_the_years_chosen_in_the_window_are_used(self, simulation, tmp_path, monkeypatch):
        def window(mapping, domain, available, context, write):
            chosen = (
                mapping, default_selection(mapping, domain), settings(start=1996, end=2003), {}, {}
            )
            write(*chosen)
            return chosen

        monkeypatch.setattr("oceanval.recipes_gui.choose_recipes", window)
        out = str(tmp_path / "matchup.py")
        oceanval.create_recipes(
            simdir=simulation, ndown=2, out=out, domain="global",
            start=2011, end=2012, ask=False, gui=True,
        )
        script = open(out).read()

        assert "    start=1996,\n    end=2003,\n" in matchup_call(script)
        # and the WOA23 decade covering them, not the one covering 2011-2012
        assert "start=1995, end=2004," in script
        assert "start=2005, end=2014," not in script


def test_matchups_leave_matchups_defaults_as_they_were(tmp_path, monkeypatch):
    """Finding the files once appended "restart" to the exclude list it was
    given, which was matchup()'s own default, so each matchup in a session
    excluded it once more, and the window, which reads the defaults, gave the
    run file filters of "restart restart"."""
    import contextlib

    from oceanval import matchall
    from oceanval.session import session_info
    from simulations import write_analysis_simulation

    root = tmp_path / "sim"
    write_analysis_simulation(str(root))
    monkeypatch.setitem(session_info, "levels_down", 1)
    monkeypatch.setitem(session_info, "require", None)
    given = ["grid_U"]
    for _ in range(2):
        # what it goes on to find does not matter here
        with contextlib.suppress(Exception):
            matchall.extract_variable_mapping(str(root), exclude=given, n_check=1)
    assert given == ["grid_U"]
    # matchup() passes its own default on, the very list the window reads
    with contextlib.suppress(Exception):
        matchall.extract_variable_mapping(str(root), exclude=matchup_defaults()["exclude"], n_check=1)
    assert matchup_defaults()["exclude"] == []


def test_a_matchup_looks_only_at_its_own_simulations_files(tmp_path, monkeypatch):
    """The files found were kept from one matchup to the next in a session, and
    the cell thickness was looked for in all of them, so a matchup could take
    its thickness from another simulation's files."""
    import contextlib

    from oceanval import matchall
    from oceanval.session import session_info
    from simulations import write_analysis_simulation

    monkeypatch.setitem(session_info, "levels_down", 1)
    monkeypatch.setitem(session_info, "require", None)
    for name in ("first", "second"):
        write_analysis_simulation(str(tmp_path / name))
        with contextlib.suppress(Exception):
            matchall.extract_variable_mapping(str(tmp_path / name), n_check=1)
    assert matchall.raw_options
    assert all(path.startswith(str(tmp_path / "second")) for path in matchall.raw_options + matchall.random_files)
