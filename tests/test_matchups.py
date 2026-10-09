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
        )
        asked = []

        def answerer(question, choices, details):
            asked.append(details)
            return "n"

        with prompts.answered_by(answerer):
            oceanval.matchup(
                sim_dir="data/example", start=2004, end=2004, cores=1, out_dir=str(tmp_path),
                thickness="data/example/e3t.nc",
            )

        [details] = asked
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
                "files": len(files),
                # one time a day in each file
                "time_res": "1d",
            }
        ]
        assert details["files"] == {pattern: files}
        # daily output, and no point datasets, is nothing to check
        assert "point_time_res" not in details
        # no is not a matchup
        assert not os.path.exists(tmp_path / "oceanval_matchups" / "mapping.csv")

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
