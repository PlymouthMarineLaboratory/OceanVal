"""The built-in sets of regions (oceanval/regions.py and data/*_subdomains.nc)."""

import os
import re

import numpy as np
import pytest
import xarray as xr

import oceanval
from oceanval import recipes_gui, regions

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "oceanval", "data")


@pytest.mark.parametrize("name", list(regions.BUILT_IN))
def test_each_set_is_a_valid_regions_file(name):
    path = regions.path(name)
    assert os.path.exists(path)
    # as a file of the user's own is checked: 2-D maps of 1, 0 or missing
    assert oceanval._check_region_file(path) >= 1
    ds = xr.open_dataset(path)
    for variable in ds.data_vars:
        assert ds[variable].attrs.get("long_name"), variable


@pytest.mark.parametrize("name", ["latbands", "cmems", "biomes"])
def test_the_new_sets_are_ocean_on_the_global_grid(name):
    ds = xr.open_dataset(regions.path(name))
    assert ds.sizes["lat"] == 181 and ds.sizes["lon"] == 361
    # no region takes in the middle of a continent (central Africa, central Asia)
    for lat, lon in ((5, 20), (45, 90)):
        assert all(np.isnan(ds[v].sel(lat=lat, lon=lon).item()) for v in ds.data_vars)
    # and the regions do not overlap, except the Full Domain
    regional = [v for v in ds.data_vars if ds[v].attrs["long_name"] != "Full Domain"]
    total = sum(np.nan_to_num(ds[v].values) for v in regional)
    assert total.max() == 1


def test_the_biomes_are_those_of_fay_and_mckinley():
    ds = xr.open_dataset(regions.path("biomes"))
    assert len(ds.data_vars) == 17
    # the North Atlantic subtropical gyre is in its STPS biome
    assert ds["na_stps"].sel(lat=28, lon=-50).item() == 1
    assert regions.SOURCES["biomes"].count("10.5194/essd-6-273-2014") == 1


def test_the_cmems_set_has_a_full_domain_and_its_sub_regions():
    ds = xr.open_dataset(regions.path("cmems"))
    names = [ds[v].attrs["long_name"] for v in ds.data_vars]
    assert names[0] == "Full Domain"
    for code in ("ARC", "NAT", "TAT", "SAT", "NPA", "TPA", "SPA", "IND", "ACC", "MED", "NORDSEA", "IBI"):
        assert any(f"({code})" in x for x in names), code
    assert ds["nordsea"].sel(lat=56, lon=3).item() == 1
    assert ds["med"].sel(lat=35, lon=18).item() == 1


def test_names_are_checked_the_same_everywhere():
    assert regions.is_built_in("cmems") and not regions.is_built_in("custom")
    assert not regions.is_built_in(None)
    errors = {}
    assert recipes_gui.check_subregions(lambda name: {"subregions": "biomes"}.get(name, ""), errors) == "biomes"
    assert errors == {}
    with pytest.raises(ValueError, match="'latbands', 'cmems', 'biomes'"):
        regions.path("nope")


def test_every_window_offers_every_set():
    for page in ("oceanval_app.html", "create_recipes_gui.html"):
        text = open(os.path.join(DATA, page)).read()
        for select in re.findall(r"<select[^>]*subregions[^>]*>(.*?)</select>", text, flags=re.S):
            values = re.findall(r'<option value="([^"]*)"', select)
            for name in regions.BUILT_IN:
                assert name in values, (page, name)


def test_every_built_in_set_has_a_description():
    for name in regions.BUILT_IN:
        assert regions.description(name, "own") != "own", name
    assert regions.description("/a/b.nc", "own") == "own"
    assert regions.description(None, "own") == "own"
