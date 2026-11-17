"""How units are written in the report (oceanval.unit_display): one set of rules
for the HTML text, the figures' labels and plain text."""
import importlib

import pytest

from oceanval import unit_display
from oceanval.tidiers import fix_unit

# unit as given -> (HTML, mathtext)
CASES = {
    # the form that rendered as bold in the HTML report
    "mmol O2/m**3": ("mmol O<sub>2</sub> m<sup>-3</sup>", "mmol O$_{2}$ m$^{-3}$"),
    "mmol O_2/m^3": ("mmol O<sub>2</sub> m<sup>-3</sup>", "mmol O$_{2}$ m$^{-3}$"),
    # the slash keeps a space: mmol m-3, never mmolm-3
    "mmol/m3": ("mmol m<sup>-3</sup>", "mmol m$^{-3}$"),
    "mg/m^3": ("mg m<sup>-3</sup>", "mg m$^{-3}$"),
    "mg m^-3": ("mg m<sup>-3</sup>", "mg m$^{-3}$"),
    "mmol m-3": ("mmol m<sup>-3</sup>", "mmol m$^{-3}$"),
    "mg C m-3": ("mg C m<sup>-3</sup>", "mg C m$^{-3}$"),
    "mol m-2 yr-1": ("mol m<sup>-2</sup> yr<sup>-1</sup>", "mol m$^{-2}$ yr$^{-1}$"),
    "mmol C/m^2/d": ("mmol C m<sup>-2</sup> d<sup>-1</sup>", "mmol C m$^{-2}$ d$^{-1}$"),
    "mmol/(m2 d)": ("mmol m<sup>-2</sup> d<sup>-1</sup>", "mmol m$^{-2}$ d$^{-1}$"),
    "W/m2": ("W m<sup>-2</sup>", "W m$^{-2}$"),
    "m-1": ("m<sup>-1</sup>", "m$^{-1}$"),
    "1/m": ("m<sup>-1</sup>", "m$^{-1}$"),
    "umol/kg": ("µmol kg<sup>-1</sup>", "µmol kg$^{-1}$"),
    "µmol/l": ("µmol l<sup>-1</sup>", "µmol l$^{-1}$"),
    "micro-mol kg-1": ("µmol kg<sup>-1</sup>", "µmol kg$^{-1}$"),
    "micromoles_per_kilogram": ("µmol kg<sup>-1</sup>", "µmol kg$^{-1}$"),
    "uatm": ("µatm", "µatm"),
    "g/kg": ("g kg<sup>-1</sup>", "g kg$^{-1}$"),
    "Pg C yr-1": ("Pg C yr<sup>-1</sup>", "Pg C yr$^{-1}$"),
    "degC": ("°C", "°C"),
    "deg_C m-2": ("°C m<sup>-2</sup>", "°C m$^{-2}$"),
    "degrees_Celsius-1": ("°C<sup>-1</sup>", "°C$^{-1}$"),
    # what is not a unit OceanVal can read is shown as it is
    "PSU": ("PSU", "PSU"),
    "1e-3": ("1e-3", "1e-3"),
    "1": ("1", "1"),
    "pH units": ("pH units", "pH units"),
    "degrees_north": ("degrees_north", "degrees_north"),
    "Chl_a": ("Chl_a", "Chl_a"),
}


@pytest.mark.parametrize("unit", CASES)
def test_each_unit_is_written_as_the_report_shows_it(unit):
    written, label = CASES[unit]
    assert unit_display.html(unit) == written
    assert fix_unit(unit) == written
    assert unit_display.mathtext(unit) == label


def test_mmol_o2_per_cubic_metre_in_plain_text():
    assert unit_display.plain("mmol O2/m**3") == "mmol O₂ m⁻³"


@pytest.mark.parametrize("unit", CASES)
def test_a_unit_written_twice_is_unchanged(unit):
    written = unit_display.html(unit)
    assert unit_display.html(written) == written
    assert unit_display.mathtext(written) == unit_display.mathtext(unit)


def _catalogue_units():
    recipes = importlib.import_module("oceanval.create_recipes")
    return sorted(set(recipes._GRIDDED_UNITS.values()) | set(recipes._POINT_UNITS.values()))


@pytest.mark.parametrize("unit", _catalogue_units())
def test_no_built_in_unit_is_left_with_markdown_or_a_lost_slash(unit):
    import re

    written = unit_display.html(unit)
    text = re.sub(r"</?su[bp]>", "", written)
    # ** is markdown's bold, and a slash left in is a unit not read
    assert "**" not in written
    assert "/" not in text
    # every power is a superscript: no m3 or m-3 left as text
    assert not re.search(r"(^|\s)[a-z]+-?\d+(\s|$)", written)
