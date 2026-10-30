"""Putting the simulations of a comparison on one grid (oceanval.sim_unify):
the box and the baseline, and a cell missing in any simulation being missing in
all."""

import glob
import os

import numpy as np
import pytest
import xarray as xr

from oceanval import analysis, sim_unify
from simulations import ANALYSIS_LATS, ANALYSIS_LONS, write_analysis_simulation

YEARS = (2013,)
ROW = {"key": "temp", "name": "Temperature", "parts": ["thetao"]}
# B is on a finer grid, which covers A's
B_LATS = list(np.arange(49.5, 53.6, 0.5))
B_LONS = list(np.arange(-4.5, 0.6, 0.5))
LEVELS = 3


def rewrite(root, change):
    for path in glob.glob(os.path.join(root, "*", "*grid_T.nc")):
        with xr.open_dataset(path) as ds:
            ds = ds.load()
        change(ds)
        ds.to_netcdf(path, encoding={"thetao": {"_FillValue": np.float32(-1e20)}})


@pytest.fixture(scope="module")
def sims(tmp_path_factory):
    """A on the usual grid, with a cell that is genuinely 0 at the surface and
    its north-east corner without water; B on a finer grid with its own cell
    without water."""
    base = tmp_path_factory.mktemp("unify")
    a, b = str(base / "a"), str(base / "b")
    write_analysis_simulation(a, years=YEARS, daily=False)
    write_analysis_simulation(b, years=YEARS, daily=False, lats=B_LATS, lons=B_LONS)

    def a_changes(ds):
        ds["thetao"][:, :, 3, 4] = np.nan
        ds["thetao"][:, 0, 0, 0] = 0.0

    def b_changes(ds):
        # the cell at 51N, -2E (and its neighbours, as it is regridded)
        j, i = B_LATS.index(51.0), B_LONS.index(-2.0)
        ds["thetao"][:, :, j - 1 : j + 2, i - 1 : i + 2] = np.nan

    rewrite(a, a_changes)
    rewrite(b, b_changes)
    return {"A": glob.glob(os.path.join(a, "*", "*grid_T.nc")), "B": glob.glob(os.path.join(b, "*", "*grid_T.nc"))}


def fields(sims, layer="surface"):
    return {name: analysis.first_step_of(files, ROW, layer, LEVELS) for name, files in sims.items()}


def prepared(sims, folder, unify, layer="surface"):
    unify = sim_unify.check_unify(unify, list(sims))
    sim_unify.prepare(str(folder), unify, ROW["key"], layer, fields(sims, layer))
    return unify


def values(ds):
    return ds.to_xarray()["temp"].values


def monthly(files, unify, sim, folder, layer="surface"):
    row = {**ROW, "unify": sim_unify.row_unify(unify, str(folder), sim)}
    ds = analysis.monthly_means_of(files, row, layer, YEARS * 2, None, LEVELS)
    ds.run()
    return values(ds)


class TestCheck:
    def test_none_is_nothing(self):
        assert sim_unify.check_unify(None, ["A", "B"]) is None
        assert "not put on a common grid" in sim_unify.describe(None)

    def test_a_baseline_is_one_of_the_simulations(self):
        assert sim_unify.check_unify({"baseline": "A"}, ["A", "B"]) == {"method": "baseline", "baseline": "A"}
        with pytest.raises(ValueError, match="not one of the simulations"):
            sim_unify.check_unify({"baseline": "C"}, ["A", "B"])

    def test_a_box(self):
        assert sim_unify.check_unify({"lon": [-4, 0], "lat": [50, 53], "res": 0.5}, ["A"]) == {
            "method": "box",
            "lon": [-4.0, 0.0],
            "lat": [50.0, 53.0],
            "res": [0.5, 0.5],
        }

    @pytest.mark.parametrize(
        "unify",
        [
            "yes",
            {"lon": [-4, 0], "lat": [50, 53]},
            {"lon": [0, -4], "lat": [50, 53], "res": 1},
            {"lon": [-4, 0], "lat": [50, 95], "res": 1},
            {"lon": [-4, 0], "lat": [50, 53], "res": 0},
            {"lon": [-4, 0], "lat": [50, 53], "res": 10},
            {"lon": [-4], "lat": [50, 53], "res": 1},
            {"baseline": "A", "res": 1},
        ],
    )
    def test_nonsense_is_refused(self, unify):
        with pytest.raises(ValueError):
            sim_unify.check_unify(unify, ["A", "B"])

    def test_how_they_are_regridded(self):
        # not said: nothing added, so what is kept is as it was, and it is bilinear
        assert "regrid" not in sim_unify.check_unify({"baseline": "A"}, ["A", "B"])
        assert "bilinear" in sim_unify.describe(sim_unify.check_unify({"baseline": "A"}, ["A", "B"]))
        assert sim_unify.check_unify({"baseline": "A", "regrid": "nn"}, ["A", "B"]) == {
            "method": "baseline", "baseline": "A", "regrid": "nn"}
        box = sim_unify.check_unify({"lon": [-4, 0], "lat": [50, 53], "res": 1, "regrid": "con"}, ["A"])
        assert box["regrid"] == "con" and box["method"] == "box"
        assert "conservative regridding" in sim_unify.describe(box)
        assert "nearest neighbour regridding" in sim_unify.describe(
            sim_unify.check_unify({"baseline": "A", "regrid": "nn"}, ["A", "B"]))
        with pytest.raises(ValueError, match="regrid must be one of"):
            sim_unify.check_unify({"baseline": "A", "regrid": "best"}, ["A", "B"])
        # a baseline with a method has nothing else
        with pytest.raises(ValueError):
            sim_unify.check_unify({"baseline": "A", "regrid": "nn", "res": 1}, ["A", "B"])

    def test_it_says_that_cells_are_missing_in_all(self):
        for unify in ({"baseline": "A"}, {"lon": [-4, 0], "lat": [50, 53], "res": 1}):
            text = sim_unify.describe(sim_unify.check_unify(unify, ["A", "B"]))
            assert "missing in all" in text


class TestBaseline:
    def test_every_simulation_is_missing_in_the_same_cells(self, sims, tmp_path):
        unify = prepared(sims, tmp_path, {"baseline": "A"})
        a = monthly(sims["A"], unify, "A", tmp_path)
        b = monthly(sims["B"], unify, "B", tmp_path)
        assert a.shape == b.shape == (12, len(ANALYSIS_LATS), len(ANALYSIS_LONS))
        assert (np.isnan(a) == np.isnan(b)).all()
        # A's own corner and B's hole are both missing in both
        assert np.isnan(a[:, 3, 4]).all()
        assert np.isnan(a[:, 1, 2]).all()
        assert np.isfinite(a[:, 2, 1]).all()

    def test_the_baseline_keeps_its_values_and_a_zero_is_a_value(self, sims, tmp_path):
        unify = prepared(sims, tmp_path, {"baseline": "A"})
        a = monthly(sims["A"], unify, "A", tmp_path)
        assert (a[:, 0, 0] == 0).all()
        # surface thetao is 10 plus the month
        assert a[:, 2, 1] == pytest.approx(10 + np.arange(1, 13))

    def test_the_others_are_regridded_to_the_baseline(self, sims, tmp_path):
        unify = prepared(sims, tmp_path, {"baseline": "A"})
        b = monthly(sims["B"], unify, "B", tmp_path)
        assert b[:, 2, 1] == pytest.approx(10 + np.arange(1, 13), abs=1e-4)

    def test_the_other_way_round(self, sims, tmp_path):
        unify = prepared(sims, tmp_path, {"baseline": "B"})
        b = monthly(sims["B"], unify, "B", tmp_path)
        a = monthly(sims["A"], unify, "A", tmp_path)
        assert a.shape == b.shape == (12, len(B_LATS), len(B_LONS))
        assert (np.isnan(a) == np.isnan(b)).all()
        assert np.isnan(b[:, B_LATS.index(51.0), B_LONS.index(-2.0)]).all()


class TestBox:
    BOX = {"lon": [-4, 0], "lat": [50, 53], "res": 1}

    def test_every_simulation_is_on_the_box(self, sims, tmp_path):
        unify = prepared(sims, tmp_path, self.BOX)
        for name in sims:
            ds = analysis.monthly_means_of(
                sims[name],
                {**ROW, "unify": sim_unify.row_unify(unify, str(tmp_path), name)},
                "surface",
                YEARS * 2,
                None,
                LEVELS,
            )
            ds.run()
            data = ds.to_xarray()
            assert list(data.lon.values) == [-4, -3, -2, -1, 0]
            assert list(data.lat.values) == [50, 51, 52, 53]

    def test_missing_in_any_is_missing_in_all(self, sims, tmp_path):
        unify = prepared(sims, tmp_path, self.BOX)
        a = monthly(sims["A"], unify, "A", tmp_path)
        b = monthly(sims["B"], unify, "B", tmp_path)
        assert (np.isnan(a) == np.isnan(b)).all()
        assert np.isnan(a[:, 3, 4]).all()
        assert np.isnan(a[:, 1, 2]).all()
        assert np.isfinite(a[:, 2, 1]).all()


def lonlat_field(lon, lat, name="temp"):
    """A dataset of one time of a smooth field on a lon-lat grid."""
    import nctoolkit as nc

    values = np.add.outer(np.asarray(lat) * 0.3, np.asarray(lon) * 0.2).astype("f4")[np.newaxis]
    data = xr.Dataset(
        {name: (("time", "lat", "lon"), values)},
        coords={"time": [np.datetime64("2000-01-01")], "lat": lat, "lon": lon},
    )
    data.lon.attrs.update(units="degrees_east", standard_name="longitude")
    data.lat.attrs.update(units="degrees_north", standard_name="latitude")
    return nc.from_xarray(data)


class TestCrop:
    """Each simulation is cropped to the target grid, a degree wider each way,
    before it is regridded: only time is saved."""

    @pytest.mark.parametrize(
        "lon, lat, box",
        [
            ([-5, 5], [50, 55], ([-6, 6], [49, 56])),
            # a grid of 0 to 360
            ([350, 359.5], [50, 55], ([-11, 0.5], [49, 56])),
            # at the poles and the dateline's edge
            ([179.5, 179.9], [89.5, 89.9], ([178.5, 180], [88.5, 90])),
            # round the globe, or across the dateline: no crop
            (list(np.arange(0.5, 360, 1)), [0, 1], None),
            ([170, 190], [0, 1], None),
            ([-179, 179], [0, 1], None),
        ],
    )
    def test_the_box(self, lon, lat, box):
        assert sim_unify.padded_box(np.array(lon), np.array(lat)) == box

    def test_a_box_that_misses_the_grid_leaves_it_as_it_was(self):
        ds = lonlat_field(np.arange(-10, 10.1, 1.0), np.arange(40, 60.1, 1.0))
        before = values(ds)
        assert sim_unify.crop_to(ds, ([100, 120], [-10, 0])) is False
        assert sim_unify.crop_to(ds, None) is False
        assert np.array_equal(values(ds), before)

    @pytest.mark.parametrize("method", ["bil", "nn", "con"])
    @pytest.mark.parametrize("unify", [{"baseline": "small"}, {"lon": [-4, 4], "lat": [49, 55], "res": 0.5}])
    def test_the_values_are_those_without_a_crop(self, tmp_path, monkeypatch, method, unify):
        sims = {
            "small": lonlat_field(np.arange(-5, 5.1, 1.0), np.arange(48, 56.1, 1.0)),
            "wide": lonlat_field(np.arange(-40.25, 40, 0.5), np.arange(20.25, 75, 0.5)),
        }
        unify = sim_unify.check_unify({**unify, "regrid": method}, list(sims))
        cropped = []
        real = sim_unify.crop_to
        monkeypatch.setattr(sim_unify, "crop_to", lambda ds, box: cropped.append(real(ds, box)) or cropped[-1])

        def wide(folder):
            sim_unify.prepare(str(folder), unify, "temp", "surface", sims)
            ds = sims["wide"].copy()
            sim_unify.apply(ds, sim_unify.row_unify(unify, str(folder), "wide"), "temp", "surface")
            return values(ds)

        with_crop = wide(tmp_path / "cropped")
        # in prepare() and in apply(): for a baseline, the wide simulation only, as the baseline is not
        # regridded; for a box, both simulations in prepare()
        assert cropped == [True] * (3 if unify["method"] == "box" else 2)
        monkeypatch.setattr(sim_unify, "crop_to", lambda ds, box: False)
        without = wide(tmp_path / "whole")
        assert with_crop.shape == without.shape
        assert np.allclose(with_crop, without, equal_nan=True)
        assert np.isfinite(with_crop).all()


def test_the_baselines_box_is_of_its_cells_with_values(tmp_path):
    """NEMO puts the cells of the land processors it leaves out at -1, -1: the
    crop box is the extent of the cells with values, not of the whole grid."""
    import json

    import nctoolkit as nc
    from test_extent import curvilinear

    field = nc.from_xarray(curvilinear(times=1, levels=1))
    unify = sim_unify.check_unify({"baseline": "amm7"}, ["amm7", "other"])
    sim_unify.prepare(str(tmp_path), unify, "thetao", "surface", {"amm7": field, "other": field.copy()})
    with open(sim_unify.box_path(str(tmp_path), "thetao", "surface")) as f:
        assert json.load(f) == {"lon": [-21.0, 14.0], "lat": [39.0, 66.0]}


class TestProgress:
    """prepare() says what it is doing, for the window."""

    def told(self, unify, sims, tmp_path):
        said = []
        unify = sim_unify.check_unify(unify, list(sims))
        sim_unify.prepare(str(tmp_path), unify, "temp", "surface", sims, progress=lambda *args: said.append(args))
        return said

    def test_a_baseline(self, tmp_path):
        sims = {
            "small": lonlat_field(np.arange(-5, 5.1, 1.0), np.arange(48, 56.1, 1.0)),
            "wide": lonlat_field(np.arange(-40.25, 40, 0.5), np.arange(20.25, 75, 0.5)),
        }
        # the baseline itself is neither cropped nor regridded
        assert self.told({"baseline": "small"}, sims, tmp_path) == [
            ("cropping", "wide"), ("regridding", "wide"), ("unifying", None)]

    def test_a_simulation_already_on_the_grid_is_not_touched(self, tmp_path):
        grid = (np.arange(-5, 5.1, 1.0), np.arange(48, 56.1, 1.0))
        sims = {"a": lonlat_field(*grid), "b": lonlat_field(*grid)}
        assert self.told({"baseline": "a"}, sims, tmp_path) == [("unifying", None)]

    def test_a_box(self, tmp_path):
        sims = {"a": lonlat_field(np.arange(-10, 10.1, 1.0), np.arange(40, 60.1, 1.0)),
                "b": lonlat_field(np.arange(-8, 8.1, 1.0), np.arange(42, 58.1, 1.0))}
        said = self.told({"lon": [-4, 4], "lat": [49, 55], "res": 1}, sims, tmp_path)
        assert said == [("cropping", "a"), ("regridding", "a"), ("cropping", "b"), ("regridding", "b"), ("unifying", None)]

    def test_a_target_round_the_globe_is_not_cropped(self, tmp_path):
        sims = {"a": lonlat_field(np.arange(0.5, 360, 1.0), np.arange(-60, 60.1, 1.0)),
                "b": lonlat_field(np.arange(0.25, 360, 0.5), np.arange(-60, 60.1, 0.5))}
        said = self.told({"baseline": "a"}, sims, tmp_path)
        assert said == [("regridding", "b"), ("unifying", None)]

    def test_the_words(self):
        assert sim_unify.stage_text("cropping", "Simulation 2") == "Cropping Simulation 2…"
        assert sim_unify.stage_text("regridding", "Fine run") == "Regridding Fine run…"
        assert sim_unify.stage_text("unifying") == "Unifying the simulations…"


class TestLayers:
    def test_every_level_is_masked_for_a_transect(self, tmp_path):
        """The levels layer has a 3D mask: a level missing in one simulation is
        missing in the other, and the levels above it are not."""
        a, b = str(tmp_path / "a"), str(tmp_path / "b")
        for root in (a, b):
            write_analysis_simulation(root, years=YEARS, daily=False)

        def no_floor(ds):
            ds["thetao"][:, -1, 0, 1] = np.nan

        rewrite(a, no_floor)
        own = {name: glob.glob(os.path.join(root, "*", "*grid_T.nc")) for name, root in (("A", a), ("B", b))}
        unify = sim_unify.check_unify({"baseline": "B"}, list(own))
        folder = tmp_path / "unify"
        sim_unify.prepare(str(folder), unify, ROW["key"], "levels", fields(own, "levels"))
        a_levels = monthly(own["A"], unify, "A", folder, "levels")
        b_levels = monthly(own["B"], unify, "B", folder, "levels")
        assert a_levels.shape == b_levels.shape == (12, LEVELS, 4, 5)
        assert (np.isnan(a_levels) == np.isnan(b_levels)).all()
        assert np.isnan(b_levels[:, -1, 0, 1]).all()
        assert np.isfinite(b_levels[:, 0, 0, 1]).all()
        assert np.isnan(b_levels).sum() == 12

    def test_a_layer_without_a_mask_is_only_regridded(self, sims, tmp_path):
        unify = sim_unify.check_unify({"baseline": "A"}, ["A", "B"])
        sim_unify.prepare(str(tmp_path), unify, ROW["key"], "surface", fields(sims))
        assert os.path.exists(sim_unify.mask_path(str(tmp_path), "temp", "surface"))
        assert not os.path.exists(sim_unify.mask_path(str(tmp_path), "temp", "bottom"))


def test_without_unify_the_analysis_is_as_it_was(sims):
    plain = analysis.monthly_means_of(sims["A"], dict(ROW), "surface", YEARS * 2, None, LEVELS)
    plain.run()
    with_none = analysis.monthly_means_of(sims["A"], {**ROW, "unify": None}, "surface", YEARS * 2, None, LEVELS)
    with_none.run()
    assert (np.isnan(values(plain)) == np.isnan(values(with_none))).all()
    assert np.nansum(values(plain)) == pytest.approx(np.nansum(values(with_none)))
    assert values(plain).shape[1:] == (len(ANALYSIS_LATS), len(ANALYSIS_LONS))
