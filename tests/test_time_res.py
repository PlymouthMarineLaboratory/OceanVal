"""The time resolution of model output, and the check of point_time_res
against it (oceanval.time_res)."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import time_res


def times(dates):
    return pd.DataFrame([{"year": y, "month": m, "day": d} for y, m, d in dates])


@pytest.mark.parametrize(
    "dates, expected",
    [
        ([(2000, m, 15) for m in range(1, 13)], "monthly"),
        # annual output and a single time are as coarse as can be told
        ([(2000 + y, 7, 1) for y in range(3)], "monthly"),
        ([(2000, 1, 1)], "monthly"),
        ([(2000, 1, d) for d in range(1, 32)], "1d"),
        # more often than daily is daily
        ([(2000, 1, d) for d in range(1, 10) for _ in range(4)], "1d"),
        ([(2000, 1, d) for d in (1, 3, 5, 7)], "2d"),
        ([(2000, m, d) for m in (1, 2) for d in range(1, 31, 5)], "5d"),
        # a 360-day calendar has a 30th of February
        ([(2000, 2, d) for d in range(1, 31)], "1d"),
    ],
)
def test_label(dates, expected):
    assert time_res.label(times(dates)) == expected


def test_finest_first():
    labels = ["monthly", "5d", "1d", "10d", None]
    assert sorted(labels, key=time_res.sort_key) == ["1d", "5d", "10d", "monthly", None]


@pytest.mark.parametrize(
    "resolution, point_time_res, expected",
    [
        ("1d", ["year", "month", "day"], False),
        ("2d", ["year", "month", "day"], True),
        ("monthly", ["year", "month", "day"], True),
        ("monthly", ["month", "day"], True),
        ("monthly", ["year", "month"], False),
        (None, ["year", "month", "day"], False),
    ],
)
def test_needs_check(resolution, point_time_res, expected):
    assert time_res.needs_check(resolution, point_time_res) is expected


ROWS = [
    {"key": "temperature/ices", "variable": "temperature", "title": "Temperature",
     "source": "ices", "time_res": "monthly", "point_time_res": ["year", "month", "day"], "own": False},
    {"key": "nitrate/ices", "variable": "nitrate", "title": "Nitrate",
     "source": "ices", "time_res": "5d", "point_time_res": ["month", "day"], "own": True},
]


def test_check_rows():
    comparisons = {("temperature", "ices"): {}, ("nitrate", "ices"): {"point_time_res": ["month", "day"]},
                   ("salinity", "ices"): {}}
    rows = time_res.check_rows(
        [("temperature", "ices"), ("nitrate", "ices"), ("salinity", "ices"), ("temperature", "ices")],
        {"temperature": "monthly", "nitrate": "5d", "salinity": "1d"},
        lambda variable, source: comparisons[(variable, source)],
        ["year", "month", "day"],
        {"temperature": "Temperature", "nitrate": "Nitrate"},
    )
    assert rows == ROWS


class Comparisons(dict):
    def __getitem__(self, name):
        return self.setdefault(name, type("D", (), {"point_comparisons": {"ices": {}}})())


def test_all_of_them_changes_matchups_and_each_one_with_its_own():
    session, definitions = {"point_time_res": ["year", "month", "day"]}, Comparisons()
    choice = time_res.clean_choice({"default": ["year", "month"]}, ROWS)
    time_res.apply(choice, ROWS, session, definitions)
    assert session["point_time_res"] == ["year", "month"]
    assert definitions["nitrate"].point_comparisons["ices"]["point_time_res"] == ["year", "month"]
    # it has none of its own, so matchup's is used
    assert "point_time_res" not in definitions["temperature"].point_comparisons["ices"]


def test_each_one():
    session, definitions = {"point_time_res": ["year", "month", "day"]}, Comparisons()
    choice = time_res.clean_choice({"datasets": {"temperature/ices": ["month"]}}, ROWS)
    said = time_res.apply(choice, ROWS, session, definitions)
    assert session["point_time_res"] == ["year", "month", "day"]
    assert definitions["temperature"].point_comparisons["ices"]["point_time_res"] == ["month"]
    assert said == ["point_time_res for Temperature (ices) is now ['month']"]


def test_keeping_them_changes_nothing():
    session, definitions = {"point_time_res": ["year", "month", "day"]}, Comparisons()
    assert time_res.apply(time_res.clean_choice(None, ROWS), ROWS, session, definitions) == []
    assert session["point_time_res"] == ["year", "month", "day"]


@pytest.mark.parametrize(
    "choice",
    [
        "year",
        {"default": ["week"]},
        {"default": []},
        {"datasets": {"temperature/ices": ["day", "day"]}},
        {"datasets": {"chlorophyll/occci": ["month"]}},
    ],
)
def test_bad_choices_are_refused(choice):
    with pytest.raises(ValueError):
        time_res.clean_choice(choice, ROWS)


def test_asked_at_a_terminal():
    answers = iter(["x", "e", "2", "9", "4"])
    choice = time_res.ask_at_terminal(ROWS, lambda question, choices: next(answers))
    assert choice == {"default": None,
                      "datasets": {"temperature/ices": ["year", "month"], "nitrate/ices": ["month"]}}


class TestGetTimeRes:
    """The resolution matchup reads from the files of a pattern in one folder,
    which are those it matches up, and not others the pattern also matches."""

    PATTERN = "nemo_1m_**_**_grid_T_**-**.nc"
    STEM = "nemo_1m_19700101_19700131_grid_T"

    @pytest.fixture(autouse=True)
    def settings(self, monkeypatch):
        from oceanval import matchall

        monkeypatch.setitem(matchall.session_info, "levels_down", 1)
        monkeypatch.setitem(matchall.session_info, "strict_names", True)
        monkeypatch.setitem(matchall.session_info, "require", None)
        monkeypatch.setitem(matchall.session_info, "fvcom", False)

    @staticmethod
    def write(path, times):
        xr.Dataset(
            {"thetao": (("time",), np.ones(len(times), dtype="f4"))},
            coords={"time": pd.to_datetime(times)},
        ).to_netcdf(path)

    def res(self, folder, pattern=None, example=None, exclude=()):
        from oceanval import matchall

        pattern = pattern or self.PATTERN
        matchall.example_files[pattern] = str(
            folder / (example or f"{self.STEM}_197001-197001.nc")
        )
        return matchall.get_time_res(pattern, str(folder), exclude)

    def test_a_sibling_stream_with_the_same_time_is_not_more_times(self, tmp_path):
        # as NEMO writes grid_T beside grid_T_globmean, both with the month's
        # one time, which the pattern's * matches alike
        self.write(tmp_path / f"{self.STEM}_197001-197001.nc", ["1970-01-16"])
        self.write(tmp_path / f"{self.STEM}_globmean_197001-197001.nc", ["1970-01-16"])

        assert self.res(tmp_path) == "monthly"

    def test_and_without_strict_names_too(self, tmp_path, monkeypatch):
        from oceanval import matchall

        monkeypatch.setitem(matchall.session_info, "strict_names", False)
        self.write(tmp_path / f"{self.STEM}_197001-197001.nc", ["1970-01-16"])
        self.write(tmp_path / f"{self.STEM}_globmean_197001-197001.nc", ["1970-01-16"])

        # the identical times are another output of the same month
        assert self.res(tmp_path) == "monthly"

    def test_excluded_files_are_not_read(self, tmp_path, monkeypatch):
        from oceanval import matchall

        monkeypatch.setitem(matchall.session_info, "strict_names", False)
        self.write(tmp_path / f"{self.STEM}_197001-197001.nc", ["1970-01-16"])
        # daily output in a stream that exclude leaves out
        self.write(
            tmp_path / f"{self.STEM}_daily_197001-197001.nc",
            pd.date_range("1970-01-01", periods=10, freq="D"),
        )

        assert self.res(tmp_path, exclude=["daily"]) == "monthly"

    def test_one_file_a_month_in_one_folder_is_monthly(self, tmp_path):
        for month in (1, 2, 3):
            self.write(
                tmp_path / f"{self.STEM}_19700{month}-19700{month}.nc",
                [f"1970-0{month}-16"],
            )

        assert self.res(tmp_path) == "monthly"

    def test_one_file_a_day_is_daily(self, tmp_path):
        for day in (1, 2, 3):
            self.write(tmp_path / f"{self.STEM}_1970010{day}-1970010{day}.nc", [f"1970-01-0{day}"])

        assert self.res(tmp_path) == "1d"

    def test_a_file_of_daily_output_is_daily(self, tmp_path):
        self.write(
            tmp_path / f"{self.STEM}_197001-197001.nc",
            pd.date_range("1970-01-01", periods=31, freq="D"),
        )

        assert self.res(tmp_path) == "1d"

    def test_a_file_of_hourly_output_is_daily(self, tmp_path):
        self.write(
            tmp_path / f"{self.STEM}_197001-197001.nc",
            pd.date_range("1970-01-01", periods=72, freq="h"),
        )

        assert self.res(tmp_path) == "1d"
