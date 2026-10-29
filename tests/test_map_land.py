import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import numpy as np
import pytest
from cartopy.io import shapereader
from cartopy.mpl.feature_artist import FeatureArtist

from oceanval import analysis_plots, map_land
from oceanval.map_land import land_below


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


@pytest.fixture
def lakes_found(monkeypatch):
    # no download: the shapefile is only drawn when the figure is
    monkeypatch.setattr(shapereader, "natural_earth", lambda **kwargs: "ne_50m_lakes.shp")


def _erie(land=True):
    """A map of Lake Erie as nctoolkit's pub_plot draws it: the data, then the land over it."""
    fig = plt.figure()
    ax = fig.add_subplot(projection=ccrs.PlateCarree())
    lon, lat = np.meshgrid(np.arange(-83.5, -78.8, 0.1), np.arange(41.3, 42.9, 0.1))
    mesh = ax.pcolormesh(lon, lat, lon + lat, transform=ccrs.PlateCarree())
    if land:
        ax.add_feature(cfeature.NaturalEarthFeature(category="physical", scale="50m", name="land", facecolor="grey"))
    return fig, ax, mesh


def _features(ax, name):
    return [x for x in ax.get_children() if isinstance(x, FeatureArtist) and x._feature.name == name]


def test_the_land_goes_beneath_the_data_and_the_lakes_over_it(lakes_found):
    fig, ax, mesh = _erie()
    [land] = _features(ax, "land")
    assert land.get_zorder() > mesh.get_zorder()

    land_below()

    [lakes] = _features(ax, "lakes")
    assert land.get_zorder() < lakes.get_zorder() < mesh.get_zorder()
    assert lakes._feature.scale == "50m"


def test_it_can_be_called_again(lakes_found):
    fig, ax, mesh = _erie()
    land_below(fig)
    land_below(fig)
    assert len(_features(ax, "lakes")) == 1


def test_without_the_lakes_the_land_is_still_put_beneath(monkeypatch):
    def offline(**kwargs):
        raise OSError("no network")
    monkeypatch.setattr(shapereader, "natural_earth", offline)
    fig, ax, mesh = _erie()
    land_below()
    assert not _features(ax, "lakes")
    assert _features(ax, "land")[0].get_zorder() < mesh.get_zorder()


def test_a_map_without_land_is_left_as_it_is(lakes_found):
    fig, ax, mesh = _erie(land=False)
    land_below()
    assert not _features(ax, "lakes")
    assert mesh.get_zorder() == 1


def test_the_analyze_scripts_write_it_out():
    code = analysis_plots._code("import nctoolkit as nc", "ds.pub_plot()")
    assert map_land.LAND_CODE in code
    assert code.rstrip().endswith("land_below()")
    # a script without a map has none of it
    assert "land_below" not in analysis_plots._code("import pandas as pd", "df.plot()")
