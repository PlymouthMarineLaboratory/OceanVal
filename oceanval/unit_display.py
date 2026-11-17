"""How a unit is written in the report: one set of rules for the HTML text and
tables (html), matplotlib's labels (mathtext) and plain text (plain).

A unit is read as factors, not edited as a string: "mmol O2/m**3" is the
factors mmol, O2 and m to the power -3, and is written "mmol O<sub>2</sub>
m<sup>-3</sup>". A factor after a slash is a divisor, its power made
negative; the powers can be written m3, m^3, m**3 or m-3; a chemical formula
(O2, CO2, NO3, O_2) has its digits as subscripts; umol, ug and uatm are
written with µ; and every way of writing degrees Celsius is °C
(tidiers.celsius). A factor that is none of these (PSU, 1e-3, Chl, degrees_north)
is left as it is, so a unit OceanVal cannot read is shown as it was given. A few
units written in words (micro-mol, micromoles_per_kilogram) are written as
symbols.

A unit already written as HTML (with <sup> or <sub>) or mathtext (with $) is
returned as it is by html, and converted by mathtext and plain, so writing a
unit twice changes nothing.
"""

import re

# a factor with a power: letters (and µ, °, %), then the power, after ^ or not
_POWER = re.compile(r"^([A-Za-zµ°%]+)\^?([+-]?\d+(?:\.\d+)?)$")
# a chemical formula: elements, each with an optional count (O2, CO2, NO3, SiO2), or O_2
_FORMULA = re.compile(r"^(?:[A-Z][a-z]?\d*)+$")
_FORMULA_UNDERSCORE = re.compile(r"^([A-Z][A-Za-z]*)_(\d+)$")
_ELEMENTS = {
    "H", "He", "C", "N", "O", "F", "Na", "Mg", "Al", "Si", "P", "S", "Cl", "K", "Ca", "Fe", "Mn",
    "Cu", "Zn", "Co", "Ni", "Ba", "Sr", "I", "Br", "B", "Ar",
}
# the units written with µ, when given with a u
_MICRO = {"umol", "ug", "uatm", "uM", "um", "uL", "ul", "uE", "umolC"}
# units written out in words, as some datasets give them
_WORDS = {
    "micro-mol": "µmol",
    "micromol": "µmol",
    "micromole": "µmol",
    "micromoles": "µmol",
    "micromoles_per_kilogram": "µmol kg-1",
    "micromoles_per_liter": "µmol l-1",
    "micromoles_per_litre": "µmol l-1",
}
_SUPERSCRIPT = str.maketrans("0123456789-+.", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺·")
_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _is_formula(token):
    """Whether token is a chemical formula with a count in it (O2, CO2, NO3)."""
    if not _FORMULA.match(token) or not re.search(r"\d", token):
        return False
    return all(element in _ELEMENTS for element in re.findall(r"[A-Z][a-z]?", token))


def _factors(unit):
    """unit as a list of (base, power, subscript): power is a string ("-3"), or
    None for none; subscript is the formula's pieces [(text, count)], or None."""
    from oceanval.tidiers import celsius

    text = celsius(str(unit).strip()).replace("**", "^")
    text = " ".join(_WORDS.get(word, word) for word in text.split(" "))
    parts = text.split("/")
    factors = []
    for i, part in enumerate(parts):
        part = part.strip()
        divisor = i > 0
        grouped = divisor and part.startswith("(") and part.endswith(")")
        if grouped:
            part = part[1:-1]
        tokens = part.split()
        for j, token in enumerate(tokens):
            # after a slash only the first factor divides, unless they are in brackets: mmol/(m2 d)
            negate = divisor and (grouped or j == 0)
            factors.append(_factor(token, negate))
    # 1/m is m-1
    if len(factors) > 1 and factors[0] == ("1", None, None):
        factors = factors[1:]
    return factors


def _factor(token, negate):
    if token in _MICRO:
        token = "µ" + token[1:]
    underscore = _FORMULA_UNDERSCORE.match(token)
    if underscore and not negate:
        return (token, None, [(underscore.group(1), underscore.group(2))])
    if _is_formula(token) and not negate:
        return (token, None, re.findall(r"([A-Z][a-z]?)(\d*)", token))
    power = _POWER.match(token)
    if power:
        base, exponent = power.group(1), power.group(2).lstrip("+")
        if negate:
            exponent = exponent[1:] if exponent.startswith("-") else "-" + exponent
        return (base, None if exponent == "1" else exponent, None)
    if negate:
        return (token, "-1", None)
    return (token, None, None)


def _write(unit, sup, sub):
    if unit is None:
        return ""
    words = []
    for base, power, subscript in _factors(unit):
        if subscript is not None:
            words.append("".join(element + (sub(count) if count else "") for element, count in subscript))
        else:
            words.append(base + (sup(power) if power else ""))
    return " ".join(words)


def _from_html(text, sup, sub):
    text = re.sub(r"<sup>(.*?)</sup>", lambda m: sup(m.group(1)), text)
    return re.sub(r"<sub>(.*?)</sub>", lambda m: sub(m.group(1)), text)


def _written(text):
    return "<sup>" in text or "<sub>" in text


def html(unit):
    """unit as the report's text and tables write it, e.g. mmol O<sub>2</sub> m<sup>-3</sup>."""
    if unit is None:
        return ""
    text = str(unit)
    if _written(text) or "$" in text:
        return text
    return _write(text, lambda p: f"<sup>{p}</sup>", lambda c: f"<sub>{c}</sub>")


def mathtext(unit):
    """unit as matplotlib (and plotnine) labels write it, e.g. mmol O$_{2}$ m$^{-3}$."""
    if unit is None:
        return ""
    text = str(unit)
    if "$" in text:
        return text
    sup = lambda p: f"$^{{{p}}}$"
    sub = lambda c: f"$_{{{c}}}$"
    if _written(text):
        return _from_html(text, sup, sub)
    return _write(text, sup, sub)


def plain(unit):
    """unit in plain text, its powers and counts as Unicode, e.g. mmol O₂ m⁻³."""
    if unit is None:
        return ""
    text = str(unit)
    sup = lambda p: p.translate(_SUPERSCRIPT)
    sub = lambda c: c.translate(_SUBSCRIPT)
    if _written(text):
        return _from_html(text, sup, sub)
    if "$" in text:
        text = re.sub(r"\$\^\{(.*?)\}\$", lambda m: sup(m.group(1)), text)
        return re.sub(r"\$_\{(.*?)\}\$", lambda m: sub(m.group(1)), text)
    return _write(text, sup, sub)
