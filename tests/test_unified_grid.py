"""Masking the matchups by a unified grid (oceanval.unified_grid): the
mask's file kept in the matchups folder, the gridded model's monthly means
put on its grid and masked, and the point matchups at locations it has no
value at dropped."""

import glob
import os

import nctoolkit as nc
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval import sim_unify, unified_grid
from oceanval.session import session_info


def lonlat(lon, lat):
    return {
        "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
        "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"}),
    }


def write_mask(path, lon, lat, missing):
    """A mask as sim_unify.prepare writes it: 0, but missing at the
    longitudes missing(lon) is True for, with an explicit _FillValue."""
    values = np.zeros((lat.size, lon.size), "f4")
    values[:, missing(lon)] = np.nan
    mask = xr.DataArray(values, coords=lonlat(lon, lat), dims=("lat", "lon"), name="grid")
    mask.to_dataset().to_netcdf(path, encoding={"grid": {"_FillValue": np.float32(sim_unify.FILL)}})
    return str(path)


def write_model(path, lon, lat, levels=1):
    """Two monthly means of a model's temperature, 5 everywhere, with
    levels depths."""
    times = pd.date_range("2000-01-01", periods=2, freq="MS")
    coords = {"time": times, **lonlat(lon, lat)}
    dims = ("time", "lat", "lon")
    shape = (2, lat.size, lon.size)
    if levels > 1:
        coords["depth"] = ("depth", np.arange(levels) * 10.0, {"units": "m", "positive": "down"})
        dims = ("time", "depth", "lat", "lon")
        shape = (2, levels, lat.size, lon.size)
    data = xr.DataArray(np.full(shape, 5.0, "f4"), coords=coords, dims=dims, name="model")
    data.to_dataset().to_netcdf(path, encoding={"model": {"_FillValue": np.float32(-1e20)}})
    return str(path)


LON = np.arange(-10, 0.01, 1.0)
LAT = np.arange(50, 60.01, 1.0)


@pytest.fixture
def mask(tmp_path):
    """Missing west of 7.5W: the three westernmost columns."""
    return write_mask(tmp_path / "mask.nc", LON, LAT, lambda lon: lon < -7.5)


class TestCheckAndKeep:
    def test_check(self, mask):
        assert unified_grid.check(None) is None
        assert unified_grid.check({"path": mask, "method": "bil"}) == {"path": mask, "method": "bil"}
        with pytest.raises(TypeError):
            unified_grid.check({"path": mask})
        with pytest.raises(TypeError):
            unified_grid.check(mask)
        with pytest.raises(ValueError, match="is not a file"):
            unified_grid.check({"path": mask + "x", "method": "bil"})
        with pytest.raises(ValueError, match="method must be one of"):
            unified_grid.check({"path": mask, "method": "best"})

    def test_keep_copies_it_into_the_matchups_folder(self, mask, tmp_path):
        folder = tmp_path / "oceanval_matchups"
        folder.mkdir()
        kept = unified_grid.keep({"path": mask, "method": "nn"}, str(folder))
        assert kept == {"path": str(folder / unified_grid.FILE), "method": "nn"}
        assert open(kept["path"], "rb").read() == open(mask, "rb").read()
        # a script run again names the copy itself
        assert unified_grid.keep(kept, str(folder)) == kept
        assert unified_grid.keep(None, str(folder)) is None


def masked_values(ds):
    return ds.to_xarray()["model"].values


class TestGridded:
    def test_on_another_grid_it_is_regridded_and_masked(self, mask, tmp_path):
        ds = nc.open_data(write_model(tmp_path / "model.nc", np.arange(-12, 2.01, 0.5), np.arange(48, 62.01, 0.5)))
        unified_grid.mask_gridded(ds, {"path": mask, "method": "bil"})
        values = masked_values(ds)
        # on the mask's grid, for every time
        assert values.shape == (2, LAT.size, LON.size)
        assert np.isnan(values[:, :, LON < -7.5]).all()
        assert (values[:, :, LON > -7.5] == 5).all()

    @pytest.mark.parametrize("method", ["bil", "nn"])
    def test_it_is_cropped_to_the_mask_first_unless_it_is_fvcom(self, mask, tmp_path, monkeypatch, method):
        path = write_model(tmp_path / "model.nc", np.arange(-40, 30.01, 0.5), np.arange(30, 75.01, 0.5))
        cropped = []
        real = sim_unify.crop_to
        monkeypatch.setattr(sim_unify, "crop_to", lambda ds, box: cropped.append(box) or real(ds, box))
        ds = nc.open_data(path)
        unified_grid.mask_gridded(ds, {"path": mask, "method": method})
        # the extent of the mask's cells with values, a degree wider each way
        assert cropped == [([-8.0, 1.0], [49.0, 61.0])]
        with_crop = masked_values(ds)
        monkeypatch.setattr(sim_unify, "crop_to", lambda ds, box: cropped.append(box) or False)
        ds = nc.open_data(path)
        unified_grid.mask_gridded(ds, {"path": mask, "method": method}, fvcom=True)
        # FVCOM output is left as it is
        assert len(cropped) == 1
        assert np.allclose(with_crop, masked_values(ds), equal_nan=True)

    def test_the_crop_is_to_the_masks_cells_with_values(self, tmp_path, monkeypatch):
        # the mask has values (0) only east of 7.5W: the crop is to those, a degree wider
        path = write_mask(tmp_path / "east.nc", LON, LAT, lambda lon: lon < -7.5)
        cropped = []
        monkeypatch.setattr(sim_unify, "crop_to", lambda ds, box: cropped.append(box) or False)
        ds = nc.open_data(write_model(tmp_path / "model.nc", np.arange(-40, 30.01, 0.5), np.arange(30, 75.01, 0.5)))
        unified_grid.mask_gridded(ds, {"path": path, "method": "nn"})
        assert cropped == [([-8.0, 1.0], [49.0, 61.0])]

    def test_on_its_grid_it_is_not_regridded(self, mask, tmp_path, monkeypatch):
        ds = nc.open_data(write_model(tmp_path / "model.nc", LON, LAT))
        asked = []
        monkeypatch.setattr(type(ds), "regrid", lambda self, *args, **kwargs: asked.append(args))
        unified_grid.mask_gridded(ds, {"path": mask, "method": "bil"})
        assert asked == []
        values = masked_values(ds)
        assert np.isnan(values[:, :, LON < -7.5]).all() and (values[:, :, LON > -7.5] == 5).all()

    def test_every_level_is_masked(self, mask, tmp_path):
        ds = nc.open_data(write_model(tmp_path / "model.nc", LON, LAT, levels=3))
        unified_grid.mask_gridded(ds, {"path": mask, "method": "nn"})
        values = masked_values(ds)
        assert values.shape == (2, 3, LAT.size, LON.size)
        assert np.isnan(values[..., LON < -7.5]).all() and (values[..., LON > -7.5] == 5).all()

    def test_no_grid_changes_nothing(self, tmp_path):
        ds = nc.open_data(write_model(tmp_path / "model.nc", LON, LAT))
        assert unified_grid.mask_gridded(ds, None) is ds
        assert (masked_values(ds) == 5).all()


def matchups():
    return pd.DataFrame({
        # in a missing cell, in cells with a value (one whose lon/lat need many decimals), beyond the grid
        "lon": [-9.3, -5.123456789, -1.5, -1.5, 5.0, -4.2],
        "lat": [55.0, 54.333333333, 51.7, 51.7, 55.0, 70.0],
        "depth": [0, 0, 0, 10, 0, 0],
        "observation": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "model": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
    })


class TestPoints:
    def test_only_locations_with_a_value_are_kept(self, mask):
        df = matchups()
        kept = unified_grid.mask_points(df, {"path": mask, "method": "bil"})
        # as they were, lon/lat to the last decimal, in their order, with the same columns
        pd.testing.assert_frame_equal(kept, df.iloc[[1, 2, 3]].reset_index(drop=True))

    def test_a_global_mask_from_0_to_360(self, tmp_path):
        lon = np.arange(0.5, 360, 1.0)
        lat = np.arange(-89.5, 90, 1.0)
        # missing from 350E to 360E, which is 10W to 0
        path = write_mask(tmp_path / "global.nc", lon, lat, lambda lon: lon > 350)
        df = pd.DataFrame({"lon": [-5.2, 20.3, -15.7], "lat": [50.1, 50.1, 50.1], "observation": [1.0, 2.0, 3.0]})
        kept = unified_grid.mask_points(df, {"path": path, "method": "bil"})
        assert list(kept.lon) == [20.3, -15.7]

    def test_no_grid_or_no_rows_changes_nothing(self, mask):
        df = matchups()
        assert unified_grid.mask_points(df, None) is df
        empty = df.iloc[:0]
        assert unified_grid.mask_points(empty, {"path": mask, "method": "nn"}) is empty

    def test_the_extrapolation_setting_is_put_back(self, mask, monkeypatch):
        monkeypatch.setenv("REMAP_EXTRAPOLATE", "on")
        unified_grid.mask_points(matchups(), {"path": mask, "method": "nn"})
        assert os.environ["REMAP_EXTRAPOLATE"] == "on"
        monkeypatch.delenv("REMAP_EXTRAPOLATE")
        unified_grid.mask_points(matchups(), {"path": mask, "method": "nn"})
        assert "REMAP_EXTRAPOLATE" not in os.environ


SIM_DIR = "data/example_temperature"


def example_grid():
    """The longitudes and latitudes of the example simulation's grid."""
    with xr.open_dataset(sorted(glob.glob(f"{SIM_DIR}/2000/01/*.nc"))[0]) as data:
        return data.lon.values, data.lat.values


@pytest.fixture
def example_mask(tmp_path):
    """A mask on the example simulation's own grid, missing in its two
    western columns."""
    lon, lat = example_grid()
    return write_mask(tmp_path / "example_mask.nc", lon, lat, lambda x: x < lon[2])


class TestMatchup:
    """matchup(unified_grid=) keeps the mask in its matchups folder and masks
    the matchups by it."""

    @pytest.fixture(autouse=True)
    def fresh(self):
        oceanval.reset()
        yield
        oceanval.reset()

    def test_gridded(self, tmp_path, example_mask):
        oceanval.add_gridded_comparison(
            name="temperature", obs_path=SIM_DIR, source="foo", model_variable="votemper",
            obs_variable="votemper", climatology=False, start=2000, end=2000,
        )
        out_dir = str(tmp_path / "out")
        oceanval.matchup(
            sim_dir=SIM_DIR, start=2000, end=2000, ask=False, cores=1, out_dir=out_dir,
            unified_grid={"path": example_mask, "method": "bil"},
        )
        kept = os.path.join(out_dir, "oceanval_matchups", unified_grid.FILE)
        assert open(kept, "rb").read() == open(example_mask, "rb").read()
        assert session_info["unified_grid"] == {"path": kept, "method": "bil"}
        with xr.open_dataset(f"{out_dir}/oceanval_matchups/gridded/temperature/foo_temperature_surface.nc") as data:
            model = data.model.values
            lon = data.lon.values
        west = lon < example_grid()[0][2]
        assert np.isnan(model[..., west]).all()
        assert np.isfinite(model[..., ~west]).any()

    @pytest.mark.parametrize("masked", [True, False])
    def test_point(self, tmp_path, example_mask, masked):
        lon, lat = example_grid()
        # inside the grid, as a point on an outer cell centre can end up outside it (see test_point_matchups)
        obs = pd.DataFrame({
            "lon": [lon[1], lon[1], lon[2], lon[2]],
            "lat": [lat[2], lat[5], lat[3], lat[4]],
            "year": 2000, "month": 1, "day": 2, "observation": [1.0, 2.0, 3.0, 4.0],
        })
        os.makedirs(tmp_path / "obs")
        obs.to_csv(tmp_path / "obs" / "obs.csv", index=False)
        oceanval.add_point_comparison(
            name="temperature", source="mine", model_variable="votemper", obs_path=str(tmp_path / "obs"),
        )
        out_dir = str(tmp_path / "out")
        oceanval.matchup(
            sim_dir=SIM_DIR, start=2000, end=2000, ask=False, cores=1, out_dir=out_dir,
            unified_grid={"path": example_mask, "method": "bil"} if masked else None,
        )
        df = pd.read_csv(f"{out_dir}/oceanval_matchups/point/surface/temperature/mine/mine_surface_temperature.csv")
        # with the mask, only those in the column it has a value in
        assert sorted(df.observation) == ([3.0, 4.0] if masked else [1.0, 2.0, 3.0, 4.0])

    def test_no_grid_masks_nothing(self, tmp_path):
        oceanval.add_gridded_comparison(
            name="temperature", obs_path=SIM_DIR, source="foo", model_variable="votemper",
            obs_variable="votemper", climatology=False, start=2000, end=2000,
        )
        out_dir = str(tmp_path / "out")
        oceanval.matchup(sim_dir=SIM_DIR, start=2000, end=2000, ask=False, cores=1, out_dir=out_dir)
        assert session_info["unified_grid"] is None
        assert not os.path.exists(os.path.join(out_dir, "oceanval_matchups", unified_grid.FILE))
        with xr.open_dataset(f"{out_dir}/oceanval_matchups/gridded/temperature/foo_temperature_surface.nc") as data:
            assert np.isfinite(data.model.values).all(axis=0).all()
