"""The extent of a model's field (oceanval.extent): the cells that have values,
not the whole grid, as NEMO puts the cells of the land processors it leaves out
at lon -1, lat -1."""

import numpy as np
import pytest
import xarray as xr

from oceanval import extent

# a north-west European shelf: 40 to 65N, 20W to 13E
LON = np.linspace(-20, 13, 34)
LAT = np.linspace(40, 65, 26)


def curvilinear(times=2, levels=3, left_out=True):
    """A NEMO-like field on nav_lon/nav_lat, with values everywhere but a block
    of left-out land processors, whose cells are at -1, -1 and have none."""
    lon, lat = np.meshgrid(LON, LAT)
    values = np.full((times, levels, LAT.size, LON.size), 5.0)
    if left_out:
        lon[:8, :10], lat[:8, :10] = -1.0, -1.0
        values[:, :, :8, :10] = np.nan
    data = xr.Dataset(
        {"thetao": (("time_counter", "deptht", "y", "x"), values)},
        coords={
            "time_counter": ("time_counter", np.arange(times) * 86400.0,
                             {"units": "seconds since 2000-01-01", "calendar": "standard", "axis": "T"}),
            "deptht": ("deptht", np.arange(levels) * 10.0, {"units": "m", "positive": "down"}),
            "nav_lon": (("y", "x"), lon, {"units": "degrees_east", "standard_name": "longitude"}),
            "nav_lat": (("y", "x"), lat, {"units": "degrees_north", "standard_name": "latitude"}),
        },
    )
    return data


def extent_of(found):
    lon, lat = found
    return [float(lon.min()), float(lon.max())], [float(lat.min()), float(lat.max())]


class TestValidLonLat:
    def test_the_left_out_cells_are_not_in_it(self):
        data = curvilinear()
        # the whole grid goes down to -1, -1
        lon, lat = extent.lonlat(data)
        assert lat.min() == -1
        assert extent_of(extent.valid_lonlat(data)) == ([-20.0, 13.0], [40.0, 65.0])

    def test_only_the_first_time_and_top_level_are_looked_at(self):
        data = curvilinear()
        # deeper, and later, the land is larger; it is the surface at the first time that counts
        data["thetao"][:, 1:, :, 30:] = np.nan
        data["thetao"][1:, :, 20:, :] = np.nan
        assert extent_of(extent.valid_lonlat(data, "thetao")) == ([-20.0, 13.0], [40.0, 65.0])

    def test_one_dimensional_coordinates(self):
        values = np.full((1, LAT.size, LON.size), 1.0)
        values[:, :, LON < -10] = np.nan
        data = xr.Dataset(
            {"chl": (("time", "lat", "lon"), values)},
            coords={"lon": ("lon", LON, {"units": "degrees_east"}), "lat": ("lat", LAT, {"units": "degrees_north"})},
        )
        assert extent_of(extent.valid_lonlat(data)) == ([-10.0, 13.0], [40.0, 65.0])

    def test_the_field_is_not_a_bounds_variable(self):
        data = curvilinear()
        data["time_bnds"] = (("time_counter", "bnds"), np.zeros((2, 2)))
        assert extent.field_of(data).name == "thetao"

    def test_what_cannot_be_told_is_none(self):
        data = curvilinear()
        data["thetao"][:] = np.nan
        assert extent.valid_lonlat(data) is None
        assert extent.valid_lonlat(xr.Dataset({"thetao": (("x",), [1.0])})) is None
        # coordinates that are not the field's
        odd = curvilinear().rename({"y": "j"})
        odd = odd.assign_coords(nav_lon=(("y", "x"), np.zeros((LAT.size, LON.size)), {"units": "degrees_east"}))
        assert extent.valid_lonlat(odd) is None


class TestOfFile:
    def test_one_slice_of_the_file_is_read_with_its_missing_values(self, tmp_path, monkeypatch):
        data = curvilinear(left_out=False)
        # land as 0, as a NEMO-ERSEM run has it, west of 10W
        values = data["thetao"].values
        values[..., LON < -10] = 0.0
        data["thetao"] = data["thetao"].copy(data=values)
        path = str(tmp_path / "model.nc")
        data.to_netcdf(path)
        seen = []
        real = xr.open_dataset
        monkeypatch.setattr(xr, "open_dataset", lambda target, *a, **k: seen.append(str(target)) or real(target, *a, **k))
        assert extent_of(extent.of_file(path, "thetao")) == ([-20.0, 13.0], [40.0, 65.0])
        assert extent_of(extent.of_file(path, "thetao", 0)) == ([-10.0, 13.0], [40.0, 65.0])
        # CDO cut the file first: the model's own file is never opened by xarray
        assert seen and path not in seen
        assert extent.of_file(str(tmp_path / "nothing.nc"), "thetao") is None


@pytest.mark.parametrize("left_out", [True, False])
def test_the_crop_box_of_the_unifier(left_out):
    from oceanval import sim_unify

    box = sim_unify.padded_box(*extent.valid_lonlat(curvilinear(left_out=left_out)))
    assert box == ([-21.0, 14.0], [39.0, 66.0])


class TestThePlacesThatUseIt:
    """Every extent OceanVal works out of a model is of its cells with values."""

    @pytest.fixture
    def amm7(self, tmp_path):
        path = str(tmp_path / "amm7.nc")
        curvilinear().to_netcdf(path)
        return path

    def test_gridded_matchups_crop_and_download_to_it(self, amm7):
        import nctoolkit as nc

        from oceanval import gridded

        assert gridded._lonlat_bounds(nc.open_data(amm7, checks=False)) == (-20.0, 13.0, 40.0, 65.0)

    def test_point_matchups_download_ices_data_for_it(self, amm7):
        from oceanval import matchall

        assert matchall._model_box(amm7, "thetao") == ([-20.0, 13.0], [40.0, 65.0])
        # the first of a sum
        assert matchall._model_box(amm7, "thetao+so") == ([-20.0, 13.0], [40.0, 65.0])

    def test_analyze_draws_its_maps_and_takes_its_means_over_it(self, amm7):
        from oceanval import analysis_plots, analysis_series

        grid = analysis_plots._plot_grid(amm7)
        assert (grid["lon"], grid["lat"]) == ([-20.0, 13.0], [40.0, 65.0])
        estimated = analysis_series.estimate_grid(amm7, "thetao")
        assert estimated["lat"][0] > 35 and estimated["lon"][0] > -25

    def test_the_maps_are_shaped_by_it(self, amm7, tmp_path):
        from oceanval import analysis_plots

        whole = str(tmp_path / "whole.nc")
        curvilinear(left_out=False).to_netcdf(whole)
        assert analysis_plots._panel_size(amm7, 1) == analysis_plots._panel_size(whole, 1)

    def test_the_viewers_coastline_is_for_it(self):
        from oceanval import viewer_plots

        data = curvilinear().thetao.isel(time_counter=0, deptht=0)
        lon, lat = data.nav_lon.values, data.nav_lat.values
        assert viewer_plots.coastline_scale(lon, lat, data.values) == viewer_plots.coastline_scale(
            *np.meshgrid(LON, LAT))
