"""The North Sea Benthos Survey (NSBS): point observations of the biomass of the
macrobenthos (the infauna of the sea bed caught on a 1 mm sieve) at stations across
the North Sea, sampled in April and May 1986 by the ICES Benthos Ecology Working Group.

Craeymeersch, J., Kingston, P., Rachor, E., Duineveld, G., Heip, C. and Vanden Berghe,
E. (1986), North Sea Benthos Survey, https://marineinfo.org/doc/dataset/67, published by
the Flanders Marine Institute (VLIZ) as a Darwin Core archive on its IPT
(https://ipt.vliz.be/eurobis/resource?r=nsbs). Heip et al. (1992), ICES Journal of Marine
Science 49: 13-22, give its biomass.

The archive is one zip of about 440 KB. Its event table holds each sample's place and
date, and its extended measurement-or-fact table the biomass of the sample, as ash-free
dry weight in g/m2, of each of its major groups (polychaetes, molluscs, echinoderms,
crustaceans and other animals). A sample's biomass is the sum of its groups. It is
turned into carbon, in mg C/m2, as models give the biomass of their benthos, by taking
organic carbon as half the ash-free dry weight (CARBON_PER_AFDW), the usual approximation.

Only the samples of the 1986 survey are kept. The archive also holds samples of MAFF's
1980-1985 surveys, but stamps every one of them 1 January 1985, so when in the year they
were taken is not known. A sample dated over a span of days (some are given as 1 April to
20 May 1986) is dated at its middle.

The observations are of the sea bed, so they have no depth: they are matched with a
model's benthic variables, which have no vertical levels.

With a data store (see oceanval.data_store), the biomass is kept in it as the archive is
first downloaded, so it is downloaded once. Without one, it is downloaded once per process.
"""

import io
import os
import shutil
import tempfile
import threading
import urllib.request
import zipfile

import numpy as np
import pandas as pd

from oceanval import data_store

POINT_COLUMNS = ["lon", "lat", "year", "month", "day", "observation"]

ARCHIVE_URL = "https://ipt.vliz.be/eurobis/archive.do?r=nsbs&v=1.0"
RESOURCE_URL = "https://ipt.vliz.be/eurobis/resource?r=nsbs"
API_BASE = ARCHIVE_URL
TIMEOUT = 300

EVENTS = "event.txt"
MEASUREMENTS = "extendedmeasurementorfact.txt"
# the measurement type of the biomass, and its units, as the archive gives them
BIOMASS = "Biomass"
BIOMASS_UNITS = "g/m2"
# the grams of organic carbon in a gram of ash-free dry weight
CARBON_PER_AFDW = 0.5
# the survey: the samples of other years (MAFF's) have no dates that can be used
SURVEY_YEAR = 1986

# the variables of the archive, by the name OceanVal gives them
VARIABLES = {"macrobenthos": BIOMASS}
YEARS = {"macrobenthos": (SURVEY_YEAR, SURVEY_YEAR)}
UNITS = {"macrobenthos": "mg C/m2"}

# what has been read without a store, by process, so a run downloads the archive once
_read = {}
_lock = threading.Lock()


def _fetch(url, path):
    """Download url to path."""
    request = urllib.request.Request(url, headers={"User-Agent": "oceanval"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(path, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)


def _table(archive, name):
    """One of the archive's tab-separated tables, every column as text."""
    try:
        with archive.open(name) as source:
            return pd.read_csv(
                io.TextIOWrapper(source, encoding="utf-8"), sep="\t", dtype=str, keep_default_na=False
            )
    except KeyError:
        raise ValueError(f"the North Sea Benthos Survey archive has no {name}") from None


def _dates(text):
    """Each event date as a pandas Timestamp, NaT where it cannot be read. A span of
    days ("1986-04-01/1986-05-20") is dated at its middle."""
    parts = text.str.split("/", n=1, expand=True)
    first = pd.to_datetime(parts[0].str.strip(), format="%Y-%m-%d", errors="coerce")
    if parts.shape[1] == 1:
        return first
    spanned = parts[1].notna()
    last = pd.to_datetime(parts[1].fillna("").str.strip(), format="%Y-%m-%d", errors="coerce")
    # a span that cannot be read, or ends before it starts, has no date
    middle = (first + (last - first) / 2).where(last >= first)
    return first.where(~spanned, middle)


def extract(path):
    """The archive at path, as {"macrobenthos": DataFrame of POINT_COLUMNS}: the
    biomass of each sample of the 1986 survey, in mg C/m2."""
    with zipfile.ZipFile(path) as archive:
        events = _table(archive, EVENTS)
        facts = _table(archive, MEASUREMENTS)
    for table, needed in (
        (events, ("eventID", "eventDate", "decimalLatitude", "decimalLongitude")),
        (facts, ("id", "measurementType", "measurementValue", "measurementUnit")),
    ):
        missing = [x for x in needed if x not in table.columns]
        if missing:
            raise ValueError(f"the North Sea Benthos Survey archive has no column {', '.join(missing)}")

    biomass = facts[(facts["measurementType"] == BIOMASS) & (facts["measurementUnit"] == BIOMASS_UNITS)]
    value = pd.to_numeric(biomass["measurementValue"], errors="coerce")
    biomass = biomass.assign(value=value)[value.notna() & (value >= 0)]
    # each sample's biomass: the sum of its groups'
    total = biomass.groupby("id")["value"].sum()

    events = events.drop_duplicates("eventID").set_index("eventID")
    events = events.loc[events.index.intersection(total.index)]
    when = _dates(events["eventDate"])
    lat = pd.to_numeric(events["decimalLatitude"], errors="coerce")
    lon = pd.to_numeric(events["decimalLongitude"], errors="coerce")
    keep = (when.dt.year == SURVEY_YEAR) & lat.between(-90, 90) & lon.between(-180, 180)
    events, when = events[keep], when[keep]
    observation = total.loc[events.index].to_numpy(float) * CARBON_PER_AFDW * 1000
    out = pd.DataFrame({
        "lon": lon[keep].to_numpy(float),
        "lat": lat[keep].to_numpy(float),
        "year": when.dt.year.to_numpy(int),
        "month": when.dt.month.to_numpy(int),
        "day": when.dt.day.to_numpy(int),
        "observation": observation,
    })
    out = out[np.isfinite(out["observation"])].reset_index(drop=True)
    return {"macrobenthos": out[POINT_COLUMNS]}


def download_all(variables=None):
    """Download the archive and read it, the file removed after. Returns
    ({variable: DataFrame}, url). The download callback of data_store.nsbs."""
    folder = tempfile.mkdtemp(prefix="oceanval_nsbs_")
    try:
        print("Downloading the North Sea Benthos Survey (0.4 MB)")
        path = os.path.join(folder, "nsbs.zip")
        _fetch(ARCHIVE_URL, path)
        found = extract(path)
        if variables is not None:
            found = {x: found[x] for x in variables}
        return found, ARCHIVE_URL
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _cut(df, lon_lim, lat_lim):
    """The rows of df in the box (None for no limit)."""
    keep = pd.Series(True, index=df.index)
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
    """An nsbs recipe's observations for the years and box, as ices.download_point_data
    gives ICES's. recipe is the dict add_point_comparison keeps, with its "variable"."""
    variable = recipe["variable"]
    if start_year > end_year:
        return pd.DataFrame(columns=POINT_COLUMNS)
    stored = data_store.nsbs([variable], download_all)
    df = stored[variable] if stored is not None else _without_store(variable)
    df = df[(df["year"] >= start_year) & (df["year"] <= end_year)]
    df = _cut(df, lon_lim, lat_lim)
    print(f"{len(df)} North Sea Benthos Survey {variable} observations")
    return df
