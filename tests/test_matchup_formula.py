
import oceanval
import nctoolkit as nc
import pickle

import numpy as np
import pandas as pd
import glob
import os
import shutil

import pytest


class TestFinal:

    def test_gridded(self):

        oceanval.definitions.reset()


        oceanval.add_point_comparison(
            name = "chlorophyll",
            obs_path="data/chl/obs_csv",
            source = "foo",
            model_variable = "P1_Chl+P2_Chl+P3_Chl+P4_Chl",
            start = 2000, 
            end = 2000
        )

        oceanval.matchup(
            "data/chl/simulation",
            start = 2000, 
            end = 2000, 
            n_dirs_down = 0,
            ask = False,
            cache = True,
            cores = 1,
            require = "model", 
            exclude = "bad"
        )

        ff = "oceanval_matchups/point/surface/chlorophyll/foo/foo_surface_chlorophyll.csv"
        df = pd.read_csv(ff)
        diff = np.abs(df['model'] - df['observation'])
        max_diff = diff.max()
        assert max_diff < 1e-6

        for ff in glob.glob(".cache_oceanval/output/*.pkl"):
            with open(ff, "rb") as f:
                df = pickle.load(f)   

                columns = ["lon", "lat", "year", "month", "day", "total"]
                for col in columns:
                    assert col in df.columns
                assert len(df) == 248


        oceanval.add_gridded_comparison(
            name = "chlorophyll",
            obs_path="data/chl/model_as_obs",
            source = "foo",
            model_variable = "P1_Chl+P2_Chl+P3_Chl+P4_Chl",
            obs_variable = "total",
            climatology = False,
            start = 2000, 
            end = 2000
        )

        oceanval.matchup(
            "data/chl/simulation",
            start = 2000, 
            end = 2000, 
            n_dirs_down = 0,
            ask = False,
            cache = True,
            cores = 1,
            require = "model", 
            exclude = "bad"
        )

        ff = "oceanval_matchups/gridded/chlorophyll/foo_chlorophyll_surface.nc"
        assert os.path.exists(ff)

        ds = nc.open_data(ff)
        df = ds.to_dataframe()
        # max diff
        diff = np.abs(df['model'] - df['observation'])
        max_diff = diff.max()
        assert max_diff < 1e-6

        ff = "oceanval_matchups/point/surface/chlorophyll/foo/foo_surface_chlorophyll.csv"
        df = pd.read_csv(ff)
        diff = np.abs(df['model'] - df['observation'])
        max_diff = diff.max()
        assert max_diff < 1e-6

        for ff in glob.glob(".cache_oceanval/output/*.pkl"):
            with open(ff, "rb") as f:
                df = pickle.load(f)   

                columns = ["lon", "lat", "year", "month", "day", "total"]
                for col in columns:
                    assert col in df.columns
                assert len(df) == 248


class TestAFormula:
    """A model variable given as a formula in CDO's expr (see
    oceanval.model_expr), not only a sum: worked out where the sum would be, at
    each time step, for point and gridded matchups. The point matchups of a
    formula are cached for that formula only."""

    SUM = "P1_Chl+P2_Chl+P3_Chl+P4_Chl"

    def run(self, model_variable, gridded=True):
        oceanval.definitions.reset()
        oceanval.add_point_comparison(
            name="chlorophyll", obs_path="data/chl/obs_csv", source="foo",
            model_variable=model_variable, start=2000, end=2000,
        )
        if gridded:
            oceanval.add_gridded_comparison(
                name="chlorophyll", obs_path="data/chl/model_as_obs", source="foo",
                model_variable=model_variable, obs_variable="total", climatology=False, start=2000, end=2000,
            )
        oceanval.matchup(
            "data/chl/simulation", start=2000, end=2000, n_dirs_down=0, ask=False, cache=True, cores=1,
            require="model", exclude="bad", overwrite=True,
        )
        point = pd.read_csv("oceanval_matchups/point/surface/chlorophyll/foo/foo_surface_chlorophyll.csv")
        if not gridded:
            return point, None
        grid = nc.open_data("oceanval_matchups/gridded/chlorophyll/foo_chlorophyll_surface.nc").to_dataframe()
        return point, grid

    def test_a_formula_is_worked_out_and_cached_for_itself(self):
        for folder in ("oceanval_matchups", ".cache_oceanval"):
            shutil.rmtree(folder, ignore_errors=True)
        # spaces and brackets as a user would type them; the definitions keep it without spaces
        point, grid = self.run(" 2 * (P1_Chl + P2_Chl + P3_Chl + P4_Chl) / 2 ")
        assert oceanval.definitions.chlorophyll.model_variable == f"2*({self.SUM})/2"
        assert len(point) > 0 and np.abs(point["model"] - point["observation"]).max() < 1e-6
        assert len(grid.dropna()) > 0 and np.abs(grid["model"] - grid["observation"]).max() < 1e-6

        # another formula of the same variables is matched up again, not taken from the cache
        point, _ = self.run(f"2*({self.SUM})", gridded=False)
        assert len(point) > 0 and np.abs(point["model"] - 2 * point["observation"]).max() < 1e-5

    def test_a_formula_cdo_could_not_work_out_is_refused_as_it_is_given(self):
        oceanval.definitions.reset()
        with pytest.raises(ValueError, match="A \\* needs something on each side"):
            oceanval.add_point_comparison(
                name="chlorophyll", obs_path="data/chl/obs_csv", source="foo",
                model_variable="P1_Chl+2*", start=2000, end=2000,
            )
        with pytest.raises(ValueError, match="works out a statistic over the grid"):
            oceanval.add_gridded_comparison(
                name="chlorophyll", obs_path="data/chl/model_as_obs", source="foo",
                model_variable="fldmean(P1_Chl)", obs_variable="total", climatology=False, start=2000, end=2000,
            )
        oceanval.definitions.reset()
