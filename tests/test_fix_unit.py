"""How units are written in the report: every way of writing degrees Celsius is °C."""
import json
from pathlib import Path

import pytest

from oceanval import analysis_plots as ap
from oceanval import pyplots
from oceanval.tidiers import celsius, fix_unit, md

SPELLINGS = [
    "degC",
    "DegC",
    "DEGC",
    "deg_C",
    "deg C",
    "deg.C",
    "degree_C",
    "degrees_C",
    "Degrees_C",
    "DEGREES_C",
    "degrees C",
    "degrees-C",
    "degree_Celsius",
    "degrees_Celsius",
    "Degrees Celsius",
    "degrees_centigrade",
    "Celsius",
    "celsius",
    "° C",
    "º C",
    "°C",
]

# not temperatures, or not Celsius
UNCHANGED = [
    "C",
    "mmol C/m^3",
    "mg C m-3",
    "degrees_north",
    "degrees_east",
    "degCm2",
    "degF",
    "K",
    "Chl_a",
]


@pytest.mark.parametrize("units", SPELLINGS)
def test_every_spelling_of_celsius_is_shown_as_degrees_c(units):
    assert celsius(units) == "°C"
    assert fix_unit(units) == "°C"


@pytest.mark.parametrize("units", UNCHANGED)
def test_what_is_not_celsius_is_left_alone(units):
    assert celsius(units) == units


def test_celsius_in_units_with_more_in_them():
    assert fix_unit("degrees_C m") == "°C m"
    assert fix_unit("deg_C m-2") == "°C m<sup>-2</sup>"
    assert fix_unit("degrees_Celsius-1") == "°C-1"
    assert fix_unit("mg C m-3") == "mg C m<sup>-3</sup>"


@pytest.mark.parametrize("units", SPELLINGS)
def test_the_spellings_oceanval_converts_between_are_all_displayed_the_same(units):
    from oceanval import unit_conversion

    # the conversion and the report read Celsius alike: no conversion is
    # suggested from any spelling to another, and the report says °C for each
    assert unit_conversion.suggest("temperature", units, "degC")["status"] == "same"
    assert fix_unit(units) == fix_unit("degC")


def test_the_analyze_report_shows_degrees_c():
    assert ap.unit_html("degrees_C") == "°C"
    assert ap.unit_text("degrees_C") == "°C"
    assert ap.legend("Temperature", "Deg_C") == "Temperature (°C)"


def test_the_python_plots_label_degrees_c():
    assert pyplots._strip_html("Model temperature (degrees_C)") == "Model temperature (°C)"
    assert pyplots._strip_html("mmol m<sup>-3</sup>") == "mmol m-3"


def test_the_unit_column_of_a_table():
    import pandas as pd

    from oceanval.tidiers import df_display

    shown = df_display(pd.DataFrame({"variable": ["temperature"], "unit": ["degrees_C"]})).data
    assert shown["Unit"].tolist() == ["°C"]


def test_in_brackets_in_text():
    assert md("Bias (deg_C) and [degrees C].").data == "Bias (°C) and [°C]."
    # a word in a sentence is not a unit
    assert md("Water temperature in Celsius.").data == "Water temperature in Celsius."


def test_point_pages_show_the_unit_as_degrees_c():
    notebook = json.loads(Path("oceanval/data/point_template.ipynb").read_text())
    code = "".join("".join(cell["source"]) + "\n" for cell in notebook["cells"] if cell["cell_type"] == "code")
    assert "unit = celsius(unit)" in code
    assert code.index("from oceanval.tidiers import celsius") < code.index("unit = celsius(unit)")
