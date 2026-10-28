"""Point matchups against synthetic observations taken from the model itself.

The observations are the model's own values at grid cell centres, averaged
over whatever time (and depth) information each csv holds, so every matchup
should give back model values equal to the observations.
"""

import glob
import os
import pickle

import nctoolkit as nc
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval


# a short simulation: the first 2 days of two months in two years
SIM_MONTHS = [(2000, 1), (2000, 2), (2001, 1), (2001, 2)]
N_DAYS = 2
THICKNESS = "data/example/e3t.nc"
# grid cells (lon index, lat index) used as observation locations. These are
# inside the grid, as a point on an outer cell centre can end up a rounding
# error outside the grid once read back from csv
CELLS = [(1, 1), (1, 4), (2, 2), (2, 6)]
PROFILE_LEVELS = [10, 30]
TOL = 1e-4


def _make_simulation(sim_dir):
    """Copy the start of a few months of data/example into sim_dir/YYYY/MM"""
    paths = []
    for year, month in SIM_MONTHS:
        src = glob.glob(f"data/example/{year}/{month:02d}/*grid_T.nc")[0]
        out_dir = os.path.join(sim_dir, str(year), f"{month:02d}")
        os.makedirs(out_dir)
        ds = nc.open_data(src, checks=False)
        ds.subset(time=list(range(N_DAYS)))
        ds.to_nc(os.path.join(out_dir, os.path.basename(src)))
        paths.append(os.path.join(out_dir, os.path.basename(src)))
    return paths


def _model_values(paths, cells=CELLS):
    """votemper at the given cells, for every day and level, with each level's depth"""
    ds = xr.concat(
        [xr.open_dataset(ff).votemper.load() for ff in paths], dim="time_counter"
    )
    lon_i = xr.DataArray([x for x, y in cells], dims="point")
    lat_i = xr.DataArray([y for x, y in cells], dims="point")
    ds = ds.isel(lon=lon_i, lat=lat_i)
    ds = ds.assign_coords(level=("deptht", np.arange(ds.sizes["deptht"])))
    df = ds.to_dataframe().reset_index()
    df = df.assign(
        year=df.time_counter.dt.year,
        month=df.time_counter.dt.month,
        day=df.time_counter.dt.day,
        value=df.votemper.astype("float64"),
    )

    # depths are the centre of each cell, as matchup works them out
    e3t = xr.open_dataset(THICKNESS).e3t.isel(time_counter=0).astype("float64")
    depth = (e3t.cumsum("deptht") - e3t / 2).isel(lon=lon_i, lat=lat_i)
    df_depth = depth.to_dataframe(name="depth").reset_index()
    df = df.merge(df_depth.loc[:, ["point", "deptht", "depth"]])

    return df.loc[:, ["lon", "lat", "year", "month", "day", "level", "depth", "value"]]


def _average(model, keys):
    """The model averaged at the resolution of keys, as observations"""
    return (
        model.groupby(keys)
        .value.mean()
        .reset_index()
        .rename(columns={"value": "observation"})
    )


def _write_obs(obs_dir, df, name="obs.csv", compression=None):
    os.makedirs(obs_dir, exist_ok=True)
    df.to_csv(os.path.join(obs_dir, name), index=False, compression=compression)
    return obs_dir


def _add(source, obs_path, **kwargs):
    oceanval.add_point_comparison(
        name="temperature",
        source=source,
        model_variable="votemper",
        obs_path=obs_path,
        **kwargs,
    )


def _matchup_dir(out_dir, layer, source):
    return f"{out_dir}/oceanval_matchups/point/{layer}/temperature/{source}/"


def _read(out_dir, layer, source):
    return pd.read_csv(_matchup_dir(out_dir, layer, source) + f"{source}_{layer}_temperature.csv")


def _matchup_dict(out_dir, layer, source):
    with open(_matchup_dir(out_dir, layer, source) + "matchup_dict.pkl", "rb") as f:
        return pickle.load(f)


def _check(df, expected, keys):
    """Check a matchup has exactly the expected rows, with model and observation matching them"""
    assert sorted(df.columns) == sorted(keys + ["model", "observation"])
    assert len(df) == len(expected)
    # locations read back from csv can be a rounding error away from the originals
    coords = [x for x in ["lon", "lat", "depth"] if x in keys]
    df = df.round({x: 6 for x in coords})
    expected = expected.round({x: 6 for x in coords})
    df = df.merge(expected, on=keys, suffixes=("", "_expected"))
    assert len(df) == len(expected)
    assert np.abs(df.model - df.observation_expected).max() < TOL
    assert np.abs(df.observation - df.observation_expected).max() < TOL


@pytest.fixture(scope="module")
def simulation(tmp_path_factory):
    sim_dir = str(tmp_path_factory.mktemp("sim"))
    paths = _make_simulation(sim_dir)
    return sim_dir, paths


@pytest.fixture(scope="module")
def model(simulation):
    sim_dir, paths = simulation
    return _model_values(paths)


SURFACE_CASES = [
    ("notime", []),
    ("month", ["month"]),
    ("year", ["year"]),
    ("yearmonth", ["year", "month"]),
    ("monthday", ["month", "day"]),
]

PROFILE_CASES = [
    ("profile", []),
    ("profilemonth", ["month"]),
]


@pytest.fixture(scope="module")
def matchups(simulation, model, tmp_path_factory):
    """One matchup of every kind of point data, returning the output directory and expected matchups"""
    sim_dir, paths = simulation
    obs_root = str(tmp_path_factory.mktemp("obs"))
    out_dir = str(tmp_path_factory.mktemp("out"))
    surface = model.query("level == 0")
    daily = ["lon", "lat", "year", "month", "day"]
    expected = dict()

    oceanval.reset()

    for source, keys in SURFACE_CASES:
        expected[source] = _average(surface, ["lon", "lat"] + keys)
        _add(source, _write_obs(f"{obs_root}/{source}", expected[source]))

    # a depth column on a surface matchup: only the top 5 m should be used
    expected["depthsurface"] = _average(surface, ["lon", "lat"])
    top = _average(surface, ["lon", "lat", "depth"])
    deep = _average(model.query("level == 10"), ["lon", "lat", "depth"])
    deep = deep.assign(observation=lambda x: x.observation + 5)
    assert deep.depth.min() > 5
    _add(
        "depthsurface",
        _write_obs(f"{obs_root}/depthsurface", pd.concat([top, deep])),
    )

    # one profile has an extra depth. nctoolkit's generate_grid treats locations
    # as a regular lon/lat grid, with evenly spaced lats, when there are as many
    # as n lons x n lats of them, which drops points in vertical matchups
    extra_depth = _model_values(paths, cells=CELLS[:1]).query("level == 20")
    profiles = pd.concat([model.query("level in @PROFILE_LEVELS"), extra_depth])
    for source, keys in PROFILE_CASES:
        expected[source] = _average(profiles, ["lon", "lat", "depth"] + keys)
        _add(source, _write_obs(f"{obs_root}/{source}", expected[source]), vertical=True)

    # observations above the centre of the top cell, as at 0 m, are in the top
    # cell, so they are compared with its value, at the depth observed. The
    # extra depth again keeps generate_grid from taking the points as a grid
    top_cell = pd.concat(
        [
            surface.assign(depth=0.0),
            surface.assign(depth=lambda x: x.depth / 2),
            model.query("level == 10"),
            extra_depth,
        ]
    )
    expected["profiletop"] = _average(top_cell, ["lon", "lat", "depth"])
    _add(
        "profiletop",
        _write_obs(f"{obs_root}/profiletop", expected["profiletop"]),
        vertical=True,
    )

    # observations split over two files, one of them compressed, with a source column
    expected["files"] = _average(surface, ["lon", "lat"])
    obs = expected["files"].assign(source="synthetic")
    _write_obs(f"{obs_root}/files", obs.iloc[:2], name="part1.csv")
    _write_obs(f"{obs_root}/files", obs.iloc[2:], name="part2.csv", compression="gzip")
    _add("files", f"{obs_root}/files")

    # the path can be one csv file, not a directory
    expected["onefile"] = _average(surface, ["lon", "lat"])
    _write_obs(f"{obs_root}/onefile", expected["onefile"], name="data.csv")
    _add("onefile", f"{obs_root}/onefile/data.csv")

    # only 2001 is compared, and the observations need doubling. One observation is
    # repeated, one is outside the model grid and one is missing
    expected["rows"] = _average(surface.query("year == 2001"), daily)
    obs = _average(surface, daily)
    first = obs.query("year == 2001").iloc[[0]]
    extra = _average(_model_values(paths, cells=[(2, 4)]).query("level == 0"), daily)
    obs = pd.concat(
        [
            obs.drop(first.index),
            first.assign(observation=lambda x: x.observation + 0.5),
            first.assign(observation=lambda x: x.observation - 0.5),
            first.assign(lon=10.0, lat=58.0),
            extra.query("year == 2001").iloc[[0]].assign(observation=np.nan),
        ]
    )
    obs = obs.assign(observation=lambda x: x.observation / 2)
    _add("rows", _write_obs(f"{obs_root}/rows", obs), start=2001, end=2001, obs_multiplier=2)

    # observations from a year the simulation does not cover
    obs = _average(surface, daily).assign(year=1990)
    _add("nooverlap", _write_obs(f"{obs_root}/nooverlap", obs))

    oceanval.matchup(
        sim_dir=sim_dir,
        start=2000,
        end=2001,
        thickness=THICKNESS,
        ask=False,
        cores=1,
        out_dir=out_dir,
    )

    yield out_dir, expected

    oceanval.reset()


class TestPointColumns:
    """Each combination of time and depth columns in the point data"""

    @pytest.mark.parametrize("source, keys", SURFACE_CASES)
    def test_surface(self, matchups, source, keys):
        out_dir, expected = matchups
        df = _read(out_dir, "surface", source)
        _check(df, expected[source], ["lon", "lat"] + keys)

    def test_depth_on_surface(self, matchups):
        """Test that only observations in the top 5 m are used, and depth is dropped"""
        out_dir, expected = matchups
        df = _read(out_dir, "surface", "depthsurface")
        _check(df, expected["depthsurface"], ["lon", "lat"])

    @pytest.mark.parametrize("source, keys", PROFILE_CASES)
    def test_profile(self, matchups, source, keys):
        out_dir, expected = matchups
        df = _read(out_dir, "all", source)
        _check(df, expected[source], ["lon", "lat", "depth"] + keys)

    def test_profile_above_the_top_cell(self, matchups):
        """Test that observations above the centre of the top cell, as at 0 m, are
        compared with the top cell, and keep the depth observed"""
        out_dir, expected = matchups
        df = _read(out_dir, "all", "profiletop")
        _check(df, expected["profiletop"], ["lon", "lat", "depth"])
        assert (df.depth == 0).sum() == len(CELLS)


class TestPointFiles:

    def test_multiple_files(self, matchups):
        """Test that all csv files are used, including compressed ones, and source is dropped"""
        out_dir, expected = matchups
        df = _read(out_dir, "surface", "files")
        _check(df, expected["files"], ["lon", "lat"])


    def test_one_file(self, matchups):
        """Test that the path can be a csv file, not a directory"""
        out_dir, expected = matchups
        df = _read(out_dir, "surface", "onefile")
        _check(df, expected["onefile"], ["lon", "lat"])


class TestLongitudes:

    def test_0_to_360(self, tmp_path):
        """Test that observation longitudes above 180 are matched as the
        negative longitudes they are"""
        sim_dir = str(tmp_path / "sim")
        paths = _make_simulation(sim_dir)
        # move the model to negative longitudes
        for ff in paths:
            with xr.open_dataset(ff) as ds:
                ds = ds.load().assign_coords(lon=ds.lon - 10)
            ds.to_netcdf(ff)
        surface = _model_values(paths).query("level == 0")
        assert surface.lon.max() < 0
        expected = _average(surface, ["lon", "lat"])
        obs = expected.assign(lon=lambda x: x.lon + 360)

        oceanval.reset()
        _add("lon360", _write_obs(str(tmp_path / "obs"), obs))
        out_dir = str(tmp_path / "out")
        oceanval.matchup(
            sim_dir=sim_dir,
            start=2000,
            end=2001,
            thickness=THICKNESS,
            ask=False,
            cores=1,
            out_dir=out_dir,
        )
        df = _read(out_dir, "surface", "lon360")
        oceanval.reset()
        assert df.lon.max() < 0
        _check(df, expected, ["lon", "lat"])


class TestPointRows:

    def test_rows(self, matchups):
        """Test that repeated observations are averaged, and off-grid or missing ones are dropped"""
        out_dir, expected = matchups
        df = _read(out_dir, "surface", "rows")
        _check(df, expected["rows"], ["lon", "lat", "year", "month", "day"])

    def test_comparison_years(self, matchups):
        """Test that only the years of the comparison are matched up"""
        out_dir, expected = matchups
        df = _read(out_dir, "surface", "rows")
        assert list(df.year.unique()) == [2001]
        matchup_dict = _matchup_dict(out_dir, "surface", "rows")
        assert matchup_dict["start"] == 2001
        assert matchup_dict["end"] == 2001

    def test_no_overlap(self, matchups):
        """Test that observations outside the simulation give no matchup"""
        out_dir, expected = matchups
        ff = _matchup_dir(out_dir, "surface", "nooverlap") + "nooverlap_surface_temperature.csv"
        assert not os.path.exists(ff)


class TestPointTimeRes:

    def test_month(self, simulation, model, tmp_path):
        """Test that point_time_res can drop year and day from daily observations"""
        sim_dir, paths = simulation
        surface = model.query("level == 0")
        daily = _average(surface, ["lon", "lat", "year", "month", "day"])
        # observations outside the simulation years are not used
        outside = daily.query("month == 1").assign(
            year=1995, observation=lambda x: x.observation + 5
        )

        oceanval.reset()
        _add("foo", _write_obs(str(tmp_path / "obs"), pd.concat([daily, outside])))
        oceanval.matchup(
            sim_dir=sim_dir,
            start=2000,
            end=2001,
            point_time_res="month",
            ask=False,
            cores=1,
            out_dir=str(tmp_path / "out"),
        )

        out_dir = str(tmp_path / "out")
        df = _read(out_dir, "surface", "foo")
        _check(df, _average(surface, ["lon", "lat", "month"]), ["lon", "lat", "month"])
        assert _matchup_dict(out_dir, "surface", "foo")["point_time_res"] == ["month"]
        oceanval.reset()

    def test_a_dataset_can_set_its_own(self, simulation, model, tmp_path):
        """A point_time_res given to add_point_comparison replaces matchup's"""
        sim_dir, paths = simulation
        surface = model.query("level == 0")
        daily = _average(surface, ["lon", "lat", "year", "month", "day"])

        oceanval.reset()
        _add("foo", _write_obs(str(tmp_path / "obs"), daily), point_time_res="month")
        oceanval.matchup(
            sim_dir=sim_dir,
            start=2000,
            end=2001,
            point_time_res=["year", "month", "day"],
            ask=False,
            cores=1,
            out_dir=str(tmp_path / "out"),
        )

        out_dir = str(tmp_path / "out")
        df = _read(out_dir, "surface", "foo")
        _check(df, _average(surface, ["lon", "lat", "month"]), ["lon", "lat", "month"])
        assert _matchup_dict(out_dir, "surface", "foo")["point_time_res"] == ["month"]
        oceanval.reset()


class TestPointStartEnd:
    """Observations without a year should only be compared with the years from start to end"""

    @pytest.mark.parametrize("layout", ["monthly", "multiyear"])
    def test_start_end(self, simulation, model, tmp_path, layout):
        sim_dir, paths = simulation
        n_dirs_down = 2
        if layout == "multiyear":
            # all of the simulation in one file
            sim_dir = str(tmp_path / "sim")
            os.makedirs(sim_dir)
            ds = nc.open_data(paths, checks=False)
            ds.merge("time")
            ds.to_nc(os.path.join(sim_dir, "amm7_multiyear_grid_T.nc"))
            n_dirs_down = 0

        surface = model.query("level == 0 and year == 2001")
        cases = [("notime", []), ("month", ["month"])]
        oceanval.reset()
        for source, keys in cases:
            obs = _average(surface, ["lon", "lat"] + keys)
            _add(source, _write_obs(str(tmp_path / "obs" / source), obs))

        out_dir = str(tmp_path / "out")
        oceanval.matchup(
            sim_dir=sim_dir,
            start=2001,
            end=2001,
            n_dirs_down=n_dirs_down,
            ask=False,
            cores=1,
            out_dir=out_dir,
        )

        for source, keys in cases:
            df = _read(out_dir, "surface", source)
            _check(df, _average(surface, ["lon", "lat"] + keys), ["lon", "lat"] + keys)
            matchup_dict = _matchup_dict(out_dir, "surface", source)
            assert matchup_dict["start"] == 2001
            assert matchup_dict["end"] == 2001
        oceanval.reset()


def _run_python(tmp_path, code):
    """Run code in a fresh python, with its output in a file: the Manager servers
    a killed process leaves behind would keep a pipe open."""
    import subprocess
    import sys

    out = tmp_path / "out.txt"
    with open(out, "w") as f:
        process = subprocess.Popen([sys.executable, "-c", code], stdout=f, stderr=subprocess.STDOUT)
        process.wait(timeout=120)
    return out.read_text().split()


def _gone(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            return "\nState:\tZ" in f.read()
    except FileNotFoundError:
        return True


class TestManagers:
    """A Manager's server is a process of its own, which only the process that started it
    stops: one started by a process that crashed ran for ever."""

    def test_none_is_started_by_importing_oceanval(self, tmp_path):
        found = _run_python(tmp_path, (
            "import multiprocessing as mp\n"
            "import nctoolkit\n"
            "before = {p.pid for p in mp.active_children()}\n"
            "import oceanval\n"
            "from oceanval import matchall\n"
            "print(len({p.pid for p in mp.active_children()} - before), matchall.session_warnings)\n"
        ))
        assert found[-2:] == ["0", "None"]

    @pytest.mark.skipif(not os.path.isdir("/proc"), reason="looks for processes in /proc")
    def test_one_ends_when_the_process_that_started_it_is_killed(self, tmp_path):
        import signal
        import time

        found = _run_python(tmp_path, (
            "import multiprocessing as mp, os, signal\n"
            "from oceanval import matchall\n"
            "others = [p.pid for p in mp.active_children()]\n"
            "manager = matchall._manager()\n"
            "print('others', *others, 'server', manager._process.pid, flush=True)\n"
            "os.kill(os.getpid(), signal.SIGKILL)\n"
        ))
        server = int(found[found.index("server") + 1])
        others = [int(pid) for pid in found[found.index("others") + 1:found.index("server")]]
        try:
            deadline = time.time() + 10
            while not _gone(server) and time.time() < deadline:
                time.sleep(0.2)
            assert _gone(server)
        finally:
            # nctoolkit's own servers, which have no such check in nctoolkit 1.3.9
            for pid in others + [server]:
                if not _gone(pid):
                    os.kill(pid, signal.SIGKILL)
