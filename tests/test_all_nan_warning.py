import subprocess
import sys


def test_all_nan_warning_is_silenced():
    code = (
        "import numpy as np\n"
        "import oceanval\n"
        "np.nanmax(np.full(3, np.nan)); np.nanmedian(np.full((2, 2), np.nan), axis=0)\n"
        "print('ok')\n"
    )
    r = subprocess.run([sys.executable, "-W", "error::RuntimeWarning", "-c", code], capture_output=True, text=True)
    assert "ok" in r.stdout, r.stderr
