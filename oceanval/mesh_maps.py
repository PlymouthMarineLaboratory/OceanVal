"""Maps of raw FVCOM output on its own mesh, for the Analyze report.

nctoolkit's pub_plot and panel_plot draw what CDO can read, and CDO cannot read
FVCOM's unstructured mesh, so these draw it as pyfvcom2 does
(https://github.com/PlymouthMarineLaboratory/pyfvcom2, plotting/plot.py,
FVCOMPlotter): a matplotlib Triangulation of the nodes and the triangles in nv
(nv.T - 1), coloured by tripcolor on cartopy axes in PlateCarree, a value on
the nodes shaded linearly across each triangle and one on the elements filling
its triangle. A triangle with a value missing (any of its nodes, or its
element) is not drawn. The land is Natural Earth's, beneath the mesh, which
is the water, with the coast over it.

mesh_plot and mesh_panel_plot take the arguments of pub_plot and panel_plot
that the Analyze figures use, so a figure's script calls them as it would
those. They read a file analyze() saved (see oceanval.fvcom_climatology), or
raw FVCOM output: the mesh is read by oceanval.viewer_mesh.Mesh, which takes
longitudes above 180 as below, and a mesh with no longitudes (x and y in
metres) is drawn on plain axes.
"""

import numpy as np

# the colour of the land, beneath the mesh, and of the coast
LAND_COLOUR = "#d9d9d9"
COAST_COLOUR = "#4d4d4d"


def _values(path, var):
    """The values of var in path, a row for each time, and where they are on
    the mesh: ((times, cells) array with NaN where missing, "node" | "nele")."""
    import netCDF4

    from oceanval.viewer_mesh import mesh_location

    with netCDF4.Dataset(path) as handle:
        variable = handle.variables[var]
        variable.set_auto_maskandscale(True)
        where = mesh_location(variable.dimensions)
        if where is None:
            raise ValueError(f"{var} in {path} is on neither the nodes nor the elements of an FVCOM mesh.")
        index = tuple(slice(None) if dim in ("time", where) else 0 for dim in variable.dimensions)
        values = np.ma.filled(np.ma.asarray(variable[index]).astype("float64"), np.nan)
    return values.reshape(-1, values.shape[-1]), where


def _robust(values, low=2, high=98):
    """The 2nd and 98th percentiles of values, or their range if those are
    the same, or None if there are none."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    lo, hi = np.percentile(finite, [low, high])
    if lo == hi:
        lo, hi = float(finite.min()), float(finite.max())
    if lo == hi:
        lo, hi = lo - 0.5, hi + 0.5
    return [float(lo), float(hi)]


def _extend(values, limits):
    """The colour bar's arrows: which ends values go beyond."""
    finite = values[np.isfinite(values)]
    below = finite.size and finite.min() < limits[0]
    above = finite.size and finite.max() > limits[1]
    return {(True, True): "both", (True, False): "min", (False, True): "max"}.get((bool(below), bool(above)), "neither")


def _draw(ax, mesh, values, where, limits, colours, geographic):
    """Colour the mesh by values on ax. Returns what tripcolor drew."""
    from matplotlib.tri import Triangulation

    triangles = mesh.triangles
    # pub_plot's own default
    colours = "viridis" if colours == "auto" else colours
    if where == "nele":
        bad = ~np.isfinite(values)
    else:
        bad = ~np.isfinite(values[triangles]).all(axis=1)
    if bad.all():
        return None
    tri = Triangulation(mesh.x, mesh.y, triangles, mask=bad)
    options = {"cmap": colours, "vmin": limits[0], "vmax": limits[1], "zorder": 2}
    if geographic:
        import cartopy.crs as ccrs

        options["transform"] = ccrs.PlateCarree()
    if where == "nele":
        # not antialiased, so no seams show between the triangles
        drawn = ax.tripcolor(tri, facecolors=values[~bad], antialiased=False, **options)
    else:
        # the masked triangles are not drawn, but gouraud shading wants a value at every node
        filled = np.where(np.isfinite(values), values, np.nanmean(values))
        drawn = ax.tripcolor(tri, filled, shading="gouraud", **options)
    return drawn


def _map_axes(fig, place, mesh, grid_labels):
    """The axes of one map of mesh, at place on fig (a subplot spec), with
    the land beneath and the coast, and its extent the mesh's."""
    used = np.unique(mesh.triangles)
    x0, x1 = float(np.nanmin(mesh.x[used])), float(np.nanmax(mesh.x[used]))
    y0, y1 = float(np.nanmin(mesh.y[used])), float(np.nanmax(mesh.y[used]))
    pad_x, pad_y = 0.02 * max(x1 - x0, 1e-9), 0.02 * max(y1 - y0, 1e-9)
    if mesh.cartesian:
        ax = fig.add_subplot(place)
        ax.set_xlim(x0 - pad_x, x1 + pad_x)
        ax.set_ylim(y0 - pad_y, y1 + pad_y)
        ax.set_aspect("equal")
        if grid_labels:
            ax.set_xlabel("x (m)")
            ax.set_ylabel("y (m)")
        else:
            ax.set_xticks([])
            ax.set_yticks([])
        return ax
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature

    ax = fig.add_subplot(place, projection=ccrs.PlateCarree())
    ax.set_extent([x0 - pad_x, x1 + pad_x, y0 - pad_y, y1 + pad_y], crs=ccrs.PlateCarree())
    # in its true proportions: a degree of longitude is shorter than one of latitude
    ax.set_aspect(1 / max(np.cos(np.radians((y0 + y1) / 2)), 0.05))
    span = max(x1 - x0, y1 - y0)
    scale = "10m" if span < 10 else "50m" if span < 60 else "110m"
    ax.add_feature(cfeature.NaturalEarthFeature("physical", "land", scale), facecolor=LAND_COLOUR, edgecolor="none", zorder=1)
    ax.add_feature(
        cfeature.NaturalEarthFeature("physical", "coastline", scale),
        facecolor="none", edgecolor=COAST_COLOUR, linewidth=0.5, zorder=3,
    )
    if grid_labels:
        lines = ax.gridlines(draw_labels=True, linewidth=0.3, color="grey", alpha=0.5)
        lines.top_labels = lines.right_labels = False
    return ax


def _mesh(path):
    from oceanval.viewer_mesh import Mesh

    return Mesh.read(path)


def mesh_plot(path, var, time=0, robust=True, limits=None, legend=None, title=None, colours="viridis",
              grid_labels=True, fig=None, gs=None, size=None, legend_position="right"):
    """Map var in the FVCOM file at path, at its time-th time, as
    nctoolkit's pub_plot maps a grid: robust colours from the 2nd to the 98th
    percentile, or limits, [low, high]; legend the colour bar's label, and
    legend_position None for no colour bar; fig and gs a figure and a subplot
    spec to draw it in. Returns the axes."""
    import matplotlib.pyplot as plt

    mesh = _mesh(path)
    values, where = _values(path, var)
    values = values[time]
    if limits is None:
        limits = _robust(values) if robust else [float(np.nanmin(values)), float(np.nanmax(values))]
    if limits is None:
        limits = [0.0, 1.0]
    if fig is None:
        fig = plt.figure(figsize=size or (8, 6))
    if gs is None:
        gs = fig.add_gridspec(1, 1)[0, 0]
    ax = _map_axes(fig, gs, mesh, grid_labels)
    drawn = _draw(ax, mesh, values, where, limits, colours, not mesh.cartesian)
    if drawn is not None and legend_position is not None:
        bar = fig.colorbar(drawn, ax=ax, extend=_extend(values, limits), shrink=0.8)
        if legend:
            bar.set_label(legend)
    if title:
        ax.set_title(title)
    return ax


def mesh_panel_plot(path, var, titles, ncol=4, limits=None, legend=None, colours="viridis", grid_labels=False,
                    size=None):
    """Map each time of var in the FVCOM file at path in a panel of its own,
    as nctoolkit's panel_plot maps a grid's: titles the title of each, in
    ncol columns, on one colour scale (limits, or the 2nd to 98th percentile
    of all of them) under one colour bar, at the right. Returns the figure."""
    import matplotlib.pyplot as plt

    mesh = _mesh(path)
    values, where = _values(path, var)
    if limits is None:
        limits = _robust(values) or [0.0, 1.0]
    panels = min(len(titles), len(values))
    ncol = max(1, min(ncol, panels))
    nrow = int(np.ceil(panels / ncol))
    fig = plt.figure(figsize=size or (3 * ncol + 1, 2.6 * nrow))
    grid = fig.add_gridspec(nrow, ncol)
    axes, drawn = [], None
    for index in range(panels):
        ax = _map_axes(fig, grid[index // ncol, index % ncol], mesh, grid_labels)
        ax.set_title(titles[index])
        drawn = _draw(ax, mesh, values[index], where, limits, colours, not mesh.cartesian) or drawn
        axes.append(ax)
    if drawn is not None:
        bar = fig.colorbar(drawn, ax=axes, extend=_extend(values, limits), shrink=0.8)
        if legend:
            bar.set_label(legend)
    return fig


def mesh_sim_plot(maps, var, ncol=3, limits=None, legend=None, colours="viridis", grid_labels=False, size=None):
    """Map var in the first time of each of maps, [(title, path)] of FVCOM files
    (one for each simulation of a comparison, each on its own mesh), in a
    panel of its own each, in ncol columns, on one colour scale (limits, or the
    2nd to 98th percentile of all of them) under one colour bar, at the right.
    Returns the figure."""
    import matplotlib.pyplot as plt

    found = []
    for title, path in maps:
        values, where = _values(path, var)
        found.append((title, _mesh(path), values[0], where))
    if limits is None:
        limits = _robust(np.concatenate([values for _, _, values, _ in found])) or [0.0, 1.0]
    ncol = max(1, min(ncol, len(found)))
    nrow = int(np.ceil(len(found) / ncol))
    fig = plt.figure(figsize=size or (3 * ncol + 1, 2.6 * nrow))
    grid = fig.add_gridspec(nrow, ncol)
    axes, drawn = [], None
    for index, (title, mesh, values, where) in enumerate(found):
        ax = _map_axes(fig, grid[index // ncol, index % ncol], mesh, grid_labels)
        ax.set_title(title)
        drawn = _draw(ax, mesh, values, where, limits, colours, not mesh.cartesian) or drawn
        axes.append(ax)
    if drawn is not None:
        bar = fig.colorbar(drawn, ax=axes, extend=_extend(np.concatenate([values for _, _, values, _ in found]), limits), shrink=0.8)
        if legend:
            bar.set_label(legend)
    return fig
