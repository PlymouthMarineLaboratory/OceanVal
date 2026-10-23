import numpy as np
import nctoolkit as nc
import pytest
import xarray as xr

from oceanval.gridded import _crop_to_model_data, _lonlat_bounds, _regrid_onto_common_grid


def _write_grid(path, lon, lat, varname):
    ds = xr.Dataset(
        {varname: (("lat", "lon"), np.random.rand(len(lat), len(lon)).astype("f4"))},
        coords={"lon": lon, "lat": lat},
    )
    ds.lon.attrs = {"units": "degrees_east", "standard_name": "longitude"}
    ds.lat.attrs = {"units": "degrees_north", "standard_name": "latitude"}
    ds.to_netcdf(path)


class TestLonLatBounds:
    def test_reads_the_grid_extent(self, tmp_path):
        path = str(tmp_path / "grid.nc")
        _write_grid(path, np.linspace(-10, 10, 5), np.linspace(40, 50, 5), "x")

        ds = nc.open_data(path, checks=False)
        lon_min, lon_max, lat_min, lat_max = _lonlat_bounds(ds)

        assert lon_min == -10
        assert lon_max == 10
        assert lat_min == 40
        assert lat_max == 50


class TestRegridOntoCommonGrid:
    def test_a_regional_model_crops_global_obs_and_skips_to_latlon(self, tmp_path):
        # a small regional model (comparable to a NW European Shelf domain)
        model_path = str(tmp_path / "model.nc")
        _write_grid(model_path, np.linspace(-3, 3, 12), np.linspace(48, 58, 20), "model")

        # a global observational product
        obs_path = str(tmp_path / "obs.nc")
        _write_grid(
            obs_path,
            np.arange(-179.875, 180, 0.25),
            np.arange(-89.875, 90, 0.25),
            "observation",
        )

        ds_model = nc.open_data(model_path, checks=False)
        ds_obs = nc.open_data(obs_path, checks=False)

        _regrid_onto_common_grid(ds_model, ds_obs)

        # cropped to the model's small extent, not put on a coarse 0.5deg
        # grid - the obs stayed at its own (finer) native resolution
        obs_xr = ds_obs.to_xarray()
        lon_coord = [c for c in obs_xr.coords if "lon" in c][0]
        spacing = float(obs_xr[lon_coord][1] - obs_xr[lon_coord][0])
        assert abs(spacing - 0.25) < 1e-6

        # both datasets end up on the same grid, which the later merge step
        # requires
        assert ds_model.to_xarray().model.shape == obs_xr.observation.shape

    def test_a_global_model_uses_a_half_degree_common_grid(self, tmp_path):
        # a near-global model - large enough that even after cropping obs to
        # its extent, that extent alone still exceeds 90 degrees
        model_path = str(tmp_path / "model.nc")
        _write_grid(
            model_path, np.linspace(-180, 179, 90), np.linspace(-80, 80, 60), "model"
        )

        obs_path = str(tmp_path / "obs.nc")
        _write_grid(
            obs_path,
            np.arange(-179.875, 180, 0.25),
            np.arange(-89.875, 90, 0.25),
            "observation",
        )

        ds_model = nc.open_data(model_path, checks=False)
        ds_obs = nc.open_data(obs_path, checks=False)

        _regrid_onto_common_grid(ds_model, ds_obs)

        model_xr = ds_model.to_xarray()
        obs_xr = ds_obs.to_xarray()

        model_lon = [c for c in model_xr.coords if "lon" in c][0]
        obs_lon = [c for c in obs_xr.coords if "lon" in c][0]
        model_lat = [c for c in model_xr.coords if "lat" in c][0]
        obs_lat = [c for c in obs_xr.coords if "lat" in c][0]

        # both regridded to a common 0.5 degree grid
        assert abs(float(model_xr[model_lon][1] - model_xr[model_lon][0]) - 0.5) < 1e-6
        assert abs(float(obs_xr[obs_lon][1] - obs_xr[obs_lon][0]) - 0.5) < 1e-6
        assert abs(float(model_xr[model_lat][1] - model_xr[model_lat][0]) - 0.5) < 1e-6
        assert abs(float(obs_xr[obs_lat][1] - obs_xr[obs_lat][0]) - 0.5) < 1e-6

        # and therefore end up with matching shapes, ready to merge
        assert model_xr.model.shape == obs_xr.observation.shape

    @pytest.mark.parametrize(
        "obs_lon, first",
        [
            # a 1 degree product's centres gave 719 cells, -179.5 to 179.5, with
            # nothing at 180 for a transect there to be interpolated from
            (np.arange(-179.5, 180, 1.0), -180.0),
            # a 0.25 degree product's gave 720 already, kept as they were
            (np.arange(-179.875, 180, 0.25), -179.875),
        ],
    )
    def test_a_global_common_grid_goes_all_the_way_round(self, tmp_path, obs_lon, first):
        # the model's outermost centres, -180 and 179, must not cut the obs either
        model_path = str(tmp_path / "model.nc")
        _write_grid(model_path, np.linspace(-180, 179, 90), np.linspace(-80, 80, 60), "model")
        obs_path = str(tmp_path / "obs.nc")
        _write_grid(obs_path, obs_lon, np.arange(-89.5, 90, 1.0), "observation")

        ds_model = nc.open_data(model_path, checks=False)
        ds_obs = nc.open_data(obs_path, checks=False)
        _regrid_onto_common_grid(ds_model, ds_obs)

        for ds in (ds_model, ds_obs):
            lon = ds.to_xarray().lon.values
            assert len(lon) == 720
            assert lon[0] == pytest.approx(first)
            assert lon[-1] == pytest.approx(first + 359.5)

    def test_a_wide_but_narrow_model_only_needs_one_dimension_over_90(self, tmp_path):
        # spans >90 degrees of longitude but a narrow band of latitude - the
        # rule is "either dimension", not "both"
        model_path = str(tmp_path / "model.nc")
        _write_grid(
            model_path, np.linspace(-150, 150, 60), np.linspace(48, 58, 10), "model"
        )

        obs_path = str(tmp_path / "obs.nc")
        _write_grid(
            obs_path,
            np.arange(-179.875, 180, 0.25),
            np.arange(-89.875, 90, 0.25),
            "observation",
        )

        ds_model = nc.open_data(model_path, checks=False)
        ds_obs = nc.open_data(obs_path, checks=False)

        _regrid_onto_common_grid(ds_model, ds_obs)

        obs_xr = ds_obs.to_xarray()
        obs_lon = [c for c in obs_xr.coords if "lon" in c][0]
        assert abs(float(obs_xr[obs_lon][1] - obs_xr[obs_lon][0]) - 0.5) < 1e-6


class TestCropToModelData:
    def _matchup(self, path, lon, lat, empty):
        """A matchup's model, missing where empty(lon, lat)."""
        lon2, lat2 = np.meshgrid(lon, lat)
        model = np.where(empty(lon2, lat2), np.nan, 1.0).astype("f4")
        ds = xr.Dataset({"model": (("lat", "lon"), model)}, coords={"lon": lon, "lat": lat})
        ds.lon.attrs = {"units": "degrees_east", "standard_name": "longitude"}
        ds.lat.attrs = {"units": "degrees_north", "standard_name": "latitude"}
        ds.to_netcdf(path, encoding={"model": {"_FillValue": -9999.0}})
        return nc.open_data(str(path), checks=False)

    def test_a_global_grid_keeps_all_its_longitudes(self, tmp_path):
        # an empty column at the edge would leave a gap at the date line
        ds = self._matchup(
            tmp_path / "global.nc", np.arange(-180, 180, 0.5), np.arange(-80, 80, 0.5),
            lambda lon, lat: (lon == -180) | (lat > 60),
        )
        _crop_to_model_data(ds)
        out = ds.to_xarray()

        assert len(out.lon) == 720
        assert float(out.lat.max()) == 60

    def test_a_regional_grid_is_cropped_to_its_values(self, tmp_path):
        ds = self._matchup(
            tmp_path / "regional.nc", np.arange(-20, 10, 0.5), np.arange(40, 70, 0.5),
            lambda lon, lat: (lon < -10) | (lat > 60),
        )
        _crop_to_model_data(ds)
        out = ds.to_xarray()

        assert float(out.lon.min()) == -10 and float(out.lon.max()) == 9.5
        assert float(out.lat.max()) == 60
