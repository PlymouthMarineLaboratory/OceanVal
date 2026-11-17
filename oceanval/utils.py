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





