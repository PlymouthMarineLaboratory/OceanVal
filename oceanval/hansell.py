"""The Hansell et al. dissolved organic matter compilation: point observations of
dissolved organic carbon (DOC) and particulate organic carbon (POC) from ship bottle samples.

Hansell, Carlson, Amon, Álvarez-Salgado, Yamashita, Romera-Castillo and Bif
(2021), Compilation of dissolved organic matter (DOM) data obtained from global
ocean observations from 1994 to 2021, Version 2 (NCEI Accession 0227166),
https://doi.org/10.25921/s4f4-ye35.

The whole compilation is one 104 MB Excel workbook: one sheet of 268,324 bottle
rows from 223 cruises, its first row the headers and its second the units. The
sheet is 786 MB uncompressed, so it is streamed row by row, keeping only the
columns used, rather than loaded with pandas or openpyxl (neither a dependency).

A value is kept when its own WOCE flag is 2 (good), it is above zero, its date is
a whole yyyymmdd and its pressure is not negative. Pressure is converted to depth
with gsw (TEOS-10), at each bottle's latitude. Longitudes above 180 are wrapped.

With a data store (see oceanval.data_store), each variable is kept in it as the
workbook is first downloaded, so it is downloaded once. Without one, it is
downloaded once per process.
"""

import os
import re
import shutil
import tempfile
import threading
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import gsw
import numpy as np
import pandas as pd

from oceanval import data_store

POINT_COLUMNS = ["lon", "lat", "year", "month", "day", "depth", "observation"]

XLSX_URL = "https://www.ncei.noaa.gov/data/oceans/ncei/ocads/data/0227166/All_Basins_Data_Merged_Hansell_2022.xlsx"
METADATA_URL = "https://www.ncei.noaa.gov/data/oceans/ncei/ocads/metadata/0227166.html"
API_BASE = XLSX_URL
TIMEOUT = 900
# a WOCE flag of 2 is a good value
GOOD_FLAG = 2
SHEET = "xl/worksheets/sheet1.xml"
STRINGS = "xl/sharedStrings.xml"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# each variable's value column and its flag column in the sheet
VARIABLES = {
    "doc": ("DOC", "DOC_FLAG_W"),
    "poc": ("POC", "POC_FLAG_W"),
}
# the years each variable's good values are in
YEARS = {
    "doc": (1994, 2021),
    "poc": (1995, 2018),
}
# the units, as the sheet's second row gives them for DOC and POC
UNITS = {
    "doc": "µmol/kg",
    "poc": "µmol/kg",
}
# the sheet's columns the readers need, by name
NEEDED = ("DATE", "LATITUDE", "LONGITUDE", "CTD PRESSURE") + tuple(
    column for pair in VARIABLES.values() for column in pair
)

# what has been read without a store, by process, so a run downloads the file once
_read = {}
_lock = threading.Lock()


def _fetch(url, path):
    """Download url to path."""
    request = urllib.request.Request(url, headers={"User-Agent": "oceanval"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(path, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)


def _column_index(reference):
    """The zero-based column number of a cell reference such as "AB12"."""
    n = 0
    for letter in re.match(r"[A-Z]+", reference).group():
        n = n * 26 + ord(letter) - 64
    return n - 1


def _strings(archive):
    """The workbook's shared strings, in order."""
    found = []
    with archive.open(STRINGS) as source:
        for _, element in ET.iterparse(source):
            if element.tag == NS + "si":
                found.append("".join(text.text or "" for text in element.iter(NS + "t")))
                element.clear()
    return found


def _rows(path):
    """Yield each row of the sheet as {column number: value}, streaming the sheet
    so that only one row's elements are held at a time. The first two rows
    (headers and units) are yielded too."""
    with zipfile.ZipFile(path) as archive:
        strings = _strings(archive)
        with archive.open(SHEET) as source:
            for _, element in ET.iterparse(source):
                if element.tag != NS + "row":
                    continue
                row = {}
                for cell in element.findall(NS + "c"):
                    value = cell.find(NS + "v")
                    if value is None:
                        continue
                    kind = cell.get("t")
                    if kind == "s":
                        row[_column_index(cell.get("r"))] = strings[int(value.text)]
                    elif kind in (None, "n"):
                        row[_column_index(cell.get("r"))] = float(value.text)
                element.clear()
                yield row


def _columns(header):
    """The column number of each NEEDED name in the header row."""
    found = {}
    for number, name in header.items():
        if name in NEEDED:
            found[name] = number
    missing = [name for name in NEEDED if name not in found]
    if missing:
        raise ValueError(f"the Hansell workbook has no column {', '.join(missing)}")
    return found


def _read_sheet(path):
    """The needed columns of every data row of the workbook at path, as a DataFrame
    with one column per NEEDED name (NaN where a cell is empty)."""
    rows = _rows(path)
    header = next(rows)
    next(rows)  # the units row
    numbers = _columns(header)
    records = []
    for row in rows:
        records.append({name: row.get(number, np.nan) for name, number in numbers.items()})
    return pd.DataFrame.from_records(records)


def _depth(pressure, latitude):
    """Depth in metres, positive down, of pressure in dbar at latitude, by gsw."""
    return -gsw.z_from_p(pressure, latitude)


def extract(path, variables=None):
    """Each of variables (all of VARIABLES by default) in the Hansell workbook at
    path, as {variable: DataFrame of POINT_COLUMNS}."""
    variables = list(VARIABLES) if variables is None else list(variables)
    df = _read_sheet(path)
    date = pd.to_numeric(df["DATE"], errors="coerce")
    year = (date // 10000).astype("Int64")
    month = ((date // 100) % 100).astype("Int64")
    day = (date % 100).astype("Int64")
    dated = date.between(19000101, 20301231) & (month.between(1, 12)) & (day.between(1, 31))
    lat = pd.to_numeric(df["LATITUDE"], errors="coerce")
    lon = pd.to_numeric(df["LONGITUDE"], errors="coerce")
    pressure = pd.to_numeric(df["CTD PRESSURE"], errors="coerce")
    placed = dated & lat.between(-90, 90) & lon.between(-360, 360) & (pressure >= 0)
    depth = pd.Series(np.nan, index=df.index)
    depth[placed] = _depth(pressure[placed].to_numpy(float), lat[placed].to_numpy(float))
    found = {}
    for variable in variables:
        value_column, flag_column = VARIABLES[variable]
        value = pd.to_numeric(df[value_column], errors="coerce")
        flag = pd.to_numeric(df[flag_column], errors="coerce")
        keep = placed & (flag == GOOD_FLAG) & value.notna() & (value > 0)
        out = pd.DataFrame({
            "lon": lon[keep].to_numpy(float),
            "lat": lat[keep].to_numpy(float),
            "year": year[keep].astype(int).to_numpy(),
            "month": month[keep].astype(int).to_numpy(),
            "day": day[keep].astype(int).to_numpy(),
            "depth": depth[keep].to_numpy(float),
            "observation": value[keep].to_numpy(float),
        })
        out["lon"] = out["lon"].where(out["lon"] <= 180, out["lon"] - 360)
        out = out.drop_duplicates().reset_index(drop=True)
        found[variable] = out[POINT_COLUMNS]
    return found


def download_all(variables=None):
    """Download the workbook and read variables from it (all of VARIABLES by
    default), the file removed after. Returns ({variable: DataFrame}, url).
    The download callback of data_store.hansell."""
    folder = tempfile.mkdtemp(prefix="oceanval_hansell_")
    try:
        print("Downloading the Hansell et al. DOM compilation (104 MB)")
        path = os.path.join(folder, "hansell.xlsx")
        _fetch(XLSX_URL, path)
        return extract(path, variables), XLSX_URL
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _cut(df, start_year, end_year, lon_lim, lat_lim):
    """The rows of df in the years and the box (None for no limit)."""
    keep = (df["year"] >= start_year) & (df["year"] <= end_year)
    if lon_lim is not None:
        keep &= (df["lon"] >= lon_lim[0]) & (df["lon"] <= lon_lim[1])
    if lat_lim is not None:
        keep &= (df["lat"] >= lat_lim[0]) & (df["lat"] <= lat_lim[1])
    return df[keep].reset_index(drop=True)


def _without_store(variable):
    """The variable's rows, downloaded once per process when there is no store."""
    with _lock:
        if variable not in _read:
            frames, _ = download_all()
            _read.update(frames)
        return _read[variable]


def download_point_data(recipe, start_year, end_year, lon_lim, lat_lim):
    """A hansell recipe's observations for the years and box, as ices.download_point_data
    gives ICES's. recipe is the dict add_point_comparison keeps, with its "variable"."""
    variable = recipe["variable"]
    if start_year > end_year:
        return pd.DataFrame(columns=POINT_COLUMNS)
    stored = data_store.hansell([variable], download_all)
    df = stored[variable] if stored is not None else _without_store(variable)
    df = _cut(df, start_year, end_year, lon_lim, lat_lim)
    print(f"{len(df)} Hansell {variable} observations passed quality control")
    return df
