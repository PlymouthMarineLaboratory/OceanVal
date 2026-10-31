import importlib
import pathlib
import re
import subprocess
import sys

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "oceanval"


def _run(tmp_path, fake):
    (tmp_path / "nctoolkit.py").write_text(fake)
    return subprocess.run(
        [sys.executable, "-c",
         "import importlib.util as u, sys;"
         f"s = u.spec_from_file_location('quiet_nc', r'{PACKAGE / 'quiet_nc.py'}');"
         "m = u.module_from_spec(s); s.loader.exec_module(m); print('ok', m.nc.__name__)"],
        capture_output=True, text=True, cwd=tmp_path,
        env={"PYTHONPATH": str(tmp_path), "PATH": ""},
    )


def test_warnings_and_prints_are_silenced(tmp_path):
    result = _run(tmp_path, "import warnings, sys\nwarnings.warn('noisy')\nprint('hello')\nprint('err', file=sys.stderr)\n")
    assert result.stdout.strip() == "ok nctoolkit"
    assert result.stderr == ""


def test_errors_still_raise_with_the_output(tmp_path):
    result = _run(tmp_path, "print('before')\nraise ImportError('broken')\n")
    assert result.returncode != 0
    assert "ImportError: broken" in result.stderr
    assert "before" in result.stdout


def test_modules_import_nctoolkit_quietly():
    direct = re.compile(r"^\s*(import nctoolkit|from nctoolkit)", re.M)
    allowed = {"quiet_nc.py", "analysis_series.py"}  # the latter takes `session` from the loaded module
    for path in PACKAGE.rglob("*.py"):
        if path.name in allowed:
            continue
        assert not direct.search(path.read_text()), f"{path.name} imports nctoolkit directly; use oceanval.quiet_nc"
