"""The built-in sets of regions (subregions=) of validate(), analyze() and
compare_simulations(), each a file data/<name>_subdomains.nc whose variables are
maps that are 1 in a region and missing elsewhere.

Every place that takes subregions asks this module whether a name is built in
and where its file is, so a new set is added here (and its file in data/) only.
The three made from the gap report (latbands, cmems, biomes) are written by
scripts/make_region_files.py, which says where their boundaries come from.
"""

import importlib.resources

# name: what the windows call it
BUILT_IN = {
    "nwes": "Northwest European Shelf subregions",
    "global": "Global subregions",
    "latbands": "Global latitude bands",
    "cmems": "CMEMS global sub-regions",
    "biomes": "Global ocean biomes (Fay and McKinley 2014)",
}

# where each set's regions come from, for the report's Data Sources
SOURCES = {
    "latbands": "Latitude bands as in Tjiputra et al. (2020) and Schwinger et al. (2016): the North Atlantic and North "
    "Pacific north of 20°N, the tropics (20°S–20°N), the southern subtropics (20–40°S) and the Southern Ocean "
    "(south of 40°S).",
    "cmems": "Approximations of the sub-regions mapped in Figure 1 of the Copernicus Marine quality information document "
    "GLO-QUID-001-028 (global ocean biogeochemistry analysis and forecast), which gives them as a map only.",
    "biomes": "The mean biomes of Fay, A. R. and McKinley, G. A. (2014), Global open ocean biomes: mean and temporal "
    "variability, Earth Syst. Sci. Data, 6, 273–284, https://doi.org/10.5194/essd-6-273-2014 "
    "(data: https://doi.org/10.1594/PANGAEA.828650, CC BY 4.0).",
}


# how a report's text names each set ("over each of ...")
DESCRIPTIONS = {
    "nwes": "the northwest European Shelf regions",
    "global": "the global regions",
    "latbands": "the global latitude bands",
    "cmems": "the CMEMS global sub-regions",
    "biomes": "the global ocean biomes of Fay and McKinley (2014)",
}


def description(subregions, own):
    """How a report's text names the set subregions; own for a file of the user's own."""
    return DESCRIPTIONS.get(subregions, own) if isinstance(subregions, str) else own


def is_built_in(subregions):
    """Whether subregions names a built-in set."""
    return isinstance(subregions, str) and subregions in BUILT_IN


def path(name):
    """The full path of the file of the built-in set name."""
    if not is_built_in(name):
        raise ValueError(f"{name} is not a built-in set of regions: they are {', '.join(repr(x) for x in BUILT_IN)}")
    return str(importlib.resources.files("oceanval").joinpath(f"data/{name}_subdomains.nc"))


def choices_text():
    """The subregions a function takes, for its error messages."""
    names = ", ".join(f"'{x}'" for x in BUILT_IN)
    return f"{names} or a path to a .nc file"


def panel_map(regions):
    """A figure of a set of regions, one panel per region, the region dark over
    light grey land, as the report's map of the regions shows them: regions is
    a built-in set's name or the path of a file. Full Domain comes first, then
    the order in the file. The window's Map it shows it. The file is small, so
    it is read whole with xarray, holding oceanval.netcdf_lock.LOCK, and drawn
    with cartopy: nctoolkit's panel_plot, which the report uses, takes too long
    to wait for in the window."""
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib.pyplot as plt
    import numpy as np
    import xarray as xr
    from matplotlib.colors import ListedColormap

    from oceanval import netcdf_lock

    file = path(regions) if is_built_in(regions) else regions
    panels = []
    with netcdf_lock.LOCK, xr.open_dataset(file) as ds:
        for name, variable in ds.data_vars.items():
            lon = [x for x in variable.coords if "lon" in x.lower()]
            lat = [x for x in variable.coords if "lat" in x.lower()]
            if not lon or not lat:
                continue
            values = np.squeeze(variable.values).astype("float64")
            inside = np.isfinite(values) & (values != 0)
            title = variable.attrs.get("long_name") or name
            panels.append((str(title), variable[lon[0]].values, variable[lat[0]].values, inside))
    if not panels:
        raise ValueError("The file has no regions in it.")
    panels = [x for x in panels if x[0] == "Full Domain"] + [x for x in panels if x[0] != "Full Domain"]
    ncol = 3 if len(panels) == 3 else int(np.ceil(np.sqrt(len(panels))))
    nrow = int(np.ceil(len(panels) / ncol))
    # the extent of every region together, a little beyond it
    lons = np.concatenate([np.broadcast_to(lon if lon.ndim == 2 else lon[None, :], inside.shape)[inside]
                           for _, lon, lat, inside in panels])
    lats = np.concatenate([np.broadcast_to(lat if lat.ndim == 2 else lat[:, None], inside.shape)[inside]
                           for _, lon, lat, inside in panels])
    extent = None
    if lons.size:
        pad_lon, pad_lat = max(1.0, 0.03 * np.ptp(lons)), max(1.0, 0.03 * np.ptp(lats))
        extent = [max(-180, lons.min() - pad_lon), min(180, lons.max() + pad_lon),
                  max(-90, lats.min() - pad_lat), min(90, lats.max() + pad_lat)]
    fig, axes = plt.subplots(nrow, ncol, figsize=(10, 2.4 * nrow), squeeze=False,
                             subplot_kw={"projection": ccrs.PlateCarree()}, constrained_layout=True)
    dark = ListedColormap(["#333333"])
    for ax, (title, lon, lat, inside) in zip(axes.ravel(), panels):
        ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="lightgrey", zorder=1)
        ax.pcolormesh(lon, lat, np.where(inside, 1.0, np.nan), cmap=dark, vmin=0, vmax=1, shading="nearest",
                      transform=ccrs.PlateCarree(), zorder=2)
        if extent is not None:
            ax.set_extent(extent, crs=ccrs.PlateCarree())
        ax.set_title(title, fontsize=9)
    for ax in axes.ravel()[len(panels):]:
        ax.set_visible(False)
    return fig
