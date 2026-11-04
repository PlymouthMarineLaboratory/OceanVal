"""Spatial means of raw FVCOM output on its own mesh, for analyze(): over the
domain, over a lon/lat box and over the regions of a regional summary, each
weighted by the areas of the cells, and the series at stations.

CDO cannot read FVCOM's mesh (see oceanval.fvcom), so nctoolkit's spatial_mean,
which the rest of Analyze uses, cannot weight its cells. The areas are worked
out from the mesh itself (pyfvcom2 has no such code):

- an element's is the area of its triangle (nv), on a sphere of the Earth's
  radius when the mesh has longitudes and latitudes (Van Oosterom and
  Strackee's formula for the solid angle of a triangle, exact for any size),
  or in the plane of its x and y, in metres, when it has none;
- a node's is a third of the area of each element it is a corner of: the
  control volume FVCOM itself gives a node (art1), made by joining the
  centroids of its triangles to the midpoints of their sides, so the nodes'
  areas add up to the mesh's.

A mean is then the sum of value x area over the cells with a value, over the
sum of their areas: weighted by area alone, as spatial_mean weights a grid's
cells. A region of a regions file (a lon-lat grid of 1 where the region is)
takes the cells of the mesh whose positions (the nodes', or the centroids of
the elements) are in its grid cells: the mesh is never regridded. A station
is the nearest node (or element) that has values, as analysis_series takes a
grid's.
"""

import numpy as np

EARTH_M = 6_371_000.0
# a station further than this many cells from a cell with values is not in the model
STATION_CELLS = 2
# a station is in the nearest of this many cells nearest it that has values
STATION_CANDIDATES = 32


class MeshCells:
    """The cells of an FVCOM mesh a variable is on (where: "node" or
    "nele"): their positions (lon, lat, or x, y in metres if the mesh has no
    longitudes), areas in square metres, and usual size in km (the mean length
    of the sides of the triangles at each)."""

    def __init__(self, path, where):
        from oceanval.viewer_mesh import Mesh

        mesh = Mesh.read(path)
        self.where = where
        self.cartesian = mesh.cartesian
        triangles = mesh.triangles
        element_areas = triangle_areas(mesh.x, mesh.y, triangles, mesh.cartesian)
        sides = _side_lengths(mesh.x, mesh.y, triangles, mesh.cartesian)
        if where == "nele":
            self.areas = element_areas
            self.lon = mesh.x[triangles].mean(axis=1)
            self.lat = mesh.y[triangles].mean(axis=1)
            if not mesh.cartesian:
                # a triangle across the dateline is not centred on the far side of the globe
                self.lon = _centroid_lon(mesh.x[triangles])
            self.size = sides.mean(axis=1)
        else:
            self.areas = node_areas(element_areas, triangles, mesh.nodes)
            self.lon, self.lat = mesh.x, mesh.y
            total = np.zeros(mesh.nodes)
            count = np.zeros(mesh.nodes)
            for corner in range(3):
                np.add.at(total, triangles[:, corner], sides.mean(axis=1))
                np.add.at(count, triangles[:, corner], 1)
            self.size = np.where(count > 0, total / np.maximum(count, 1), np.nan)


def _unit_vectors(lon, lat):
    lon, lat = np.radians(lon), np.radians(lat)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def triangle_areas(x, y, triangles, cartesian=False):
    """The area of each triangle of a mesh (triangles: (n, 3), its nodes from
    0) in square metres: x and y longitudes and latitudes on a sphere of the
    Earth's radius, or with cartesian metres in a plane."""
    x, y = np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")
    if cartesian:
        xs, ys = x[triangles], y[triangles]
        return 0.5 * np.abs(
            (xs[:, 1] - xs[:, 0]) * (ys[:, 2] - ys[:, 0]) - (xs[:, 2] - xs[:, 0]) * (ys[:, 1] - ys[:, 0])
        )
    a, b, c = (_unit_vectors(x[triangles[:, k]], y[triangles[:, k]]) for k in range(3))
    triple = np.abs(np.einsum("ij,ij->i", a, np.cross(b, c)))
    below = 1 + np.einsum("ij,ij->i", a, b) + np.einsum("ij,ij->i", b, c) + np.einsum("ij,ij->i", c, a)
    return 2 * np.arctan2(triple, below) * EARTH_M**2


def node_areas(element_areas, triangles, nodes):
    """The area of each node's control volume: a third of each element's
    area it is a corner of."""
    areas = np.zeros(nodes)
    for corner in range(3):
        np.add.at(areas, triangles[:, corner], element_areas / 3)
    return areas


def _centroid_lon(corners):
    """The longitude of the centroid of each triangle, its corners' (n, 3)
    longitudes, in -180 to 180."""
    first = corners[:, :1]
    unwrapped = first + (corners - first + 180) % 360 - 180
    return (unwrapped.mean(axis=1) + 180) % 360 - 180


def _distance_km(lon1, lat1, lon2, lat2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_M / 1000 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _side_lengths(x, y, triangles, cartesian):
    """The length in km of the three sides of each triangle, (n, 3)."""
    starts, ends = triangles, np.roll(triangles, -1, axis=1)
    if cartesian:
        return np.hypot(x[ends] - x[starts], y[ends] - y[starts]) / 1000
    return _distance_km(x[starts], y[starts], x[ends], y[ends])


def weighted_mean(fields, weights):
    """The mean of each row of fields (times, cells) weighted by weights
    (cells), over the cells with a value; NaN for a row with none."""
    fields = np.atleast_2d(np.asarray(fields, dtype="float64"))
    ok = np.isfinite(fields) & np.isfinite(weights)[None, :] & (weights > 0)[None, :]
    total = np.where(ok, fields * np.where(np.isfinite(weights), weights, 0)[None, :], 0.0).sum(axis=1)
    area = np.where(ok, np.where(np.isfinite(weights), weights, 0)[None, :], 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(area > 0, total / np.where(area > 0, area, 1.0), np.nan)


def wrap(lon):
    return (np.asarray(lon, dtype="float64") + 180) % 360 - 180


def in_box(cells, lon_lim, lat_lim):
    """Which of cells are inside the lon/lat box."""
    lon = wrap(cells.lon)
    low, high = wrap(lon_lim[0]), wrap(lon_lim[1])
    if lon_lim[1] - lon_lim[0] >= 360:
        across = np.ones(lon.shape, dtype=bool)
    elif low <= high:
        across = (lon >= low) & (lon <= high)
    else:
        across = (lon >= low) | (lon <= high)
    return across & (cells.lat >= lat_lim[0]) & (cells.lat <= lat_lim[1])


def _axis_index(axis, values, wrapped=False):
    """The index of the cell of the 1-D axis (the centres of a regular or
    irregular grid's cells) each of values is in, or -1 outside it."""
    axis = np.asarray(axis, dtype="float64")
    order = np.argsort(axis)
    centres = axis[order]
    if len(centres) == 1:
        return np.zeros(np.shape(values), dtype="int64")
    edges = np.concatenate(
        [[centres[0] - (centres[1] - centres[0]) / 2], (centres[1:] + centres[:-1]) / 2,
         [centres[-1] + (centres[-1] - centres[-2]) / 2]]
    )
    values = np.asarray(values, dtype="float64")
    if wrapped:
        # longitudes as the axis has them: -180 to 180 or 0 to 360
        values = np.where(values < edges[0], values + 360, values)
        values = np.where(values > edges[-1], values - 360, values)
    found = np.searchsorted(edges, values, side="right") - 1
    inside = (found >= 0) & (found < len(centres))
    return np.where(inside, order[np.clip(found, 0, len(centres) - 1)], -1)


def region_cells(region_file, cells):
    """The regions of region_file (see analysis_series.region_masks) the cells
    of a mesh are in: {name: boolean array over the cells}, from the grid cell
    of the file each cell's position is in. Raises ValueError for a file
    whose longitudes and latitudes are not 1-D axes."""
    import xarray as xr

    from oceanval import analysis_series

    found = {}
    with xr.open_dataset(region_file, decode_times=False) as ds:
        names = list(ds.coords) + list(ds.variables)
        lon_name = next((name for name in names if "lon" in name.lower()), None)
        lat_name = next((name for name in names if "lat" in name.lower()), None)
        if lon_name is None or lat_name is None or ds[lon_name].ndim != 1 or ds[lat_name].ndim != 1:
            raise ValueError(
                f"The regions file {region_file} is not on a grid of 1-D longitudes and latitudes, so the regions "
                "cannot be found on the FVCOM mesh."
            )
        lon_dim, lat_dim = ds[lon_name].dims[0], ds[lat_name].dims[0]
        i = _axis_index(ds[lon_name].values, cells.lon, wrapped=True)
        j = _axis_index(ds[lat_name].values, cells.lat)
        inside = (i >= 0) & (j >= 0)
        for variable in ds.data_vars:
            mask = ds[variable]
            if lon_dim not in mask.dims or lat_dim not in mask.dims:
                continue
            mask = mask.squeeze([dim for dim in mask.dims if dim not in (lon_dim, lat_dim)])
            values = np.asarray(mask.transpose(lat_dim, lon_dim).values, dtype="float64")
            held = np.zeros(cells.lon.shape, dtype=bool)
            held[inside] = values[j[inside], i[inside]] == 1
            long_name = mask.attrs.get("long_name")
            found[long_name if isinstance(long_name, str) and long_name else str(variable)] = held
    domain = analysis_series.DOMAIN.replace(" ", "").lower()
    return {name: held for name, held in found.items() if name.replace(" ", "").lower() != domain}


class Series:
    """The series of the means over the domain (in the lon/lat box, if there
    is one) and over each region in region_file (if any), and at each of
    stations ({name: [lon, lat]}), of the fields of a mesh's cells, added one
    time at a time, so that only a time's field need be held. regions_for
    keeps the regions of each region_file and mesh."""

    def __init__(self, cells, region_file=None, lon_lim=None, lat_lim=None, stations=None, regions_for=None):
        self.cells = cells
        self.weights = cells.areas
        self.lon_lim = lon_lim
        if lon_lim is not None:
            self.weights = np.where(in_box(cells, lon_lim, lat_lim), self.weights, 0.0)
        self.regions = {}
        if region_file is not None:
            regions_for = {} if regions_for is None else regions_for
            key = (region_file, cells.where, len(cells.areas))
            if key not in regions_for:
                regions_for[key] = region_cells(region_file, cells)
            self.regions = {name: np.where(held, self.weights, 0.0) for name, held in regions_for[key].items()}
        # the cells nearest each station, of which the nearest with values is the station's
        self.stations = {}
        for name, (lon, lat) in (stations or {}).items():
            distance = _distance_km(lon, lat, cells.lon, cells.lat)
            nearest = np.argsort(distance)[:STATION_CANDIDATES]
            self.stations[name] = (nearest, distance[nearest])
        self.times, self.domain, self.by_region, self.at_stations = [], [], {name: [] for name in self.regions}, {
            name: [] for name in self.stations
        }

    def add(self, time, field):
        """Add the field (a value for each cell, NaN where there is none) of
        time, (year, month)."""
        self.times.append(time)
        self.domain.append(weighted_mean(field, self.weights)[0])
        for name, weights in self.regions.items():
            self.by_region[name].append(weighted_mean(field, weights)[0])
        for name, (nearest, _) in self.stations.items():
            self.at_stations[name].append(np.asarray(field, dtype="float64")[nearest])

    def region_rows(self):
        """(rows, notes), as analysis_series.region_values gives them."""
        from oceanval.analysis_series import DOMAIN

        rows = [("domain", DOMAIN, y, m, v) for (y, m), v in zip(self.times, self.domain)]
        notes = []
        for name, values in self.by_region.items():
            if not np.isfinite(values).any():
                notes.append(f"{name} has no cells in the model's domain{' and box' if self.lon_lim else ''}, so it is left out.")
                continue
            rows.extend(("region", name, y, m, v) for (y, m), v in zip(self.times, values))
        return rows, notes

    def station_rows(self):
        """(rows, found), as analysis_series.station_values gives them: each
        station in the nearest cell with values at any time. Raises
        ValueError for the stations further than STATION_CELLS of its cells'
        sides from it."""
        rows, found, problems = [], {}, []
        for name, (nearest, distance) in self.stations.items():
            values = np.vstack(self.at_stations[name]) if self.at_stations[name] else np.zeros((0, len(nearest)))
            wet = np.isfinite(values).any(axis=0)
            if not wet.any():
                problems.append(f"{name} has no cell of the FVCOM mesh with values near it: it is on land, or outside the model.")
                continue
            pick = int(np.argmax(wet))
            index, km = int(nearest[pick]), float(distance[pick])
            size = float(self.cells.size[index])
            if np.isfinite(size) and km > STATION_CELLS * size:
                problems.append(
                    f"{name} is {km:.0f} km from the nearest cell of the FVCOM mesh with values, more than "
                    f"{STATION_CELLS} cells ({STATION_CELLS * size:.0f} km): it is on land, or outside the model."
                )
                continue
            found[name] = {"lon": float(wrap(self.cells.lon[index])), "lat": float(self.cells.lat[index]),
                           "distance": round(km, 2)}
            for (year, month), value in zip(self.times, values[:, pick]):
                rows.append(("station", name, year, month, float(value) if np.isfinite(value) else np.nan))
        if problems:
            raise ValueError("; ".join(problems))
        return rows, found


def domain_mean(path, key):
    """The area-weighted mean over the mesh of key at each time in path, a
    file analyze() saved on an FVCOM mesh (see oceanval.fvcom_climatology), as
    analysis_plots.domain_mean gives it: (times, values, note), note "" as the
    areas are the mesh's own."""
    import cftime
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        variable = handle.variables[key]
        variable.set_auto_maskandscale(True)
        where = "nele" if "nele" in variable.dimensions else "node"
        values = np.ma.filled(np.ma.asarray(variable[:]).astype("float64"), np.nan)
        time = handle.variables["time"]
        times = cftime.num2date(time[:], time.units, getattr(time, "calendar", "standard"))
    cells = MeshCells(path, where)
    values = values.reshape(-1, values.shape[-1])
    return list(np.atleast_1d(times)), weighted_mean(values, cells.areas), ""
