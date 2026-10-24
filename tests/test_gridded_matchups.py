
import oceanval
import nctoolkit as nc
import pickle

import numpy as np
import pandas as pd
import glob
import os
import shutil
import tempfile

import pytest


class TestFinal:

    def test_gridded(self):

        # the surface climatology of data/evaldata against data/example is
        # matched up and checked in TestMultipleSources
        shutil.rmtree("oceanval_matchups", ignore_errors=True)
        oceanval.reset()

        oceanval.add_gridded_comparison(
            name = "temperature",
            #obs_path="data/evaldata/gridded/nws/temperature",
            obs_path = "data/example_temperature",
            source = "foo",
            model_variable = "votemper",
            obs_variable = "votemper",
            climatology = False,
            start = 2000, 
            end = 2010
        )

        oceanval.matchup(
            sim_dir = "data/example_temperature",
            exclude = "ptrc",
            start = 2000,
            end = 2001,
            ask = False,
            cores = 1)
        
        assert os.path.exists("oceanval_matchups/gridded/temperature/foo_temperature_surface.nc")
        assert os.path.exists("oceanval_matchups/gridded/temperature/foo_temperature_surface_definitions.pkl")
        assert os.path.exists("oceanval_matchups/gridded/temperature/foo_matchup_dict.pkl")
        assert os.path.exists("oceanval_matchups/gridded/temperature/foo_temperature_summary.pkl")
        assert os.path.exists("oceanval_matchups/mapping.csv")
        assert os.path.exists("oceanval_matchups/short_titles.pkl")
        assert os.path.exists("oceanval_matchups/variables_matched.pkl")
        
        ds = nc.open_data("oceanval_matchups/gridded/temperature/foo_temperature_surface.nc")
        assert ds.years == [2000, 2001]
        df = ds.to_dataframe().assign(diff = lambda x: x.model - x.observation)
        # get absolute max difference
        max_diff = np.abs(df['diff']).max()
        assert max_diff < 1e-5 

        ff ="oceanval_matchups/gridded/temperature/foo_matchup_dict.pkl"
        with open(ff, 'rb') as f:
            matchup_dict = pickle.load(f)
            start = matchup_dict['start']
            end = matchup_dict['end']
            assert start == 2000
            assert end == 2001
        
        # read in the definitions
        ff = "oceanval_matchups/gridded/temperature/foo_temperature_surface_definitions.pkl"
        with open(ff, 'rb') as f:
            definitions = pickle.load(f)
            model_variable = definitions["temperature"].model_variable
            comparison = definitions["temperature"].gridded_comparisons["foo"]
            obs_variable = comparison["obs_variable"]
            start = comparison["start"]
            end = comparison["end"]
            obs_path = comparison["obs_path"]
            climatology = comparison["climatology"]
            short_name = definitions["temperature"].short_name
            long_name = definitions["temperature"].long_name
            short_title = definitions["temperature"].short_title
            sources = list(definitions["temperature"].gridded_comparisons)
            point_comparisons = definitions["temperature"].point_comparisons
            n_levels = definitions["temperature"].n_levels
            thredds = comparison["thredds"]

            assert model_variable == "votemper"
            assert obs_variable == "votemper"
            assert start == 2000
            assert end == 2001
            assert obs_path == "data/example_temperature"
            assert climatology is False
            assert short_name == "temperature"
            assert long_name == "temperature"
            assert short_title == "Temperature"
            assert sources == ["foo"]
            assert point_comparisons == {}
            n_levels = 51
            thredds = False

        oceanval.reset()

        oceanval.add_point_comparison(
            name = "temperature",
            obs_path="data/evaldata/point/nws/all/temperature",
            source = "foo",
            model_variable = "votemper",
            vertical = True,
            obs_adder = 273.15
        )
        oceanval.matchup(
            sim_dir = "data/example",
            start = 2000,
            end = 2001,
            ask = False,
            thickness = "data/example/e3t.nc",
            cores = 1)

        ff = "oceanval_matchups/point/all/temperature/foo/foo_all_temperature_definitions.pkl"
        assert os.path.exists(ff)
        ff = "oceanval_matchups/point/all/temperature/foo/matchup_dict.pkl"
        assert os.path.exists(ff)

        ff = "oceanval_matchups/point/all/temperature/foo/foo_all_temperature.csv"
        assert os.path.exists(ff)
        df = pd.read_csv(ff)
        # mean absolute difference between model and observation
        df = df.assign(diff = lambda x: x.model - x.observation)
        mean_abs_diff = np.abs(df['diff']).mean()
        assert mean_abs_diff < 0.1 

        # create the oceanval report directory to make sure validate removes it
        os.makedirs("oceanval_report", exist_ok=True)

        oceanval.validate(test = True)
        ff = "oceanval_report.html"
        assert os.path.exists(ff)
        os.remove(ff)


        text = "This is getting to the end!"
        ff_html = "oceanval_report/_build/html/notebooks/002_foo_temperature.html"
        # check that text appears in ff_html
        with open(ff_html, 'r') as f:
            html_data = f.read()
            assert text in html_data
        ff_html = "oceanval_report/_build/html/notebooks/001_foo_all_temperature.html"

        with open(ff_html, 'r') as f:
            html_data = f.read()
            assert text in html_data

    
        ff = "oceanval_results/annual_mean/annualmean_temperature_foo.nc"
        assert os.path.exists(ff)
        ff = "oceanval_results/annual_mean/annualmean_temperature_foo.pkl"
        assert os.path.exists(ff)
        ff = "oceanval_results/monthly_mean/monthlymean_temperature_foo.nc"
        assert os.path.exists(ff)
        ff = "oceanval_results/monthly_mean/monthlymean_temperature_foo.pkl"
        assert os.path.exists(ff)
        ff = "oceanval_results/temporals/temperature_cor_foo.nc"
        assert os.path.exists(ff)
        ff = "oceanval_results/temporals/temperature_cor_foo.pkl"
        assert os.path.exists(ff)

        # a very basic test of compare, just to make sure it runs

        paths = glob.glob("oceanval_report")
        for p in paths:
            if "oceanval_report" in p:
                # check if it's a directory
                if os.path.isdir(p) is False:
                    os.remove(p)
        shutil.rmtree("oceanval_report", ignore_errors=True)

        paths = glob.glob("oceanval_matchups/**/**/**")
        nc_paths = [x for x in paths if ".nc" in x]

        for p in paths:
            if "oceanval_matchups" in p:
                # check if it's a file
                if os.path.isfile(p):
                    os.remove(p)
        paths = glob.glob("oceanval_matchups/**/**/**")
        for p in paths:
            if "oceanval_matchups" in p:
                # check if it's a file
                if os.path.isfile(p):
                    os.remove(p)
        


        paths = glob.glob("oceanval_matchups/**/**/**")
        for p in paths:
            if "oceanval_matchups" in p:
                # check if it's a file
                if os.path.isfile(p) is False:
                    shutil.rmtree(p, ignore_errors=True) 
        shutil.rmtree("oceanval_matchups/gridded/temperature", ignore_errors=True)
        shutil.rmtree("oceanval_matchups/gridded", ignore_errors=True)
        shutil.rmtree("oceanval_matchups", ignore_errors=True)

        paths = glob.glob("oceanval_results/**/**/**")
        for p in paths:
            if "oceanval_results" in p:
                # check if it's a file
                if os.path.isfile(p):
                    os.remove(p)
        shutil.rmtree("oceanval_results", ignore_errors=True)


        oceanval.reset()
        # add gridded comparison again to make sure reset worked
        oceanval.add_gridded_comparison(
            name = "temperature",
            obs_path="data/evaldata/gridded/nws/temperature",
            source = "foo",
            model_variable = "votemper",
            obs_variable = "votemper",
            climatology = True,
            start = 2000,
            end = 2010,
            vertical = True
        )

        oceanval.matchup(
            sim_dir = "data/example",
            start = 2000,
            end = 2000,
            thickness = "z_level",
            ask = False,
            cores = 1)
        ff = "oceanval_matchups/gridded/temperature/foo_temperature_vertical.nc"
        assert os.path.exists(ff)
        ds = nc.open_data(ff)
        df = ds.to_dataframe().assign(diff = lambda x: x.model - x.observation)
        # mean absolute difference between model and observation
        df = df.assign(diff = lambda x: x.model - x.observation)
        # max absolute difference
        assert np.abs(df['diff']).max() < 1e-5 




class TestMultipleSources:

    def test_each_source_is_matched_up(self):
        shutil.rmtree("oceanval_matchups", ignore_errors=True)
        oceanval.reset()

        # the same observations under two sources, one offset by a degree, so
        # the matchups can be told apart
        for source, adder in [("foo", 273.15), ("bar", 274.15)]:
            oceanval.add_gridded_comparison(
                name = "temperature",
                obs_path="data/evaldata/gridded/nws/temperature",
                source = source,
                model_variable = "votemper",
                obs_variable = "votemper",
                climatology = True,
                start = 2000,
                end = 2010,
                obs_adder = adder
            )
        for source, vertical in [("foo", True), ("bar", False)]:
            oceanval.add_point_comparison(
                name = "temperature",
                obs_path="data/evaldata/point/nws/all/temperature",
                source = source,
                model_variable = "votemper",
                vertical = vertical,
                obs_adder = 273.15
            )

        oceanval.matchup(
            sim_dir = "data/example",
            start = 2000,
            end = 2000,
            ask = False,
            thickness = "data/example/e3t.nc",
            cores = 1)

        gridded_dir = "oceanval_matchups/gridded/temperature/"
        for source in ["foo", "bar"]:
            assert os.path.exists(gridded_dir + f"{source}_temperature_surface.nc")
            assert os.path.exists(gridded_dir + f"{source}_temperature_surface_definitions.pkl")
            assert os.path.exists(gridded_dir + f"{source}_matchup_dict.pkl")
            assert os.path.exists(gridded_dir + f"{source}_temperature_summary.pkl")

        # each file holds its own source's observations
        for source, expected in [("foo", 0), ("bar", -1)]:
            ds = nc.open_data(gridded_dir + f"{source}_temperature_surface.nc")
            df = ds.to_dataframe().assign(diff = lambda x: x.model - x.observation)
            assert np.abs(df["diff"] - expected).max() < 1e-4

        assert os.path.exists("oceanval_matchups/point/all/temperature/foo/foo_all_temperature.csv")
        assert os.path.exists("oceanval_matchups/point/surface/temperature/bar/bar_surface_temperature.csv")

        # each is a matchup validate() can make a page for, or leave out
        assert [(m["kind"], m["key"], m["layer"]) for m in oceanval.selection.on_disk(".")] == [
            ("gridded", "temperature/bar", None),
            ("gridded", "temperature/foo", None),
            ("point", "temperature/bar", "surface"),
            ("point", "temperature/foo", "all"),
        ]
        # all of them made by this run, for validate(previous=False)
        assert oceanval.selection.last_run(".") == {
            ("gridded", "temperature", "bar", None),
            ("gridded", "temperature", "foo", None),
            ("point", "temperature", "bar", "surface"),
            ("point", "temperature", "foo", "all"),
        }

        class Built(Exception):
            pass

        def stop(*args, **kwargs):
            raise Built

        out_dir = tempfile.mkdtemp()
        try:
            with pytest.MonkeyPatch.context() as monkeypatch:
                # the notebooks are all written by then
                monkeypatch.setattr(oceanval, "_build_book", stop)
                with pytest.raises(Built):
                    oceanval.validate(out_dir=out_dir, variables=["temperature/foo"], test=True)
            notebooks = sorted(
                os.path.basename(x)
                for x in glob.glob(f"{out_dir}/oceanval_report/notebooks/*.ipynb")
            )
            # numbered from 1, as validate() numbers them
            assert {x.split("_", 1)[1] for x in notebooks if "temperature" in x} == {
                "foo_all_temperature.ipynb", "foo_temperature.ipynb",
            }
            assert {x.split("_", 1)[0] for x in notebooks if "temperature" in x} == {"001", "002"}
            with open(f"{out_dir}/oceanval_report/_toc.yml") as toc:
                assert "bar" not in toc.read()
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

        shutil.rmtree("oceanval_matchups", ignore_errors=True)
        oceanval.reset()

    def test_a_later_run_into_the_same_directory(self):
        """matchup's overwrite picks the matchups there already that are made
        again, and validate's previous whether those of the earlier run are
        in the report."""
        shutil.rmtree("oceanval_matchups", ignore_errors=True)

        def run(sources, overwrite):
            oceanval.reset()
            for source in sources:
                oceanval.add_gridded_comparison(
                    name = "temperature",
                    obs_path="data/evaldata/gridded/nws/temperature",
                    source = source,
                    model_variable = "votemper",
                    obs_variable = "votemper",
                    climatology = True,
                    start = 2000,
                    end = 2010,
                    obs_adder = 273.15
                )
            oceanval.matchup(
                sim_dir = "data/example", start = 2000, end = 2000, ask = False, cores = 1,
                overwrite = overwrite)

        def made(source):
            return os.path.getmtime(f"oceanval_matchups/gridded/temperature/{source}_temperature_surface.nc")

        class Built(Exception):
            pass

        def stop(*args, **kwargs):
            raise Built

        def notebooks(**kwargs):
            out_dir = tempfile.mkdtemp()
            try:
                with pytest.MonkeyPatch.context() as monkeypatch:
                    # the notebooks are all written by then
                    monkeypatch.setattr(oceanval, "_build_book", stop)
                    with pytest.raises(Built):
                        oceanval.validate(out_dir=out_dir, test=True, **kwargs)
                return sorted(
                    os.path.basename(x).split("_", 1)[1]
                    for x in glob.glob(f"{out_dir}/oceanval_report/notebooks/*temperature.ipynb")
                )
            finally:
                shutil.rmtree(out_dir, ignore_errors=True)

        try:
            run(["foo", "bar"], True)
            before = made("bar")
            # foo is not matched up again, so overwriting it does nothing, and bar is kept
            run(["bar"], ["temperature/foo"])
            assert made("bar") == before
            assert oceanval.selection.last_run(".") == {("gridded", "temperature", "bar", None)}
            assert notebooks() == ["bar_temperature.ipynb", "foo_temperature.ipynb"]
            assert notebooks(previous=False) == ["bar_temperature.ipynb"]
            assert notebooks(previous=["temperature/foo"]) == ["bar_temperature.ipynb", "foo_temperature.ipynb"]
            # and bar is made again once it is picked
            run(["bar"], ["temperature/bar"])
            assert made("bar") > before
        finally:
            shutil.rmtree("oceanval_matchups", ignore_errors=True)
            oceanval.reset()


class TestSurfaceOfMultiLevelObservations:
    """A surface-only comparison with multi-level observations takes their top
    level with top() for the WOA23 recipes, and with topvalue for any other."""

    @staticmethod
    def monthly_obs(folder):
        """Twelve monthly files of multi-level temperature, standing in for
        WOA23's, made from the example model's own output."""
        os.makedirs(folder)
        files = []
        for month in range(1, 13):
            source = f"data/example/2000/{month:02d}/amm7_1d_2000{month:02d}01_2000{month:02d}"
            source = glob.glob(source + "*_grid_T.nc")[0]
            out = os.path.join(folder, f"obs_{month:02d}.nc")
            ds = nc.open_data(source, checks=False)
            ds.subset(variables="votemper")
            ds.tmean()
            ds.to_nc(out, zip=False, overwrite=True)
            files.append(out)
        return files

    @staticmethod
    def spy_on_obs(monkeypatch, obs_files):
        """Which of top and topvalue are asked of the observations, before any
        of them has been run, so that they are still the files given."""
        asked = []
        names = {os.path.basename(x) for x in obs_files}
        top, cdo_command = nc.DataSet.top, nc.DataSet.cdo_command

        def on_obs(ds):
            try:
                return os.path.basename(str(ds[0])) in names
            except Exception:
                return False

        def spy_top(self):
            if on_obs(self):
                asked.append("top")
            return top(self)

        def spy_cdo_command(self, command=None, *args, **kwargs):
            if command == "topvalue" and on_obs(self):
                asked.append("topvalue")
            return cdo_command(self, command, *args, **kwargs)

        monkeypatch.setattr(nc.DataSet, "top", spy_top)
        monkeypatch.setattr(nc.DataSet, "cdo_command", spy_cdo_command)
        return asked

    @staticmethod
    def matchup():
        oceanval.matchup(
            sim_dir = "data/example",
            start = 2000,
            end = 2000,
            ask = False,
            cores = 1)

    def test_woa23_takes_the_top_level(self, tmp_path, monkeypatch):
        shutil.rmtree("oceanval_matchups", ignore_errors=True)
        oceanval.reset()
        files = self.monthly_obs(str(tmp_path / "woa23"))
        asked = self.spy_on_obs(monkeypatch, files)

        oceanval.add_gridded_comparison(
            name = "temperature",
            model_variable = "votemper",
            recipe = {"temperature": "woa23"},
            obs_variable = "votemper",
            start = 2000,
            end = 2000,
            file_check = False,
        )
        # the twelve OPeNDAP urls are served by the local files
        local = dict(zip(
            oceanval.definitions["temperature"].gridded_comparisons["WOA23"]["obs_path"], files))

        def open_thredds(path, **kwargs):
            paths = [local.get(x, x) for x in ([path] if isinstance(path, str) else path)]
            return nc.open_data(paths if len(paths) > 1 else paths[0], checks = False)

        monkeypatch.setattr(nc, "open_thredds", open_thredds)
        self.matchup()

        assert "top" in asked
        assert "topvalue" not in asked
        assert os.path.exists("oceanval_matchups/gridded/temperature/WOA23_temperature_surface.nc")
        shutil.rmtree("oceanval_matchups", ignore_errors=True)
        oceanval.reset()

    def test_other_sources_take_the_topvalue(self, tmp_path, monkeypatch):
        shutil.rmtree("oceanval_matchups", ignore_errors=True)
        oceanval.reset()
        files = self.monthly_obs(str(tmp_path / "foo"))
        asked = self.spy_on_obs(monkeypatch, files)

        oceanval.add_gridded_comparison(
            name = "temperature",
            obs_path = str(tmp_path / "foo"),
            source = "foo",
            model_variable = "votemper",
            obs_variable = "votemper",
            climatology = True,
            start = 2000,
            end = 2000,
        )
        self.matchup()

        assert "topvalue" in asked
        assert "top" not in asked
        assert os.path.exists("oceanval_matchups/gridded/temperature/foo_temperature_surface.nc")
        shutil.rmtree("oceanval_matchups", ignore_errors=True)
        oceanval.reset()


class TestCoresAfterThredds:
    """nctoolkit's open_thredds sets its cores to 1 for the rest of the
    session, so each variable's model files must be read with the cores asked
    for, not those the previous variable's observations left."""

    def test_every_variable_reads_the_model_with_the_cores(self, tmp_path, monkeypatch):
        shutil.rmtree("oceanval_matchups", ignore_errors=True)
        oceanval.reset()
        files = TestSurfaceOfMultiLevelObservations.monthly_obs(str(tmp_path / "obs"))

        for source in ["foo", "bar"]:
            oceanval.add_gridded_comparison(
                name = "temperature",
                obs_path = files,
                source = source,
                model_variable = "votemper",
                obs_variable = "votemper",
                climatology = True,
                start = 2000,
                end = 2000,
                thredds = True,
                file_check = False,
            )

        def open_thredds(path, **kwargs):
            nc.options(cores = 1)
            return nc.open_data(path, checks = False)

        read_with = []
        model_dataset = oceanval.gridded.model_dataset

        def spy_model_dataset(*args, **kwargs):
            read_with.append(nc.session_info["cores"])
            # with several cores the model files are read in pools, which hang when
            # collected with nctoolkit 1.3.6 and before: only the cores are looked at
            nc.options(cores = 1)
            return model_dataset(*args, **kwargs)

        monkeypatch.setattr(nc, "open_thredds", open_thredds)
        monkeypatch.setattr(oceanval.gridded, "model_dataset", spy_model_dataset)
        oceanval.matchup(
            sim_dir = "data/example",
            start = 2000,
            end = 2000,
            ask = False,
            cores = 2)

        assert read_with == [2, 2]
        shutil.rmtree("oceanval_matchups", ignore_errors=True)
        oceanval.reset()
