import gc
import multiprocessing
import multiprocessing.resource_tracker as resource_tracker
import os
import sys

import pytest

from oceanval import semaphore_trace

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no named semaphores")


@pytest.fixture
def trace(tmp_path):
    path = tmp_path / "semaphores.log"
    original = resource_tracker.register
    assert semaphore_trace.install(str(path))
    yield path
    semaphore_trace.uninstall()
    assert resource_tracker.register is original


def _make_lock_and_leak():
    lock = multiprocessing.get_context("spawn").Lock()  # noqa: F841 - never released
    os._exit(0)


def test_a_semaphore_left_by_a_killed_child_is_reported(trace):
    lock = multiprocessing.get_context("spawn").Lock()
    del lock
    gc.collect()

    pid = os.fork()
    if pid == 0:
        _make_lock_and_leak()
    os.waitpid(pid, 0)

    leaked = semaphore_trace.leaked(trace)
    assert [entry["pid"] for entry in leaked] == [pid]
    assert leaked[0]["rtype"] == "semaphore"
    assert any("_make_lock_and_leak" in frame for frame in leaked[0]["stack"])
    text = semaphore_trace.report(trace)
    assert "1 registered and never unregistered" in text
    assert "_make_lock_and_leak" in text
    # removed here, rather than by the tracker with a warning
    import _multiprocessing

    _multiprocessing.sem_unlink(leaked[0]["name"])
    resource_tracker.unregister(leaked[0]["name"], "semaphore")


def test_nothing_leaked_is_said(trace):
    lock = multiprocessing.get_context("spawn").Lock()
    del lock
    gc.collect()
    assert semaphore_trace.leaked(trace) == []
    assert "was unregistered" in semaphore_trace.report(trace)


def test_without_the_variable_nothing_is_patched(monkeypatch):
    monkeypatch.delenv(semaphore_trace.ENV, raising=False)
    original = resource_tracker.register
    semaphore_trace.install_from_environment()
    assert resource_tracker.register is original
