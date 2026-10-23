"""validate's transect option: checking it, extracting the gridded matchups
along it, and plotting what is found."""

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import transects

needs_to_transect = pytest.mark.skipif(
    not transects.available(), reason="needs nctoolkit 1.3.6 or later"
)

# the levels of a vertical matchup are the observations' own, so can be uneven
DEPTHS = [0.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0]

NORTH_SOUTH = {"start": [-5, 45], "end": [-5, 65]}
EAST_WEST = {"start": [-15, 50], "end": [5, 50]}


def write_matchup(
    path, depths=None, depth_name="deptht", months=12, lon=None, lat=None, valid_lat=None,
    own_axes=False,
):
    """A gridded matchup as oceanval writes it, on a regular grid: model and
    observation, with the model 1 higher, and -9999 as missing. With depths,
    the water column is shallower to the west, so there is a seabed to see.

    valid_lat is the (low, high) latitudes there are values between. If
    own_axes, the model has a depth axis of its own, as the matchups made from
    the observations and the model separately do: CDO names it deptht_2.
    """
    lon = np.arange(-20, 10.01, 1.0) if lon is None else np.asarray(lon, dtype=float)
    lat = np.arange(40, 70.01, 1.0) if lat is None else np.asarray(lat, dtype=float)
    time = pd.to_datetime([f"2000-{month:02d}-16" for month in range(1, months + 1)])
    coords = {
        "time": time,
        "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
        "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"}),
    }
    month = np.arange(months)
    model_dims = None
    if depths is None:
        dims = ("time", "lat", "lon")
        t, y, x = np.meshgrid(month, lat, lon, indexing="ij")
        observation = 10 + 0.2 * (y - 40) - 0.05 * x + 2 * np.sin(2 * np.pi * (t + 1) / 12)
    else:
        depths = np.asarray(depths, dtype=float)
        dims = ("time", depth_name, "lat", "lon")
        coords[depth_name] = (
            depth_name, depths, {"units": "m", "positive": "down", "axis": "Z"}
        )
        if own_axes:
            model_dims = ("time", depth_name + "_2", "lat", "lon")
            coords[depth_name + "_2"] = (
                depth_name + "_2", depths, {"units": "m", "positive": "down", "axis": "Z"}
            )
        t, z, y, x = np.meshgrid(month, depths, lat, lon, indexing="ij")
        observation = 20 - 0.3 * (y - 40) - 0.02 * z + 2 * np.sin(2 * np.pi * (t + 1) / 12)
        # the seabed deepens towards the east
        observation = np.where(z > 50 + 20 * (x + 20), np.nan, observation)
    model = observation + 1.0
    if valid_lat is not None:
        outside = (y < valid_lat[0]) | (y > valid_lat[1])
        observation, model = np.where(outside, np.nan, observation), np.where(outside, np.nan, model)
    data = xr.Dataset(
        {
            "observation": (dims, observation.astype("f4"), {"units": "mmol/m3"}),
            "model": (model_dims or dims, model.astype("f4"), {"units": "mmol/m3"}),
        },
        coords=coords,
    )
    encoding = {name: {"_FillValue": -9999.0} for name in ("model", "observation")}
    data.to_netcdf(path, encoding=encoding)
    return str(path)


@pytest.fixture(scope="module")
def matchups(tmp_path_factory):
    """The matchup files the tests extract from."""
    folder = tmp_path_factory.mktemp("matchups")
    files = {"surface": write_matchup(folder / "surface.nc")}
    for name in ("deptht", "depth", "lev", "z"):
        files[name] = write_matchup(folder / f"vertical_{name}.nc", DEPTHS, name)
    files["own_axes"] = write_matchup(folder / "own_axes.nc", DEPTHS, "deptht", own_axes=True)
    files["one_month"] = write_matchup(folder / "one_month.nc", months=1)
    files["one_depth"] = write_matchup(folder / "one_depth.nc", [10.0], "deptht")
    files["narrow"] = write_matchup(folder / "narrow.nc", valid_lat=(50, 60))
    return files


class TestCheckTransect:
    @needs_to_transect
    def test_a_north_south_transect(self):
        assert transects.check_transect({"start": [-30, 0], "end": [-30, 65]}) == {
            "start": [-30.0, 0.0],
            "end": [-30.0, 65.0],
        }

    @needs_to_transect
    def test_an_east_west_transect_in_either_direction(self):
        assert transects.check_transect({"start": [-10, 55], "end": [8, 55]})["end"] == [8.0, 55.0]
        assert transects.check_transect({"start": [8, 55], "end": [-10, 55]})["end"] == [-10.0, 55.0]

    @needs_to_transect
    def test_it_is_normalised_to_lists_of_floats(self):
        found = transects.check_transect(
            {"start": (np.int64(-30), np.float32(0)), "end": (-30, 65)}
        )

        assert found == {"start": [-30.0, 0.0], "end": [-30.0, 65.0]}
        assert all(type(x) is float for point in found.values() for x in point)
        # so it can be sent to a child process as json
        assert json.loads(json.dumps(found)) == found

    def test_none_is_no_transect(self):
        assert transects.check_transect(None) is None

    @pytest.mark.parametrize("value", ["30W", 5, [-30, 0, -30, 65], ("start", "end")])
    def test_it_is_a_dict(self, value):
        with pytest.raises(TypeError, match="transect must be a dict of start and end"):
            transects.check_transect(value)

    @pytest.mark.parametrize(
        "value",
        [{}, {"start": [-30, 0]}, {"end": [-30, 65]}, {"start": [0, 0], "end": [0, 1], "x": 1}],
    )
    def test_it_has_a_start_and_an_end(self, value):
        with pytest.raises(ValueError, match="two keys, start and end"):
            transects.check_transect(value)

    @pytest.mark.parametrize(
        "point",
        [[-30], [-30, 0, 1], "30W", None, ["a", 0], [True, 0], [float("nan"), 0], [0, float("inf")]],
    )
    def test_each_end_is_a_lon_and_a_lat(self, point):
        with pytest.raises(ValueError, match=r"end must be \[lon, lat\], two numbers"):
            transects.check_transect({"start": [-30, 0], "end": point})

    @pytest.mark.parametrize(
        "start, message",
        [
            ([-181, 0], "start longitude must be between -180 and 360"),
            ([361, 0], "start longitude must be between -180 and 360"),
            ([-30, -91], "start latitude must be between -90 and 90"),
            ([-30, 91], "start latitude must be between -90 and 90"),
        ],
    )
    def test_the_ends_are_on_the_globe(self, start, message):
        with pytest.raises(ValueError, match=message):
            transects.check_transect({"start": start, "end": [-30, 0] if start[0] == -30 else [start[0], 0]})

    def test_the_ends_are_not_the_same_point(self):
        with pytest.raises(ValueError, match="start and end are the same point"):
            transects.check_transect({"start": [-30, 0], "end": [-30, 0]})

    @pytest.mark.parametrize(
        "end", [[-20, 65], [-30.001, 0.001], [0, 1], [-29, 0.5], [330, 10]]
    )
    def test_a_diagonal_transect_is_refused(self, end):
        with pytest.raises(ValueError) as error:
            transects.check_transect({"start": [-30, 0], "end": end})

        assert str(error.value) == transects.RULE
        assert "north-south (the same longitude at both ends)" in str(error.value)
        assert "east-west (the same latitude at both ends)" in str(error.value)

    def test_an_nctoolkit_that_cannot_extract_it_is_reported(self, monkeypatch):
        monkeypatch.setattr(transects, "available", lambda: False)

        with pytest.raises(ValueError, match="nctoolkit 1.3.6 or later"):
            transects.check_transect({"start": [-30, 0], "end": [-30, 65]})
        # what is wrong with it is said first, as it is the user's to fix
        with pytest.raises(ValueError, match="north-south"):
            transects.check_transect({"start": [-30, 0], "end": [-20, 65]})
        # no transect, no need for it
        assert transects.check_transect(None) is None


class TestWords:
    def test_the_direction(self):
        assert transects.direction(NORTH_SOUTH) == "north-south"
        assert transects.direction(EAST_WEST) == "east-west"

    @pytest.mark.parametrize(
        "lon, written",
        [(-30, "30°W"), (0, "0°"), (10.5, "10.5°E"), (190, "170°W"), (-180, "180°"),
         (2.25, "2.25°E"), (359, "1°W")],
    )
    def test_longitudes(self, lon, written):
        assert transects.format_lon(lon) == written

    @pytest.mark.parametrize(
        "lat, written", [(65, "65°N"), (0, "0°"), (-10, "10°S"), (47.5, "47.5°N"), (-0.25, "0.25°S")]
    )
    def test_latitudes(self, lat, written):
        assert transects.format_lat(lat) == written

    def test_depths(self):
        assert transects.format_depth(500) == "500 m"
        assert transects.format_depth(3.04) == "3.04 m"
        assert transects.format_depth(0) == "0 m"

    def test_where_it_runs(self):
        assert transects.describe({"start": [-30, 0], "end": [-30, 65]}) == (
            "north–south along 30°W, from 0° to 65°N"
        )
        assert transects.describe({"start": [-30, 65], "end": [-30, 0]}) == (
            "north–south along 30°W, from 65°N to 0°"
        )
        assert transects.describe({"start": [-10, 55], "end": [8, 55]}) == (
            "east–west along 55°N, from 10°W to 8°E"
        )

    def test_longitudes_are_wrapped_to_the_matchups(self):
        # the matchups have -180 to 180
        assert transects.line_ends({"start": [350, 10], "end": [350, 50]}) == ([-10, 10], [-10, 50])
        assert transects.line_ends({"start": [-30, 0], "end": [-30, 5]}) == ([-30, 0], [-30, 5])
        # not the long way round the globe
        assert transects.line_ends({"start": [170, 0], "end": [190, 0]}) == ([170, 0], [190, 0])


class TestMapExtent:
    def test_room_is_left_around_the_line(self):
        west, east, south, north = transects.map_extent({"start": [-30, 0], "end": [-30, 65]})

        assert (west, east) == (-30 - 32.5, -30 + 32.5)
        assert (south, north) == (-13, 78)

    def test_it_stays_on_the_globe(self):
        west, east, south, north = transects.map_extent({"start": [-175, 80], "end": [-175, 90]})

        assert west >= -180 and east <= 180
        assert south >= -90 and north <= 90

    def test_a_short_line_is_not_zoomed_in_on(self):
        west, east, south, north = transects.map_extent({"start": [2, 50], "end": [2, 52]})

        assert east - west >= 20 and north - south >= 12


class TestOnTheGrid:
    # a 1 degree product's centres put on a half degree grid stopped at 179.5°W
    # and 179.5°E: there was nothing at 180° for to_transect to interpolate across
    SHORT = np.arange(-179.5, 179.51, 0.5)
    DATELINE = {"start": [180, -10], "end": [180, 10]}

    def test_longitudes_all_the_way_round(self):
        assert transects.wraps_round(np.arange(-179.5, 180, 1.0))
        assert transects.wraps_round(np.arange(0.5, 360, 1.0))
        assert transects.wraps_round(np.arange(-180, 180, 0.5))
        # curvilinear, as a model's are: each row's centres shifted unevenly
        rows = np.random.default_rng(1).uniform(0, 1, (50, 1))
        assert transects.wraps_round(np.arange(-180, 180, 1.0)[None, :] + rows)
        assert not transects.wraps_round(np.arange(-20, 10, 1.0)[None, :] + rows)

    def test_longitudes_with_a_gap(self):
        assert not transects.wraps_round(self.SHORT)
        assert not transects.wraps_round(np.arange(-20, 10, 0.1))
        # across the date line, the gap is in the middle
        assert not transects.wraps_round(np.arange(170, 190.01, 0.5))
        assert not transects.wraps_round([5.0])

    def test_a_line_off_the_grid_says_so(self, tmp_path):
        path = write_matchup(tmp_path / "short.nc", lon=self.SHORT, lat=np.arange(-20, 20.1, 0.5))

        with pytest.raises(ValueError, match="along 180°, outside the matchup's longitudes, 179.5°W to 179.5°E"):
            transects.check_on_grid(path, self.DATELINE)
        with pytest.raises(ValueError, match="along 179.75°E"):
            transects.check_on_grid(path, {"start": [179.75, -10], "end": [179.75, 10]})
        # the edge columns themselves are on it
        transects.check_on_grid(path, {"start": [179.5, -10], "end": [179.5, 10]})
        transects.check_on_grid(path, {"start": [-179.5, -10], "end": [-179.5, 10]})

    def test_a_grid_all_the_way_round_has_180(self, tmp_path):
        path = write_matchup(tmp_path / "round.nc", lon=np.arange(-180, 179.51, 0.5), lat=np.arange(-20, 20.1, 0.5))

        transects.check_on_grid(path, self.DATELINE)

    def test_a_grid_across_the_date_line(self, tmp_path):
        path = write_matchup(tmp_path / "pacific.nc", lon=np.arange(170, 190.01, 0.5))

        transects.check_on_grid(path, {"start": [185, 45], "end": [185, 65]})
        transects.check_on_grid(path, {"start": [-175, 45], "end": [-175, 65]})
        with pytest.raises(ValueError, match="along 160°E, outside the matchup's longitudes, 170°E to 170°W"):
            transects.check_on_grid(path, {"start": [160, 45], "end": [160, 65]})

    def test_an_east_west_line_off_the_grid_says_so(self, matchups):
        with pytest.raises(ValueError, match="along 75°N, outside the matchup's latitudes, 40°N to 70°N"):
            transects.check_on_grid(matchups["surface"], {"start": [-15, 75], "end": [5, 75]})
        transects.check_on_grid(matchups["surface"], EAST_WEST)
        transects.check_on_grid(matchups["surface"], NORTH_SOUTH)


class TestDepthName:
    def dataset(self, name, **attrs):
        return xr.Dataset(
            {"observation": (("time", name, "lat", "lon"), np.zeros((1, 3, 2, 2)))},
            coords={name: (name, [1.0, 2.0, 3.0], attrs), "time": [0]},
        )

    @pytest.mark.parametrize("name", ["depth", "deptht", "depthu", "lev", "olevel", "st_ocean", "z", "zlev"])
    def test_the_usual_names(self, name):
        assert transects.depth_name(self.dataset(name)) == name

    def test_what_the_file_says_about_it(self):
        assert transects.depth_name(self.dataset("k", axis="Z")) == "k"
        assert transects.depth_name(self.dataset("k", positive="down")) == "k"
        assert transects.depth_name(self.dataset("k", standard_name="depth")) == "k"

    def test_the_one_dimension_that_is_not_time_or_horizontal(self):
        assert transects.depth_name(self.dataset("k")) == "k"

    def test_time_and_horizontal_dimensions_are_not_depths(self):
        data = xr.Dataset(
            {"observation": (("time_counter", "ncells", "level"), np.zeros((1, 4, 3)))}
        )

        assert transects.depth_name(data) == "level"

    def test_it_is_not_guessed_from_two(self):
        data = xr.Dataset({"observation": (("a", "b", "lat", "lon"), np.zeros((2, 2, 2, 2)))})

        with pytest.raises(ValueError, match="depth coordinate .* could not be identified"):
            transects.depth_name(data)


@needs_to_transect
class TestExtraction:
    def test_about_one_point_per_grid_cell(self, matchups):
        # 66 points for 65 degrees on a 1 degree grid, as in nctoolkit's gallery
        assert transects.transect_steps(matchups["surface"], {"start": [-5, 0], "end": [-5, 65]}) == 66
        assert transects.transect_steps(matchups["surface"], EAST_WEST) == 21

    def test_the_points_are_limited(self, tmp_path):
        fine = write_matchup(tmp_path / "fine.nc", lat=np.arange(40, 41.001, 0.0005))

        assert transects.transect_steps(fine, {"start": [-5, 40], "end": [-5, 41]}) == transects.MAX_STEPS
        assert transects.transect_steps(fine, {"start": [-5, 40], "end": [-5, 40.0005]}) == 2

    def test_the_surface_is_a_monthly_climatology_along_the_line(self, matchups):
        frame = transects.surface_frame(matchups["surface"], NORTH_SOUTH, 21)

        assert list(frame.columns) == ["position", "month", "model", "observation", "bias"]
        assert sorted(frame.month.unique()) == list(range(1, 13))
        # the latitudes of the line, once for each month
        assert sorted(frame.position.unique()) == list(np.arange(45.0, 65.1, 1.0))
        assert len(frame) == 21 * 12
        assert np.allclose(frame.bias, 1.0, atol=1e-4)
        # the seasonal cycle peaks in March, as the data were made to
        march = frame.query("month == 3").observation.mean()
        assert march > frame.query("month == 9").observation.mean()
        # the same observations as the file has, at 5°W
        at_45 = frame.query("position == 45 and month == 3").observation.iloc[0]
        expected = 10 + 0.2 * 5 + 0.05 * 5 + 2 * np.sin(2 * np.pi * 3 / 12)
        assert at_45 == pytest.approx(expected, abs=1e-3)

    def test_an_east_west_line_is_by_longitude(self, matchups):
        frame = transects.surface_frame(matchups["surface"], EAST_WEST, 21)

        assert sorted(frame.position.unique()) == list(np.arange(-15.0, 5.1, 1.0))
        assert sorted(frame.month.unique()) == list(range(1, 13))
        assert len(frame) == 21 * 12

    def test_steps_that_do_not_match_the_grid_give_the_same_result(self, matchups):
        # nctoolkit returns a lon-lat grid when the steps fall evenly on one, and
        # a line of points otherwise
        grid = transects.surface_frame(matchups["surface"], NORTH_SOUTH, 21)
        points = transects.surface_frame(matchups["surface"], NORTH_SOUTH, 37)

        assert len(points) == 37 * 12
        assert points.position.min() == 45 and points.position.max() == 65
        assert sorted(points.month.unique()) == list(range(1, 13))
        # the field is linear in latitude, so interpolation agrees where they meet
        both = grid.merge(points, on=["position", "month"], suffixes=("_grid", "_points"))
        assert len(both) > 12
        assert np.allclose(both.observation_grid, both.observation_points, atol=1e-3)

    def test_an_unstructured_east_west_line(self, matchups):
        points = transects.surface_frame(matchups["surface"], EAST_WEST, 37)

        assert len(points) == 37 * 12
        assert points.position.min() == -15 and points.position.max() == 5

    def test_only_the_stretch_with_values_is_kept(self, matchups):
        frame = transects.surface_frame(matchups["narrow"], NORTH_SOUTH, 21)

        # values are between 50 and 60°N, and bilinear interpolation needs
        # the cells beside a point, so the ends can be a cell short of that
        assert 50 <= frame.position.min() <= 51
        assert 59 <= frame.position.max() <= 60
        assert frame.model.notna().all()

    def test_a_line_with_no_values_is_empty(self, matchups):
        far = {"start": [-5, 68], "end": [-5, 69]}
        frame = transects.surface_frame(matchups["narrow"], far, 3)

        assert len(frame) == 0
        assert list(frame.columns) == ["position", "month", "model", "observation", "bias"]

    def test_a_line_along_180_is_found_on_a_grid_all_the_way_round(self, tmp_path):
        path = write_matchup(tmp_path / "round.nc", lon=np.arange(-180, 179.51, 0.5), lat=np.arange(-20, 20.1, 0.5))
        frame = transects.surface_frame(path, TestOnTheGrid.DATELINE, 41)

        assert len(frame) == 41 * 12
        assert frame.model.notna().all() and frame.observation.notna().all()

    def test_a_line_off_the_grid_is_not_extracted(self, tmp_path):
        path = write_matchup(tmp_path / "short.nc", lon=TestOnTheGrid.SHORT, lat=np.arange(-20, 20.1, 0.5))

        with pytest.raises(ValueError, match="outside the matchup's longitudes"):
            transects.surface_frame(path, TestOnTheGrid.DATELINE, 41)

    def test_a_single_month_has_one_month(self, matchups):
        frame = transects.surface_frame(matchups["one_month"], NORTH_SOUTH, 21)

        assert list(frame.month.unique()) == [1]

    def test_gaps_in_the_line_are_kept_as_gaps(self, tmp_path):
        path = tmp_path / "gap.nc"
        write_matchup(path)
        with xr.open_dataset(path) as data:
            data = data.load()
        data["observation"] = data.observation.where(data.lat != 55)
        data["model"] = data.model.where(data.lat != 55)
        data.to_netcdf(tmp_path / "gap2.nc", encoding={n: {"_FillValue": -9999.0} for n in ("model", "observation")})

        frame = transects.surface_frame(str(tmp_path / "gap2.nc"), NORTH_SOUTH, 21)

        # bilinear interpolation reaches into the rows beside it, so the gap is wider than the row
        assert frame.model.isna().any()
        assert frame.position.min() == 45 and frame.position.max() == 65

    @pytest.mark.parametrize("name", ["deptht", "depth", "lev", "z"])
    def test_the_water_column_is_on_30_evenly_spaced_depths(self, matchups, name):
        frame = transects.section_frame(matchups[name], NORTH_SOUTH, 21)

        assert list(frame.columns) == ["position", "depth", "model", "observation", "bias"]
        depths = np.sort(frame.depth.unique())
        # from the shallowest to the deepest the matchup has, however its levels are spaced
        assert len(depths) == transects.N_DEPTHS == 30
        assert depths[0] == 0 and depths[-1] == 500
        assert np.allclose(np.diff(depths), 500 / 29)
        assert sorted(frame.position.unique()) == list(np.arange(45.0, 65.1, 1.0))
        assert len(frame) == 30 * 21
        assert np.allclose(frame.bias.dropna(), 1.0, atol=1e-3)

    def test_the_model_and_observations_can_have_a_depth_axis_each(self, matchups):
        # one depth axis for the observations and one for the model, as in the
        # matchups oceanval makes: they are not every pair of depths
        with xr.open_dataset(matchups["own_axes"]) as data:
            assert data.observation.dims[1] == "deptht"
            assert data.model.dims[1] == "deptht_2"
        frame = transects.section_frame(matchups["own_axes"], NORTH_SOUTH, 21)
        expected = transects.section_frame(matchups["deptht"], NORTH_SOUTH, 21)

        assert len(frame) == 30 * 21
        assert frame.depth.nunique() == 30 and frame.position.nunique() == 21
        assert np.allclose(frame.bias.dropna(), 1.0, atol=1e-3)
        pd.testing.assert_frame_equal(frame, expected)

    def test_the_seabed_has_no_values(self, matchups):
        frame = transects.section_frame(matchups["deptht"], EAST_WEST, 21)
        # the water is 150 m deep at 15°W, and deeper than the 500 m the matchup goes to at 5°E
        west = frame[frame.position == -15]
        east = frame[frame.position == 5]

        assert len(west) == len(east) == 30
        # interpolating between a level with values and one without gives none
        assert west.query("depth > 160").model.isna().all()
        assert west.query("depth <= 100").model.notna().all()
        assert east.model.notna().all()

    def test_the_section_is_an_annual_mean(self, matchups):
        frame = transects.section_frame(matchups["deptht"], NORTH_SOUTH, 21)
        surface = frame[(frame.depth == 0) & (frame.position == 45)].observation.iloc[0]
        # the seasonal cycle averages away over the 12 months
        expected = 20 - 0.3 * 5
        assert surface == pytest.approx(expected, abs=1e-3)

    def test_a_single_depth_is_no_section(self, matchups):
        assert transects.section_frame(matchups["one_depth"], NORTH_SOUTH, 21) is None

    def test_units_are_as_the_report_writes_them(self, matchups):
        assert transects.unit_text(matchups["surface"], "temperature") == "°C"
        assert transects.unit_text(matchups["surface"], "nitrate") == "mmolm$^{-3}$"


@pytest.fixture(scope="module")
def frames(matchups):
    """What the plots are drawn from."""
    return {
        "months_ns": transects.surface_frame(matchups["surface"], NORTH_SOUTH, 21),
        "months_ew": transects.surface_frame(matchups["surface"], EAST_WEST, 21),
        "section_ns": transects.section_frame(matchups["deptht"], NORTH_SOUTH, 21),
        "section_ew": transects.section_frame(matchups["deptht"], EAST_WEST, 21),
        "one_month": transects.surface_frame(matchups["one_month"], NORTH_SOUTH, 21),
    }


@needs_to_transect
class TestPlots:
    @pytest.fixture(autouse=True)
    def close_figures(self):
        yield
        plt.close("all")

    @staticmethod
    def panels(fig):
        """The plot's own axes, without the colour bars."""
        return [ax for ax in fig.axes if ax.get_label() != "<colorbar>"]

    @staticmethod
    def colour_maps(fig):
        return [ax.collections[0].get_cmap().name for ax in TestPlots.panels(fig)]

    def test_months_north_south_is_month_across_and_latitude_up(self, frames):
        fig = transects.plot_months(frames["months_ns"], NORTH_SOUTH, "mmol", "nitrate")
        model, observation, bias = self.panels(fig)

        assert [ax.get_title() for ax in (model, observation, bias)] == [
            "Model", "Observation", "Model − observation",
        ]
        # three panels side by side, and a colour bar each for the values and the bias
        assert len(fig.axes) == 5
        assert model.get_position().y0 == pytest.approx(bias.get_position().y0)
        assert [label.get_text() for label in model.get_xticklabels()] == [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
        ]
        fig.canvas.draw()
        labels = [label.get_text() for label in model.get_yticklabels()]
        assert all(label.endswith("°N") for label in labels if label)
        assert model.get_ylabel() == "Latitude"
        assert not model.yaxis_inverted()
        # viridis for the values and blue-white-red for the bias, as in the rest of the report
        assert self.colour_maps(fig) == ["viridis", "viridis", "bwr"]

    def test_months_east_west_is_longitude_across_and_month_down(self, frames):
        fig = transects.plot_months(frames["months_ew"], EAST_WEST, "mmol", "nitrate")
        model, observation, bias = self.panels(fig)

        # stacked, so that west to east reads across all of them
        assert model.get_position().x0 == pytest.approx(bias.get_position().x0)
        assert model.get_position().y0 > bias.get_position().y0
        assert bias.get_xlabel() == "Longitude"
        fig.canvas.draw()
        labels = [label.get_text() for label in bias.get_xticklabels() if label.get_text()]
        assert all(label.endswith(("°W", "°E")) or label == "0°" for label in labels)
        # January at the top, in all three
        assert all(ax.yaxis_inverted() for ax in (model, observation, bias))
        assert self.colour_maps(fig) == ["viridis", "viridis", "bwr"]

    def test_the_bias_is_centred_on_zero_and_the_values_share_a_scale(self, frames):
        fig = transects.plot_months(frames["months_ns"], NORTH_SOUTH, "mmol", "nitrate")
        model, observation, bias = self.panels(fig)

        low, high = bias.collections[0].get_clim()
        assert low == -high
        assert model.collections[0].get_clim() == observation.collections[0].get_clim()

    def test_where_there_are_no_values_is_grey(self, frames):
        fig = transects.plot_months(frames["months_ns"], NORTH_SOUTH, "mmol", "nitrate")

        assert all(ax.get_facecolor()[:3] == pytest.approx((0.851, 0.871, 0.878), abs=1e-2)
                   for ax in self.panels(fig))

    def test_chlorophyll_is_on_a_log_scale(self, frames):
        fig = transects.plot_months(frames["months_ns"], NORTH_SOUTH, "mg", "chlorophyll", log=True)
        model = self.panels(fig)[0]

        assert type(model.collections[0].norm).__name__ == "LogNorm"

    def test_a_single_month_is_a_line(self, frames):
        fig = transects.plot_line(frames["one_month"], NORTH_SOUTH, "mmol", "nitrate")
        [ax] = self.panels(fig)

        assert [line.get_label() for line in ax.get_lines()] == ["Model", "Observation"]
        assert ax.get_xlabel() == "Latitude"

    def test_the_section_is_position_across_and_depth_down(self, frames):
        fig = transects.plot_section(frames["section_ns"], NORTH_SOUTH, "mmol", "nitrate")
        model, observation, bias = self.panels(fig)

        assert model.get_ylabel() == "Depth (m)"
        # the surface is at the top
        assert model.yaxis_inverted()
        assert model.get_ylim() == pytest.approx((500, 0))
        assert model.get_xlabel() == "Latitude"
        fig.canvas.draw()
        assert all(label.get_text().endswith("°N") for label in model.get_xticklabels() if label.get_text())
        assert self.colour_maps(fig) == ["viridis", "viridis", "bwr"]
        assert [ax.get_title() for ax in (model, observation, bias)] == [
            "Model", "Observation", "Model − observation",
        ]

    def test_the_section_of_an_east_west_line_is_by_longitude(self, frames):
        fig = transects.plot_section(frames["section_ew"], EAST_WEST, "mmol", "nitrate")
        model = self.panels(fig)[0]

        assert model.get_xlabel() == "Longitude"
        fig.canvas.draw()
        assert any(label.get_text().endswith(("°W", "°E")) for label in model.get_xticklabels())

    def test_the_map_shows_the_line_over_the_land(self):
        pytest.importorskip("cartopy")
        fig = transects.plot_map({"start": [-30, 0], "end": [-30, 65]})
        [ax] = fig.axes

        line = [artist for artist in ax.get_lines() if len(artist.get_xdata()) == 2][0]
        assert list(line.get_xdata()) == [-30, -30]
        assert list(line.get_ydata()) == [0, 65]
        # the end labels, with the latitude as the label of a north-south line
        assert sorted(text.get_text() for text in ax.texts) == ["0°", "65°N"]
        west, east, south, north = transects.map_extent({"start": [-30, 0], "end": [-30, 65]})
        extent = ax.get_extent()
        assert (extent[0], extent[1], extent[2], extent[3]) == pytest.approx((west, east, south, north), abs=1e-6)

    def test_the_map_of_an_east_west_line_labels_the_longitudes(self):
        pytest.importorskip("cartopy")
        fig = transects.plot_map(EAST_WEST)
        [ax] = fig.axes

        assert sorted(text.get_text() for text in ax.texts) == ["15°W", "5°E"]
