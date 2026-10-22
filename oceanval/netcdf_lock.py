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
