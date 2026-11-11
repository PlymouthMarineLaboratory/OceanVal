"""How OceanVal converts observations into the model's units, for the oceanval
window's units step."""

import pytest

from oceanval.create_recipes import POINT_RECIPE_CATALOGUE, RECIPE_CATALOGUE
from oceanval.unit_conversion import suggest

# (variable, the observations' units, the model's, multiplier, adder). The
# model's units are as NEMO-ERSEM, FVCOM-ERSEM and CMIP output write them.
CONVERTED = [
    # WOA23 is per kilogram, and the model per volume
    ("nitrate", "micromoles_per_kilogram", "mmol N/m^3", 1.025, 0),
    ("oxygen", "micromoles_per_kilogram", "mmol O_2/m^3", 1.025, 0),
    # ICES oxygen is in millilitres of the gas
    ("oxygen", "ml/l", "mmol O_2/m^3", 44.661, 0),
    ("oxygen", "ml/l", "mol m-3", 0.044661, 0),
    ("oxygen", "mL L-1", "umol kg-1", 43.5717, 0),
    # ICES alkalinity is in milliequivalents
    ("alkalinity", "mEq/l", "umol/kg", 975.61, 0),
    ("alkalinity", "mEq/l", "mmol eq/m^3", 1000, 0),
    ("nitrate", "mmol/m^3", "mol m-3", 0.001, 0),
    ("chlorophyll", "mg/m^3", "kg m-3", 1e-06, 0),
    ("temperature", "K", "degC", 1, -273.15),
    ("temperature", "degC", "K", 1, 273.15),
    ("temperature", "degF", "degrees_C", 0.555556, -17.7778),
    # another name for a variable with rules of its own
    ("sst", "K", "degree_C", 1, -273.15),
    # powers as NEMO-MEDUSA writes them
    ("silicate", "micromoles_per_kilogram", "mmol Si/m**3", 1.025, 0),
    ("nitrate", "micromoles_per_kilogram", "mmol N/m**3", 1.025, 0),
    ("oxygen", "micromoles_per_kilogram", "mmol O_2/m**3", 1.025, 0),
    ("alkalinity", "micro-mol kg-1", "meq/m**3", 1.025, 0),
    # a label written onto its unit
    ("nitrate", "micromoles_per_kilogram", "mmolN/m3", 1.025, 0),
    ("nitrate", "\u00b5mol/l", "mol-N/L", 1e-06, 0),
    # a mass, where the units say what it is of, or of oxygen, taken as O2
    ("nitrate", "\u00b5mol/l", "mg N/m3", 14.007, 0),
    ("phosphate", "mg P/m3", "mmol/m3", 0.0322851, 0),
    ("oxygen", "ml/l", "mg/l", 1.42906, 0),
    ("oxygen", "micromoles_per_kilogram", "mg O2/l", 0.032798, 0),
    # fluxes per day, second and year: the MPI-SOM-FFN air-sea CO2 flux is in mol m-2 yr-1,
    # ERSEM's in mmol C/m^2/d, and a year is 365.25 days
    ("co2flux", "mol m-2 yr-1", "mmol C/m^2/d", 2.73785, 0),
    ("co2flux", "mmol C/m^2/d", "mol m-2 yr-1", 0.36525, 0),
    ("co2flux", "mol m-2 yr-1", "mmol m-2 d-1", 2.73785, 0),
    ("co2flux", "mol m-2 yr-1", "mmol/m2/day", 2.73785, 0),
    ("co2flux", "mol m-2 yr-1", "mol per m2 per day", 0.00273785, 0),
    # a month is a twelfth of a year
    ("co2flux", "mol m-2 yr-1", "mol m-2 month-1", 0.0833333, 0),
    # a mass of carbon: CMIP6's fgco2 is kg m-2 s-1, as carbon; 1 mol C = 12.011 g
    ("co2flux", "mol C m-2 yr-1", "kg m-2 s-1", 3.80606e-10, 0),
    ("co2flux", "mol C m-2 yr-1", "kg C m-2 s-1", 3.80606e-10, 0),
    ("co2flux", "mol C m-2 yr-1", "kgC m-2 s-1", 3.80606e-10, 0),
    ("co2flux", "mol C m-2 yr-1", "g C m-2 yr-1", 12.011, 0),
    ("co2flux", "mol C m-2 yr-1", "mg C m-2 d-1", 32.8843, 0),
    # a mole of CO2 holds a mole of carbon
    ("co2flux", "mol C m-2 yr-1", "kg CO2 m-2 s-1", 1.39456e-09, 0),
    # the same units on a different footing
    ("nitrate", "mmol/m^3", "mmol/dm3", 0.001, 0),
]

SAME = [
    # P is not poise, nor N newton
    ("phosphate", "mmol/m^3", "mmol P/m^3"),
    ("nitrate", "mmol/m^3", "mmol N m-3"),
    ("silicate", "µmol/l", "mmol Si/m^3"),
    ("nitrate", "µM", "mmol/m3"),
    ("alkalinity", "micro-mol kg-1", "umol/kg"),
    ("chlorophyll", "µg/l", "mg/m^3"),
    ("chlorophyll", "milligram m-3", "mg Chl/m^3"),
    ("chlorophyll", "mg/m^3", "mg chl a m-3"),
    ("chlorophyll", "mg/m³", "mg m⁻³"),
    ("chlorophyll", "milligram m-3", "mg chl/m**3"),
    ("chlorophyll", "mg/m^3", "mgChl/m3"),
    ("kd490", "m-1", "1/m"),
    ("kd490", "m-1", "per m"),
    ("kd490", "m-1", "m**-1"),
    ("kd490", "m-1", "/m"),
    ("temperature", "°C", "degrees_C"),
    ("temperature", "degC", "degree_Celsius"),
    ("temperature", "degC", "C"),
    ("oxygen", "ml/l", "ml/l"),
    # salinity is on one scale however it is written, not a factor of 1000
    ("salinity", "dimensionless", "1e-3"),
    ("salinity", "psu", "1e-3"),
    ("salinity", "1", "PSU"),
    ("Salinity", "0.001", "practical_salinity_units"),
    ("salinity", "1", "1.e-3"),
    # and pH has no units, even where they are missing
    ("ph", "total scale", "-"),
    ("ph", "pH units", None),
    # fluxes, however the time is written
    ("co2flux", "mmol m-2 d-1", "mmol/m^2/d"),
    ("co2flux", "mmol C/m^2/day", "mmol m-2 d-1"),
    ("co2flux", "mol C m^-2 yr^-1", "mol m-2 yr-1"),
    ("co2flux", "mol m-2 yr-1", "mol/m²/year"),
    ("co2flux", "kg m-2 s-1", "kg C m-2 s-1"),
]

UNKNOWN = [
    # moles and grams
    ("nitrate", "mmol/m^3", "mg/m^3"),
    ("chlorophyll", "mg/m3", "mmol/m3"),
    # a mass of something the variable is not
    ("nitrate", "mg P/m3", "mmol/m3"),
    ("nitrate", "mmol/m^3", None),
    ("nitrate", None, "mmol/m^3"),
    ("nitrate", "mmol/m^3", " "),
    # a rate
    ("nitrate", "mmol/m^3", "mmol/m^2/d"),
    ("nitrate", "mmol/m^3", "banana"),
    # alone, C is carbon as likely as a temperature
    ("nitrate", "mmol/m^3", "C"),
    # equivalents are only for alkalinity, and millilitres of gas only for oxygen
    ("nitrate", "mEq/l", "mmol/m3"),
    ("chl", "ml/l", "mmol/m3"),
    # more likely the same scale written two ways than a factor of 1000
    ("chl", "1", "1e-3"),
    ("salinity", "g/l", "psu"),
    ("temperature", "degC", "mmol/m3"),
    # units that are no use as a conversion
    ("nitrate", "0 mmol/m3", "mmol/m3"),
    ("nitrate", "mmol/m3", "0 mmol/m3"),
    ("nitrate", "1e400 mmol/m3", "mmol/m3"),
    ("nitrate", "mmol/", "mmol/m3"),
    # a mass flux is only taken as carbon for the CO2 flux, and only against moles of it
    ("nitrate", "mmol/m^3", "kg m-2 s-1"),
    ("nitrate", "mmol/m^2/d", "kg m-2 s-1"),
    ("co2flux", "mol m-2 yr-1", "kg N m-2 s-1"),
    ("co2flux", "mol m-2 yr-1", "mmol/m^3"),
    # a speed is not a flux
    ("co2flux", "mol m-2 yr-1", "m s-1"),
    # no prefix on a day: this is not a milli-day
    ("co2flux", "mol m-2 yr-1", "mmol m-2 md-1"),
]


@pytest.mark.parametrize("variable, obs, model, multiplier, adder", CONVERTED)
def test_conversions(variable, obs, model, multiplier, adder):
    found = suggest(variable, obs, model)

    assert found["status"] == "convert"
    assert (found["multiplier"], found["adder"]) == (multiplier, adder)


@pytest.mark.parametrize("variable, obs, model", SAME)
def test_the_same_units(variable, obs, model):
    found = suggest(variable, obs, model)

    assert (found["status"], found["multiplier"], found["adder"]) == ("same", 1, 0)


@pytest.mark.parametrize("variable, obs, model", UNKNOWN)
def test_units_that_cannot_be_compared(variable, obs, model):
    found = suggest(variable, obs, model)

    assert (found["status"], found["multiplier"], found["adder"]) == ("unknown", None, None)
    assert found["note"].startswith("Not checked: ")


def test_what_is_assumed_is_said():
    assert "a seawater density of 1025 kg/m³" in suggest(
        "nitrate", "micromoles_per_kilogram", "mmol/m^3"
    )["note"]
    assert "44.661 µmol" in suggest("oxygen", "ml/l", "mmol/m^3")["note"]
    assert "an equivalent of alkalinity as a mole" in suggest(
        "alkalinity", "mEq/l", "mmol/m^3"
    )["note"]


def test_what_a_mass_is_taken_to_be_is_said():
    assert "a molar mass of 14.007 g/mol for N" in suggest(
        "nitrate", "\u00b5mol/l", "mg N/m3"
    )["note"]
    assert "the oxygen as O\u2082" in suggest("oxygen", "ml/l", "mg/l")["note"]
    # and why a mass cannot be turned into moles
    assert "“mg/m^3” does not" in suggest("nitrate", "mmol/m^3", "mg/m^3")["note"]


def test_what_could_not_be_read_is_named():
    assert "“banana”" in suggest("nitrate", "mmol/m^3", "banana")["note"]
    assert "“banana” in the units “mmol banana/m3”" in suggest(
        "nitrate", "mmol/m^3", "mmol banana/m3"
    )["note"]
    assert "the simulation's files do not give its units" in suggest(
        "nitrate", "mmol/m^3", None
    )["note"]


def test_the_length_of_a_year_is_said_only_where_it_is_used():
    per_day = suggest("co2flux", "mol m-2 yr-1", "mmol C/m^2/d")["note"]
    assert "a year of 365.25 days" in per_day
    assert "a year of 365.25 days" in suggest("co2flux", "mmol m-2 d-1", "mol m-2 yr-1")["note"]
    # a month and a year are not given a length; nor are two units of a day or two of a year
    assert "365.25" not in suggest("co2flux", "mol m-2 yr-1", "mol m-2 month-1")["note"]
    assert "365.25" not in suggest("co2flux", "mmol m-2 d-1", "mol m-2 s-1")["note"]
    assert "365.25" not in suggest("co2flux", "mol m-2 yr-1", "mmol m-2 yr-1")["note"]


def test_a_mass_of_carbon_is_said_where_the_units_do_not_say_it():
    labelled = suggest("co2flux", "mol C m-2 yr-1", "kg C m-2 s-1")["note"]
    assert "a molar mass of 12.011 g/mol for C" in labelled
    # CMIP6's fgco2 is "expressed as carbon" and has no label
    unlabelled = suggest("co2flux", "mol C m-2 yr-1", "kg m-2 s-1")["note"]
    assert "the mass as carbon, of 12.011 g/mol" in unlabelled
    assert "a molar mass of 44.009 g/mol for CO2" in suggest(
        "co2flux", "mol C m-2 yr-1", "kg CO2 m-2 s-1"
    )["note"]


UPWARD = "surface_upward_mass_flux_of_carbon_dioxide_expressed_as_carbon"
DOWNWARD = "surface_downward_mass_flux_of_carbon_dioxide_expressed_as_carbon"


@pytest.mark.parametrize(
    "description",
    [UPWARD, "positive up", "air-sea CO2 flux, positive out of the ocean", "outgassing of CO2"],
)
def test_a_flux_positive_the_other_way_is_warned_of(description):
    """The built-in observations are positive into the ocean: a model whose flux
    is positive out of it needs a negative multiplier, which OceanVal does not
    fill in, as it is only told by words."""
    found = suggest("co2flux", "mol m-2 yr-1", "mol m-2 yr-1", model_description=description)

    assert (found["status"], found["multiplier"], found["adder"]) == ("warn", 1, 0)
    assert "positive out of the ocean" in found["note"] and "negative number" in found["note"]


def test_a_sign_warning_goes_with_a_conversion():
    found = suggest("co2flux", "mol m-2 yr-1", "mmol C/m^2/d", model_description=UPWARD)

    assert (found["status"], found["multiplier"], found["adder"]) == ("convert", 2.73785, 0)
    assert "a year of 365.25 days" in found["note"] and "negative number" in found["note"]


@pytest.mark.parametrize(
    "model_description, obs_description",
    [
        (None, None),
        # ERSEM's long_name does not say, so nothing is guessed
        ("air-sea flux of CO2", None),
        (DOWNWARD, None),
        ("positive down", None),
        # observations that are positive the same way
        (UPWARD, "air-sea flux, positive up"),
        # both ways said at once
        (f"{UPWARD} {DOWNWARD}", None),
    ],
)
def test_no_sign_warning_where_the_flux_goes_the_same_way_or_is_not_said(
    model_description, obs_description
):
    found = suggest(
        "co2flux",
        "mol m-2 yr-1",
        "mol m-2 yr-1",
        model_description=model_description,
        obs_description=obs_description,
    )

    assert found["status"] == "same"


def test_the_sign_is_only_warned_of_for_the_co2_flux():
    found = suggest("nitrate", "mmol/m^3", "mmol/m^3", model_description=UPWARD)

    assert found["status"] == "same"


def test_observations_positive_upward_are_warned_of_against_a_downward_model():
    found = suggest(
        "co2flux",
        "mol m-2 yr-1",
        "mol m-2 yr-1",
        model_description=DOWNWARD,
        obs_description="flux of CO2, positive out of the ocean",
    )

    assert found["status"] == "warn"
    assert "positive into the ocean" in found["note"]


ABSOLUTE = "absolute salinity sea_water_absolute_salinity"
CONSERVATIVE = "conservative temperature sea_water_conservative_temperature"


@pytest.mark.parametrize(
    "variable, obs, model, description, said",
    [
        ("salinity", "1", "1e-3", ABSOLUTE, "Absolute Salinity"),
        ("so", "psu", "g/kg", "sea_water_absolute_salinity", "Absolute Salinity"),
        ("temperature", "degC", "degC", CONSERVATIVE, "Conservative Temperature"),
    ],
)
def test_teos10_variables_are_warned_of(variable, obs, model, description, said):
    """The units are the same, but observations are seldom these quantities,
    and no multiplier and adder turn one into the other."""
    found = suggest(variable, obs, model, model_description=description)

    assert (found["status"], found["multiplier"], found["adder"]) == ("warn", 1, 0)
    assert said in found["note"]


def test_a_teos10_warning_goes_with_a_conversion():
    found = suggest("temperature", "K", "degC", model_description=CONSERVATIVE)

    assert (found["status"], found["multiplier"], found["adder"]) == ("convert", 1, -273.15)
    assert "Conservative Temperature" in found["note"]


@pytest.mark.parametrize(
    "model_description, obs_description",
    [
        (None, None),
        ("sea water practical salinity", None),
        # observations of Absolute Salinity too
        (ABSOLUTE, "Absolute Salinity"),
    ],
)
def test_no_warning_where_the_quantities_are_the_same(model_description, obs_description):
    found = suggest(
        "salinity",
        "1",
        "1e-3",
        model_description=model_description,
        obs_description=obs_description,
    )

    assert found["status"] == "same"


@pytest.mark.parametrize(
    "entry",
    RECIPE_CATALOGUE + POINT_RECIPE_CATALOGUE,
    ids=lambda entry: f"{entry['variable']}-{entry['recipe']}",
)
def test_every_recipes_units_are_recognised(entry):
    """So a recipe added in units OceanVal cannot read is caught."""
    assert suggest(entry["variable"], entry["units"], entry["units"])["status"] == "same"
