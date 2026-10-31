"""Which code made a semaphore that leaked (macOS).

On macOS multiprocessing starts processes by spawning, so each Lock, Queue or
Pool it makes is a named semaphore that a resource_tracker process is told of.
If the process owning one ends without running its finalizers (killed, or
os._exit), the tracker warns at shutdown "There appear to be 1 leaked semaphore
objects to clean up" and removes it itself. The warning is harmless, but says
nothing of what made the semaphore.

With OCEANVAL_TRACE_SEMAPHORES naming a file, the oceanval command calls
install(path): every register/unregister of the tracker (multiprocessing's, and
multiprocess's, which nctoolkit uses on macOS) is appended to the file as a JSON
line with the process and the stack of the call. Processes forked from the
command keep the patch. Then

    python -m oceanval.semaphore_trace <file>

lists those registered and never unregistered, with where each was made.

This imports nothing of OceanVal, and never raises.
"""

import contextlib
import json
import os
import sys
import time
import traceback

ENV = "OCEANVAL_TRACE_SEMAPHORES"
STACK_DEPTH = 15

_installed = {}


def _tracker_modules():
    modules = []
    for name in ("multiprocessing.resource_tracker", "multiprocess.resource_tracker"):
        with contextlib.suppress(Exception):
            __import__(name)
            modules.append(sys.modules[name])
    return modules


def _write(path, entry):
    with contextlib.suppress(Exception):
        line = (json.dumps(entry) + "\n").encode("utf-8")
        # one write of a file opened to append: lines from processes forked
        # from this one are never mixed, and no lock is taken
        handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(handle, line)
        finally:
            os.close(handle)


def _wrapped(original, action, module, path):
    def call(name, rtype, *args, **kwargs):
        with contextlib.suppress(Exception):
            _write(
                path,
                {
                    "time": time.time(),
                    "action": action,
                    "module": module,
                    "pid": os.getpid(),
                    "ppid": os.getppid(),
                    "rtype": rtype,
                    "name": name,
                    "stack": traceback.format_stack(limit=STACK_DEPTH)[:-1],
                },
            )
        return original(name, rtype, *args, **kwargs)

    call.__wrapped__ = original
    return call


def install(path):
    """Record every register/unregister of the resource trackers in path.
    Calling it again does nothing. Returns whether it is installed."""
    try:
        path = os.path.abspath(os.path.expanduser(path))
        for module in _tracker_modules():
            if module.__name__ in _installed:
                continue
            originals = (module.register, module.unregister)
            module.register = _wrapped(originals[0], "register", module.__name__, path)
            module.unregister = _wrapped(originals[1], "unregister", module.__name__, path)
            _installed[module.__name__] = originals
        return bool(_installed)
    except Exception:  # noqa: BLE001 - a trace must never stop OceanVal
        return False


def install_from_environment():
    path = os.environ.get(ENV)
    if path:
        install(path)


def uninstall():
    for name, (register, unregister) in list(_installed.items()):
        module = sys.modules.get(name)
        if module is not None:
            module.register, module.unregister = register, unregister
        del _installed[name]


def leaked(path):
    """The entries registered in path and never unregistered, in the order
    made (a name made again after being removed counts once more)."""
    open_ = {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            key = (entry.get("module"), entry.get("rtype"), entry.get("name"))
            if entry.get("action") == "register":
                open_.setdefault(key, []).append(entry)
            elif open_.get(key):
                open_[key].pop()
    found = [entry for entries in open_.values() for entry in entries]
    return sorted(found, key=lambda entry: entry.get("time", 0))


def report(path):
    entries = leaked(path)
    if not entries:
        return f"Every resource registered in {path} was unregistered."
    parts = [f"{len(entries)} registered and never unregistered:"]
    for entry in entries:
        parts.append(
            f"\n{entry.get('rtype')} {entry.get('name')} ({entry.get('module')}), "
            f"made in process {entry.get('pid')} (parent {entry.get('ppid')}):\n"
            + "".join(entry.get("stack") or [])
        )
    return "\n".join(parts)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m oceanval.semaphore_trace <file>", file=sys.stderr)
        return 2
    print(report(argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
