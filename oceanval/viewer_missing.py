"""What the viewer's setup page assumes to be missing values, before the
output is read in full: the value many cells of the model data share in every
file (see oceanval.missing_probe, which Analyze uses), or, for a NEMO-ERSEM
simulation, 0. The page puts the answer in its missing value rows to be
checked; nothing here is applied until Continue is pressed with the rows as
they are.
"""

from oceanval import missing_probe, viewer_catalogue


def guess(simdir, ndown, exclude="", require="", stopped=lambda: False, keep=None, paths=None):
    """{"value": the value many cells share, as a string as a box holds it, or
    None, "ersem": whether the variables are those of a NEMO-ERSEM simulation}
    for the output ndown directories below simdir that pass the file filters
    (words separated by spaces, as scan takes them). It looks at the first
    variable that can be mapped, in the first file type that has one. It
    never raises.

    stopped() is asked whether the answer is still wanted, between the file
    types read and before the files are looked at: a guess the user has moved
    on from gives up rather than hold oceanval.netcdf_lock.LOCK, which the
    viewer's own reading of the output needs.

    paths, if given, are the simulation's files, found already, so its
    directories are not walked again (see viewer_catalogue.scan).

    keep(catalogue), if given, is offered what was read of the output: the
    window keeps it for Continue to carry on with, as it is the very reading
    the viewer needs (see viewer_app._viewer_taken). Whatever keep does not
    take is closed here, so no reading is ever left open by mistake.
    """
    nothing = {"value": None, "ersem": False}
    try:
        found = viewer_catalogue.scan(
            simdir, ndown, exclude, require, stopped=stopped, wait=False, paths=paths
        )
    except Exception:  # noqa: BLE001 - no files, or unreadable ones, are told by the page's own check
        return nothing
    taken = False
    try:
        from oceanval.recipes_gui import is_likely_nemo_ersem

        names = sorted({name for stream in found.streams for name in stream["variables"]})
        value = None
        for stream in found.streams:
            name = next((name for name, about in stream["variables"].items() if viewer_catalogue.mappable(about)), None)
            if name:
                if stopped():
                    return nothing
                value = missing_probe.probe(stream["files"], name)
                break
        answer = {"value": value, "ersem": bool(is_likely_nemo_ersem(names))}
        # offered only once the reading is whole, and only if it is still wanted
        taken = bool(keep is not None and not stopped() and keep(found))
        return answer
    except Exception:  # noqa: BLE001 - a guess must not stop the page
        return nothing
    finally:
        if not taken:
            found.close()
