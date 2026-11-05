"""The lock every read of a netCDF file in OceanVal's own process holds.

netCDF-C and HDF5 are not thread-safe, and xarray gives each file it opens for
reading a lock of its own, so two threads opening, reading or closing different
files at once can crash the process (a segmentation fault). The window reads
files in threads of its own (each simulation's variables and times, the model's
missing value, the grids), so each of those reads holds LOCK. CDO runs in
processes of its own, and needs none.
"""

import threading

LOCK = threading.Lock()


_hdf5_libraries = None


def _loaded_hdf5():
    """The libhdf5 netCDF reads files with, as ctypes libraries: the ones loaded in
    this process (not h5py's own copy in a pip wheel, a different library with
    error settings of its own), else the one the system finds."""
    import ctypes
    import ctypes.util
    import os

    paths = []
    try:
        with open("/proc/self/maps") as f:
            for line in f:
                path = line.split()[-1] if line.strip() else ""
                name = os.path.basename(path)
                # libhdf5.so.320.2.0, not libhdf5_hl (its high-level library)
                if name.startswith("libhdf5.") and path not in paths:
                    paths.append(path)
    except OSError:
        found = ctypes.util.find_library("hdf5")
        paths = [found] if found else []
    libraries = []
    for path in paths:
        try:
            libraries.append(ctypes.CDLL(path))
        except OSError:
            continue
    return libraries


def silence_hdf5():
    """Stop HDF5 printing its error stack to the terminal from the calling thread.

    HDF5 keeps its error-printing setting for each thread, and netCDF turns it off
    only for the thread that first loaded it, so a thread of the window that makes
    a file (nctoolkit's temporary files, the unified grid's mask) can make HDF5 print
    stacks of "unable to open file ... No such file or directory" for a file it was
    about to create. The errors are not ours to show: a real failure still raises a
    Python exception. Never raises (no netCDF4, or no libhdf5 found, does nothing)."""
    global _hdf5_libraries
    try:
        if _hdf5_libraries is None:
            import netCDF4  # noqa: F401 - loads the libhdf5 whose setting this changes

            _hdf5_libraries = _loaded_hdf5()
        for library in _hdf5_libraries:
            # H5Eset_auto2(H5E_DEFAULT, NULL, NULL): no function to print the stack when an error is raised
            library.H5Eset_auto2(0, None, None)
    except Exception:  # noqa: BLE001 - a quiet terminal is not worth a failure
        pass


_threads_quietened = False


def silence_new_threads():
    """Make every thread started from now on call silence_hdf5() first (the window's
    server threads and its readers, so no call site has to), once. libhdf5 is found
    here, by the thread that calls this, not by each new thread."""
    global _threads_quietened
    if _threads_quietened:
        return
    _threads_quietened = True
    silence_hdf5()
    run = threading.Thread.run

    def quiet_run(self):
        silence_hdf5()
        run(self)

    threading.Thread.run = quiet_run
