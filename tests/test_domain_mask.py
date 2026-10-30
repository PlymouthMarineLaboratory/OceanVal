"""Masking the matchups to part of the domain (oceanval.domain_mask): the
user's file checked, kept cleaned in the matchups folder, and applied by
nearest neighbour."""

import glob
import json
import os
import textwrap

import nctoolkit as nc
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval import domain_mask, sim_unify
from oceanval.app import App
from oceanval.session import session_info

from test_app import get_json, leftover_dir, output_continue, post, through_output, wait_for  # noqa: F401 (leftover_dir is autouse)


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


def masked_values(ds):
    return ds.to_xarray()["model"].values


SIM_DIR = "data/example_temperature"


def example_grid():
    """The longitudes and latitudes of the example simulation's grid."""
    with xr.open_dataset(sorted(glob.glob(f"{SIM_DIR}/2000/01/*.nc"))[0]) as data:
        return data.lon.values, data.lat.values


def matchups_script(tmp_path, first):
    """A stand-in for matchup, asking whether the matchups are right, of a
    simulation whose first file is first."""
    details = {
        "kind": "matchups",
        "sim_dir": str(tmp_path),
        "years": [2000, 2000],
        "rows": [{"variable": "temperature", "title": "Temperature", "model_variable": "temp",
                  "pattern": "*.nc", "observations": ["COBE2"], "files": 1}],
        "files": {"*.nc": [first]},
    }
    script = tmp_path / "run.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            answer = prompts.ask(question, ("y", "n"), details={details!r})
            print("answer:", answer)
            print("settings:", json.dumps(getattr(answer, "settings", {{}})))
            print("matching up")
            """
        )
    )
    return script


def at_the_question(app, tmp_path, action):
    """Start a run of action, and carry on past the matchups question's
    first answer, which the window holds."""
    app.action = action
    app.start_run(["matchup", str(matchups_script(tmp_path, "a.nc"))], "python run.py")
    wait_for(lambda: app.question is not None)
    number = app.question.id
    assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
    return number


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = App(cwd=str(tmp_path))
    app.start()
    # the validate run, which is not started
    started = app.start_run
    monkeypatch.setattr(app, "start_run", lambda args, label: None if args[0] == "validate" else started(args, label))
    yield app
    app.close()


REPORT = {"cores": "1", "missing_from": "-1e20"}



def write_user_mask(path, lon, lat, wanted, outside=0.0, name="region", times=None, extra=None):
    """A mask as a user would give it: 1 where wanted(lon) is True, outside
    (0 or nan) elsewhere, at times time steps if given, with extra variables."""
    values = np.where(wanted(lon)[None, :], 1.0, outside) * np.ones((lat.size, 1))
    coords, dims = lonlat(lon, lat), ("lat", "lon")
    if times is not None:
        coords["time"] = ("time", np.arange(times, dtype="f8"), {"units": "days since 2000-01-01"})
        dims = ("time",) + dims
        values = np.broadcast_to(values, (times,) + values.shape)
    data = xr.DataArray(values.astype("f4"), coords=coords, dims=dims, name=name).to_dataset()
    for extra_name in extra or []:
        data[extra_name] = data[name]
    data.to_netcdf(path)
    return str(path)


EAST = lambda lon: lon > -7.5  # noqa: E731 - the eight eastern columns of LON


@pytest.fixture
def user_mask(tmp_path):
    return write_user_mask(tmp_path / "region.nc", LON, LAT, EAST)


class TestInspect:
    @pytest.mark.parametrize("outside", [0.0, np.nan])
    def test_ones_and_zeros_or_missing(self, tmp_path, outside):
        path = write_user_mask(tmp_path / "m.nc", LON, LAT, EAST, outside=outside)
        mask, error = domain_mask.inspect(path)
        assert error is None
        assert mask.dims == ("lat", "lon")
        assert (mask.values[:, EAST(LON)] == 1).all()
        assert np.isnan(mask.values[:, ~EAST(LON)]).all()
        assert domain_mask.cells(mask) == (LAT.size * EAST(LON).sum(), LAT.size * LON.size)

    def test_a_single_time_step_is_fine(self, tmp_path):
        mask, error = domain_mask.inspect(write_user_mask(tmp_path / "m.nc", LON, LAT, EAST, times=1))
        assert error is None and mask.dims == ("lat", "lon") and "time" not in mask.coords

    @pytest.mark.parametrize("make, words", [
        (lambda tmp: write_user_mask(tmp / "m.nc", LON, LAT, EAST, times=2), "single time step"),
        (lambda tmp: write_user_mask(tmp / "m.nc", LON, LAT, EAST, extra=["other"]), "single variable"),
        (lambda tmp: write_user_mask(tmp / "m.nc", LON, LAT, EAST, outside=2.0), "other values"),
        (lambda tmp: write_user_mask(tmp / "m.nc", LON, LAT, lambda lon: lon > 100), "no cells of 1"),
        (lambda tmp: (tmp / "m.nc").write_text("not netCDF") and str(tmp / "m.nc"), "could not be read"),
        (lambda tmp: str(tmp / "none.nc"), "no file"),
    ])
    def test_refusals(self, tmp_path, make, words):
        mask, error = domain_mask.inspect(make(tmp_path))
        assert mask is None and words in error

    def test_check(self, user_mask):
        assert domain_mask.check(None) is None
        assert domain_mask.check(user_mask) == os.path.abspath(user_mask)
        with pytest.raises(TypeError):
            domain_mask.check(1)
        with pytest.raises(ValueError, match="no file"):
            domain_mask.check(user_mask + "x")


class TestKeep:
    def test_it_is_written_as_ones_and_missing(self, user_mask, tmp_path):
        folder = tmp_path / "oceanval_matchups"
        folder.mkdir()
        kept = domain_mask.keep(user_mask, str(folder))
        assert kept == {"path": str(folder / domain_mask.FILE), "method": "nn"}
        with xr.open_dataset(kept["path"], mask_and_scale=False) as data:
            assert data.region.attrs["_FillValue"] == np.float32(domain_mask.FILL)
            raw = data.region.values
        assert (raw[:, EAST(LON)] == 1).all() and (raw[:, ~EAST(LON)] == domain_mask.FILL).all()
        # the file kept is a mask itself, so a script run again can name it
        assert domain_mask.keep(kept["path"], str(folder)) == kept
        assert domain_mask.keep(None, str(folder)) is None


@pytest.fixture
def kept(user_mask, tmp_path):
    folder = tmp_path / "kept"
    folder.mkdir()
    return domain_mask.keep(user_mask, str(folder))


class TestGridded:
    def test_on_another_grid_it_is_regridded_and_masked(self, kept, tmp_path):
        ds = nc.open_data(write_model(tmp_path / "model.nc", np.arange(-12, 2.01, 0.5), np.arange(48, 62.01, 0.5)))
        domain_mask.mask_gridded(ds, kept)
        values = masked_values(ds)
        assert values.shape == (2, LAT.size, LON.size)
        assert np.isnan(values[:, :, ~EAST(LON)]).all()
        assert (values[:, :, EAST(LON)] == 5).all()

    def test_on_its_grid_only_the_cells_of_1_are_kept(self, kept, tmp_path):
        ds = nc.open_data(write_model(tmp_path / "model.nc", LON, LAT, levels=3))
        domain_mask.mask_gridded(ds, kept)
        values = masked_values(ds)
        assert np.isnan(values[..., ~EAST(LON)]).all()
        assert (values[..., EAST(LON)] == 5).all()

    @pytest.mark.parametrize("method", ["nn"])
    def test_it_is_cropped_to_the_mask_first_unless_it_is_fvcom(self, kept, tmp_path, monkeypatch, method):
        path = write_model(tmp_path / "model.nc", np.arange(-40, 30.01, 0.5), np.arange(30, 75.01, 0.5))
        cropped = []
        real = sim_unify.crop_to
        monkeypatch.setattr(sim_unify, "crop_to", lambda ds, box: cropped.append(box) or real(ds, box))
        ds = nc.open_data(path)
        domain_mask.mask_gridded(ds, kept)
        # the extent of the mask's cells of 1, a degree wider each way
        assert cropped == [([-8.0, 1.0], [49.0, 61.0])]
        with_crop = masked_values(ds)
        monkeypatch.setattr(sim_unify, "crop_to", lambda ds, box: cropped.append(box) or False)
        ds = nc.open_data(path)
        domain_mask.mask_gridded(ds, kept, fvcom=True)
        # FVCOM output is left as it is
        assert len(cropped) == 1
        assert np.allclose(with_crop, masked_values(ds), equal_nan=True)

    def test_on_its_grid_it_is_not_regridded(self, kept, tmp_path, monkeypatch):
        ds = nc.open_data(write_model(tmp_path / "model.nc", LON, LAT))
        asked = []
        monkeypatch.setattr(type(ds), "regrid", lambda self, *args, **kwargs: asked.append(args))
        domain_mask.mask_gridded(ds, kept)
        assert asked == []

    def test_no_mask_changes_nothing(self, tmp_path):
        ds = nc.open_data(write_model(tmp_path / "model.nc", LON, LAT))
        assert domain_mask.mask_gridded(ds, None) is ds
        assert (masked_values(ds) == 5).all()


class TestPoints:
    def test_only_locations_in_cells_of_1_are_kept(self, kept):
        df = pd.DataFrame({"lon": [-9.2, -5.1, -1.3], "lat": [52.2, 55.4, 58.1], "observation": [1.0, 2.0, 3.0]})
        assert list(domain_mask.mask_points(df, kept).observation) == [2.0, 3.0]

    def test_a_global_mask_from_0_to_360(self, tmp_path):
        lon = np.arange(0.5, 360, 1.0)
        lat = np.arange(-89.5, 90, 1.0)
        # missing from 350E to 360E, which is 10W to 0
        path = write_mask(tmp_path / "global.nc", lon, lat, lambda lon: lon > 350)
        df = pd.DataFrame({"lon": [-5.2, 20.3, -15.7], "lat": [50.1, 50.1, 50.1], "observation": [1.0, 2.0, 3.0]})
        kept = domain_mask.mask_points(df, {"path": path, "method": "nn"})
        assert list(kept.lon) == [20.3, -15.7]

    def test_no_mask_or_no_rows_changes_nothing(self, kept):
        df = pd.DataFrame({"lon": [-5.1], "lat": [55.4], "observation": [2.0]})
        assert domain_mask.mask_points(df, None) is df
        empty = df.iloc[:0]
        assert domain_mask.mask_points(empty, kept) is empty

    def test_the_extrapolation_setting_is_put_back(self, kept, monkeypatch):
        df = pd.DataFrame({"lon": [-5.1], "lat": [55.4], "observation": [2.0]})
        monkeypatch.setenv("REMAP_EXTRAPOLATE", "on")
        domain_mask.mask_points(df, kept)
        assert os.environ["REMAP_EXTRAPOLATE"] == "on"
        monkeypatch.delenv("REMAP_EXTRAPOLATE")
        domain_mask.mask_points(df, kept)
        assert "REMAP_EXTRAPOLATE" not in os.environ


class TestMatchup:
    @pytest.fixture(autouse=True)
    def fresh(self):
        oceanval.reset()
        yield
        oceanval.reset()

    def test_gridded(self, tmp_path):
        lon, lat = example_grid()
        # the example's two western columns are left out
        path = write_user_mask(tmp_path / "region.nc", lon, lat, lambda x: x >= lon[2])
        oceanval.add_gridded_comparison(
            name="temperature", obs_path=SIM_DIR, source="foo", model_variable="votemper",
            obs_variable="votemper", climatology=False, start=2000, end=2000,
        )
        out_dir = str(tmp_path / "out")
        oceanval.matchup(sim_dir=SIM_DIR, start=2000, end=2000, ask=False, cores=1, out_dir=out_dir, domain_mask=path)
        kept = os.path.join(out_dir, "oceanval_matchups", domain_mask.FILE)
        assert session_info["domain_mask"] == {"path": kept, "method": "nn"}
        with xr.open_dataset(f"{out_dir}/oceanval_matchups/gridded/temperature/foo_temperature_surface.nc") as data:
            model, model_lon = data.model.values, data.lon.values
        west = model_lon < lon[2]
        assert np.isnan(model[..., west]).all()
        assert np.isfinite(model[..., ~west]).any()

    def test_point(self, tmp_path):
        lon, lat = example_grid()
        path = write_user_mask(tmp_path / "region.nc", lon, lat, lambda x: x >= lon[2])
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
        oceanval.matchup(sim_dir=SIM_DIR, start=2000, end=2000, ask=False, cores=1, out_dir=out_dir, domain_mask=path)
        df = pd.read_csv(f"{out_dir}/oceanval_matchups/point/surface/temperature/mine/mine_surface_temperature.csv")
        assert sorted(df.observation) == [3.0, 4.0]

    def test_a_bad_file_is_refused_before_anything_is_done(self, tmp_path):
        path = write_user_mask(tmp_path / "region.nc", LON, LAT, EAST, times=2)
        out_dir = str(tmp_path / "out")
        with pytest.raises(ValueError, match="single time step"):
            oceanval.matchup(sim_dir=SIM_DIR, start=2000, end=2000, ask=False, cores=1, out_dir=out_dir, domain_mask=path)
        assert not os.path.exists(os.path.join(out_dir, "oceanval_matchups"))


def sent_settings(app):
    """What the window sent along with yes, as the stand-in for matchup printed it."""
    wait_for(lambda: "matching up" in app.console.since(0, None)["text"])
    text = app.console.since(0, None)["text"]
    return json.loads(text.split("settings: ", 1)[1].splitlines()[0])


class TestTheWindow:
    """The question, asked with the other matchup boxes: No to start, a file for Yes,
    checked and drawn, and sent along with yes."""

    def test_no_to_start(self):
        from oceanval.app import default_output_form, default_validate_form

        assert default_output_form()["domain_mask"] == default_validate_form()["domain_mask"] == "no"

    def test_the_map_is_only_drawn_while_it_is_asked(self, app, tmp_path, user_mask):
        assert post(app, "/api/domain_mask", {"path": user_mask})[0] == 409
        at_the_question(app, tmp_path, "matchup_validate")
        output_continue(app)
        assert app.view == "report_options"
        status, reply = post(app, "/api/domain_mask", {"path": user_mask})
        assert status == 200 and reply["ok"], reply
        assert (reply["kept"], reply["total"]) == (LAT.size * EAST(LON).sum(), LAT.size * LON.size)
        assert reply["item"]["doc"]
        status, reply = post(app, "/api/domain_mask", {"path": "nothing.nc"})
        assert status == 200 and reply == {"ok": False, "error": "There is no file there."}

    @pytest.mark.parametrize("action", ["matchup_validate", "matchup"])
    def test_yes_needs_a_mask_and_sends_it(self, app, tmp_path, user_mask, action):
        at_the_question(app, tmp_path, action)
        written = []
        app._script_writer = (lambda *choices, **report: written.append(choices[2]), ({}, [], {}, {}))
        bad = write_user_mask(tmp_path / "two.nc", LON, LAT, EAST, times=2)
        if action == "matchup":
            wait_for(lambda: app.view == "output")
            form = {"out": "matchup.py", "cores": "1", "domain_mask": "yes"}
            status, reply = post(app, "/api/output", {"form": form})
            assert status == 400 and reply["errors"] == {"domain_mask_path": "Give the path of the netCDF file."}
            status, reply = post(app, "/api/output", {"form": dict(form, domain_mask_path=bad)})
            assert status == 400 and "single time step" in reply["errors"]["domain_mask_path"]
            # relative to the directory worked in
            output_continue(app, domain_mask="yes", domain_mask_path=os.path.basename(user_mask))
        else:
            output_continue(app)
            form = dict(REPORT, domain_mask="yes")
            status, reply = post(app, "/api/report", {"form": form})
            assert status == 400 and reply["errors"] == {"domain_mask_path": "Give the path of the netCDF file."}
            assert post(app, "/api/report", {"form": dict(form, domain_mask_path=user_mask)})[0] == 200
        settings = sent_settings(app)
        assert settings["domain_mask"] == user_mask
        # the script names the user's own file, which matchup copies
        assert written[-1]["domain_mask"] == user_mask

    def test_no_sends_none(self, app, tmp_path, user_mask):
        at_the_question(app, tmp_path, "matchup_validate")
        output_continue(app)
        # a file typed, then No
        form = dict(REPORT, domain_mask="no", domain_mask_path=user_mask)
        assert post(app, "/api/report", {"form": form})[0] == 200
        assert sent_settings(app)["domain_mask"] is None


def test_the_question_in_a_browser(browser, tmp_path, user_mask, monkeypatch):
    """No to start; Yes shows the file's box, and the mask is drawn once it is
    given, the report blocked until then."""
    app = App(cwd=str(tmp_path))
    started = app.start_run
    monkeypatch.setattr(app, "start_run", lambda args, label: None if args[0] == "validate" else started(args, label))
    app.action = "matchup_validate"
    app.start_run(["matchup", str(matchups_script(tmp_path, "a.nc"))], "python run.py")
    url = app.start()
    try:
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(url)
        page.wait_for_selector("#review:not([hidden])")
        page.click("#actions button:has-text('Yes')")
        through_output(page)
        page.wait_for_selector("#view-validate:not([hidden])")
        page.select_option("#v-cores", "1")
        page.fill("#v-missing_from", "-1e20")
        assert page.is_visible("#v-group-domain-mask")
        assert page.is_checked("#v-dm-no") and page.is_hidden("#v-dm-detail")
        page.wait_for_function("() => !document.getElementById('build').disabled")
        page.check("#v-dm-yes")
        assert page.is_visible("#v-domain_mask_path")
        assert "nearest neighbour" in page.text_content("#v-dm-detail")
        assert page.is_disabled("#build")
        page.fill("#v-domain_mask_path", str(tmp_path / "nothing.nc"))
        page.wait_for_selector("#vm-domain_mask_path.is-error")
        assert "There is no file there." in page.text_content("#vm-domain_mask_path")
        page.fill("#v-domain_mask_path", user_mask)
        page.wait_for_selector("#dm-map-v.is-drawn", timeout=60000)
        assert "cells will be matched up" in page.text_content("#vm-domain_mask_path")
        page.wait_for_function("() => !document.getElementById('build').disabled")
        page.click("#build")
        assert sent_settings(app)["domain_mask"] == user_mask
    finally:
        app.close()
