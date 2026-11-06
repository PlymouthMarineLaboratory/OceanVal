"""The domain of a simulation, worked out from where its cells are (see
oceanval.domain_detect): northwest European Shelf if more than 80% of them are
inside the Full Domain of nwes_subdomains.nc, else global."""

import numpy as np
import pytest
import xarray as xr

from oceanval import domain_detect

pytestmark = pytest.mark.real_domain


def model_file(path, lon, lat, name="thetao", values=None):
    lon, lat = np.asarray(lon, dtype="float64"), np.asarray(lat, dtype="float64")
    data = np.ones((1, lat.size, lon.size)) if values is None else values
    ds = xr.Dataset(
        {name: (("time", "lat", "lon"), data, {"long_name": "temperature"})},
        coords={
            "time": [0.0],
            "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
            "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"}),
        },
    )
    ds["time"].attrs["units"] = "days since 2000-01-01"
    ds.to_netcdf(path)
    return str(path)


def test_the_shelf_is_the_full_domain_variable():
    with xr.open_dataset(domain_detect.shelf_file()) as shelf:
        assert domain_detect.VARIABLE in shelf
        assert shelf[domain_detect.VARIABLE].attrs["long_name"] == "Full Domain"


def test_a_model_inside_the_shelf(tmp_path):
    path = model_file(tmp_path / "a.nc", np.arange(0, 3, 0.25), np.arange(53, 57, 0.25))
    assert domain_detect.detect([path], "thetao") == "nwes"


def test_a_global_model(tmp_path):
    path = model_file(tmp_path / "a.nc", np.arange(-179.5, 180, 2), np.arange(-89.5, 90, 2))
    assert domain_detect.detect([path], "thetao") == "global"


def test_a_model_elsewhere(tmp_path):
    path = model_file(tmp_path / "a.nc", np.arange(140, 160, 0.5), np.arange(-40, -20, 0.5))
    assert domain_detect.detect([path], "thetao") == "global"


@pytest.mark.parametrize("outside, expected", [(0.15, "nwes"), (0.3, "global")])
def test_the_share_that_must_be_inside(tmp_path, outside, expected):
    """A box of 40 columns: the first (1 - outside) inside the shelf, the rest far to the west."""
    lat = np.arange(53, 57, 0.25)
    inside = int(round(40 * (1 - outside)))
    lon = np.concatenate([np.linspace(0, 3, inside), np.linspace(-60, -40, 40 - inside)])
    path = model_file(tmp_path / "a.nc", np.sort(lon), lat)
    assert domain_detect.detect([path], "thetao") == expected


def test_cells_without_values_are_not_counted(tmp_path):
    """The land of a model on a grid that is mostly outside the shelf has no values."""
    lon, lat = np.arange(-30, 3, 0.25), np.arange(53, 57, 0.25)
    values = np.full((1, lat.size, lon.size), np.nan)
    values[:, :, lon >= 0] = 1.0
    path = model_file(tmp_path / "a.nc", lon, lat, values=values)
    assert domain_detect.detect([path], "thetao") == "nwes"


def test_a_sum_of_variables_is_read_by_its_first(tmp_path):
    path = model_file(tmp_path / "a.nc", np.arange(0, 3, 0.25), np.arange(53, 57, 0.25))
    assert domain_detect.detect([path], "thetao+other") == "nwes"


def test_raw_fvcom_is_looked_up_by_node(tmp_path):
    for lon0, expected in ((0.0, "nwes"), (-60.0, "global")):
        path = tmp_path / f"fvcom{lon0}.nc"
        xr.Dataset(
            {"lon": ("node", np.linspace(lon0, lon0 + 3, 20)), "lat": ("node", np.linspace(53, 57, 20))}
        ).to_netcdf(path)
        assert domain_detect.detect([str(path)], None, fvcom=True) == expected


def test_fvcom_longitudes_above_180_are_wrapped(tmp_path):
    path = tmp_path / "fvcom.nc"
    xr.Dataset({"lon": ("node", np.linspace(0, 3, 20) % 360), "lat": ("node", np.linspace(53, 57, 20))}).to_netcdf(path)
    assert domain_detect.detect([str(path)], None, fvcom=True) == "nwes"


def test_nothing_that_can_be_read_is_global(tmp_path):
    bad = tmp_path / "bad.nc"
    bad.write_text("not netcdf")
    assert domain_detect.detect([], "thetao") == "global"
    assert domain_detect.detect([str(bad)], "thetao") == "global"
    assert domain_detect.detect([str(bad)], None, fvcom=True) == "global"
