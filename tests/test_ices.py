"""The ICES point recipe's parsing of export files, without the network.

The headers and file layout here are copied from real ICES CTD exports.
"""

import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from oceanval.ices import POINT_COLUMNS, _read_export, download_point_data, to_oceanval_format

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
