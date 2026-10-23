"""restrict_r_to_conda (oceanval.utils), which points rpy2/%%R at the conda env's R
in the process importing OceanVal and in each report kernel."""

import os
import sys

import pytest

from oceanval.utils import restrict_r_to_conda


@pytest.fixture
def conda_env(tmp_path, monkeypatch):
    os.makedirs(tmp_path / "lib" / "R")
    monkeypatch.setenv("CONDA_PREFIX", str(tmp_path))
    for var in ["DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH", "R_HOME", "PATH",
                "R_LIBS_USER", "R_LIBS_SITE", "R_LIBS", "R_ENVIRON_USER", "R_PROFILE_USER"]:
        # recorded by monkeypatch, so each is put back as it was (or removed) after the test
        monkeypatch.setenv(var, os.environ.get(var, ""))
    monkeypatch.delenv("DYLD_LIBRARY_PATH")
    monkeypatch.delenv("LD_LIBRARY_PATH")
    return tmp_path


def test_conda_lib_is_never_on_dyld_library_path(conda_env, monkeypatch):
    # dyld swaps libraries by file name, so ImageIO's libPng.dylib became conda's
    # libpng and every %%R figure on macOS could kill the report's kernel
    monkeypatch.setattr(sys, "platform", "darwin")
    restrict_r_to_conda()
    assert "DYLD_LIBRARY_PATH" not in os.environ
    assert "LD_LIBRARY_PATH" not in os.environ
    assert os.environ["R_HOME"] == str(conda_env / "lib" / "R")


def test_conda_lib_comes_first_on_ld_library_path(conda_env, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/somewhere/else")
    restrict_r_to_conda()
    assert os.environ["LD_LIBRARY_PATH"].split(os.pathsep) == [
        str(conda_env / "lib"), "/somewhere/else"
    ]
    assert "DYLD_LIBRARY_PATH" not in os.environ
    assert os.environ["R_HOME"] == str(conda_env / "lib" / "R")
