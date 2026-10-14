"""The ICES point recipe's parsing of export files, without the network.

The headers and file layout here are copied from real ICES CTD exports.
"""

import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from oceanval import ices
from oceanval.ices import (
    POINT_COLUMNS,
    _read_export,
    download_point_data,
    to_oceanval_format,
    without_repeated_stations,
)

TEMP = "Temperature (TEMPPR01_UPAA) [degC]"
DEPTH = "Depth (ADEPZZ01_ULAA) [m]"

HEADER = [
    "Cruise",
    "Station",
    "Type",
    "yyyy-mm-ddThh:mm:ss.sss",
    "Longitude [degrees_east]",
    "Latitude [degrees_north]",
    "Bot. Depth [m]",
    DEPTH,
    f"QV:ODV:{DEPTH}",
    TEMP,
    f"QV:ODV:{TEMP}",
]

PREAMBLE = [
    "//SDN_parameter_mapping",
    "//<subject>SDN:LOCAL:Depth (ADEPZZ01_ULAA)</subject><object>SDN:P01::ADEPZZ01</object><units>SDN:P06::ULAA</units>",
    "//<subject>SDN:LOCAL:Temperature (TEMPPR01_UPAA)</subject><object>SDN:P01::TEMPPR01</object><units>SDN:P06::UPAA</units>",
]


def _raw(rows):
    return pd.DataFrame(rows, columns=HEADER)


def _row(time="2008-03-04T13:38Z", depth=1.0, depth_flag=0, temp=8.5, temp_flag=0):
    return ["26DA", "0003", "*", time, 3.6853, 54.8707, 44, depth, depth_flag, temp, temp_flag]


def _export_zip(lines):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("abc.csv", "\n".join(lines))
        zf.writestr("abc_Criteria.json", "{}")
        zf.writestr("abc_Disclaimer.txt", "disclaimer")
    return buffer.getvalue()


def test_columns_are_renamed_to_oceanval_point_columns():
    df = to_oceanval_format(_raw([_row()]), "TEMPPR01")

    assert list(df.columns) == POINT_COLUMNS
    row = df.iloc[0]
    assert (row.lon, row.lat) == (3.6853, 54.8707)
    assert (row.year, row.month, row.day) == (2008, 3, 4)
    assert row.depth == 1.0
    assert row.observation == 8.5


def test_times_with_and_without_seconds_are_parsed():
    df = to_oceanval_format(
        _raw([_row(time="2008-01-02T03:04Z"), _row(time="2008-12-31T23:59:59.500Z")]),
        "TEMPPR01",
    )

    assert df[["year", "month", "day"]].values.tolist() == [[2008, 1, 2], [2008, 12, 31]]


def test_times_with_different_utc_offsets_in_the_same_export_are_parsed():
    # real ICES exports mix "Z" timestamps with explicit non-zero offsets
    # from different contributing institutes in the same file.
    df = to_oceanval_format(
        _raw([_row(time="2008-01-02T03:04:00Z"), _row(time="2008-06-15T10:00:00+01:00")]),
        "TEMPPR01",
    )

    assert df[["year", "month", "day"]].values.tolist() == [[2008, 1, 2], [2008, 6, 15]]


@pytest.mark.parametrize(
    "time", ["1981-04-27T24", "1981-04-27T24:00", "1981-04-27T24:00:00Z", "1981-04-27T24:00-01:00"]
)
def test_hour_24_is_read_as_the_next_day(time):
    # older ICES cruises stamp the end of a day as hour 24, which pandas
    # refuses to parse
    df = to_oceanval_format(_raw([_row(time=time)]), "TEMPPR01")

    assert df[["year", "month", "day"]].values.tolist() == [[1981, 4, 28]]


def test_hour_24_on_the_last_day_of_a_year_is_the_next_year():
    df = to_oceanval_format(_raw([_row(time="2008-12-31T24")]), "TEMPPR01")

    assert df[["year", "month", "day"]].values.tolist() == [[2009, 1, 1]]


def test_times_without_a_day_are_dropped_not_given_one():
    df = to_oceanval_format(
        _raw([_row(time="2008-05", temp=1.0), _row(time="2008", temp=2.0), _row(temp=3.0)]),
        "TEMPPR01",
    )

    assert df.observation.tolist() == [3.0]


def test_unreadable_times_are_dropped_without_raising():
    df = to_oceanval_format(
        _raw([_row(time="2008-05-04T25", temp=1.0), _row(time="2008-05-04T24:30", temp=2.0), _row(temp=3.0)]),
        "TEMPPR01",
    )

    assert df.observation.tolist() == [3.0]


def test_the_times_read_differently_or_dropped_are_counted(capsys):
    to_oceanval_format(
        _raw([_row(time="1981-04-27T24"), _row(time="1985-05"), _row(time="2008-05-04T25"), _row()]),
        "TEMPPR01",
    )

    assert capsys.readouterr().out == (
        "ICES times: 1 stamped 24:00 read as 00:00 of the next day; "
        "1 without a full date and 1 that could not be read were dropped\n"
    )


def test_nothing_is_said_about_times_that_are_all_read(capsys):
    to_oceanval_format(_raw([_row(), _row(time="2008-03-04")]), "TEMPPR01")

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("flags", [dict(temp_flag=1), dict(temp_flag=4), dict(depth_flag=1), dict(depth_flag=8)])
def test_rows_are_dropped_unless_both_quality_flags_are_zero(flags):
    df = to_oceanval_format(_raw([_row(temp=8.5), _row(temp=9.5, **flags)]), "TEMPPR01")

    assert df.observation.tolist() == [8.5]


def test_missing_observations_are_dropped():
    df = to_oceanval_format(_raw([_row(temp=8.5), _row(temp=np.nan)]), "TEMPPR01")

    assert df.observation.tolist() == [8.5]


def test_a_missing_parameter_gives_empty_data():
    # ICES splits each analyte across several method-specific codes, so a
    # real export can legitimately have no column for one particular code -
    # that's "no observations", not an error.
    df = to_oceanval_format(_raw([_row()]), "PSALPR01")

    assert len(df) == 0
    assert list(df.columns) == POINT_COLUMNS


def test_ambiguous_parameter_code_raises():
    header = HEADER + ["Temperature (TEMPPR01_UPAB) [degC]", "QV:ODV:Temperature (TEMPPR01_UPAB) [degC]"]
    raw = pd.DataFrame([_row() + [9.0, 0]], columns=header)

    with pytest.raises(ValueError, match="TEMPPR01"):
        to_oceanval_format(raw, "TEMPPR01")


def test_missing_depth_column_raises():
    header = [c for c in HEADER if c not in (DEPTH, f"QV:ODV:{DEPTH}")]
    raw = pd.DataFrame([[v for v, c in zip(_row(), HEADER) if c in header]], columns=header)

    with pytest.raises(ValueError, match="ADEPZZ01"):
        to_oceanval_format(raw, "TEMPPR01")


ICES_PARAMETER_CODES = [
    "TEMPPR01",
    "PSALPR01",
    "ALKYZZXX",
    "AMONZZXX",
    "CPHLZZXX",
    "NTRAZZXX",
    "DOXYZZXX",
    "PHXXZZXX",
    "PHOSZZXX",
    "SLCAZZXX",
]


@pytest.mark.parametrize("code", ICES_PARAMETER_CODES)
def test_to_oceanval_format_extracts_any_ices_parameter_code(code):
    # to_oceanval_format matches columns by parameter code, not a fixed
    # header - this checks that column-matching genuinely works for every
    # code the built-in recipes use, not just temperature's.
    value_col = f"X ({code}_ABCD) [unit]"
    header = HEADER[:-2] + [value_col, f"QV:ODV:{value_col}"]
    raw = pd.DataFrame([_row()[:-2] + [7.0, 0]], columns=header)

    df = to_oceanval_format(raw, code)

    assert df.observation.tolist() == [7.0]


def test_empty_export_gives_empty_point_columns():
    df = to_oceanval_format(pd.DataFrame(), "TEMPPR01")

    assert len(df) == 0
    assert list(df.columns) == POINT_COLUMNS


def test_years_outside_the_simulation_give_no_data_without_a_download():
    recipe = {"parameter": "TEMPPR01", "dataset": "CTD"}

    df = download_point_data(recipe, 2010, 2004, [3, 4], [54, 55])

    assert len(df) == 0
    assert list(df.columns) == POINT_COLUMNS


def test_export_zip_is_read_past_the_comment_lines():
    lines = PREAMBLE + [",".join(HEADER), ",".join(str(x) for x in _row())]

    raw = _read_export(_export_zip(lines))

    assert list(raw.columns) == HEADER
    assert to_oceanval_format(raw, "TEMPPR01").observation.tolist() == [8.5]


def _points(rows):
    """Observations in oceanval's point columns, one (lon, lat, year, month,
    day, depth, observation) row each."""
    return pd.DataFrame(rows, columns=POINT_COLUMNS)


def test_bottle_values_at_a_ctd_station_are_left_out():
    ctd = _points([[3.6853, 54.8707, 2008, 3, 4, 1.0, 8.5]])
    ice = _points(
        [
            [3.6853, 54.8707, 2008, 3, 4, 10.0, 8.0],  # the same cast, another depth
            [3.6853, 54.8707, 2008, 3, 5, 1.0, 7.0],  # the next day
            [3.8, 54.8707, 2008, 3, 4, 1.0, 6.0],  # elsewhere that day
        ]
    )

    df = without_repeated_stations([("CTD", ctd), ("ICE", ice)])

    assert df.observation.tolist() == [8.5, 7.0, 6.0]
    assert list(df.index) == [0, 1, 2]


def test_positions_the_same_to_a_hundredth_of_a_degree_are_one_station():
    ctd = _points([[3.6853, 54.8707, 2008, 3, 4, 1.0, 8.5]])
    ice = _points([[3.6851, 54.8711, 2008, 3, 4, 1.0, 8.0]])

    df = without_repeated_stations([("CTD", ctd), ("ICE", ice)])

    assert df.observation.tolist() == [8.5]


def test_the_dataset_given_first_is_preferred():
    ctd = _points([[3.6853, 54.8707, 2008, 3, 4, 1.0, 8.5]])
    ice = _points([[3.6853, 54.8707, 2008, 3, 4, 10.0, 8.0]])

    df = without_repeated_stations([("ICE", ice), ("CTD", ctd)])

    assert df.observation.tolist() == [8.0]


def test_stations_in_one_dataset_alone_are_all_kept():
    ctd = _points([[3.6853, 54.8707, 2008, 3, 4, 1.0, 8.5], [3.6853, 54.8707, 2008, 3, 4, 2.0, 8.4]])

    df = without_repeated_stations([("CTD", ctd)])

    assert df.observation.tolist() == [8.5, 8.4]


def test_datasets_without_observations_are_skipped():
    ice = _points([[3.6853, 54.8707, 2008, 3, 4, 10.0, 8.0]])
    empty = pd.DataFrame(columns=POINT_COLUMNS)

    assert without_repeated_stations([("CTD", empty), ("ICE", ice)]).observation.tolist() == [8.0]
    nothing = without_repeated_stations([("CTD", empty), ("ICE", empty)])
    assert len(nothing) == 0
    assert list(nothing.columns) == POINT_COLUMNS
    assert len(without_repeated_stations([])) == 0


def test_the_observations_left_out_are_counted(capsys):
    ctd = _points([[3.6853, 54.8707, 2008, 3, 4, 1.0, 8.5]])
    ice = _points([[3.6853, 54.8707, 2008, 3, 4, 10.0, 8.0], [3.6853, 54.8707, 2008, 3, 4, 20.0, 7.5]])

    without_repeated_stations([("CTD", ctd), ("ICE", ice)])

    assert capsys.readouterr().out == (
        "2 ICES ICE observations at stations already in the CTD data were left out\n"
    )


def _export(rows):
    return _export_zip(PREAMBLE + [",".join(HEADER)] + [",".join(str(x) for x in row) for row in rows])


def _row_at(lon, **kwargs):
    row = _row(**kwargs)
    row[4] = lon
    return row


class _FakeIces:
    """The ICES API, without the network: each dataset's export, and the
    order jobs were started and waited on. A dataset whose export is None
    has no data ("NotFound")."""

    def __init__(self, exports):
        self.exports = exports
        self.calls = []

    def start_job(self, session, criteria):
        self.calls.append(("start", criteria["dataset"]))
        return criteria["dataset"]

    def wait_for_job(self, session, job_id):
        self.calls.append(("wait", job_id))
        return self.exports[job_id] is not None

    def session(self):
        fake = self

        class Response:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                pass

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, timeout):
                return Response(fake.exports[url.rsplit("/", 1)[1]])

        return Session()


@pytest.fixture
def fake_ices(monkeypatch):
    def install(exports):
        fake = _FakeIces(exports)
        monkeypatch.setattr(ices, "_start_job", fake.start_job)
        monkeypatch.setattr(ices, "_wait_for_job", fake.wait_for_job)
        monkeypatch.setattr(ices.requests, "Session", fake.session)
        return fake

    return install


def test_every_dataset_is_downloaded_and_repeated_stations_left_out(fake_ices):
    fake = fake_ices(
        {
            "CTD": _export([_row(depth=1.0, temp=8.5), _row(depth=2.0, temp=8.4)]),
            "ICE": _export([_row(depth=10.0, temp=8.0), _row_at(3.8, depth=1.0, temp=6.0)]),
        }
    )

    df = download_point_data(
        {"parameter": "TEMPPR01", "dataset": ["CTD", "ICE"]}, 2008, 2008, [3, 4], [54, 55]
    )

    assert df.observation.tolist() == [8.5, 8.4, 6.0]
    # both exports are asked for before either is waited on
    assert fake.calls == [("start", "CTD"), ("start", "ICE"), ("wait", "CTD"), ("wait", "ICE")]


def test_a_dataset_without_data_is_skipped(fake_ices, capsys):
    fake_ices({"CTD": None, "ICE": _export([_row(depth=10.0, temp=8.0)])})

    df = download_point_data(
        {"parameter": "TEMPPR01", "dataset": ["CTD", "ICE"]}, 2008, 2008, [3, 4], [54, 55]
    )

    assert df.observation.tolist() == [8.0]
    assert "ICES has no CTD data for these years and this area" in capsys.readouterr().out


def test_a_single_dataset_is_downloaded_as_before(fake_ices):
    fake = fake_ices({"ICE": _export([_row(depth=10.0, temp=8.0), _row(depth=20.0, temp=7.5)])})

    df = download_point_data({"parameter": "TEMPPR01", "dataset": "ICE"}, 2008, 2008, [3, 4], [54, 55])

    assert df.observation.tolist() == [8.0, 7.5]
    assert list(df.columns) == POINT_COLUMNS
    assert fake.calls == [("start", "ICE"), ("wait", "ICE")]


def test_no_data_in_any_dataset_gives_empty_point_columns(fake_ices):
    fake_ices({"CTD": None, "ICE": None})

    df = download_point_data(
        {"parameter": "TEMPPR01", "dataset": ["CTD", "ICE"]}, 2008, 2008, [3, 4], [54, 55]
    )

    assert len(df) == 0
    assert list(df.columns) == POINT_COLUMNS
