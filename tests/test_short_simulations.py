import oceanval 
import os
import pytest
import tempfile
import shutil


class TestMatchup:
    """Test suite for matchup function"""
    
    def test_a_short_one(self):
        """Test that a simulation with less than 1 Year of data works"""
        oceanval.reset()
        oceanval.add_gridded_comparison(
            name = "temperature",
            obs_path = "data/evaldata/gridded/nws/temperature/model_2000.nc",
            model_variable = "votemper",
            obs_variable = "votemper",
            source = "foo", 
            climatology = True
            )
        oceanval.matchup("data/monthly",
            ask = False,
            # one core, as nctoolkit's pools for more can hang
            cores = 1,
            start = 2000, end = 2000)

        import nctoolkit as nc
        ds = nc.open_data("oceanval_matchups/gridded/temperature/foo_temperature_surface.nc")
        assert ds.months == [1,3,5, 7, 9, 11, 12]
        df = ds.to_dataframe()
        # max absolute diff between model and obs should be < 0.01
        assert abs(df["model"] - df["observation"]).max() < 0.01
