from oceanval.quiet_nc import nc
import xarray as xr
import numpy as np
import subprocess
import os
import sys
import warnings
from oceanval.session import session_info


def loud_warning(headline, lines, warning=None):
    """Print a banner that cannot be lost in the scroll of a long run.

    Used where something has been skipped rather than raising, so the user
    still finds out about it at the point it happened as well as in the
    summary at the end.
    """
    print("")
    print("!" * 78)
    print(f"!!!  {headline}")
    print("!!!")
    for line in lines:
        print(f"!!!  {line}".rstrip())
    print("!" * 78)
    print("")

    if warning is not None:
        warnings.warn(warning)


def bin_value(x, bin_res):
    return np.floor((x + bin_res / 2) / bin_res + 0.5) * bin_res - bin_res / 2


def extension_of_directory(starting_directory, exclude=[]):
    levels = session_info["levels_down"]

    new_directory = ""
    for i in range(levels):
        new_directory = new_directory + "/**"
    return new_directory + "/"


def _prepend_path(var, value):
    existing = [p for p in os.environ.get(var, "").split(os.pathsep) if p]
    if value not in existing:
        os.environ[var] = os.pathsep.join([value] + existing)


def restrict_r_to_conda():
    # pins rpy2/%%R to the active conda env's R, so it can't fall back to a system R install
    conda_prefix = os.environ.get("CONDA_PREFIX", sys.prefix)
    r_home = os.path.join(conda_prefix, "lib", "R")
    if not os.path.isdir(r_home):
        return
    os.environ["R_HOME"] = r_home
    _prepend_path("PATH", os.path.join(conda_prefix, "bin"))
    # not DYLD_LIBRARY_PATH on macOS: dyld swaps libraries by file name, system ones too, so
    # ImageIO's own libPng.dylib became conda's libpng (the file system ignores case) and R's
    # quartz png device crashed the kernel; conda's macOS libraries find each other by @rpath
    if sys.platform != "darwin":
        _prepend_path("LD_LIBRARY_PATH", os.path.join(conda_prefix, "lib"))

    # R still falls back to a personal package library (e.g. ~/R/...) unless
    # told otherwise, which can hold packages built against a different R ABI
    # and crash with "undefined symbol" errors. Force it to only use the
    # library bundled with the conda R install.
    os.environ["R_LIBS_USER"] = os.path.join(r_home, "library")
    os.environ["R_LIBS_SITE"] = os.path.join(r_home, "library")
    os.environ["R_LIBS"] = ""
    # don't let a personal ~/.Renviron or ~/.Rprofile override the above
    os.environ["R_ENVIRON_USER"] = ""
    os.environ["R_PROFILE_USER"] = ""



