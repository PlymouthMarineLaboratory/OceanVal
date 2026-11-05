"""HDF5 prints its error stack to the terminal from a thread of the window for a file
netCDF only probes (a file about to be made); netcdf_lock.silence_hdf5 stops that."""

import os
import subprocess
import sys
import textwrap

import oceanval

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(oceanval.__file__)))

# probes a file that is not there, as netCDF does before it makes one, from a thread
PROBE = """
import sys, threading
sys.path.insert(0, {root!r})
import netCDF4  # loads libhdf5
from oceanval import netcdf_lock
{setup}
def work():
    for library in netcdf_lock._loaded_hdf5():
        library.H5Fis_accessible(b"/nonexistent_oceanval_probe.nc", 0)
thread = threading.Thread(target=work)
thread.start()
thread.join()
"""


def _stderr(setup):
    code = textwrap.dedent(PROBE).format(root=ROOT, setup=setup)
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return done.stderr


def test_a_thread_prints_the_error_stack_unless_it_is_silenced():
    # the control: the build prints, or there is nothing to silence
    if "HDF5-DIAG" not in _stderr(""):
        import pytest

        pytest.skip("this libhdf5 does not print its errors from a thread")
    assert "HDF5-DIAG" not in _stderr("netcdf_lock.silence_new_threads()")


def test_silence_hdf5_in_the_thread_itself():
    code = textwrap.dedent(PROBE).format(root=ROOT, setup="")
    code = code.replace("def work():", "def work():\n    netcdf_lock.silence_hdf5()")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "HDF5-DIAG" not in done.stderr


def test_silencing_never_raises_without_a_library(monkeypatch):
    from oceanval import netcdf_lock

    monkeypatch.setattr(netcdf_lock, "_hdf5_libraries", None)
    monkeypatch.setattr(netcdf_lock, "_loaded_hdf5", lambda: 1 / 0)
    netcdf_lock.silence_hdf5()
