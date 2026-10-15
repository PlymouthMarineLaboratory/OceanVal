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
    ("kd490", "m-1", "1/m"),
    ("kd490", "m-1", "per m"),
    ("temperature", "°C", "degrees_C"),
    ("temperature", "degC", "degree_Celsius"),
    ("temperature", "degC", "C"),
    ("oxygen", "ml/l", "ml/l"),
    # salinity is on one scale however it is written, not a factor of 1000
    ("salinity", "dimensionless", "1e-3"),
    ("salinity", "psu", "1e-3"),
    ("salinity", "1", "PSU"),
    ("Salinity", "0.001", "practical_salinity_units"),
    # and pH has no units, even where they are missing
    ("ph", "total scale", "-"),
    ("ph", "pH units", None),
]

UNKNOWN = [
    # moles and grams
    ("nitrate", "mmol/m^3", "mg/m^3"),
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


def test_what_could_not_be_read_is_named():
    assert "“banana”" in suggest("nitrate", "mmol/m^3", "banana")["note"]
    assert "the simulation's files do not give its units" in suggest(
        "nitrate", "mmol/m^3", None
    )["note"]


@pytest.mark.parametrize(
    "entry",
    RECIPE_CATALOGUE + POINT_RECIPE_CATALOGUE,
    ids=lambda entry: f"{entry['variable']}-{entry['recipe']}",
)
def test_every_recipes_units_are_recognised(entry):
    """So a recipe added in units OceanVal cannot read is caught."""
    assert suggest(entry["variable"], entry["units"], entry["units"])["status"] == "same"
