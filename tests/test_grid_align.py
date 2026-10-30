"""How the grid simulations share is worked out (oceanval.grid_align), which
Compare simulations' grids step uses."""


import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import grid_align, sim_unify


def write_grid(path, res, land_west_of, fill=-1e20, bounds=False):
    """A simulation's file on a lon-lat grid of res degrees, with two times
    and three depths: 5 everywhere, but fill west of land_west_of. bounds:
    with the time and depth bounds variables NEMO writes, which CDO keeps
    ahead of the variable."""
    lon = np.arange(-10 + res / 2, 10, res)
    lat = np.arange(40 + res / 2, 60, res)
    values = np.full((2, 3, lat.size, lon.size), 5.0)
    values[:, :, :, lon < land_west_of] = fill
    data = xr.Dataset(
        {"temp": (("time", "depth", "lat", "lon"), values, {"long_name": "Temperature", "units": "degC"})},
        coords={"time": pd.date_range("2000-01-01", periods=2), "depth": [0.0, 10.0, 20.0], "lat": lat, "lon": lon},
    )
    data.lon.attrs.update(units="degrees_east", standard_name="longitude")
    data.lat.attrs.update(units="degrees_north", standard_name="latitude")
    data.depth.attrs.update(units="m", positive="down")
    if bounds:
        data["time"].attrs["bounds"] = "time_bnds"
        data["depth"].attrs["bounds"] = "depth_bnds"
        data["time_bnds"] = (("time", "bnds"), np.zeros((2, 2)))
        data["depth_bnds"] = (("depth", "bnds"), np.zeros((3, 2)))
        data["time_bnds"].encoding["units"] = "days since 2000-01-01"
        data = data[["time_bnds", "depth_bnds", "temp"]]
    data.to_netcdf(path)
    return str(path)


@pytest.fixture
def grids(tmp_path):
    """Two simulations' files: a 1 degree grid with land west of 5W, and a
    half degree grid with land west of 0, whose land is 0."""
    return (
        write_grid(tmp_path / "a.nc", 1.0, -5),
        write_grid(tmp_path / "b.nc", 0.5, 0, fill=0.0),
    )


class TestUnified:
    def test_the_missing_values_are_made_missing(self, grids):
        field = grid_align.field_for_cdo(grids[0], "temp", 0, [(-1e20, -1e20)])
        values = field.to_xarray()["temp"].values.squeeze()
        assert values.shape == (20, 20)
        assert np.isnan(values[:, :5]).all() and (values[:, 5:] == 5).all()

    @pytest.mark.parametrize("method", list(grid_align.REGRID_METHODS))
    def test_the_cells_every_simulation_has_are_1(self, grids, method):
        fields = {
            "1": grid_align.field_for_cdo(grids[0], "temp", 0, [(-1e20, -1e20)]),
            "2": grid_align.field_for_cdo(grids[1], "temp", 0, [(0, 0)]),
        }
        mask, shared, total = grid_align.unified(fields, "1", method)
        values = mask.values.squeeze()
        # on the baseline's grid: the cells east of 0 have values in both
        assert values.shape == (20, 20) and (shared, total) == (200, 400)
        assert (values[:, 10:] == 1).all() and np.isnan(values[:, :10]).all()
        assert grid_align.about(mask)["lon"] == "lon"

    @pytest.mark.parametrize("method", ["bil", "nn"])
    def test_on_a_box_there_is_no_baseline(self, grids, method):
        fields = {
            "1": grid_align.field_for_cdo(grids[0], "temp", 0, [(-1e20, -1e20)]),
            "2": grid_align.field_for_cdo(grids[1], "temp", 0, [(0, 0)]),
        }
        box = sim_unify.check_unify({"lon": [-9, 9], "lat": [41, 59], "res": 1}, ["1", "2"])
        mask, shared, total = grid_align.unified(fields, None, method, box=box)
        values = mask.values.squeeze()
        # nctoolkit's to_latlon has cells on both edges of the box: 19 by 19
        assert values.shape == (19, 19) and total == 361
        # the second simulation has land west of 0, so only the cells east of it have a value in both
        lon = mask.lon.values.squeeze()
        assert (values[:, lon >= 1] == 1).all() and np.isnan(values[:, lon <= -1]).all()
        assert 0 < shared <= total

    @pytest.mark.parametrize("method", ["nn", "bil"])
    def test_the_variable_is_not_a_bounds_variable(self, tmp_path, method):
        # NEMO's files have time_counter_bnds and deptht_bnds, which come first once CDO has selected the variable
        with_bounds = write_grid(tmp_path / "nemo.nc", 1.0, -5, bounds=True)
        plain = write_grid(tmp_path / "plain.nc", 0.5, 0, fill=0.0)
        field = grid_align.field_for_cdo(with_bounds, "temp", 0, [(-1e20, -1e20)])
        assert "time_bnds" in field.to_xarray().data_vars
        fields = {"1": field, "2": grid_align.field_for_cdo(plain, "temp", 0, [(0, 0)])}
        mask, shared, total = grid_align.unified(fields, "1", method)
        assert mask.values.squeeze().shape == (20, 20) and (shared, total) == (200, 400)
        fields = {"1": grid_align.field_for_cdo(plain, "temp", 0, [(0, 0)]), "2": field}
        shared, total = grid_align.unified(fields, "1", method)[1:]
        # bilinear regridding spreads the missing cells at the edge of the land
        assert total == 1600 and (shared == 800 if method == "nn" else 0 < shared <= 800)

    def test_field_variable(self):
        data = xr.Dataset({
            "time_bnds": (("time", "bnds"), np.zeros((1, 2))),
            "temp": (("time", "y", "x"), np.zeros((1, 3, 4))),
            "mask": (("y", "x"), np.zeros((3, 4))),
        })
        assert sim_unify.field_variable(data, "temp").name == "temp"
        assert sim_unify.field_variable(data, "grid").name == "temp"
        with pytest.raises(ValueError, match="bounds"):
            sim_unify.field_variable(data[["time_bnds"]], "grid")

    def test_on_the_other_grid_as_the_baseline(self, grids):
        fields = {
            "1": grid_align.field_for_cdo(grids[0], "temp", 0, [(-1e20, -1e20)]),
            "2": grid_align.field_for_cdo(grids[1], "temp", 0, [(0, 0)]),
        }
        mask, shared, total = grid_align.unified(fields, "2", "nn")
        assert mask.values.squeeze().shape == (40, 40) and (shared, total) == (800, 1600)

    def test_the_baseline_and_method_must_be_chosen(self, grids):
        fields = {"1": grid_align.field_for_cdo(grids[0], "temp")}
        with pytest.raises(ValueError, match="baseline"):
            grid_align.unified(fields, "3", "bil")
        with pytest.raises(ValueError, match="regridding method"):
            grid_align.unified(fields, "1", "best")

    def test_compare_simulations_still_regrids_bilinearly(self):
        asked = []

        class Dataset:
            def regrid(self, grid, method):
                asked.append(method)

            def to_latlon(self, **options):
                asked.append(options["method"])

            def run(self):
                pass

        sim_unify._regrid(Dataset(), {"method": "baseline", "is_baseline": False, "folder": "f"}, "k", "surface")
        sim_unify._regrid(Dataset(), {"method": "box", "lon": [0, 1], "lat": [0, 1], "res": 1}, "k", "surface")
        sim_unify._regrid(Dataset(), {"method": "baseline", "is_baseline": False, "folder": "f", "regrid": "nn"}, "k", "surface")
        assert asked == ["bil", "bil", "nn"]
