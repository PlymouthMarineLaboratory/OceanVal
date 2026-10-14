"""Model times that mark the end of the period each mean covers (see
oceanval.time_stamps)."""

import datetime
import pickle

import cftime
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import time_stamps

UNITS = "seconds since 1996-01-01 00:00:00"


def _write(path, times, cell_methods="time: mean", calendar="standard", bounds=None, variable="temp"):
    """A small file of variable at times (cftime datetimes, or tuples for
    them), with the cell_methods given and, if given, time bounds as
    (start, end) pairs."""
    times = [cftime.datetime(*x, calendar=calendar) if isinstance(x, tuple) else x for x in times]
    values = cftime.date2num(times, UNITS, calendar=calendar)
    ds = xr.Dataset(
        {variable: (("time", "lat", "lon"), np.ones((len(times), 2, 2)))},
        coords={"time": values, "lat": [50.0, 51.0], "lon": [-4.0, -3.0]},
    )
    ds.time.attrs.update(units=UNITS, calendar=calendar, axis="T")
    if cell_methods is not None:
        ds[variable].attrs["cell_methods"] = cell_methods
    if bounds is not None:
        pairs = [
            [cftime.datetime(*x, calendar=calendar) if isinstance(x, tuple) else x for x in pair]
            for pair in bounds
        ]
        ds["time_bnds"] = (("time", "bnds"), cftime.date2num(pairs, UNITS, calendar=calendar))
        ds.time.attrs["bounds"] = "time_bnds"
    ds.to_netcdf(path)
    return str(path)


def _daily(year, month, end_stamped, calendar="standard"):
    """A month of daily means, dated at the end of each day (00:00 the next)
    or at noon within it."""
    first = cftime.datetime(year, month, 1, calendar=calendar)
    times = []
    day = first
    while day.month == month:
        times.append(day + datetime.timedelta(days=1) if end_stamped else day + datetime.timedelta(hours=12))
        day += datetime.timedelta(days=1)
    return times


def _evidence(paths, variable="temp"):
    return time_stamps._evidence(sorted(paths), variable)


class TestFindingEnds:
    def test_daily_means_a_file_a_month_dated_at_the_end_of_each_day(self, tmp_path):
        paths = [
            _write(tmp_path / f"d_2000{month:02}.nc", _daily(2000, month, True))
            for month in (1, 2, 3)
        ]
        evidence = _evidence(paths)

        assert evidence["first"] == "2000-01-02 00:00"
        assert evidence["last"] == "2000-04-01 00:00"
        assert evidence["first_shifted"] == "2000-01-01 00:00"
        assert evidence["last_shifted"] == "2000-03-31 00:00"
        assert "has its times in January 2000 but the last, which is 2000-02-01 00:00" in evidence["reason"]
        assert evidence["has_bounds"] is False

    def test_monthly_means_a_file_a_month_with_the_year_last_in_the_next(self, tmp_path):
        # GETM's: January's mean at 00:00 on 1 February, ..., December's on 1 January
        paths = [
            _write(tmp_path / f"m_2000{month:02}.nc", [(2000 + month // 12, month % 12 + 1, 1)])
            for month in range(1, 13)
        ]
        evidence = _evidence(paths)

        assert evidence["reason"] == "the last time, 2001-01-01 00:00, is the only one in 2001"
        assert (evidence["first"], evidence["last_shifted"]) == ("2000-02-01 00:00", "2000-12-31 00:00")

    def test_monthly_means_in_one_file(self, tmp_path):
        times = [(2000, month, 1) for month in range(2, 13)] + [(2001, 1, 1)]
        assert _evidence([_write(tmp_path / "m.nc", times)]) is not None

    @pytest.mark.parametrize("calendar", ["noleap", "360_day"])
    def test_other_calendars(self, tmp_path, calendar):
        paths = [
            _write(tmp_path / f"d_{month}.nc", _daily(2001, month, True, calendar), calendar=calendar)
            for month in (2, 3)
        ]
        evidence = _evidence(paths)

        assert evidence["calendar"] == calendar
        assert evidence["last_shifted"] == ("2001-03-30 00:00" if calendar == "360_day" else "2001-03-31 00:00")

    def test_daily_means_within_each_day_are_not_ends(self, tmp_path):
        paths = [
            _write(tmp_path / f"d_{month}.nc", _daily(2000, month, False)) for month in (1, 2)
        ]
        assert _evidence(paths) is None

    def test_daily_means_dated_at_the_start_of_each_day_are_not_ends(self, tmp_path):
        paths = [
            _write(tmp_path / f"d_{month}.nc", [x - datetime.timedelta(hours=12) for x in _daily(2000, month, False)])
            for month in (1, 2)
        ]
        assert _evidence(paths) is None

    def test_monthly_means_dated_at_the_start_of_each_month_are_not_ends(self, tmp_path):
        # a run of a whole year and one more month looks like an end at the
        # end, but it starts at the start of a year
        times = [(2000, month, 1) for month in range(1, 13)] + [(2001, 1, 1)]
        assert _evidence([_write(tmp_path / "m.nc", times)]) is None

    def test_values_at_an_instant_are_not_ends(self, tmp_path):
        paths = [
            _write(tmp_path / f"d_{month}.nc", _daily(2000, month, True), cell_methods=cell_methods)
            for month, cell_methods in ((1, None), (2, None))
        ]
        assert _evidence(paths) is None
        point = [_write(tmp_path / "p.nc", _daily(2000, 1, True), cell_methods="time: point")]
        assert _evidence(point) is None

    def test_other_statistics_over_time_count(self, tmp_path):
        paths = [
            _write(tmp_path / f"d_{month}.nc", _daily(2000, month, True), cell_methods="area: mean time: maximum")
            for month in (1, 2)
        ]
        assert _evidence(paths) is not None

    def test_bounds_that_put_each_time_at_its_end_say_so(self, tmp_path):
        times = _daily(2000, 1, True)
        bounds = [(x - datetime.timedelta(days=1), x) for x in times]
        evidence = _evidence([_write(tmp_path / "b.nc", times, bounds=bounds)])

        assert evidence["has_bounds"] is True
        assert evidence["reason"] == "its time bounds show each time is the end of its period"

    def test_bounds_that_put_each_time_within_its_period_are_not_ends(self, tmp_path):
        # midnight, at a month's end and all, but the bounds say otherwise
        times = _daily(2000, 1, True)
        bounds = [(x - datetime.timedelta(hours=12), x + datetime.timedelta(hours=12)) for x in times]
        assert _evidence([_write(tmp_path / "b.nc", times, bounds=bounds)]) is None

    def test_a_variable_that_is_not_there_or_a_file_that_cannot_be_read(self, tmp_path):
        path = _write(tmp_path / "d.nc", _daily(2000, 1, True))
        broken = tmp_path / "broken.nc"
        broken.write_text("not netCDF")
        assert _evidence([path], "salt") is None
        assert _evidence([str(broken)]) is None

    def test_the_first_of_a_sum_is_read(self, tmp_path):
        paths = [_write(tmp_path / f"d_{month}.nc", _daily(2000, month, True)) for month in (1, 2)]
        assert _evidence(paths, "temp+other") is not None

    def test_only_the_first_and_last_two_files_are_read(self):
        assert time_stamps._sample(["a", "b", "c", "d", "e"]) == ["a", "d", "e"]
        assert time_stamps._sample(["a"]) == ["a"]


class TestCheckRows:
    def test_a_row_for_each_variable_that_looks_end_stamped(self, tmp_path):
        means = [_write(tmp_path / f"m_{month}.nc", _daily(2000, month, True)) for month in (1, 2)]
        within = [_write(tmp_path / f"w_{month}.nc", _daily(2000, month, False)) for month in (1, 2)]
        chosen = pd.DataFrame(
            {
                "variable": ["temperature", "salinity", "chlorophyll"],
                "model_variable": ["temp", "temp", "temp"],
                "pattern": ["m_*.nc", "m_*.nc", "w_*.nc"],
            }
        )
        rows = time_stamps.check_rows(
            chosen, {"m_*.nc": means, "w_*.nc": within}, {"temperature": "Temperature"}
        )

        assert [(row["variable"], row["title"]) for row in rows] == [
            ("temperature", "Temperature"),
            ("salinity", "salinity"),
        ]
        assert rows[0]["pattern"] == "m_*.nc" and rows[0]["model_variable"] == "temp"
        assert rows[0]["key"] == "temperature"

    def test_no_files_no_rows(self):
        chosen = pd.DataFrame({"variable": ["t"], "model_variable": ["temp"], "pattern": ["x"]})
        assert time_stamps.check_rows(chosen, {}) == []


class TestWords:
    ROW = {
        "title": "Temperature", "model_variable": "temp", "pattern": "*/m_*.nc",
        "cell_methods": "time: mean", "has_bounds": False, "first": "2000-02-01 00:00",
        "last": "2001-01-01 00:00", "first_shifted": "2000-01-31 00:00",
        "last_shifted": "2000-12-31 00:00", "reason": "the last time, 2001-01-01 00:00, is the only one in 2001",
    }

    def test_the_warning(self):
        text = time_stamps.warning([self.ROW])
        assert "Temperature: temp in */m_*.nc, cell_methods \"time: mean\", no time bounds" in text
        assert "shifted back one day: 2000-01-31 00:00 to 2000-12-31 00:00" in text
        assert "no time bounds to say for certain" in text
        assert "time_shift=-1" not in text

        unasked = time_stamps.warning([self.ROW], asked=False)
        assert "The times are left as they are" in unasked and "time_shift=-1" in unasked

    def test_the_end_message(self):
        assert "time_shift=-1" in time_stamps.end_message([self.ROW])

    def test_describe_and_the_report(self):
        assert time_stamps.describe(-1) == "back one day"
        assert time_stamps.describe(2) == "forward 2 days"
        assert time_stamps.report_text(0) == ""
        assert time_stamps.report_text(-1).endswith("as they mark the end of the period each model value covers.")
        assert time_stamps.report_text(-2) == "The model's times were shifted back 2 days before matching up."

    def test_the_question(self, capsys):
        answers = iter(["maybe", "Y"])
        asked = []

        def ask(question, choices, details=None):
            asked.append((question, choices, details))
            return next(answers)

        assert time_stamps.ask([self.ROW], ask) == -1
        assert asked[0] == (
            "Shift the model's times back one day? (y/n) ", ("y", "n"),
            {"kind": "time_shift", "rows": [self.ROW], "suggested": -1},
        )
        assert "Provide y or n" in capsys.readouterr().out
        assert time_stamps.ask([self.ROW], lambda *args, **kwargs: "n") == 0

    @pytest.mark.parametrize("value, valid", [(None, True), (0, True), (-1, True), (3, True),
                                              (True, False), (-1.0, False), ("-1", False)])
    def test_valid(self, value, valid):
        assert time_stamps.valid(value) is valid


class TestShiftingDates:
    @pytest.mark.parametrize(
        "calendar, date, shifted",
        [
            ("standard", (2001, 1, 1), (2000, 12, 31)),
            ("standard", (2000, 3, 1), (2000, 2, 29)),
            ("noleap", (2000, 3, 1), (2000, 2, 28)),
            ("360_day", (2000, 3, 1), (2000, 2, 30)),
            ("gregorian", (2000, 2, 1), (2000, 1, 31)),
            ("nonsense", (2000, 2, 1), (2000, 1, 31)),
        ],
    )
    def test_back_a_day(self, calendar, date, shifted):
        df = pd.DataFrame({"year": [date[0]], "month": [date[1]], "day": [date[2]]})
        out = time_stamps.shift_dates(df, -1, calendar)
        assert tuple(out.iloc[0]) == shifted

    def test_the_rows_stay_in_order_and_other_columns_are_kept(self):
        df = pd.DataFrame({"year": [2000, 2000], "month": [1, 2], "day": [2, 1], "path": ["a", "a"]})
        out = time_stamps.shift_dates(df, -1)
        assert out.to_dict("list") == {"year": [2000, 2000], "month": [1, 1], "day": [1, 31], "path": ["a", "a"]}
        # the frame given is left alone
        assert df.day.tolist() == [2, 1]

    def test_the_index_of_every_file(self):
        times = {
            "a": pd.DataFrame({"year": [2001], "month": [1], "day": [1]}),
            "b": pd.DataFrame({"year": [2001], "month": [3], "day": [1]}),
        }
        time_stamps.shift_index(times, -1, {"a": "standard", "b": "360_day"}.get)
        assert tuple(times["a"].iloc[0]) == (2000, 12, 31)
        assert tuple(times["b"].iloc[0]) == (2001, 2, 30)
        assert time_stamps.shift_index(times, 0, None) is times

    def test_the_calendar_of_a_file(self, tmp_path):
        assert time_stamps.calendar(_write(tmp_path / "a.nc", _daily(2001, 1, True, "noleap"), calendar="noleap")) == "noleap"
        assert time_stamps.calendar(str(tmp_path / "missing.nc")) == "standard"


class TestStoredShift:
    def test_read_from_a_matchup_dict(self, tmp_path):
        path = tmp_path / "matchup_dict.pkl"
        with open(path, "wb") as f:
            pickle.dump({"start": 2000, "end": 2000, "time_shift": -1}, f)
        assert time_stamps.stored_time_shift(str(path)) == -1

        # made before there was a time_shift, or not there at all
        with open(path, "wb") as f:
            pickle.dump({"start": 2000, "end": 2000}, f)
        assert time_stamps.stored_time_shift(str(path)) == 0
        assert time_stamps.stored_time_shift(str(tmp_path / "missing.pkl")) == 0
