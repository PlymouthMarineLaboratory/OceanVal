"""Import nctoolkit without its warnings or printed output.

Every module imports it as ``from oceanval.quiet_nc import nc``. An error in the
import is never hidden: whatever was printed is replayed and the error raised.
"""

import contextlib
import io
import sys
import warnings

_out = io.StringIO()
_err = io.StringIO()

try:
    with warnings.catch_warnings(), contextlib.redirect_stdout(_out), contextlib.redirect_stderr(_err):
        warnings.simplefilter("ignore")
        import nctoolkit as nc
except BaseException:
    sys.stdout.write(_out.getvalue())
    sys.stderr.write(_err.getvalue())
    raise
