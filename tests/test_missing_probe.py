"""Finding the model's missing value from its files (see oceanval.missing_probe)."""

import numpy as np
import pytest
import xarray as xr

from oceanval import missing_probe


def write(path, data, levels=None, dtype="float32", steps=1):
    """A file whose variable is data (lat, lon) at each of steps times, or
    (levels, lat, lon) if levels are given."""
    data = np.asarray(data, dtype=dtype)
    dims = ("time", "lat", "lon")
    coords = {"time": np.arange(steps, dtype="float64"), "lat": np.arange(data.shape[-2], dtype="float64"),
              "lon": np.arange(data.shape[-1], dtype="float64")}
    if levels is not None:
        dims = ("time", "deptht", "lat", "lon")
        coords["deptht"] = np.asarray(levels, dtype="float64")
    shape = (steps,) + data.shape
    ds = xr.Dataset({"thetao": (dims, np.broadcast_to(data, shape).copy())}, coords=coords)
    ds.time.attrs["units"] = "days since 2000-01-01"
    ds.to_netcdf(path, encoding={"thetao": {"_FillValue": None}})
    return str(path)


def sea(seed, value, shape=(10, 10), land=30):
    """Varying values everywhere, except `land` cells that are value."""
    rng = np.random.default_rng(seed)
    data = rng.random(shape) + 5
    data.flat[:land] = value
    return data


@pytest.mark.parametrize("value, found", [(0.0, "0"), (1e20, "1e+20"), (-999.0, "-999")])
def test_a_value_the_same_in_many_cells_of_every_file_is_found(tmp_path, value, found):
    files = [write(tmp_path / f"f{i}.nc", sea(i, value)) for i in range(4)]
    assert missing_probe.probe(files, "thetao") == found


def test_the_value_in_most_cells_wins(tmp_path):
    def data(seed):
        out = sea(seed, 0.0, land=10)
        out.flat[10:40] = 1e20
        return out

    files = [write(tmp_path / f"f{i}.nc", data(i)) for i in range(3)]
    assert missing_probe.probe(files, "thetao") == "1e+20"


def test_nothing_is_found_where_no_cells_stay_the_same(tmp_path):
    files = [write(tmp_path / f"f{i}.nc", sea(i, 0.0, land=0)) for i in range(3)]
    assert missing_probe.probe(files, "thetao") is None


def test_a_value_in_too_few_cells_is_not_found(tmp_path):
    files = [write(tmp_path / f"f{i}.nc", sea(i, 0.0, shape=(20, 20), land=2)) for i in range(3)]
    assert missing_probe.probe(files, "thetao") is None


def test_a_field_that_does_not_change_has_no_missing_value_to_find(tmp_path):
    # periodic output, as a model run twice over: every cell is the same in every file, with many values
    same = np.arange(100.0).reshape(10, 10)
    files = [write(tmp_path / f"f{i}.nc", same) for i in range(3)]
    assert missing_probe.probe(files, "thetao") is None
    # or the same everywhere
    files = [write(tmp_path / f"u{i}.nc", np.full((10, 10), 5.0)) for i in range(3)]
    assert missing_probe.probe(files, "thetao") is None


def test_a_value_that_changes_between_files_is_not_found(tmp_path):
    files = [write(tmp_path / f"f{i}.nc", sea(i, float(i))) for i in range(3)]
    assert missing_probe.probe(files, "thetao") is None


def test_the_surface_of_a_water_column_is_looked_at(tmp_path):
    # the surface is the level nearest 0, whichever end it is at: land is 0 there only
    def column(seed, levels):
        surface = sea(seed, 0.0)
        deep = sea(seed + 100, 7.0, land=0)
        return np.stack([surface, deep] if levels[0] < levels[1] else [deep, surface])

    for levels in ([1.0, 50.0], [50.0, 1.0]):
        files = [write(tmp_path / f"{levels[0]}_{i}.nc", column(i, levels), levels=levels) for i in range(3)]
        assert missing_probe.probe(files, "thetao") == "0"


def test_one_file_is_looked_at_through_its_time_steps(tmp_path):
    # the sea changes with the time, the land does not
    steps = np.stack([sea(0, 0.0) + step * (np.arange(100).reshape(10, 10) >= 30) for step in range(4)])
    file = tmp_path / "one.nc"
    ds = xr.Dataset({"thetao": (("time", "lat", "lon"), steps.astype("float32"))},
                    coords={"time": np.arange(4.0), "lat": np.arange(10.0), "lon": np.arange(10.0)})
    ds.time.attrs["units"] = "days since 2000-01-01"
    ds.to_netcdf(file, encoding={"thetao": {"_FillValue": None}})
    assert missing_probe.probe([str(file)], "thetao") == "0"


def test_a_variable_not_in_the_files_or_files_not_readable_give_none(tmp_path):
    files = [write(tmp_path / f"f{i}.nc", sea(i, 0.0)) for i in range(3)]
    assert missing_probe.probe(files, "not_there") is None
    broken = tmp_path / "broken.nc"
    broken.write_text("not a netCDF file")
    assert missing_probe.probe([str(broken), str(broken)], "thetao") is None
    assert missing_probe.probe([], "thetao") is None


def test_only_so_many_files_are_read(tmp_path, monkeypatch):
    files = [write(tmp_path / f"f{i:02d}.nc", sea(i, 0.0)) for i in range(25)]
    opened = []
    real = xr.open_dataset
    monkeypatch.setattr(xr, "open_dataset", lambda path, *a, **k: opened.append(path) or real(path, *a, **k))
    assert missing_probe.probe(files, "thetao") == "0"
    assert len(opened) == missing_probe.MOST_FILES
    # evenly spread, so the first and the last are among them
    assert files[0] in opened and files[-1] in opened
