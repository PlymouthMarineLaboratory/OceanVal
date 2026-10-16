"""Download point observations from the ICES oceanographic database.

Talks to the public API behind https://ocean.ices.dk: an export job is
queued for each ICES dataset a recipe uses, polled until it finishes, and
its zipped ODV-style csv is read straight into a dataframe in oceanval's
point format. A recipe using more than one dataset gets them joined, leaving
out what a later dataset has at a station an earlier one already has.

With a data store (see oceanval.data_store), the exports are kept in it a
year at a time, as ICES gave them, and what it has is taken from it.
"""

import io
import time
import zipfile

import pandas as pd
import requests

from oceanval import data_store

API_BASE = "https://ocean.ices.dk/api"
POLL_INTERVAL = 5
TIMEOUT = 1800

DEPTH_CODE = "ADEPZZ01"
TIME_COLUMN = "yyyy-mm-ddThh:mm:ss.sss"
LON_COLUMN = "Longitude [degrees_east]"
LAT_COLUMN = "Latitude [degrees_north]"
POINT_COLUMNS = ["lon", "lat", "year", "month", "day", "depth", "observation"]


def _start_job(session, criteria):
    resp = session.post(
        f"{API_BASE}/Download/GetStationData",
        json={"criteria": criteria, "emailAddress": None},
        timeout=60,
    )
    resp.raise_for_status()
    job_id = resp.json().get("id")
    if not job_id:
        raise RuntimeError(f"The ICES API did not return a job id: {resp.text}")
    return job_id


def _wait_for_job(session, job_id):
    """Poll until the job finishes. Returns True if it produced a file, and
    False if ICES found no data for the criteria (status "NotFound")."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        resp = session.get(f"{API_BASE}/Download/GetStationDataStatus/{job_id}", timeout=60)
        resp.raise_for_status()
        body = resp.json()
        status = (body.get("status") or "").lower()
        if status == "completed":
            return True
        if status == "notfound":
            return False
        if status == "failed":
            raise RuntimeError(f"ICES export job {job_id} failed: {body.get('message', body)}")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError(f"Timed out after {TIMEOUT}s waiting for ICES export job {job_id}")


def _export_lines(zip_bytes):
    """The lines of the data csv in an ICES export zip, which also holds
    criteria and disclaimer files, without the block of "//" comment lines it
    opens with."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        member = [x for x in zf.namelist() if x.lower().endswith(".csv")][0]
        lines = zf.read(member).decode("utf-8", errors="replace").splitlines()
    return [x for x in lines if not x.startswith("//")]


def _read_export(zip_bytes):
    """The data csv from an ICES export zip."""
    lines = _export_lines(zip_bytes)
    if len(lines) == 0:
        return pd.DataFrame()
    return pd.read_csv(io.StringIO("\n".join(lines)), low_memory=False)


def _rows_by_year(zip_bytes, first_year, last_year):
    """An ICES export's rows by the year of their timestamp, as text, so they
    can be kept in the data store as they came. The year is read from the
    timestamp as written, as ICES picks the rows for dateFrom and dateTo."""
    lines = _export_lines(zip_bytes)
    if len(lines) == 0:
        return {}
    rows = pd.read_csv(io.StringIO("\n".join(lines)), dtype=str, keep_default_na=False)
    if TIME_COLUMN not in rows.columns:
        raise ValueError(f"The ICES export has no {TIME_COLUMN} column")
    year = rows[TIME_COLUMN].str.strip().str[:4]
    return {x: rows.loc[year == str(x)] for x in range(int(first_year), int(last_year) + 1)}


def _download_exports(wanted):
    """Download ICES exports by way of the data store: for each (dataset,
    first_year, last_year, box) wanted, its rows by year, or the exception
    that stopped it. Every export is asked for before any is waited on, so
    ICES prepares them side by side."""
    results = [None] * len(wanted)
    with requests.Session() as session:
        jobs = []
        for i, (dataset, first, last, box) in enumerate(wanted):
            print(f"Downloading ICES {dataset} data for {first}-{last}, lon {box[:2]}, lat {box[2:]}")
            try:
                jobs.append((i, _start_job(session, _criteria(dataset, first, last, box[:2], box[2:]))))
            except Exception as error:
                results[i] = error
        for i, job_id in jobs:
            dataset, first, last, _ = wanted[i]
            try:
                if not _wait_for_job(session, job_id):
                    print(f"ICES has no {dataset} data for {first}-{last} in this area")
                    results[i] = {}
                    continue
                resp = session.get(f"{API_BASE}/Download/GetStationDataFile/{job_id}", timeout=600)
                resp.raise_for_status()
                results[i] = _rows_by_year(resp.content, first, last)
            except Exception as error:
                results[i] = error
    return results


def _in_box(frames, lon_lim, lat_lim):
    """Rows of exports, as one, cut to the box lon_lim x lat_lim (edges in)."""
    frames = [x for x in frames if len(x) > 0]
    if len(frames) == 0:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)
    lon = pd.to_numeric(raw[LON_COLUMN], errors="coerce")
    lat = pd.to_numeric(raw[LAT_COLUMN], errors="coerce")
    inside = (
        (lon >= float(lon_lim[0])) & (lon <= float(lon_lim[1]))
        & (lat >= float(lat_lim[0])) & (lat <= float(lat_lim[1]))
    )
    return raw.loc[inside].reset_index(drop=True)


def _value_and_flag(raw, code):
    """Value column and its ODV quality flag column for an ICES parameter code.

    Headers look like "Temperature (TEMPPR01_UPAA) [degC]", with the flag in
    "QV:ODV:Temperature (TEMPPR01_UPAA) [degC]". The unit code after the
    parameter code can vary, so only the parameter code is matched.
    """
    matches = [x for x in raw.columns if f"({code}_" in x]
    values = [x for x in matches if not x.startswith("QV:")]
    if len(values) != 1:
        raise ValueError(f"Expected one ICES column for {code}, found {values}")
    value = values[0]
    flag = f"QV:ODV:{value}"
    if flag not in raw.columns:
        raise ValueError(f"No quality flag column found for {value}")
    return value, flag


FULL_DATE = r"^\d{4}-\d{2}-\d{2}"
HOUR_24 = r"^\d{4}-\d{2}-\d{2}T24(?::00(?::00(?:\.0+)?)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?$"


def _parse_times(stamps):
    """Parse ICES timestamps, returning the times (NaT where a row has to be
    dropped) and how many were read as the next day, had no full date, or
    could not be read.

    ICES exports mix timestamps with different UTC offsets (and some with
    none at all) in the same file, which pandas otherwise refuses to parse
    into one column. Some are stamped hour 24 ("1981-04-27T24"), which
    ISO 8601 makes 00:00 of the next day but pandas refuses, and a few give
    only a year and month ("1985-05"), which pandas would silently make the
    1st of the month, so they are dropped rather than given a day.
    """
    stamps = stamps.astype(str).str.strip()
    full = stamps.str.match(FULL_DATE)
    hour_24 = stamps.str.match(HOUR_24)
    stamps = stamps.where(~hour_24, stamps.str.replace("T24", "T00", n=1, regex=False))
    times = pd.to_datetime(stamps.where(full), format="ISO8601", utc=True, errors="coerce")
    times = times.where(~hour_24, times + pd.Timedelta(days=1))
    counts = {
        "next_day": int(hour_24.sum()),
        "no_full_date": int((~full).sum()),
        "unreadable": int((full & times.isna()).sum()),
    }
    return times, counts


def to_oceanval_format(raw, parameter_code):
    """Convert an ICES export to oceanval point columns, keeping only rows
    where both the observation and depth quality flags are 0 (good).

    Only the date of each observation is kept. Hour 24 is read as 00:00 of
    the next day, and rows whose timestamp has no day (year and month, or
    year, only) or cannot be read are dropped, saying how many.

    Many ICES analytes are split across several method-specific parameter
    codes (filtered/unfiltered, autoanalyser/manual, ...), so a dataset can
    genuinely have no column for one particular code even though it has
    data for that analyte under a different one. That's treated as "no
    observations", not an error - unlike an entirely missing depth column,
    which would point to something actually wrong with the export.
    """
    if len(raw) == 0:
        return pd.DataFrame(columns=POINT_COLUMNS)

    if not any(f"({parameter_code}_" in x for x in raw.columns):
        return pd.DataFrame(columns=POINT_COLUMNS)

    value, value_flag = _value_and_flag(raw, parameter_code)
    depth, depth_flag = _value_and_flag(raw, DEPTH_CODE)

    df = raw.loc[(raw[value_flag] == 0) & (raw[depth_flag] == 0)]
    df = df.dropna(subset=[value, depth])
    times, counts = _parse_times(df[TIME_COLUMN])
    if any(counts.values()):
        print(
            f"ICES times: {counts['next_day']} stamped 24:00 read as 00:00 of the next day; "
            f"{counts['no_full_date']} without a full date and {counts['unreadable']} "
            "that could not be read were dropped"
        )
    df = df.loc[times.notna()]
    times = times.loc[times.notna()]

    return pd.DataFrame(
        {
            "lon": df[LON_COLUMN].astype(float),
            "lat": df[LAT_COLUMN].astype(float),
            "year": times.dt.year,
            "month": times.dt.month,
            "day": times.dt.day,
            "depth": df[depth].astype(float),
            "observation": df[value].astype(float),
        }
    ).reset_index(drop=True)


# stations are told apart by their date and their position to this many
# decimal places of a degree (about 1 km)
STATION_DECIMALS = 2


def _station_keys(df):
    return pd.MultiIndex.from_arrays(
        [
            df["lon"].astype(float).round(STATION_DECIMALS),
            df["lat"].astype(float).round(STATION_DECIMALS),
            df["year"].astype(int),
            df["month"].astype(int),
            df["day"].astype(int),
        ]
    )


def without_repeated_stations(frames):
    """Join the observations of several ICES datasets, given as (dataset,
    dataframe) pairs in order of preference, leaving out those of a dataset
    at a station one before it already has.

    Bottles are fired on CTD casts, so the same cast can be in both the CTD
    and the bottle dataset, with the same values, under a different cruise
    or station name. So a station is its date and position, which keeps a
    cast from being counted twice.
    """
    kept = []
    seen = None
    for i, (dataset, df) in enumerate(frames):
        if len(df) == 0:
            continue
        keys = _station_keys(df)
        if seen is not None:
            repeated = keys.isin(seen)
            if repeated.any():
                earlier = " and ".join(x for x, _ in frames[:i])
                print(
                    f"{int(repeated.sum())} ICES {dataset} observations at stations already "
                    f"in the {earlier} data were left out"
                )
            df = df.loc[~repeated]
            keys = keys[~repeated]
        kept.append(df)
        seen = keys if seen is None else seen.append(keys)
    if len(kept) == 0:
        return pd.DataFrame(columns=POINT_COLUMNS)
    return pd.concat(kept, ignore_index=True)


def _criteria(dataset, start_year, end_year, lon_lim, lat_lim):
    return {
        "dataset": dataset,
        "dateFrom": f"{int(start_year)}-01-01",
        "dateTo": f"{int(end_year)}-12-31",
        "longitudeWest": float(lon_lim[0]),
        "longitudeEast": float(lon_lim[1]),
        "latitudeSouth": float(lat_lim[0]),
        "latitudeNorth": float(lat_lim[1]),
        "delimiter": ",",
        "useCompactFormat": False,
        "useISO8601": True,
        "includeOriginalVariables": False,
    }


def download_point_data(recipe, start_year, end_year, lon_lim, lat_lim):
    """Download an ICES recipe's observations for the given years and box.

    recipe is the dict stored by add_point_comparison, with the ICES
    "parameter" code and "dataset" code to request. The dataset can be a
    list of codes in order of preference: one export is asked for from
    each, and they are joined by without_repeated_stations.
    """
    # the comparison's start/end can fall outside the simulation's years
    if start_year > end_year:
        return pd.DataFrame(columns=POINT_COLUMNS)
    datasets = recipe["dataset"]
    if isinstance(datasets, str):
        datasets = [datasets]

    # by way of the data store, if there is one: what it has of these years
    # and this area is taken from it, and what is downloaded is kept in it
    stored = data_store.ices(datasets, start_year, end_year, lon_lim, lat_lim, _download_exports)
    if stored is not None:
        frames = []
        for dataset in datasets:
            df = to_oceanval_format(_in_box(stored[dataset], lon_lim, lat_lim), recipe["parameter"])
            if len(datasets) > 1:
                print(f"{len(df)} ICES {dataset} observations passed quality control")
            frames.append((dataset, df))
        df = without_repeated_stations(frames)
        print(f"{len(df)} ICES observations passed quality control")
        return df

    print(
        f"Downloading ICES {' and '.join(datasets)} data ({recipe['parameter']}) for "
        f"{int(start_year)}-{int(end_year)}, lon {lon_lim}, lat {lat_lim}"
    )
    frames = []
    with requests.Session() as session:
        # every export is asked for before waiting on any, so ICES
        # prepares them side by side
        jobs = [
            (dataset, _start_job(session, _criteria(dataset, start_year, end_year, lon_lim, lat_lim)))
            for dataset in datasets
        ]
        for dataset, job_id in jobs:
            if not _wait_for_job(session, job_id):
                print(f"ICES has no {dataset} data for these years and this area")
                continue
            resp = session.get(f"{API_BASE}/Download/GetStationDataFile/{job_id}", timeout=600)
            resp.raise_for_status()
            df = to_oceanval_format(_read_export(resp.content), recipe["parameter"])
            if len(datasets) > 1:
                print(f"{len(df)} ICES {dataset} observations passed quality control")
            frames.append((dataset, df))

    df = without_repeated_stations(frames)
    print(f"Downloaded {len(df)} ICES observations that passed quality control")
    return df
