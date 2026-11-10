"""The Hansell et al. DOM compilation's point recipe: its workbook read, without the network.

The workbook is written here as Excel writes one (a sheet of inline shared strings and
numbers, a header row and a units row), since openpyxl is not a dependency.
"""

import io
import zipfile
from xml.sax.saxutils import escape

import gsw
import numpy as np
import pandas as pd
import pytest

from oceanval import hansell
from oceanval.parsers import find_recipe

from test_data_store import store  # noqa: F401 - the fixture

HEADER = [
    "EXPOCODE", "CRUISE", "STATION", "CAST", "BOTTLE", "BOTTLE_FLAG_W", "DATE", "LATITUDE",
    "LONGITUDE", "BOTTOM DEPTH", "CTD PRESSURE", "DOC", "DOC_FLAG_W", "POC", "POC_FLAG_W",
]
UNITS = [
    "", "", "", "", "", "", "yyyymmdd", "DEGREES NORTH", "DEGREES EAST", "METERS", "DBAR",
    "UMOL/KG", "", "UMOL/KG", "",
]


def _column_letters(number):
    letters = ""
    number += 1
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _workbook(path, rows):
    """Write rows (lists of header, units, then data values) as an xlsx at path,
    with its strings in a shared strings table as Excel writes them."""
    strings = []

    def cell(reference, value):
        if value is None or value == "":
            return ""
        if isinstance(value, str):
            if value not in strings:
                strings.append(value)
            return f'<c r="{reference}" t="s"><v>{strings.index(value)}</v></c>'
        return f'<c r="{reference}"><v>{value}</v></c>'

    body = []
    for row_number, values in enumerate(rows, start=1):
        cells = "".join(cell(f"{_column_letters(i)}{row_number}", value) for i, value in enumerate(values))
        body.append(f'<row r="{row_number}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(body) + "</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="MergedBasins_V5" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    shared = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{escape(x)}</t></si>" for x in strings)
        + "</sst>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", shared)


def _data(date, lat, lon, pressure, doc, doc_flag, poc, poc_flag, flag=2):
    row = [""] * len(HEADER)
    row[HEADER.index("EXPOCODE")] = "33RO20110926"
    row[HEADER.index("DATE")] = date
    row[HEADER.index("LATITUDE")] = lat
    row[HEADER.index("LONGITUDE")] = lon
    row[HEADER.index("CTD PRESSURE")] = pressure
    row[HEADER.index("DOC")] = doc
    row[HEADER.index("DOC_FLAG_W")] = doc_flag
    row[HEADER.index("POC")] = poc
    row[HEADER.index("POC_FLAG_W")] = poc_flag
    return row


@pytest.fixture
def workbook(tmp_path):
    rows = [
        HEADER,
        UNITS,
        # good DOC and POC at 1000 dbar, 30 N
        _data(20110928, 30.0, -40.0, 1000.0, 60.0, 2, 3.0, 2),
        # DOC flagged 1 (not good), POC good
        _data(20110928, 30.0, -40.0, 1000.0, 58.0, 1, 3.5, 2),
        # DOC of zero is no measurement
        _data(20110928, 31.0, -41.0, 500.0, 0.0, 2, -999, 9),
        # a bad date (7 digits) and a sentinel date are dropped
        _data(2013031, 10.0, 20.0, 100.0, 70.0, 2, 1.0, 2),
        _data(-999, 10.0, 20.0, 100.0, 70.0, 2, 1.0, 2),
        # a negative pressure is dropped
        _data(20110928, 10.0, 20.0, -999, 70.0, 2, 1.0, 2),
        # a longitude past 180 is wrapped
        _data(20120101, -20.0, 213.6, 50.0, 75.0, 2, -999, 9),
        # an exact duplicate of the first row is kept once
        _data(20110928, 30.0, -40.0, 1000.0, 60.0, 2, 3.0, 2),
    ]
    path = tmp_path / "hansell.xlsx"
    _workbook(path, rows)
    return path


def test_extract_keeps_good_values_only(workbook):
    found = hansell.extract(workbook)

    doc = found["doc"]
    assert list(doc.columns) == hansell.POINT_COLUMNS
    # kept: the good DOC at 1000 dbar (its duplicate once) and the good one at 50 dbar.
    # Not kept: the flag-1 DOC, the zero, the 7-digit and -999 dates, the negative pressure
    assert sorted(doc["observation"]) == [60.0, 75.0]
    assert (doc["observation"] > 0).all()

    # POC keeps its own flag: the row whose DOC is flag 1 still has good POC
    poc = found["poc"]
    assert sorted(poc["observation"]) == [3.0, 3.5]


def test_extract_converts_pressure_to_depth_with_gsw(workbook):
    doc = hansell.extract(workbook)["doc"]
    row = doc[(doc["lon"] == -40.0) & (doc["year"] == 2011)].iloc[0]
    expected = -gsw.z_from_p(1000.0, 30.0)
    assert row["depth"] == pytest.approx(expected)
    assert row["depth"] > 0


def test_extract_wraps_longitude_and_keeps_date_parts(workbook):
    doc = hansell.extract(workbook)["doc"]
    wrapped = doc[doc["year"] == 2012].iloc[0]
    assert wrapped["lon"] == pytest.approx(213.6 - 360)
    assert (wrapped["year"], wrapped["month"], wrapped["day"]) == (2012, 1, 1)


def test_extract_rejects_a_workbook_without_the_columns(tmp_path):
    path = tmp_path / "short.xlsx"
    _workbook(path, [["EXPOCODE", "DATE"], ["", "yyyymmdd"], ["x", 20110928]])
    with pytest.raises(ValueError, match="no column"):
        hansell.extract(path)


def test_download_point_data_cuts_years_and_box(monkeypatch, workbook):
    frames = hansell.extract(workbook)
    monkeypatch.setattr(hansell, "_without_store", lambda variable: frames[variable])
    monkeypatch.setattr(hansell.data_store, "hansell", lambda variables, download: None)
    recipe = {"kind": "hansell", "variable": "doc"}

    # the 2011 row only: the 2012 one is out of the years
    everything = hansell.download_point_data(recipe, 2000, 2011, None, None)
    assert list(everything["year"]) == [2011]

    # the 2011 row is in the box; the 2012 one (lon 213.6 wrapped to -146.4) is not
    boxed = hansell.download_point_data(recipe, 1990, 2030, (-50, -30), (25, 35))
    assert len(boxed) == 1 and boxed["lat"].between(25, 35).all()

    assert hansell.download_point_data(recipe, 2021, 2010, None, None).empty


def test_recipes_resolve_to_point_recipes():
    doc = find_recipe({"doc": "hansell"}, cwd="/nonexistent")
    assert doc["point"] is True
    assert doc["builtin"] == "hansell"
    assert doc["hansell_variable"] == "doc"
    assert doc["source"] == "Hansell"
    assert "10.25921/s4f4-ye35" in doc["source_info"]

    poc = find_recipe({"poc": "hansell"}, cwd="/nonexistent")
    assert poc["hansell_variable"] == "poc"
    assert poc["short_name"] == "POC"


def test_poc_is_only_for_known_variables():
    with pytest.raises(ValueError):
        find_recipe({"nitrate": "hansell"}, cwd="/nonexistent")


def test_poc_identified_in_nemo_pisces_output():
    from oceanval import variable_identify

    contents = pd.DataFrame({
        "variable": ["POC", "GOC", "DOC"],
        "unit": ["mmol/m3", "mmol/m3", "mmol/m3"],
    })
    model_variable, how = variable_identify.identify("poc", contents)
    assert model_variable == "POC+GOC"
    assert how is not None


def test_store_piece_names_are_point_kinds():
    from oceanval import data_store

    assert "hansell" in data_store.POINT_KINDS
    assert data_store.PIECE_NAME.match("hansell/" + "0" * 32 + ".csv.gz")


def test_the_store_keeps_the_workbook_once(store, workbook, monkeypatch):
    """With a data store, the workbook is downloaded once and read back from it."""
    downloads = []

    def download(variables=None):
        downloads.append(list(variables) if variables else None)
        return hansell.extract(workbook, variables), "https://www.ncei.noaa.gov/x.xlsx"

    monkeypatch.setattr(hansell, "download_all", download)
    store("always")
    recipe = {"kind": "hansell", "variable": "doc"}

    first = hansell.download_point_data(recipe, 1990, 2030, None, None)
    second = hansell.download_point_data(recipe, 1990, 2030, None, None)

    assert len(downloads) == 1
    assert sorted(first["observation"]) == sorted(second["observation"]) == [60.0, 75.0]
