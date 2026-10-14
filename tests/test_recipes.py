import pytest

from oceanval.parsers import find_recipe, recipe_list


RECIPE_CASES = [
    (next(iter(recipe)), recipe[next(iter(recipe))])
    for recipe in recipe_list
]


@pytest.mark.parametrize("name, source", RECIPE_CASES)
def test_recipe_returns_required_metadata(name, source):
    """Every registered recipe should resolve without accessing remote data."""
    start = 2005 if source == "woa23" and name in {"temperature", "salinity"} else None
    end = 2014 if start is not None else None

    result = find_recipe({name: source}, start=start, end=end)

    required_fields = {
        "name",
        "source",
        "source_info",
        "obs_path",
        "obs_variable",
        "climatology",
        "thredds",
    }
    assert required_fields.issubset(result)
    assert result["name"] == name
    assert result["obs_variable"]
    assert result["obs_path"]
    assert result["source"]
    assert isinstance(result["climatology"], bool)
    assert result["thredds"] is ("glodap" not in source.lower())
    # every recipe here is gridded
    assert result["point"] is False


@pytest.mark.parametrize(
    "recipe, expected_source",
    [
        ({"temperature": "cobe2"}, "COBE2"),
        ({"nitrate": "woa23"}, "WOA23"),
        ({"chlorophyll": "occci"}, "OCCCI"),
        ({"ph": "glodap"}, "GLODAPv2.2016b"),
        ({"oxygen": "nsbc"}, "NSBC"),
    ],
)
def test_recipe_source_metadata(recipe, expected_source):
    """Representative recipes should identify their expected data source."""
    result = find_recipe(
        recipe,
        start=2005 if recipe == {"temperature": "cobe2"} else None,
        end=2014 if recipe == {"temperature": "cobe2"} else None,
    )

    assert result["source"] == expected_source


@pytest.mark.parametrize(
    "name, filename_variable",
    [
        ("ammonium", "ammonium"),
        ("chlorophyll", "chlorophyll_a"),
        ("nitrate", "nitrate"),
        ("oxygen", "oxygen"),
        ("phosphate", "phosphate"),
        ("salinity", "salinity"),
        ("silicate", "silicate"),
        ("temperature", "temperature"),
    ],
)
def test_nsbc_recipe_uses_canonical_thredds_url(name, filename_variable):
    result = find_recipe({name: "nsbc"})

    assert result["obs_path"] == (
        "https://icdc.cen.uni-hamburg.de/thredds/dodsC/ftpthredds/nsbc/"
        "level_3/climatological_monthly_mean/"
        f"NSBC_Level3_{filename_variable}__UHAM_ICDC__v1.1__0.25x0.25deg__OAN_1960_2014.nc"
    )


@pytest.mark.parametrize(
    "recipe, expected_url",
    [
        (
            {"temperature": "cobe2"},
            "https://psl.noaa.gov/thredds/dodsC/Datasets/COBE2/sst.mon.mean.nc",
        ),
        (
            {"ph": "glodap"},
            "https://www.ncei.noaa.gov/data/oceans/archive/arc0221/0286118/1.1/"
            "data/0-data/GLODAPv2.2016b_MappedClimatologies/GLODAPv2.2016b.pHtsinsitutp.nc",
        ),
        (
            {"alkalinity": "glodap"},
            "https://www.ncei.noaa.gov/data/oceans/archive/arc0221/0286118/1.1/"
            "data/0-data/GLODAPv2.2016b_MappedClimatologies/GLODAPv2.2016b.TAlk.nc",
        ),
    ],
)
def test_single_file_recipe_urls_match_v020(recipe, expected_url):
    assert find_recipe(recipe)["obs_path"] == expected_url


def test_woa23_temperature_and_salinity_select_requested_period():
    """WOA23 temperature and salinity URLs should encode the decade period."""
    for name, variable in (("temperature", "t_an"), ("salinity", "s_an")):
        result = find_recipe({name: "woa23"}, start=1995, end=2004)

        assert result["obs_variable"] == variable
        assert len(result["obs_path"]) == 12
        assert "95A4" in result["obs_path"][0]
        assert result["climatology"] is True


def test_woa23_temperature_and_salinity_return_monthly_files():
    result = find_recipe({"temperature": "woa23"}, start=2005, end=2014)

    assert len(result["obs_path"]) == 12
    assert result["obs_path"][0].endswith("woa23_A5B4_t01_01.nc")
    assert result["obs_path"][-1].endswith("woa23_A5B4_t12_01.nc")


@pytest.mark.parametrize(
    "name, variable, first_file, last_file",
    [
        ("chlorophyll", "chlor_a", "OCx-199801-fv6.0.nc", "OCx-202412-fv6.0.nc"),
        ("kd490", "kd_490", "KD490_Lee-199801-fv6.0.nc", "KD490_Lee-202412-fv6.0.nc"),
    ],
)
def test_occci_recipes_use_v020_monthly_collections(name, variable, first_file, last_file):
    result = find_recipe({name: "occci"})

    assert result["obs_variable"] == variable
    assert len(result["obs_path"]) == 324
    assert result["obs_path"][0].endswith(first_file)
    assert result["obs_path"][-1].endswith(last_file)


def test_woa23_temperature_and_salinity_require_years():
    """Decadal WOA23 recipes must receive a climatology period."""
    for name in ("temperature", "salinity"):
        with pytest.raises(ValueError, match="Start and end depth must be provided"):
            find_recipe({name: "woa23"})


def test_woa23_rejects_years_outside_supported_periods():
    with pytest.raises(ValueError, match="End year cannot be greater than 2022"):
        find_recipe({"temperature": "woa23"}, start=2023, end=2032)


def test_recipe_name_and_source_are_case_insensitive():
    result = find_recipe({"TeMpErAtUrE": "CoBe2"})

    assert result["name"] == "temperature"
    assert result["source"] == "COBE2"


@pytest.mark.parametrize(
    "recipe, message",
    [
        ({}, "exactly one key"),
        ({"temperature": "cobe2", "oxygen": "woa23"}, "exactly one key"),
        ({"temperature": None}, "not valid"),
        ({"temperature": "unknown"}, "not valid"),
        ({"unknown": "cobe2"}, "not valid"),
    ],
)
def test_invalid_recipe_definitions_raise(recipe, message):
    with pytest.raises(ValueError, match=message):
        find_recipe(recipe)


ICES_RECIPE_CASES = [
    ("temperature", "TEMPPR01", ["CTD", "ICE"], "Temperature"),
    ("salinity", "PSALPR01", ["CTD", "ICE"], "Salinity"),
    ("alkalinity", "ALKYZZXX", "ICE", "Total Alkalinity"),
    ("ammonium", "AMONZZXX", "ICE", "Ammonium"),
    ("chlorophyll", "CPHLZZXX", "ICE", "Chlorophyll"),
    ("nitrate", "NTRAZZXX", "ICE", "Nitrate"),
    ("oxygen", "DOXYZZXX", "ICE", "Oxygen"),
    ("ph", "PHXXZZXX", "ICE", "pH"),
    ("phosphate", "PHOSZZXX", "ICE", "Phosphate"),
    ("silicate", "SLCAZZXX", "ICE", "Silicate"),
]


@pytest.mark.parametrize("name, parameter, dataset, short_title", ICES_RECIPE_CASES)
def test_ices_recipe_metadata(name, parameter, dataset, short_title):
    result = find_recipe({name: "ices"})

    assert result["point"] is True
    assert result["name"] == name
    assert result["source"] == "ICES"
    assert result["ices_parameter"] == parameter
    assert result["ices_dataset"] == dataset
    assert result["short_title"] == short_title


def test_ices_recipe_parameter_codes_are_unique():
    codes = [parameter for _, parameter, _, _ in ICES_RECIPE_CASES]

    assert len(codes) == len(set(codes))


def test_ices_has_no_recipe_for_variables_outside_its_10():
    with pytest.raises(ValueError, match="not valid"):
        find_recipe({"kd490": "ices"})
