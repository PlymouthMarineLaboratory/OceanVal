"""The viewer's map, drawn with hvplot and sent to the page as Bokeh
json_items.

How a map is drawn follows ncplot (ncplot/plot.py): an image for a regular
longitude/latitude grid, a quadmesh for an uneven or curvilinear one, the
grid's own indices when its longitudes/latitudes cannot place the cells
(ncplot's change_coords: model grids whose land cells all have 0 or a fill
value), a colour scale symmetric about 0 in RdBu_r when the values span 0
(ncplot's _get_color_limits), and coastlines at a resolution set by the
extent (get_coastline), by geoviews.

holoviews, hvplot and bokeh take seconds to import, so they are imported
when the first map is drawn, not with this module, which the window imports.
"""

import warnings

import numpy as np
import pandas as pd
import xarray as xr

# a quadmesh of more cells than this is thinned out to it, as a browser draws each cell
MAX_QUADS = 250_000
# an image of more than this is thinned out too
MAX_PIXELS = 4_000_000

COLOUR_MAPS = ["viridis", "cividis", "plasma", "inferno", "magma", "turbo", "RdBu_r", "coolwarm", "Blues", "YlGnBu", "gray"]


def has_geoviews():
    try:
        import cartopy  # noqa: F401
        import geoviews  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def has_cartopy():
    try:
        import cartopy  # noqa: F401
        import shapely  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


# a note of map_plot's that starts so says the Natural Earth coastline could not be read, which the page tells the user
COAST_FAILED = "The Natural Earth coastline could not be read"
# (scale, extent) -> its coastline's (x, y), as reading the shapefile takes a while
_coastlines = {}


def natural_earth_coastline(x_range, y_range, scale):
    """The Natural Earth coastline (at scale: "10m", "50m" or "110m") in the
    longitudes x_range and latitudes y_range, as (x, y) arrays with NaN
    between its lines. cartopy downloads the coastline the first time a
    scale is used, so this can fail without the internet."""
    key = (scale, *(round(float(value), 4) for value in (*x_range, *y_range)))
    if key not in _coastlines:
        import cartopy.feature
        from shapely.geometry import box

        (x0, x1), (y0, y1) = x_range, y_range
        frame = box(x0, y0, x1, y1)
        feature = cartopy.feature.NaturalEarthFeature("physical", "coastline", scale)
        xs, ys = [], []
        for geometry in feature.intersecting_geometries((x0, x1, y0, y1)):
            clipped = geometry.intersection(frame)
            for line in getattr(clipped, "geoms", [clipped]):
                points = np.asarray(line.coords) if not line.is_empty and hasattr(line, "coords") else np.zeros((0, 2))
                if len(points) >= 2:
                    xs += [points[:, 0], [np.nan]]
                    ys += [points[:, 1], [np.nan]]
        _coastlines[key] = (np.concatenate(xs)[:-1], np.concatenate(ys)[:-1]) if xs else (np.zeros(0), np.zeros(0))
    return _coastlines[key]


def mesh_coastline(mesh, x, y):
    """The Natural Earth coastline over a map of an FVCOM mesh, whose axes
    are x and y (longitude and latitude). Drawn in the map's own axes, not
    by geoviews, which would have to reproject the map's image (and needs
    scipy to). Returns (overlay or None, notes)."""
    if mesh.cartesian:
        return None, ["The mesh is in metres, not longitude and latitude, so there is no coastline."]
    if not has_cartopy():
        return None, ["Coastlines need cartopy installed."]
    # a margin, so the coastline goes on a little past the mesh as the map is panned
    x0, x1 = float(np.nanmin(mesh.x)), float(np.nanmax(mesh.x))
    y0, y1 = float(np.nanmin(mesh.y)), float(np.nanmax(mesh.y))
    pad_x, pad_y = max((x1 - x0) * 0.25, 0.05), max((y1 - y0) * 0.25, 0.05)
    try:
        coast_x, coast_y = natural_earth_coastline(
            (max(x0 - pad_x, -180), min(x1 + pad_x, 180)), (max(y0 - pad_y, -90), min(y1 + pad_y, 90)),
            coastline_scale(mesh.x, mesh.y),
        )
    except Exception as error:  # noqa: BLE001 - told to the user
        return None, [f"{COAST_FAILED} (cartopy downloads it the first time): {error}"]
    if not len(coast_x):
        return None, []
    import holoviews as hv

    return hv.Path((coast_x, coast_y), kdims=[x, y]).opts(color="#111827", line_width=1.2), []


def frame_aspect(x_range, y_range, axes):
    """The height / width the map's frame needs for its x and y distances to
    be on one scale: axes "lonlat" (a degree of longitude is cos(latitude) of
    a degree of latitude, at the map's middle latitude), "metres", or
    anything else (the grid's indices), which have no distances: None."""
    if axes not in ("lonlat", "metres"):
        return None
    (x0, x1), (y0, y1) = x_range, y_range
    xspan, yspan = abs(float(x1) - float(x0)), abs(float(y1) - float(y0))
    if not (np.isfinite(xspan) and np.isfinite(yspan)) or xspan == 0 or yspan == 0:
        return None
    if axes == "lonlat":
        middle = np.clip((float(y0) + float(y1)) / 2, -89, 89)
        xspan *= np.cos(np.radians(middle))
    return yspan / xspan


def _render(plot, axes=None):
    """The figure as a json_item, with "aspect": its frame's height / width
    (frame_aspect), which the page sizes the frame by, or None to fill the
    space."""
    import holoviews as hv
    from bokeh.embed import json_item
    from bokeh.models import BoxZoomTool

    figure = hv.render(plot, backend="bokeh")
    figure.sizing_mode = "stretch_both"
    figure.toolbar.logo = None
    aspect = None
    try:
        aspect = frame_aspect((figure.x_range.start, figure.x_range.end), (figure.y_range.start, figure.y_range.end), axes)
    except (AttributeError, TypeError, ValueError):
        pass
    if aspect is not None:
        # a zoomed box keeps the frame's proportions
        for tool in figure.select(type=BoxZoomTool):
            tool.match_aspect = True
    item = json_item(figure)
    item["aspect"] = aspect
    return item


def colour_limits(values, vmin=None, vmax=None, log=False):
    """The colour scale's limits, and whether it is symmetric about 0: the
    limits given, or the values' range, made symmetric about 0 when it spans
    it (as ncplot's _get_color_limits)."""
    finite = np.asarray(values, dtype="float64")
    finite = finite[np.isfinite(finite)]
    if log:
        finite = finite[finite > 0]
    low = float(finite.min()) if finite.size else 0.0
    high = float(finite.max()) if finite.size else 1.0
    if vmin is not None and vmax is not None:
        return (float(vmin), float(vmax)), False
    if vmin is not None:
        return (float(vmin), max(high, float(vmin))), False
    if vmax is not None:
        return (min(low, float(vmax)), float(vmax)), False
    if low < 0 < high and not log:
        top = max(-low, high)
        return (-top, top), True
    if low == high:
        return (low - 0.5, high + 0.5) if low == 0 else (low * 0.99, high * 1.01) if low > 0 else (low * 1.01, high * 0.99), False
    return (low, high), False


def _repeated_coordinates(lons, lats):
    """Whether cells share a longitude and latitude: the 0s (or fill values)
    a model writes at land cells, which put those cells on top of one
    another (ncplot's change_coords, which looks for a value far more common
    than any other; here any pair of cells in one place is enough, as a
    quadmesh is pulled out of shape by one)."""
    pairs = pd.DataFrame({"lon": np.asarray(lons).ravel(), "lat": np.asarray(lats).ravel()})
    return bool(pairs.duplicated().any())


def _uneven(values):
    values = np.asarray(values, dtype="float64")
    if values.size < 3:
        return False
    steps = np.abs(np.diff(values))
    if steps.min() == 0:
        return True
    return steps.max() / steps.min() > 1.001


def _thin(data, limit):
    cells = int(np.prod(data.shape))
    if cells <= limit:
        return data, 1
    stride = int(np.ceil(np.sqrt(cells / limit)))
    return data.isel({dim: slice(None, None, stride) for dim in data.dims}), stride


def coastline_scale(lon, lat, values=None):
    """ncplot's get_coastline: the resolution of the coastline for an extent:
    that of the cells with values, if values (of the same shape) are given."""
    lon, lat = np.asarray(lon, dtype="float64"), np.asarray(lat, dtype="float64")
    if values is not None:
        valid = np.isfinite(np.asarray(values, dtype="float64"))
        if valid.shape == lon.shape == lat.shape and valid.any():
            lon, lat = lon[valid], lat[valid]
    area = (np.nanmax(lon) - np.nanmin(lon)) * (np.nanmax(lat) - np.nanmin(lat)) / 360**2
    if area < 0.0016:
        return "10m"
    if area < 0.25:
        return "50m"
    return "110m"


def _without_grid_mapping(data):
    """data without what rioxarray reads a crs from: a grid_mapping attribute
    or encoding, and the coordinates of a crs (spatial_ref, crs)."""
    data = data.drop_vars([key for key in data.coords if key in ("spatial_ref", "crs")])
    data = data.copy()
    data.attrs = {key: value for key, value in data.attrs.items() if key != "grid_mapping"}
    data.encoding = {key: value for key, value in data.encoding.items() if key != "grid_mapping"}
    # rioxarray (newer versions look in more places) must find no crs: a coordinate's attributes
    # (crs_wkt, spatial_ref, grid_mapping) and encoding go too
    for name in data.coords:
        data[name].attrs = {key: value for key, value in data[name].attrs.items()
                            if key not in ("grid_mapping", "crs_wkt", "spatial_ref", "crs")}
        data[name].encoding = {key: value for key, value in data[name].encoding.items() if key != "grid_mapping"}
    return data


def mesh_image(data, about, mesh):
    """A slice of a variable on an FVCOM mesh (one value for each node or
    element) as a 2-D DataArray on the mesh's Raster (see oceanval.viewer_mesh),
    with the about that draws it: its axes as longitude and latitude, or x
    and y in metres. Also the note saying so."""
    raster = mesh.raster()
    image = raster.image(np.asarray(data.values).ravel(), about["mesh"])
    y, x = ("y", "x") if mesh.cartesian else ("lat", "lon")
    drawn = xr.DataArray(image, dims=(y, x), coords={y: raster.y_axis, x: raster.x_axis}, name=data.name)
    about = dict(about, horizontal=[y, x], lon=x, lat=y, cartesian=mesh.cartesian)
    where = "nodes, shaded across each triangle" if about["mesh"] == "node" else "elements, each filling its triangle"
    note = (
        f"FVCOM's mesh of {mesh.nodes:,} nodes and {mesh.elements:,} triangles, the values on its {where}, "
        f"drawn on a grid of {raster.shape[1]} by {raster.shape[0]}."
    )
    return drawn, about, note


def map_plot(data, about, name, cmap=None, vmin=None, vmax=None, log=False, coast=False, mesh=None):
    """The map of a 2-D slice (see viewer_catalogue.Catalogue.field), as (json_item, with
    its frame's "aspect" (see _render), notes, the colour scale's limits). A slice of a variable on an FVCOM mesh is
    drawn as an image of the mesh (mesh_image), and needs its mesh."""
    import hvplot.xarray  # noqa: F401 - registers .hvplot

    notes = []
    if about.get("mesh"):
        data, about, note = mesh_image(data, about, mesh)
        notes.append(note)
    data = data.squeeze(drop=True).astype("float64")
    data.name = name
    lon, lat = about.get("lon"), about.get("lat")
    if lon not in data.coords or lat not in data.coords:
        lon = lat = None
    y_dim, x_dim = about["horizontal"][-2:]
    kind = "image"
    if lon is not None:
        lons, lats = np.asarray(data[lon].values), np.asarray(data[lat].values)
        if lons.ndim == 1 and lats.ndim == 1:
            # a regular lon-lat grid is an image; an uneven one a quadmesh
            x, y = lon, lat
            kind = "quadmesh" if _uneven(lats) or _uneven(lons) else "image"
            if data[lat].dims[0] != data.dims[0]:
                data = data.transpose(data[lat].dims[0], data[lon].dims[0])
        elif not np.all(np.isfinite(lons)) or not np.all(np.isfinite(lats)) or _repeated_coordinates(lons, lats):
            # cells the coordinates cannot place: the grid's own indices (ncplot's change_coords)
            lon = lat = None
            notes.append("The longitudes and latitudes cannot place every cell, so the grid's own indices are shown.")
        else:
            x, y = lon, lat
            kind = "quadmesh"
    if lon is None:
        data = data.drop_vars([key for key in data.coords if key not in data.dims])
        data = data.assign_coords({dim: np.arange(data.sizes[dim]) for dim in data.dims})
        data = data.transpose(y_dim, x_dim)
        x, y = x_dim, y_dim
        kind = "image"
    if kind == "quadmesh":
        data, stride = _thin(data, MAX_QUADS)
    else:
        data, stride = _thin(data, MAX_PIXELS)
    if stride > 1:
        notes.append(f"Every {stride}th cell is shown, as the grid is large.")

    (low, high), symmetric = colour_limits(data.values, vmin, vmax, log)
    if log and high <= 0:
        log = False
        notes.append("There are no positive values, so the scale is not logarithmic.")
    if log:
        low = max(low, np.nanmin(np.where(data.values > 0, data.values, np.nan)))
    colour = cmap or ("RdBu_r" if symmetric else "viridis")
    # no title (an empty one: holoviews would make one from the slice's time or level): what it said is on
    # the page (the variable, the time) and the colour bar (the units)
    options = dict(
        x=x, y=y, cmap=colour, clim=(low, high), logz=log, title="",
        tools=["hover"], responsive=True, colorbar=True, clabel=about.get("units") or None,
    )
    cartesian = about.get("cartesian", False)
    # a mesh's coastline is drawn over its image in its own axes (mesh_coastline), not by geoviews
    on_mesh = bool(about.get("mesh"))
    geographic = coast and lon is not None and not on_mesh and has_geoviews()
    if coast and lon is not None and not on_mesh and not geographic:
        notes.append("Coastlines need cartopy and geoviews installed.")
    if geographic:
        # the extent of the cells with values: a grid can have cells far from the domain (NEMO's at -1, -1)
        lon_cells = data[x].broadcast_like(data).transpose(*data.dims).values
        lat_cells = data[y].broadcast_like(data).transpose(*data.dims).values
        # the axes are longitudes and latitudes: say so, and hide any grid mapping from rioxarray, whose
        # crs hvplot would use instead of this one (and geoviews refuses a crs unlike its coastline's)
        import cartopy.crs as ccrs

        data = _without_grid_mapping(data)
        options.update(geo=True, crs=ccrs.PlateCarree(), coastline=coastline_scale(lon_cells, lat_cells, data.values))
    else:
        options.update(xlabel=x, ylabel=y)
        if cartesian:
            options.update(xlabel="x (m)", ylabel="y (m)")
        elif lon is not None:
            options.update(xlabel="Longitude", ylabel="Latitude")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        draw = data.hvplot.quadmesh if kind == "quadmesh" else data.hvplot.image
        try:
            plot = draw(**options)
        except ValueError as problem:
            # geoviews refusing a crs that is not its coastline's: draw the map without the coastline
            if not (geographic and "coordinate reference system" in str(problem)):
                raise
            for key in ("geo", "crs", "coastline"):
                options.pop(key)
            options.update(xlabel="Longitude", ylabel="Latitude")
            notes.append("The coastline could not be drawn over this map.")
            geographic = False
            plot = draw(**options)
        if coast and on_mesh:
            line, coast_notes = mesh_coastline(mesh, x, y)
            if line is not None:
                plot = (plot * line).opts(responsive=True)
            notes += coast_notes
    axes = None if lon is None else "metres" if cartesian else "lonlat"
    return _render(plot, axes), notes, [float(low), float(high)]
