import oceanval
import glob
import os
import pytest
import tempfile
import shutil

import nctoolkit as nc

from oceanval import matchall, prompts
from oceanval.session import session_info

needs_two_cores = pytest.mark.skipif(
    (os.cpu_count() or 1) < 2, reason="needs a machine with two cores"
)


class TestMatchup:
    """Test suite for matchup function"""

    @pytest.fixture(autouse=True)
    def clean_matchups(self):
        # in a fixture, so that it is only removed when the tests run, not
        # whenever this file is imported
        yield
        shutil.rmtree("oceanval_matchups", ignore_errors=True)

    def test_missing_sim_dir(self):
        """Test that ValueError is raised when sim_dir is not provided"""
        oceanval.reset()
        
        with pytest.raises(ValueError, match="Please provide a sim_dir directory"):
            oceanval.matchup(start=2000, end=2001)
    
    def test_nonexistent_sim_dir(self):
        """Test that ValueError is raised when sim_dir doesn't exist"""
        oceanval.reset()
        
        with pytest.raises(ValueError, match="does not exist"):
            oceanval.matchup(sim_dir="/nonexistent/path", start=2000, end=2001)
    
    def test_missing_start_year(self):
        """Test that ValueError is raised when start is not provided"""
        oceanval.reset()
        
        with pytest.raises(ValueError, match="Please provide a start year"):
            oceanval.matchup(sim_dir="data/example", end=2001)
    
    def test_missing_end_year(self):
        """Test that ValueError is raised when end is not provided"""
        oceanval.reset()
        
        with pytest.raises(ValueError, match="Please provide an end year"):
            oceanval.matchup(sim_dir="data/example", start=2000)
    
    def test_invalid_start_type(self):
        """Test that TypeError is raised when start is not an integer"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="Start must be an integer"):
            oceanval.matchup(sim_dir="data/example", start="2000", end=2001)
    
    def test_invalid_end_type(self):
        """Test that TypeError is raised when end is not an integer"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="End must be an integer"):
            oceanval.matchup(sim_dir="data/example", start=2000, end="2001")
    
    def test_invalid_ask_type(self):
        """Test that TypeError is raised when ask is not boolean"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="ask must be a boolean"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, ask="yes",
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    
    def test_invalid_overwrite_type(self):
        """Test that TypeError is raised when overwrite is not boolean"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="overwrite must be a boolean"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, overwrite="yes",
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    
    def test_invalid_n_dirs_down_type(self):
        """Test that TypeError is raised when n_dirs_down is not an integer"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="n_dirs_down must be an integer"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, n_dirs_down="2",
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    
    def test_negative_n_dirs_down(self):
        """Test that ValueError is raised when n_dirs_down is negative"""
        oceanval.reset()
        
        with pytest.raises(ValueError, match="n_dirs_down must be a positive integer"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, n_dirs_down=-1,
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    
    def test_invalid_exclude_type(self):
        """Test that TypeError is raised when exclude is not list or string"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="exclude must be a list or a string"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, exclude=123,
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    
    def test_exclude_string_conversion(self):
        """Test that exclude string is converted to list"""
        oceanval.reset()
        
        # This should not raise an error - just testing type conversion
        # We can't fully test matchup without proper simulation data, but we can test parameter handling
        try:
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, exclude="test", ask=False)
        except ValueError as e:
            # Expected to fail because no variables are defined, but shouldn't fail on exclude type
            assert "exclude must be a list" not in str(e)
    
    def test_invalid_require_type(self):
        """Test that TypeError is raised when require is not list or string"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="require must be a list or a string"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, require=123,
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    
    def test_require_string_conversion(self):
        """Test that require string is converted to list"""
        oceanval.reset()
        
        # Test that string is properly converted - should not raise TypeError about require
        try:
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, require="test", ask=False)
        except ValueError as e:
            # Expected to fail for other reasons, but not require type
            assert "require must be a list" not in str(e)
    
    def test_invalid_point_time_res_type(self):
        """Test that TypeError is raised when point_time_res is not list or string"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="point_time_res must be a list or a string"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, point_time_res=123,
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    
    def test_point_time_res_string_conversion(self):
        """Test that point_time_res string is converted to list"""
        oceanval.reset()
        
        # Should not raise TypeError about point_time_res
        try:
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, point_time_res="year", ask=False)
        except ValueError as e:
            assert "point_time_res must be a list" not in str(e)
    
    def test_nonexistent_thickness_file(self):
        """Test that FileNotFoundError is raised when thickness file doesn't exist"""
        oceanval.reset()
        
        with pytest.raises(FileNotFoundError, match="does not exist"):
            oceanval.matchup(
                sim_dir="data/example",
                start=2000,
                end=2001,
                thickness="/nonexistent/thickness.nc",
                lon_lim=[-10,10],
                lat_lim=[40,50]
            )
    
    def test_lon_lat_lim_mismatch(self):
        """Test that TypeError is raised when only one of lon_lim/lat_lim is provided"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="lon_lim and lat_lim must be lists"):
            oceanval.matchup(
                sim_dir="data/example",
                start=2000,
                end=2001,
                lon_lim=[-10, 10]
            )
        
        with pytest.raises(TypeError, match="lon_lim and lat_lim must be lists"):
            oceanval.matchup(
                sim_dir="data/example",
                start=2000,
                end=2001,
                lat_lim=[40, 50]
            )
    
    def test_no_variables_defined(self):
        """Test that ValueError is raised when no variables are requested for validation"""
        oceanval.reset()
        
        with pytest.raises(ValueError, match="You do not appear to have asked for any variables to be validated"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, ask=False, lon_lim=[-10,10], lat_lim=[40,50])
    
    def test_cores_parameter(self):
        """Test that cores parameter is handled correctly"""
        oceanval.reset()
        # cores = -1 should faile
        with pytest.raises(ValueError, match="cores must be a positive integer"):
            oceanval.matchup(
                sim_dir="data/example",
                start=2000,
                end=2001,
                cores=-1,
                ask=False,
                lon_lim=[-10,10],
                lat_lim=[40,50]
            )
        # cores = 100000 should fail
        with pytest.raises(ValueError, match="is greater than the number of system cores"):
            oceanval.matchup(
                sim_dir="data/example",
                start=2000,
                end=2001,
                cores=100000,
                ask=False,
                lon_lim=[-10,10],
                lat_lim=[40,50]
            )
        
    def test_invalid_out_dir_type(self):
        """Test that TypeError is raised when out_dir is not a string"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="out_dir must be a string"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, out_dir=123,
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
                
    def test_exclude_item_type(self):
        """Test that TypeError is raised when an item in exclude list is not a string"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="each item in exclude must be a string"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, exclude=["valid", 123],
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    def test_require_item_type(self):
        """Test that TypeError is raised when an item in require list is not a string"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="each item in require must be a string"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, require=["valid", 123],
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    
    def test_invalid_cache_type(self):
        """Test that TypeError is raised when cache is not boolean"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="cache must be a boolean"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, cache="yes",
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    
    def test_invalid_n_check_type(self):
        """Test that TypeError is raised when n_check is not integer or None"""
        oceanval.reset()
        
        with pytest.raises(TypeError, match="n_check must be an integer"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, n_check="ten",
            lon_lim=[-10,10], lat_lim=[40,50]
                             )
    def test_negative_n_check(self):
        """Test that ValueError is raised when n_check is negative"""
        oceanval.reset()
        
        with pytest.raises(ValueError, match="n_check must be a positive integer"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, n_check=-5,
            lon_lim=[-10,10], lat_lim=[40,50]
                             )

    # as_missing tests
    def test_invalid_as_missing_type(self):
         """Test that TypeError is raised when as_missing is not boolean"""
         oceanval.reset()
         
         with pytest.raises(TypeError, match="as_missing must be a float"):
             oceanval.matchup(sim_dir="data/example", start=2000, end=2001, as_missing="yes",
             lon_lim=[-10,10], lat_lim=[40,50]
                              )
                              # test elements of as_missing list
    def test_as_missing_item_type(self):
        """Test that TypeError is raised when an item in as_missing list is not float"""
        oceanval.reset()
            
        with pytest.raises(TypeError, match="as_missing list elements must be float"): 
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, as_missing=[0.1, "invalid"],
            lon_lim=[-10,10], lat_lim=[40,50]
                            )

    # strict_names test
    def test_invalid_strict_names_type(self):
            """Test that TypeError is raised when strict_names is not boolean"""
            oceanval.reset()
            
            with pytest.raises(TypeError, match="strict_names must be a boolean"):
                oceanval.matchup(sim_dir="data/example", start=2000, end=2001, strict_names="yes",
                lon_lim=[-10,10], lat_lim=[40,50]
                                )


class TestCores:
    """matchup's cores are for its own work. Once it ends, nctoolkit's own
    options are as they were, as nctoolkit would otherwise go on running NCO,
    and CDO on multi-file datasets, in pools of its own, which can hang."""

    @needs_two_cores
    def test_nctoolkits_options_are_put_back_after_an_error(self):
        oceanval.reset()
        nc.options(cores=1)

        # fvcom is checked once the cores are set
        with pytest.raises(TypeError, match="fvcom must be a boolean"):
            oceanval.matchup(
                sim_dir="data/example", start=2000, end=2000, cores=2, fvcom="yes"
            )

        assert nc.session_info["cores"] == 1

    @needs_two_cores
    def test_whether_it_returns_or_raises(self):
        @matchall._restores_nctoolkit_options
        def uses_them(fail):
            nc.options(cores=2, parallel=True)
            if fail:
                raise RuntimeError("failed")
            return "done"

        nc.options(cores=1, parallel=False)
        assert uses_them(False) == "done"
        assert (nc.session_info["cores"], nc.session_info["parallel"]) == (1, False)

        with pytest.raises(RuntimeError, match="failed"):
            uses_them(True)
        assert (nc.session_info["cores"], nc.session_info["parallel"]) == (1, False)


class TestMatchupsQuestion:
    """What matchup sends with its question of whether the matchups are right,
    for the oceanval window to show as a table."""

    def test_the_files_matchup_reads(self, tmp_path, monkeypatch):
        names = [
            "2011/x_2011_grid_T.nc",
            "2012/x_2012_grid_T.nc",
            # a longer name than the example's, a skipped word, and a restart directory
            "2012/x_20121_grid_T.nc",
            "2012/x_2012_bad_grid_T.nc",
            "restart/x_2013_grid_T.nc",
            "2012/y_2012_grid_T.nc",
        ]
        for name in names:
            (tmp_path / name).parent.mkdir(exist_ok=True)
            (tmp_path / name).write_text("")
        pattern = "x_**_grid_T.nc"
        monkeypatch.setitem(session_info, "levels_down", 1)
        monkeypatch.setitem(matchall.example_files, pattern, str(tmp_path / names[0]))

        found = matchall._files_found(str(tmp_path), [pattern], ["bad"], ["grid_T"], True)
        assert found == {pattern: [str(tmp_path / names[0]), str(tmp_path / names[1])]}
        found = matchall._files_found(str(tmp_path), [pattern], ["bad"], ["nothing"], False)
        assert found == {pattern: []}

    def test_each_matchup_to_make_goes_with_the_question(self, tmp_path):
        """For the window to list, to choose those the report validates."""
        oceanval.reset()
        oceanval.add_gridded_comparison(
            name="temperature",
            obs_path="data/evaldata/gridded/nws/temperature",
            source="foo",
            model_variable="votemper",
            obs_variable="votemper",
            climatology=True,
        )
        oceanval.add_point_comparison(
            name="temperature",
            obs_path="data/evaldata/point/nws/all/temperature",
            source="bar",
            model_variable="votemper",
            vertical=True,
            start=2004,
        )
        asked = []

        def answerer(question, choices, details):
            asked.append(details)
            return "n"

        with prompts.answered_by(answerer):
            oceanval.matchup(
                sim_dir="data/example", start=2000, end=2004, cores=1, out_dir=str(tmp_path),
                thickness="data/example/e3t.nc",
            )

        [details] = asked
        # each dataset is matched up for the run's years, or its own within
        # them, and the window shows a row for each of these
        assert details["rows"][0]["groups"] == [
            {"years": [2000, 2004], "sources": ["foo"], "gridded": True},
            {"years": [2004, 2004], "sources": ["bar (point)"], "gridded": False},
        ]
        assert details["matchups"] == [
            {"kind": "gridded", "variable": "temperature", "source": "foo", "layer": None,
             "vertical": False, "title": "Temperature", "key": "temperature/foo"},
            {"kind": "point", "variable": "temperature", "source": "bar", "layer": "all",
             "vertical": True, "title": "Temperature", "key": "temperature/bar"},
        ]
        oceanval.reset()

    def test_each_variable_and_its_files_go_with_the_question(self, tmp_path):
        oceanval.reset()
        oceanval.add_gridded_comparison(
            name="temperature",
            obs_path="data/evaldata/gridded/nws/temperature",
            source="foo",
            model_variable="votemper",
            obs_variable="votemper",
            climatology=True,
        )
        asked = []

        def answerer(question, choices, details):
            asked.append((question, choices, details))
            return "n"

        with prompts.answered_by(answerer):
            result = oceanval.matchup(
                sim_dir="data/example", start=2004, end=2004, cores=1, out_dir=str(tmp_path)
            )

        assert result is None
        [(question, choices, details)] = asked
        assert (question, choices) == ("Are you happy with these matchups? (y/n) ", ("y", "n"))
        sim_dir = os.path.abspath("data/example")
        files = sorted(
            os.path.relpath(path, sim_dir)
            for path in glob.glob(os.path.join(sim_dir, "*", "*", "*_grid_T.nc"))
        )
        pattern = "amm7_1d_**_**_grid_T.nc"
        assert (details["kind"], details["sim_dir"], details["years"]) == (
            "matchups",
            sim_dir,
            [2004, 2004],
        )
        assert details["rows"] == [
            {
                "variable": "temperature",
                "title": "Temperature",
                "model_variable": "votemper",
                "pattern": pattern,
                "observations": ["foo"],
                # matched up for the run's years
                "groups": [{"years": [2004, 2004], "sources": ["foo"], "gridded": True}],
                "files": len(files),
                # one time a day in each file
                "time_res": "1d",
            }
        ]
        assert details["files"] == {pattern: files}
        # how the times of the files are read, for the window's check of their dates
        assert details["fvcom"] is False
        assert "as_missing" not in details
        # daily output, and no point datasets, is nothing to check
        assert "point_time_res" not in details
        # no is not a matchup
        assert not os.path.exists(tmp_path / "oceanval_matchups" / "mapping.csv")

    def test_the_times_the_window_read_are_not_read_again(self, tmp_path, monkeypatch):
        """Yes from the oceanval window can come with the times of the files
        its check of their dates read (see oceanval.model_check), which
        matchup starts from, rather than read them all again."""
        from oceanval import model_check

        oceanval.reset()
        oceanval.add_gridded_comparison(
            name="temperature",
            obs_path="data/evaldata/gridded/nws/temperature",
            source="foo",
            model_variable="votemper",
            obs_variable="votemper",
            climatology=True,
        )
        files = sorted(glob.glob(os.path.abspath("data/example/*/*/*_grid_T.nc")))
        index = str(tmp_path / "times.pkl")
        # all but the last, which matchup reads itself
        model_check.save_times_index(index, matchall.index_times(files[:-1]))

        class Stop(Exception):
            pass

        given = []

        def index_times(paths, fvcom=False, times=None, progress=None):
            given.append(dict(times))
            raise Stop()

        monkeypatch.setattr(matchall, "index_times", index_times)
        with prompts.answered_by(lambda *asked: prompts.Answer("y", {"times_index": index})):
            with pytest.raises(Stop):
                oceanval.matchup(
                    sim_dir="data/example", start=2004, end=2004, cores=1,
                    out_dir=str(tmp_path), as_missing=0,
                )
        oceanval.reset()

        # matchup's first call starts from all the times the check read
        assert set(given[0]) == set(files[:-1])

class TestPointTimeResCheck:
    """Point datasets matched by day against output coarser than daily are
    asked about once the matchups are right (see oceanval.time_res)."""

    def register(self, **own):
        oceanval.reset()
        oceanval.add_point_comparison(
            name="temperature",
            source="foo",
            model_variable="votemper",
            obs_path="data/evaldata/point/nws/all/temperature",
            **own,
        )

    def matchup(self, tmp_path, answers, **kwargs):
        asked = []

        def answerer(question, choices, details):
            asked.append((question, choices, details))
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with prompts.answered_by(answerer):
            try:
                oceanval.matchup(
                    sim_dir="data/example", start=2004, end=2004, cores=1,
                    out_dir=str(tmp_path), **kwargs,
                )
            except KeyboardInterrupt:
                pass
        return asked

    def test_monthly_output_flags_a_dataset_matched_by_day(self, tmp_path, monkeypatch):
        monkeypatch.setattr(matchall, "get_time_res", lambda *args: "monthly")
        self.register()
        asked = self.matchup(tmp_path, ["n"])

        [(_, _, details)] = asked
        assert details["rows"][0]["time_res"] == "monthly"
        assert details["point_time_res"] == {
            "default": ["year", "month", "day"],
            "rows": [
                {
                    "key": "temperature/foo",
                    "variable": "temperature",
                    "title": "Temperature",
                    "source": "foo",
                    "time_res": "monthly",
                    "point_time_res": ["year", "month", "day"],
                    "own": False,
                }
            ],
        }

    def test_daily_output_or_matching_by_month_is_not_flagged(self, tmp_path, monkeypatch):
        self.register()
        [(_, _, details)] = self.matchup(tmp_path, ["n"])
        assert "point_time_res" not in details

        monkeypatch.setattr(matchall, "get_time_res", lambda *args: "monthly")
        self.register(point_time_res=["year", "month"])
        [(_, _, details)] = self.matchup(tmp_path, ["n"])
        assert "point_time_res" not in details

    def test_the_window_chooses_along_with_yes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(matchall, "get_time_res", lambda *args: "5d")
        self.register(point_time_res=["month", "day"])
        choice = {"default": None, "datasets": {"temperature/foo": ["month"]}}
        original = matchall.time_res.apply

        def apply(*args):
            original(*args)
            # stopped once the choice is used, before anything is matched up
            raise KeyboardInterrupt

        monkeypatch.setattr(matchall.time_res, "apply", apply)
        asked = self.matchup(tmp_path, [prompts.Answer("y", {"point_time_res": choice})])

        # not asked again at the terminal
        assert len(asked) == 1
        comparison = oceanval.definitions["temperature"].point_comparisons["foo"]
        assert comparison["point_time_res"] == ["month"]

    def test_a_terminal_is_asked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(matchall, "get_time_res", lambda *args: "monthly")
        self.register()
        original = matchall.time_res.apply

        def apply(*args):
            original(*args)
            raise KeyboardInterrupt

        monkeypatch.setattr(matchall.time_res, "apply", apply)
        # a plain answer is one typed at a terminal
        asked = self.matchup(tmp_path, ["y", "a", "2"])

        assert [question for question, _, _ in asked][1].startswith("Change point_time_res")
        assert session_info["point_time_res"] == ["year", "month"]


def _model_file(path, variables):
    """A small netCDF file with the given variables on depth levels"""
    import numpy as np
    import xarray as xr

    shape = (3, 2, 2)
    ds = xr.Dataset(
        {name: (("deptht", "lat", "lon"), np.ones(shape)) for name in variables},
        coords={"deptht": [0.5, 1.5, 2.5], "lat": [50.0, 51.0], "lon": [-4.0, -3.0]},
    )
    ds.to_netcdf(path)
    return str(path)


class TestThicknessFile:
    """The thickness variable is found by its exact name only"""

    def test_a_variable_with_the_name_in_it_is_not_taken(self, tmp_path):
        # ERSEM's depth integrated trends have e3t in their names
        trends = _model_file(tmp_path / "grid_T.nc", ["XAD_O3_c_e3t", "O3_c_e3t"])
        real = _model_file(tmp_path / "ptrc_T.nc", ["e3t", "N3_n"])

        assert matchall._thickness_file([trends, real], "e3t") == real
        assert matchall._thickness_file([real, trends], "e3t") == real

    def test_no_exact_name_says_which_names_have_it_in_them(self, tmp_path):
        trends = _model_file(tmp_path / "grid_T.nc", ["XAD_O3_c_e3t", "O3_c_e3t", "votemper"])

        with pytest.raises(ValueError) as error:
            matchall._thickness_file([trends], "e3t")

        assert str(error.value) == (
            "No variable named e3t was found in the simulation output. Variables with e3t "
            "in their names: O3_c_e3t, XAD_O3_c_e3t. Give the exact name of the cell "
            "thickness variable as thickness, or a thickness file."
        )

    def test_no_similar_names_are_listed_when_there_are_none(self, tmp_path):
        other = _model_file(tmp_path / "grid_T.nc", ["votemper"])

        with pytest.raises(ValueError) as error:
            matchall._thickness_file([other], "e3t")

        assert str(error.value) == (
            "No variable named e3t was found in the simulation output. Give the exact "
            "name of the cell thickness variable as thickness, or a thickness file."
        )


def _thickness_nc(path, name, time=True, positive="down", levels=3, vertical_coordinate=True):
    """A small cell thickness file, with or without a time dimension"""
    import numpy as np
    import xarray as xr

    dims = (("time_counter",) if time else ()) + ("deptht", "lat", "lon")
    shape = ((1,) if time else ()) + (levels, 2, 2)
    coords = {"lat": [50.0, 51.0], "lon": [-4.0, -3.0]}
    if vertical_coordinate:
        coords["deptht"] = np.arange(levels) + 0.5
    if time:
        coords["time_counter"] = [0.0]
    ds = xr.Dataset({name: (dims, np.ones(shape))}, coords=coords)
    ds.lat.attrs.update(units="degrees_north", standard_name="latitude")
    ds.lon.attrs.update(units="degrees_east", standard_name="longitude")
    if time:
        ds.time_counter.attrs.update(units="seconds since 2000-01-01", standard_name="time")
    if vertical_coordinate and positive:
        ds.deptht.attrs["positive"] = positive
    ds.to_netcdf(path)
    return str(path)


class TestStaticThickness:
    """A cell thickness with no time dimension, such as NEMO's e3t_0"""

    @pytest.fixture(autouse=True)
    def one_core(self):
        # nctoolkit's pools can hang, and is_z_up runs ncks through them
        cores = nc.session.session_info["cores"]
        nc.options(cores=1)
        yield
        nc.options(cores=cores)

    @pytest.mark.parametrize("time", [True, False])
    @pytest.mark.parametrize("positive, up", [("down", False), ("up", True)])
    def test_the_direction_is_read_with_or_without_time(self, tmp_path, time, positive, up):
        path = _thickness_nc(tmp_path / "e3t.nc", "e3t_0", time=time, positive=positive)

        assert matchall.is_z_up(path, "e3t_0") is up

    def test_no_positive_attribute_says_so(self, tmp_path):
        path = _thickness_nc(tmp_path / "e3t.nc", "e3t_0", time=False, positive=None)

        with pytest.raises(ValueError, match="vertical coordinate deptht of e3t_0 .* has no positive attribute"):
            matchall.is_z_up(path, "e3t_0")

    def test_no_vertical_coordinate_says_so(self, tmp_path):
        path = _thickness_nc(tmp_path / "e3t.nc", "e3t_0", time=False, vertical_coordinate=False)

        with pytest.raises(ValueError, match="has no vertical coordinate"):
            matchall.is_z_up(path, "e3t_0")

    def test_a_single_level_is_not_selected(self, tmp_path):
        # an empty selection would leave CDO waiting for ever
        one = nc.open_data(_thickness_nc(tmp_path / "one.nc", "e3t", levels=1), checks=False)
        three = nc.open_data(_thickness_nc(tmp_path / "three.nc", "e3t"), checks=False)

        with pytest.raises(ValueError, match="has only one vertical level"):
            matchall._thickness_levels(one, "e3t", "e3t in one.nc")
        assert matchall._thickness_levels(three, "e3t", "e3t in three.nc") == ["e3t"]

    def test_the_thickness_is_picked_out_of_a_file(self, tmp_path):
        import xarray as xr

        single = _thickness_nc(tmp_path / "single.nc", "e3t_0", time=False)
        assert matchall._thickness_from_file(single) == "e3t_0"

        ds = xr.open_dataset(single).load()
        ds["cell_thickness_t"] = ds.e3t_0
        ds["e3t_0"] = ds.e3t_0 * 2
        ds.to_netcdf(tmp_path / "named.nc")
        assert matchall._thickness_from_file(str(tmp_path / "named.nc")) == "cell_thickness_t"

        ds = ds.rename({"cell_thickness_t": "e3w_0"})
        ds.to_netcdf(tmp_path / "two.nc")
        with pytest.raises(ValueError, match=r"more than one variable \(.*e3t_0.*e3w_0"):
            matchall._thickness_from_file(str(tmp_path / "two.nc"))

    def test_a_mesh_file_thickness_cdo_cannot_read_says_so(self, tmp_path):
        import numpy as np
        import xarray as xr

        # as in NEMO mesh files: 4D, with a first dimension that is not time
        xr.Dataset({"e3t_0": (("t", "z", "y", "x"), np.ones((1, 3, 2, 2)))}).to_netcdf(tmp_path / "mesh.nc")

        with pytest.raises(ValueError, match="e3t_0 in .* cannot be read by CDO"):
            matchall._thickness_from_file(str(tmp_path / "mesh.nc"))

    def test_a_static_thickness_file_gives_the_same_matchups(self, tmp_path):
        import pandas as pd
        import xarray as xr

        # the example thickness without its time dimension
        e3t = xr.open_dataset("data/example/e3t.nc", decode_times=False).load()
        e3t.isel(time_counter=0, drop=True)[["e3t"]].to_netcdf(tmp_path / "e3t_static.nc")
        # a few of the example observations, to keep it quick
        obs = pd.read_csv("data/evaldata/point/nws/all/temperature/model_temperature.csv")
        obs = obs.query("month == 1").iloc[::500]
        os.makedirs(tmp_path / "obs")
        obs.to_csv(tmp_path / "obs" / "obs.csv", index=False)

        matchups = []
        for name, thickness in [("time", "data/example/e3t.nc"), ("static", str(tmp_path / "e3t_static.nc"))]:
            oceanval.reset()
            oceanval.add_point_comparison(
                name="temperature", source="foo", model_variable="votemper",
                obs_path=str(tmp_path / "obs"), vertical=True, obs_adder=273.15,
            )
            oceanval.matchup(
                sim_dir="data/example", start=2000, end=2000, ask=False,
                thickness=thickness, cores=1, out_dir=str(tmp_path / name),
            )
            df = pd.read_csv(
                tmp_path / name / "oceanval_matchups/point/all/temperature/foo/foo_all_temperature.csv"
            )
            columns = sorted(df.columns)
            matchups.append(df.loc[:, columns].sort_values(columns).reset_index(drop=True))
        oceanval.reset()

        assert len(matchups[0]) > 0
        pd.testing.assert_frame_equal(matchups[0], matchups[1])


def _levels_nc(path, levels, positive=None):
    """A small file of temperature on the given vertical levels"""
    import numpy as np
    import xarray as xr

    ds = xr.Dataset(
        {"votemper": (("time_counter", "deptht", "lat", "lon"), np.ones((1, len(levels), 2, 2)))},
        coords={"time_counter": [0.0], "deptht": levels, "lat": [50.0, 51.0], "lon": [-4.0, -3.0]},
    )
    ds.lat.attrs.update(units="degrees_north", standard_name="latitude")
    ds.lon.attrs.update(units="degrees_east", standard_name="longitude")
    ds.time_counter.attrs.update(units="seconds since 2000-01-01", standard_name="time")
    if positive:
        ds.deptht.attrs["positive"] = positive
    ds.to_netcdf(path)
    return str(path)


def _upside_down(ds):
    """The same ocean with its first level at the bottom, on heights, positive up"""
    ds = ds.isel(deptht=slice(None, None, -1))
    attrs = dict(ds.deptht.attrs, positive="up")
    attrs.pop("bounds", None)
    ds = ds.drop_vars([x for x in ["deptht_bnds"] if x in ds])
    ds = ds.assign_coords(deptht=-ds.deptht.values)
    ds.deptht.attrs.update(attrs)
    return ds


def _getm_like(ds, e3t):
    """
    The same ocean as GETM writes it: its first level at the bottom, on a vertical
    dimension with no coordinate variable, whose heights (positive up) are a 4D
    coordinate named in each variable's coordinates attribute. The cell thickness
    and a 2D sea surface temperature are in the output, as GETM's hnt and sst are.
    """
    import numpy as np

    ds = _upside_down(ds)
    heights = ds.deptht.values
    ds = ds.drop_vars("deptht").rename({"deptht": "z"})
    variables = [x for x in ds.data_vars if ds[x].dims == ("time_counter", "z", "lat", "lon")]
    if "votemper" in ds:
        thickness = e3t.e3t.isel(time_counter=0, deptht=slice(None, None, -1)).values
        ds["e3t"] = (("time_counter", "z", "lat", "lon"), np.broadcast_to(thickness, ds.votemper.shape).copy())
        ds.e3t.attrs.update(standard_name="cell_thickness", long_name="cell thickness", units="m")
        ds["sst"] = ds.votemper.isel(z=-1)
        ds.sst.attrs.update(long_name="sea surface temperature")
        variables.append("e3t")
    shape = [ds.sizes[x] for x in ("time_counter", "z", "lat", "lon")]
    zct = np.broadcast_to(heights[None, :, None, None], shape).copy()
    ds = ds.assign_coords(zct=(("time_counter", "z", "lat", "lon"), zct))
    ds.zct.attrs.update(axis="Z", positive="up", standard_name="height_above_mean_sea_level", units="m")
    for x in variables:
        ds[x].encoding["coordinates"] = "zct"
    return ds


class TestVerticalDirection:
    """Model output whose first level is the bottom is matched as if it were the surface first"""

    @pytest.fixture(autouse=True)
    def one_core(self):
        cores = nc.session.session_info["cores"]
        nc.options(cores=1)
        yield
        nc.options(cores=cores)

    @pytest.mark.parametrize(
        "levels, positive, heights",
        [
            ([0.5, 1.5, 2.5], "down", False),
            ([0.5, 1.5, 2.5], None, False),
            ([-2.5, -1.5, -0.5], "up", True),
            ([-2.5, -1.5, -0.5], None, True),
            ([0.5, 1.5, 2.5], "up", True),
        ],
    )
    def test_heights_are_told_from_depths(self, tmp_path, levels, positive, heights):
        path = _levels_nc(tmp_path / "t.nc", levels, positive)

        assert matchall._levels_are_heights(path, "votemper") is heights

    @pytest.fixture(scope="class")
    def upside_down(self, tmp_path_factory):
        """An upside-down copy of 2000 of the example simulation, with its thickness, and a
        few of the example point observations"""
        import glob
        import pandas as pd
        import xarray as xr

        root = tmp_path_factory.mktemp("upside_down")
        for ff in glob.glob("data/example/2000/*/*.nc"):
            out = root / "sim" / os.path.relpath(ff, "data/example")
            os.makedirs(out.parent, exist_ok=True)
            with xr.open_dataset(ff, decode_times=False) as ds:
                _upside_down(ds.load()).to_netcdf(out)
        with xr.open_dataset("data/example/e3t.nc", decode_times=False) as ds:
            _upside_down(ds.load()).to_netcdf(root / "e3t.nc")
        obs = pd.read_csv("data/evaldata/point/nws/all/temperature/model_temperature.csv")
        os.makedirs(root / "obs")
        obs.query("month == 1").iloc[::500].to_csv(root / "obs" / "obs.csv", index=False)
        return root

    def register(self, obs_path):
        oceanval.reset()
        oceanval.add_point_comparison(
            name="temperature", source="foo", model_variable="votemper",
            obs_path=obs_path, vertical=True, obs_adder=273.15,
        )
        oceanval.add_gridded_comparison(
            name="temperature", source="bar", model_variable="votemper", obs_variable="votemper",
            obs_path="data/evaldata/gridded/nws/temperature", climatology=True,
            start=2000, end=2010, obs_adder=273.15, vertical=True,
        )

    def test_upside_down_output_gives_the_same_matchups(self, upside_down, tmp_path):
        import numpy as np
        import pandas as pd
        import xarray as xr

        outputs = []
        for name, sim_dir, thickness in [
            ("normal", "data/example", "data/example/e3t.nc"),
            ("upside_down", str(upside_down / "sim"), str(upside_down / "e3t.nc")),
        ]:
            self.register(str(upside_down / "obs"))
            matchall.matchup(
                sim_dir=sim_dir, start=2000, end=2000, ask=False, thickness=thickness,
                cores=1, out_dir=str(tmp_path / name),
            )
            matchups = tmp_path / name / "oceanval_matchups"
            point = pd.read_csv(matchups / "point/all/temperature/foo/foo_all_temperature.csv")
            columns = sorted(point.columns)
            point = point.loc[:, columns].sort_values(columns).reset_index(drop=True)
            gridded = xr.open_dataset(matchups / "gridded/temperature/bar_temperature_vertical.nc").load()
            outputs.append((point, gridded))
        oceanval.reset()

        (point, gridded), (point_up, gridded_up) = outputs
        assert len(point) > 0
        pd.testing.assert_frame_equal(point, point_up)
        for variable in ["model", "observation"]:
            assert np.array_equal(gridded[variable].values, gridded_up[variable].values, equal_nan=True)

    @pytest.fixture(scope="class")
    def getm_like(self, upside_down):
        """A GETM-like copy of 2000 of the example simulation, beside the upside-down one"""
        import glob
        import xarray as xr

        root = upside_down / "getm"
        with xr.open_dataset("data/example/e3t.nc", decode_times=False) as e3t:
            e3t = e3t.load()
        for ff in glob.glob("data/example/2000/*/*.nc"):
            out = root / os.path.relpath(ff, "data/example")
            os.makedirs(out.parent, exist_ok=True)
            with xr.open_dataset(ff, decode_times=False) as ds:
                _getm_like(ds.load(), e3t).to_netcdf(out)
        return root

    def test_the_top_cell_is_found_on_a_vertical_axis_without_levels(self, getm_like, tmp_path):
        """CDO's topvalue takes the last level of such an axis, the bottom once inverted"""
        import glob
        import pandas as pd

        path = sorted(glob.glob(str(getm_like / "2000/*/*grid_T.nc")))[0]
        thickness = nc.open_data(path, checks=False)
        thickness.subset(variables="e3t", time=0)
        thickness.invert_levels()
        thickness.run()
        depths = thickness.copy()
        depths.vertical_cumsum()
        thickness / 2
        depths - thickness
        depths.rename({depths.variables[0]: "depth"})
        depths.run()

        locs = pd.DataFrame({"lon": [3.25], "lat": [54.25]})
        top = matchall._top_cell_depths(locs, depths, path, "votemper")
        expected = nc.open_data("data/example/e3t.nc", checks=False)
        expected.subset(variables="e3t")
        expected.cdo_command("sellevidx,1")
        expected = expected.match_points(locs, quiet=True).e3t.values[0] / 2

        assert top.top_depth.values[0] == pytest.approx(expected)

    def test_getm_like_output_gives_the_same_point_matchups(self, getm_like, upside_down, tmp_path):
        import pandas as pd

        outputs = []
        for name, sim_dir, thickness in [
            ("normal", "data/example", "data/example/e3t.nc"),
            ("getm", str(getm_like), "e3t"),
        ]:
            oceanval.reset()
            oceanval.add_point_comparison(
                name="temperature", source="foo", model_variable="votemper",
                obs_path=str(upside_down / "obs"), vertical=True, obs_adder=273.15,
            )
            matchall.matchup(
                sim_dir=sim_dir, start=2000, end=2000, ask=False, thickness=thickness,
                cores=1, out_dir=str(tmp_path / name),
            )
            point = pd.read_csv(tmp_path / name / "oceanval_matchups/point/all/temperature/foo/foo_all_temperature.csv")
            columns = sorted(point.columns)
            outputs.append(point.loc[:, columns].sort_values(columns).reset_index(drop=True))
        oceanval.reset()

        assert len(outputs[0]) > 0
        pd.testing.assert_frame_equal(outputs[0], outputs[1])

    def test_a_surface_field_through_the_water_column_says_so(self, getm_like, upside_down, tmp_path):
        oceanval.reset()
        oceanval.add_point_comparison(
            name="temperature", source="foo", model_variable="sst",
            obs_path=str(upside_down / "obs"), vertical=True, obs_adder=273.15,
        )

        with pytest.raises(ValueError, match=r"sst in .*grid_T.nc has only one vertical level, so temperature \(foo\)"):
            matchall.matchup(
                sim_dir=str(getm_like), start=2000, end=2000, ask=False, thickness="e3t",
                cores=1, out_dir=str(tmp_path / "sst"),
            )
        assert not os.path.exists(tmp_path / "sst" / "oceanval_matchups" / "point")
        oceanval.reset()

    def test_heights_on_z_levels_say_so(self, upside_down, tmp_path):
        self.register(str(upside_down / "obs"))

        with pytest.raises(ValueError, match='votemper .* are heights .* thickness="z_level"'):
            matchall.matchup(
                sim_dir=str(upside_down / "sim"), start=2000, end=2000, ask=False,
                thickness="z_level", cores=1, out_dir=str(tmp_path / "z_level"),
            )
        oceanval.reset()


def _end_stamped(ds):
    """The same daily means as GETM dates them: at the end of each day (00:00
    the next), with no time bounds to say so"""
    ds = ds.drop_vars([x for x in ["time_counter_bnds"] if x in ds])
    attrs = dict(ds.time_counter.attrs)
    attrs.pop("bounds", None)
    # noon, the middle of each day, to 00:00 the next
    ds = ds.assign_coords(time_counter=ds.time_counter.values + 12 * 3600)
    ds.time_counter.attrs.update(attrs)
    for x in ds.data_vars:
        if "time_counter" in ds[x].dims:
            ds[x].attrs["cell_methods"] = "time: mean"
    return ds


class TestTimeShift:
    """Model times that look like the end of the period each mean covers are
    asked about before the matchups, so that they show the dates matched up,
    and shifted back a day if the user says so (see oceanval.time_stamps)"""

    @pytest.fixture(autouse=True)
    def one_core(self):
        cores = nc.session.session_info["cores"]
        nc.options(cores=1)
        yield
        nc.options(cores=cores)

    @pytest.fixture(scope="class")
    def end_stamped(self, tmp_path_factory):
        """2000 of the example simulation dated as GETM dates its means, and
        some of the example point observations, on every month's 1st among
        other days"""
        import pandas as pd
        import xarray as xr

        root = tmp_path_factory.mktemp("end_stamped")
        for ff in glob.glob("data/example/2000/*/*grid_T.nc"):
            out = root / "sim" / os.path.relpath(ff, "data/example")
            os.makedirs(out.parent, exist_ok=True)
            with xr.open_dataset(ff, decode_times=False) as ds:
                _end_stamped(ds.load()).to_netcdf(out)
        obs = pd.read_csv("data/evaldata/point/nws/all/temperature/model_temperature.csv")
        os.makedirs(root / "obs")
        obs.query("depth < 1").iloc[::7].to_csv(root / "obs" / "obs.csv", index=False)
        return root

    @pytest.fixture(scope="class")
    def monthly_end_stamped(self, tmp_path_factory):
        """2000 of the example simulation as monthly means, a file a month,
        each dated at the end of its month, as GETM dates them: December's at
        00:00 on 1 January 2001"""
        import numpy as np
        import xarray as xr

        root = tmp_path_factory.mktemp("monthly_end_stamped")
        for ff in sorted(glob.glob("data/example/2000/*/*grid_T.nc")):
            month = int(os.path.basename(os.path.dirname(ff)))
            out = root / "sim" / os.path.relpath(ff, "data/example").replace("_1d_", "_1m_")
            os.makedirs(out.parent, exist_ok=True)
            with xr.open_dataset(ff, decode_times=False) as ds:
                ds = _end_stamped(ds.load())
                mean = ds.mean("time_counter", keep_attrs=True).expand_dims("time_counter")
                ends = np.datetime64(f"{2000 + month // 12}-{month % 12 + 1:02}-01") - np.datetime64("1900-01-01")
                mean = mean.assign_coords(time_counter=[ends / np.timedelta64(1, "s")])
                mean.time_counter.attrs.update(ds.time_counter.attrs)
                mean.to_netcdf(out)
        return root

    def register(self, end_stamped):
        oceanval.reset()
        oceanval.add_point_comparison(
            name="temperature", source="foo", model_variable="votemper",
            obs_path=str(end_stamped / "obs"), obs_adder=273.15,
        )
        oceanval.add_gridded_comparison(
            name="temperature", source="bar", model_variable="votemper", obs_variable="votemper",
            obs_path="data/evaldata/gridded/nws/temperature", climatology=True,
            start=2000, end=2010, obs_adder=273.15,
        )

    def asked(self, end_stamped, tmp_path, monkeypatch, answers, **kwargs):
        """What matchup asks, answered with answers, and the time_shift it
        goes on with: stopped once it reads the times of the files."""

        class Stop(Exception):
            pass

        shifts = []

        def index_times(paths, fvcom=False, times=None, progress=None):
            shifts.append(session_info["time_shift"])
            raise Stop()

        monkeypatch.setattr(matchall, "index_times", index_times)
        asked = []

        def answerer(question, choices, details):
            asked.append((question, details))
            return answers.pop(0)

        self.register(end_stamped)
        with prompts.answered_by(answerer):
            with pytest.raises(Stop):
                matchall.matchup(
                    sim_dir=str(end_stamped / "sim"), start=2000, end=2000, cores=1,
                    out_dir=str(tmp_path), **kwargs,
                )
        oceanval.reset()
        return asked, shifts[0]

    def test_it_is_asked_before_the_matchups(self, end_stamped, tmp_path, monkeypatch):
        asked, shift = self.asked(end_stamped, tmp_path, monkeypatch, ["y", "y"])

        (shift_question, details), (matchups_question, matchups) = asked
        assert shift_question == "Shift the model's times back one day? (y/n) "
        assert (details["kind"], details["suggested"]) == ("time_shift", -1)
        [row] = details["rows"]
        assert (row["variable"], row["model_variable"]) == ("temperature", "votemper")
        assert (row["first"], row["last"]) == ("2000-01-02 00:00", "2001-01-01 00:00")
        assert "but the last, which is 2000-02-01 00:00" in row["reason"]
        # the matchups then show the dates as shifted
        assert matchups_question == "Are you happy with these matchups? (y/n) "
        assert (matchups["kind"], matchups["time_shift"]) == ("matchups", -1)
        assert shift == -1

    @pytest.mark.parametrize("answer, shift", [("y", -1), ("n", 0)])
    def test_the_answer_is_used(self, end_stamped, tmp_path, monkeypatch, capsys, answer, shift):
        asked, shifted = self.asked(end_stamped, tmp_path, monkeypatch, [answer, "y"])

        assert asked[1][1]["time_shift"] == shifted == shift
        out = capsys.readouterr().out
        assert f"Add time_shift={shift} to your matchup call" in out
        # said before the mapping is shown
        assert out.index("Possible problem with the model's times") < out.index("Inferred mapping")

    def test_without_asking_the_times_are_left_as_they_are(self, end_stamped, tmp_path, monkeypatch, capsys):
        session_info["end_messages"] = []
        asked, shift = self.asked(end_stamped, tmp_path, monkeypatch, [], ask=False)

        assert asked == [] and shift == 0
        out = capsys.readouterr().out
        assert "Possible problem with the model's times" in out
        assert "The times are left as they are" in out
        assert any("time_shift=-1" in x for x in session_info["end_messages"])
        session_info["end_messages"] = []

    @pytest.mark.parametrize("time_shift", [0, -1])
    def test_a_time_shift_given_is_not_asked_about(self, end_stamped, tmp_path, monkeypatch, time_shift):
        asked, shift = self.asked(end_stamped, tmp_path, monkeypatch, ["y"], time_shift=time_shift)

        [(_, details)] = asked
        assert details["kind"] == "matchups" and details["time_shift"] == time_shift
        assert shift == time_shift

    def test_output_with_times_within_each_day_is_not_asked_about(self, tmp_path):
        oceanval.reset()
        oceanval.add_gridded_comparison(
            name="temperature", source="bar", model_variable="votemper", obs_variable="votemper",
            obs_path="data/evaldata/gridded/nws/temperature", climatology=True,
        )
        details = []

        def answerer(question, choices, given):
            details.append(given)
            return "n"

        with prompts.answered_by(answerer):
            matchall.matchup(sim_dir="data/example", start=2000, end=2000, cores=1, out_dir=str(tmp_path))
        oceanval.reset()
        [matchups] = details
        assert (matchups["kind"], matchups["time_shift"]) == ("matchups", 0)

    @pytest.mark.parametrize("time_shift", ["-1", True, -1.0])
    def test_a_time_shift_must_be_whole_days(self, time_shift):
        oceanval.reset()
        with pytest.raises(TypeError, match="time_shift must be a whole number of days"):
            oceanval.matchup(
                sim_dir="data/example", start=2000, end=2000, time_shift=time_shift,
                lon_lim=[-10, 10], lat_lim=[40, 50],
            )

    def test_shifted_back_they_give_the_same_matchups_as_times_within_each_day(self, end_stamped, tmp_path, capsys):
        import pickle

        import numpy as np
        import pandas as pd
        import xarray as xr

        outputs = []
        for name, sim_dir, time_shift in [
            ("within", "data/example", None),
            ("end", str(end_stamped / "sim"), -1),
            ("end_unshifted", str(end_stamped / "sim"), 0),
        ]:
            self.register(end_stamped)
            matchall.matchup(
                sim_dir=sim_dir, start=2000, end=2000, ask=False, cores=1,
                out_dir=str(tmp_path / name), time_shift=time_shift,
            )
            matchups = tmp_path / name / "oceanval_matchups"
            point = pd.read_csv(matchups / "point/surface/temperature/foo/foo_surface_temperature.csv")
            columns = sorted(point.columns)
            point = point.loc[:, columns].sort_values(columns).reset_index(drop=True)
            gridded = xr.open_dataset(matchups / "gridded/temperature/bar_temperature_surface.nc").load()
            with open(matchups / "point/surface/temperature/foo/matchup_dict.pkl", "rb") as f:
                point_shift = pickle.load(f)["time_shift"]
            with open(matchups / "gridded/temperature/bar_matchup_dict.pkl", "rb") as f:
                gridded_shift = pickle.load(f)["time_shift"]
            outputs.append((point, gridded, point_shift, gridded_shift))
        oceanval.reset()

        (point, gridded, *shifts), (point_end, gridded_end, *end_shifts), (point_raw, gridded_raw, _, _) = outputs
        assert shifts == [0, 0] and end_shifts == [-1, -1]
        assert len(point) > 0
        pd.testing.assert_frame_equal(point, point_end)
        for variable in ["model", "observation"]:
            assert np.array_equal(gridded[variable].values, gridded_end[variable].values, equal_nan=True)
        # and not shifted, they do not: the 1st of each month has no model output
        assert len(point_raw) < len(point)
        assert not np.array_equal(gridded.model.values, gridded_raw.model.values, equal_nan=True)

        # made again with overwrite=False only if made with another shift
        capsys.readouterr()
        self.register(end_stamped)
        matchall.matchup(
            sim_dir=str(end_stamped / "sim"), start=2000, end=2000, ask=False, cores=1,
            out_dir=str(tmp_path / "end"), time_shift=0, overwrite=False,
        )
        oceanval.reset()
        out = capsys.readouterr().out
        assert "The temperature (bar) matchup was made with the model's times shifted differently" in out
        assert "The surface temperature (foo) matchup was made with the model's times shifted differently" in out
        with open(tmp_path / "end/oceanval_matchups/gridded/temperature/bar_matchup_dict.pkl", "rb") as f:
            assert pickle.load(f)["time_shift"] == 0

    def test_the_year_last_monthly_mean_dated_in_the_next_year_is_kept(self, monthly_end_stamped, tmp_path):
        """A file holding only a time in the next year, before it is shifted,
        is still matched up"""
        import numpy as np
        import xarray as xr

        outputs = []
        for name, sim_dir, time_shift in [
            ("within", "data/example", None),
            ("end", str(monthly_end_stamped / "sim"), -1),
        ]:
            oceanval.reset()
            oceanval.add_gridded_comparison(
                name="temperature", source="bar", model_variable="votemper", obs_variable="votemper",
                obs_path="data/evaldata/gridded/nws/temperature", climatology=True,
                start=2000, end=2010, obs_adder=273.15,
            )
            matchall.matchup(
                sim_dir=sim_dir, start=2000, end=2000, ask=False, cores=1,
                out_dir=str(tmp_path / name), time_shift=time_shift,
            )
            path = tmp_path / name / "oceanval_matchups/gridded/temperature/bar_temperature_surface.nc"
            outputs.append(xr.open_dataset(path).load())
        oceanval.reset()

        within, end = outputs
        assert len(end.time_counter) == 12
        assert [int(x) for x in end.time_counter.dt.month] == list(range(1, 13))
        assert np.allclose(within.model.values, end.model.values, equal_nan=True, atol=1e-4)
