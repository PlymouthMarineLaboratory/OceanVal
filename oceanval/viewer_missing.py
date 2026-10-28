"""What the viewer's setup page assumes to be missing values, before the
output is read in full: the value many cells of the model data share in every
file (see oceanval.missing_probe, which Analyze uses), or, for a NEMO-ERSEM
simulation, 0. The page puts the answer in its missing value rows to be
checked; nothing here is applied until Continue is pressed with the rows as
they are.
"""

from oceanval import missing_probe, viewer_catalogue


def guess(simdir, ndown, exclude="", require=""):
    """{"value": the value many cells share, as a string as a box holds it, or
    None, "ersem": whether the variables are those of a NEMO-ERSEM simulation}
    for the output ndown directories below simdir that pass the file filters
    (words separated by spaces, as scan takes them). It looks at the first
    variable that can be mapped, in the first file type that has one. It
    never raises."""
    nothing = {"value": None, "ersem": False}
    try:
        found = viewer_catalogue.scan(simdir, ndown, exclude, require, wait=False)
    except Exception:  # noqa: BLE001 - no files, or unreadable ones, are told by the page's own check
        return nothing
    try:
        from oceanval.recipes_gui import is_likely_nemo_ersem

        names = sorted({name for stream in found.streams for name in stream["variables"]})
        value = None
        for stream in found.streams:
            name = next((name for name, about in stream["variables"].items() if viewer_catalogue.mappable(about)), None)
            if name:
                value = missing_probe.probe(stream["files"], name)
                break
        return {"value": value, "ersem": bool(is_likely_nemo_ersem(names))}
    except Exception:  # noqa: BLE001 - a guess must not stop the page
        return nothing
    finally:
        found.close()
