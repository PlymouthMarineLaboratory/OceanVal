"""What a simulation's output holds, for the viewer (oceanval.viewer_catalogue,
from ncviewer's tests): its file types, variables and times, the slices
mapped, sums, missing values, the colour scale of an animation, and the
times of the files read in the background."""

import os

import numpy as np
import pytest
from viewer_simulations import levels_file, nemo_simulation, regular_file

from oceanval import viewer_catalogue as catalogue
from oceanval import viewer_times as times


@pytest.fixture
def simulation(viewer_simulation):
    return viewer_simulation


@pytest.fixture
def found(simulation):
    return catalogue.scan(simulation, 2)


def test_streams_and_variables(found):
    summary = found.summary()
    assert summary["files"] == 48
    assert [stream["pattern"] for stream in summary["streams"]] == [
        "amm7_1m_**_**_grid_T.nc", "amm7_1m_**_**_ptrc_T.nc",
    ]
    variables = summary["variables"]
    # bounds and coordinates are not variables to look at
    assert set(variables) == {"votemper", "sossheig", "chl"}
    # a variable in two file types says both
    assert [entry["stream"] for entry in variables["votemper"]["streams"]] == [0, 1]
    temperature = variables["votemper"]["streams"][0]
    assert temperature["levels"] == 4
    assert temperature["level_values"] == [1.0, 5.0, 20.0, 50.0]
    assert variables["chl"]["long_name"] == "Chlorophyll"


def test_times(found):
    stream = found.summary()["streams"][0]
    assert stream["steps"] == 24
    assert stream["first"] == "2000-01-16 12:00:00"
    assert stream["last"] == "2001-12-16 12:00:00"
    assert stream["calendar"] == "noleap"
    assert stream["resolution"] == "monthly"
    assert stream["times"] == sorted(stream["times"])


def test_times_are_kept(simulation, monkeypatch):
    catalogue.scan(simulation, 2)
    read = []
    monkeypatch.setattr(times, "file_times", lambda path: read.append(path))
    again = catalogue.scan(simulation, 2)
    assert read == []
    assert again.summary()["streams"][0]["steps"] == 24


def test_a_changed_file_is_read_again(tmp_path):
    root = nemo_simulation(str(tmp_path / "sim"), years=(2000,), layout="flat")
    catalogue.scan(root, 0)
    path = sorted(os.listdir(root))[0]
    os.utime(os.path.join(root, path), ns=(1, 1))
    kept = times.load_cache([os.path.join(root, name) for name in os.listdir(root)])
    assert os.path.join(root, path) not in kept
    assert len(kept) == 23


def test_360_day_daily(tmp_path):
    root = nemo_simulation(str(tmp_path / "sim"), years=(2000,), layout="flat", per_month=30, calendar="360_day")
    stream = catalogue.scan(root, 0).summary()["streams"][0]
    assert stream["steps"] == 360
    assert stream["calendar"] == "360_day"
    assert stream["resolution"] == "daily"
    assert "2000-02-30 12:00:00" in stream["times"]


def test_many_files_in_a_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(catalogue, "PARALLEL_FROM", 2)
    root = nemo_simulation(str(tmp_path / "sim"), years=(2000,), layout="flat")
    told = []
    found = catalogue.scan(root, 0, progress=lambda **news: told.append(news))
    assert found.summary()["streams"][0]["steps"] == 12
    assert told[-1] == {"phase": "variables", "done": 2, "total": 2}
    assert {"phase": "times", "done": 24, "total": 24} in told


def test_stopped(simulation):
    with pytest.raises(catalogue.Stopped):
        catalogue.scan(simulation, 2, stopped=lambda: True)


def test_no_files(tmp_path):
    with pytest.raises(ValueError):
        catalogue.scan(str(tmp_path), 0)


def test_field(found):
    data, when = found.field(0, "votemper", step=5, level=1)
    assert data.dims == ("y", "x")
    assert when == "2000-06-16 00:00:00"
    assert "nav_lon" in data.coords
    # a later month is warmer, a deeper level colder
    assert float(found.field(0, "votemper", 6, 1)[0].mean()) > float(data.mean())
    assert float(found.field(0, "votemper", 5, 3)[0].mean()) < float(data.mean())
    # a variable with no levels takes none
    data, when = found.field(0, "sossheig", step=2, level=3)
    assert data.dims == ("y", "x") and when == "2000-03-16 12:00:00"


def test_regular_grid(tmp_path):
    regular_file(str(tmp_path / "r.nc"))
    found = catalogue.scan(str(tmp_path), 0)
    # only what can be mapped is listed: not a series with no horizontal dimensions
    assert list(found.summary()["variables"]) == ["sst"]
    data, when = found.field(0, "sst", 1)
    assert data.dims == ("lat", "lon")


def _levels_of(tmp_path, **options):
    levels_file(str(tmp_path / "levels.nc"), **options)
    entry = catalogue.scan(str(tmp_path), 0).summary()["variables"]["temp"]["streams"][0]
    return entry["level_labels"], entry["surface_level"]


@pytest.mark.parametrize("options, surface", [
    # GETM's: a height positive up, rising from the bottom, with its land value
    ({"height": "up"}, "last"),
    ({"height": "down"}, "first"),
    # no vertical coordinate: a z-level model has less sea at depth
    ({"stacking": "surface_first"}, "first"),
    ({"stacking": "surface_last"}, "last"),
])
def test_levels_with_no_depths_say_which_is_the_surface(tmp_path, options, surface):
    labels, at = _levels_of(tmp_path, **options)
    if surface == "last":
        assert labels == ["0 (bottom)", "1", "2", "3", "4 (surface)"] and at == 4
    else:
        assert labels == ["0 (surface)", "1", "2", "3", "4 (bottom)"] and at == 0


def test_levels_that_cannot_be_told_apart_are_not_named(tmp_path):
    # every level as wet as the others and no vertical coordinate
    assert _levels_of(tmp_path) == (None, 0)


def test_levels_with_depths_are_not_named(found):
    entry = found.summary()["variables"]["votemper"]["streams"][0]
    assert entry["level_labels"] is None and entry["surface_level"] == 0


def test_the_ends_are_told_from_a_small_slice(tmp_path, monkeypatch):
    """Only a strided slice of the first time step is read."""
    monkeypatch.setattr(catalogue, "_ENDS_VALUES", 10)
    levels_file(str(tmp_path / "levels.nc"), height="up", nt=3)
    with catalogue.open_dataset(str(tmp_path / "levels.nc")) as ds:
        select = catalogue._small_slice(ds["zc"], {"z"}, "time")
        assert select["time"] == 0 and "z" not in select
        assert select["y"] == slice(None, None, 4) and select["x"] == slice(None, None, 4)
        assert catalogue._ends(ds, "temp", "z", "time") == "surface_last"


def test_resolution():
    assert catalogue.resolution([]) == ""
    assert catalogue.resolution(["2000-01-01 00:00:00", "2000-01-01 06:00:00", "2000-01-01 12:00:00", "2000-01-01 18:00:00"]) == "6-hourly"
    assert catalogue.resolution([f"2000-01-{day:02d} 12:00:00" for day in range(1, 31, 5)]) == "5-daily"
    assert catalogue.resolution([f"{year}-07-01 00:00:00" for year in range(2000, 2005)]) == "annual"


def test_missing_mask():
    import xarray as xr

    from oceanval.viewer_catalogue import missing_mask

    data = xr.DataArray(np.array([[0, -999, 5.5, 1e20, np.nan]], dtype="float32"))
    assert missing_mask(data, []).values.sum() == 0
    # a single number, and a float32 1e20, which is not the float64 one
    assert missing_mask(data, [(-999, -999)]).values.tolist() == [[False, True, False, False, False]]
    assert missing_mask(data, [(1e20, 1e20)]).values.tolist() == [[False, False, False, True, False]]
    # a range includes both ends, and its ends can be either way round
    assert missing_mask(data, [(-999, 0)]).values.tolist() == [[True, True, False, False, False]]
    assert missing_mask(data, [(5.5, 0)]).values.tolist() == [[True, False, True, False, False]]
    # several, and NaN never matches
    assert missing_mask(data, [(0, 0), (1e20, 1e20)]).values.sum() == 2
    # a number too big for the type matches nothing, and is no error
    assert missing_mask(data, [(1e40, 1e41)]).values.sum() == 0
    integers = xr.DataArray(np.array([0, 1, 2, 3]))
    assert missing_mask(integers, [(1, 2)]).values.tolist() == [False, True, True, False]
    assert missing_mask(integers, [(0.5, 1.5)]).values.tolist() == [False, True, False, False]


def test_field_with_missing_values(found):
    data, _ = found.field(0, "votemper", 0, 0)
    assert data.attrs["n_missing"] == 0
    rows = sorted(set(np.round(data.values[np.isfinite(data.values)], 3)))
    low = float(rows[0])
    masked, _ = found.field(0, "votemper", 0, 0, [(low, low)])
    assert masked.attrs["n_missing"] > 0
    assert np.isnan(masked.values).sum() == np.isnan(data.values).sum() + masked.attrs["n_missing"]
    assert float(np.nanmin(masked.values)) > low
    # everything from the lowest to the highest
    everything, _ = found.field(0, "votemper", 0, 0, [(low - 1, float(rows[-1]) + 1)])
    assert np.isnan(everything.values).all()


def test_sum_of_variables(found):
    chl, when = found.field(1, "chl", 3, 1)
    temperature, _ = found.field(1, "votemper", 3, 1)
    total, summed_when = found.field(1, ["chl", "votemper"], 3, 1)
    assert summed_when == when
    assert np.allclose(total.values, (chl + temperature).values, equal_nan=True)
    # land is missing in both, so in the sum
    assert np.isnan(total.values).sum() == np.isnan(chl.values).sum() > 0
    # one name in a list is that variable
    alone, _ = found.field(1, ["chl"], 3, 1)
    assert np.array_equal(alone.values, chl.values, equal_nan=True)
    about = found.combined(1, ["chl", "votemper"])
    assert about["name"] == "chl + votemper"
    assert about["long_name"] == "Chlorophyll + Temperature"
    assert about["units_differ"] == ["degC", "mg m-3"]
    assert found.combined(1, ["chl"])["units_differ"] is None


def test_sum_applies_missing_values_to_each_variable(found):
    chl, _ = found.field(1, "chl", 3, 1)
    low = float(np.nanmin(chl.values))
    # a range that only chl has in it: those cells are missing in the sum too
    total, _ = found.field(1, ["chl", "votemper"], 3, 1, [(low, low)])
    assert total.attrs["n_missing"] > 0
    plain, _ = found.field(1, ["chl", "votemper"], 3, 1)
    assert np.isnan(total.values).sum() == np.isnan(plain.values).sum() + total.attrs["n_missing"]


def test_what_cannot_be_added(found):
    with pytest.raises(ValueError, match="different dimensions"):
        found.combined(0, ["votemper", "sossheig"])
    with pytest.raises(ValueError, match="only be added once"):
        found.combined(0, ["votemper", "votemper"])
    with pytest.raises(ValueError, match="not in that file type"):
        found.combined(0, ["votemper", "chl"])
    with pytest.raises(ValueError, match="Choose a variable"):
        found.combined(0, [])


def test_limits_cover_the_whole_run(found):
    low, high, symmetric = found.limits(1, ["chl"], level=1)
    # wider than any one month's range
    month_low = month_high = None
    for step in range(24):
        data, _ = found.field(1, "chl", step, 1)
        values = data.values[np.isfinite(data.values)]
        month_low = values.min() if month_low is None else min(month_low, values.min())
        month_high = values.max() if month_high is None else max(month_high, values.max())
    first, _ = found.field(1, "chl", 0, 1)
    assert low == pytest.approx(float(month_low)) and high == pytest.approx(float(month_high))
    # the first month is not the whole run
    assert high > float(np.nanmax(first.values))
    assert symmetric is False


def test_limits_use_missing_values_and_sums(found):
    low, high, _ = found.limits(1, ["chl"], level=1)
    cut, cut_high, _ = found.limits(1, ["chl"], level=1, missing=[(low, low + 0.5)])
    assert cut > low
    total_low, total_high, _ = found.limits(1, ["chl", "votemper"], level=1)
    assert total_low > high
    # everything missing: nothing to give
    assert found.limits(1, ["chl"], level=1, missing=[(-1e9, 1e9)]) is None


def test_limits_read_a_few_steps_only(found, monkeypatch):
    steps = []
    real = found.field
    monkeypatch.setattr(found, "field", lambda stream, names, step, *args: steps.append(step) or real(stream, names, step, *args))
    found.limits(1, ["chl"], level=0, samples=5)
    assert steps == [0, 6, 12, 17, 23]


def test_limits_span_zero_are_symmetric(tmp_path):
    import xarray as xr

    lon, lat = np.arange(4.0), np.arange(3.0)
    values = np.stack([np.full((3, 4), -2.0), np.full((3, 4), 5.0)])
    xr.Dataset(
        {"anomaly": (("time", "lat", "lon"), values, {"units": "K"})},
        coords={"lon": ("lon", lon, {"units": "degrees_east"}), "lat": ("lat", lat, {"units": "degrees_north"}),
                "time": ("time", [0.0, 30.0], {"units": "days since 2000-01-01", "calendar": "standard"})},
    ).to_netcdf(tmp_path / "a.nc")
    found = catalogue.scan(str(tmp_path), 0)
    assert found.limits(0, ["anomaly"]) == (-5.0, 5.0, True)


# ---- the times read in the background ----

def _time_fields(streams):
    return [
        {key: stream[key] for key in ("steps", "times", "has_times", "resolution", "calendar", "unreadable", "pending")}
        for stream in streams
    ]


def test_a_scan_that_does_not_wait_has_the_first_times_only(simulation):
    quick = catalogue.scan(simulation, 2, wait=False)
    try:
        assert not quick.times_complete
        # the first file of each file type: one time, its 23 other files not read yet
        assert [(stream["steps"][0][0], len(stream["steps"]), stream["pending"]) for stream in quick.streams] == [
            ("2000-01-16 12:00:00", 1, 23), ("2000-01-16 12:00:00", 1, 23),
        ]
        # the variables are there, and so is a map of the first time
        assert set(quick.summary()["variables"]) == {"votemper", "sossheig", "chl"}
        assert quick.summary()["times_complete"] is False
        data, when = quick.field(1, ["chl"], 0, 0)
        assert when == "2000-01-16 12:00:00" and np.isfinite(data.values).any()
        told = []
        quick.read_times(lambda done, total: told.append((done, total)))
        assert quick.times_complete
        # counted over every file: the two first files were in already
        assert told[0] == (2, 48) and told[-1] == (48, 48) and [done for done, _ in told] == sorted(done for done, _ in told)
        full = catalogue.scan(simulation, 2)
        assert _time_fields(quick.streams) == _time_fields(full.streams)
        assert quick.summary()["streams"][0]["step_files"] == list(range(24))
        full.close()
    finally:
        quick.close()


def test_the_times_are_read_in_a_process_of_their_own(simulation, monkeypatch):
    monkeypatch.setattr(catalogue, "PARALLEL_FROM", 4)
    quick = catalogue.scan(simulation, 2, wait=False)
    try:
        quick.read_times(cores=2)
        assert quick.times_complete and [stream["pending"] for stream in quick.streams] == [0, 0]
        monkeypatch.setattr(catalogue, "PARALLEL_FROM", 10_000)
        assert _time_fields(quick.streams) == _time_fields(catalogue.scan(simulation, 2).streams)
    finally:
        quick.close()


def test_times_read_here_are_read_under_the_lock(simulation, monkeypatch):
    # the window's map reads are in other threads, and netCDF-C and HDF5 are not thread-safe
    # (oceanval.netcdf_lock.LOCK)
    monkeypatch.setattr(catalogue, "PARALLEL_FROM", 10_000)
    quick = catalogue.scan(simulation, 2, wait=False)
    real, held = times.file_times, []
    monkeypatch.setattr(times, "file_times", lambda path: held.append(catalogue.LOCK.locked()) or real(path))
    try:
        quick.read_times()
        assert len(held) == 46 and all(held)
    finally:
        quick.close()


def test_reading_the_times_can_be_stopped(simulation, monkeypatch):
    monkeypatch.setattr(catalogue, "PARALLEL_FROM", 10_000)
    quick = catalogue.scan(simulation, 2, wait=False)
    try:
        calls = []
        with pytest.raises(catalogue.Stopped):
            quick.read_times(stopped=lambda: calls.append(1) or len(calls) > 10)
        # it is as it was, and can be used
        assert not quick.times_complete and quick.streams[0]["pending"] == 23
        assert quick.field(1, ["chl"], 0, 0)[1] == "2000-01-16 12:00:00"
        # what was read is kept in the cache, so it is not read again
        kept = times.load_cache([path for stream in quick.streams for path in stream["files"]])
        assert 10 <= len(kept) <= 14
        quick.close()
        # closing stops it too
        with pytest.raises(catalogue.Stopped):
            quick.read_times()
    finally:
        quick.close()


def test_a_scan_that_does_not_wait_uses_the_cache(simulation):
    catalogue.scan(simulation, 2).close()
    again = catalogue.scan(simulation, 2, wait=False)
    try:
        # everything was kept: nothing is pending, and nothing is left to read
        assert again.times_complete and [stream["pending"] for stream in again.streams] == [0, 0]
        assert len(again.streams[0]["steps"]) == 24
        again.read_times(lambda done, total: pytest.fail("nothing is to be read"))
    finally:
        again.close()


def test_limits_of_some_of_the_steps(found):
    chl = 1
    whole = found.limits(chl, ["chl"], 0)
    one = found.limits(chl, ["chl"], 0, first=2, last=2)
    data, _ = found.field(chl, ["chl"], 2, 0)
    assert one[:2] == (pytest.approx(float(np.nanmin(data.values))), pytest.approx(float(np.nanmax(data.values))))
    both = found.limits(chl, ["chl"], 0, first=2, last=3)
    assert one[0] >= both[0] and one[1] <= both[1] and both[1] < whole[1]
    # steps beyond the run, and a first after the last, are brought in
    assert found.limits(chl, ["chl"], 0, first=99, last=200)[:2] == found.limits(chl, ["chl"], 0, first=23, last=23)[:2]
    assert found.limits(chl, ["chl"], 0, first=5, last=2)[:2] == found.limits(chl, ["chl"], 0, first=2, last=2)[:2]


def test_restart_files_are_left_out_only_by_the_filters(tmp_path):
    """Files are never left out unasked: only the Skip box's words leave
    restart files out."""
    root = nemo_simulation(str(tmp_path / "sim"), years=(2000,), layout="flat")
    regular_file(os.path.join(root, "model_restart.nc"))
    every = catalogue.scan(root, 0)
    assert "sst" in every.summary()["variables"]
    skipped = catalogue.scan(root, 0, exclude="restart")
    assert "sst" not in skipped.summary()["variables"]
    assert skipped.summary()["files"] == every.summary()["files"] - 1


def test_stopping_the_times_kills_the_process_reading_them(simulation, monkeypatch):
    """Back stops the times being read at once, though the process reading
    them is waiting on a file."""
    import threading
    import time

    monkeypatch.setattr(catalogue, "PARALLEL_FROM", 4)
    quick = catalogue.scan(simulation, 2, wait=False)
    started, outcome = threading.Event(), []

    def progress(done, total):
        started.set()

    def read():
        try:
            quick.read_times(progress)
            outcome.append("read")
        except catalogue.Stopped:
            outcome.append("stopped")

    # the process reads the files very slowly, so it is stopped part way
    real = times.Reading.run

    def slow(self, paths, found):
        def found_slowly(path, read):
            found(path, read)
            time.sleep(0.2)

        return real(self, paths, found_slowly)

    monkeypatch.setattr(times.Reading, "run", slow)
    thread = threading.Thread(target=read)
    thread.start()
    try:
        assert started.wait(30)
        quick.stop_times()
        thread.join(30)
        assert outcome == ["stopped"] and not quick.times_complete
    finally:
        quick.close()
        thread.join(30)
