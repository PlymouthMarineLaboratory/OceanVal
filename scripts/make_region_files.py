"""Make the built-in region files latbands_subdomains.nc, cmems_subdomains.nc and
biomes_subdomains.nc in oceanval/data/.

They are on the 1-degree grid of global_subdomains.nc (lat -90..90, lon -180..180,
181 x 361), each region a variable that is 1 inside it and missing elsewhere, with
its name as long_name. Land (the countries of TM_WORLD_BORDERS-0.3, which is in
oceanval/data/) is missing in every region.

    python scripts/make_region_files.py /path/to/fay_mckinley_biomes.nc

The biomes are the mean biomes of Fay and McKinley (2014), Global open ocean
biomes: mean and temporal variability, Earth Syst. Sci. Data, 6, 273-284,
https://doi.org/10.5194/essd-6-273-2014, data https://doi.org/10.1594/PANGAEA.828650
(CC BY 4.0). Download the netCDF with
    curl -L -o biomes.nc "https://doi.pangaea.de/10.1594/PANGAEA.828650?format=textfile"
Their codes 1-17 are in the order of the paper's Table 2, which this script checks
by the area of each.

The latitude bands are those of the gap report's latband_regions (after
Tjiputra et al. 2020 and Schwinger et al. 2016). The CMEMS sub-regions follow
Figure 1 of the CMEMS QUID GLO-QUID-001-028, which shows them as a map without
coordinates, so the boxes here are approximations of it.
"""

import os
import sys

import geopandas as gpd
import numpy as np
import shapely
import xarray as xr

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "oceanval", "data")

LAT = np.arange(-90.0, 90.5, 1.0)
LON = np.arange(-180.0, 180.5, 1.0)

# Table 2 of Fay and McKinley (2014): name, mean biome area (10^6 km2)
BIOMES = [
    ("np_ice", "North Pacific ice (NP ICE)", 4.5852),
    ("np_spss", "North Pacific subpolar seasonally stratified (NP SPSS)", 12.838),
    ("np_stss", "North Pacific subtropical seasonally stratified (NP STSS)", 6.8257),
    ("np_stps", "North Pacific subtropical permanently stratified (NP STPS)", 41.048),
    ("pac_equ_w", "West Pacific equatorial (Pac EQU W)", 11.593),
    ("pac_equ_e", "East Pacific equatorial (Pac EQU E)", 14.890),
    ("sp_stps", "South Pacific subtropical permanently stratified (SP STPS)", 52.705),
    ("na_ice", "North Atlantic ice (NA ICE)", 5.4750),
    ("na_spss", "North Atlantic subpolar seasonally stratified (NA SPSS)", 10.062),
    ("na_stss", "North Atlantic subtropical seasonally stratified (NA STSS)", 5.9744),
    ("na_stps", "North Atlantic subtropical permanently stratified (NA STPS)", 17.464),
    ("atl_equ", "Atlantic equatorial (Atl EQU)", 7.4147),
    ("sa_stps", "South Atlantic subtropical permanently stratified (SA STPS)", 18.055),
    ("ind_stps", "Indian Ocean subtropical permanently stratified (IND STPS)", 35.936),
    ("so_stss", "Southern Ocean subtropical seasonally stratified (SO STSS)", 29.692),
    ("so_spss", "Southern Ocean subpolar seasonally stratified (SO SPSS)", 30.628),
    ("so_ice", "Southern Ocean ice (SO ICE)", 18.678),
]


def ocean_mask(lat2, lon2):
    """True at the cell centres that are not inside a country."""
    world = gpd.read_file(os.path.join(DATA, "TM_WORLD_BORDERS-0.3.shp"))
    land = shapely.unary_union(list(world.geometry))
    points = shapely.points(lon2.ravel(), lat2.ravel())
    inside = shapely.contains(land, points) | shapely.touches(land, points)
    return ~inside.reshape(lat2.shape)


def basins(lat2, lon2):
    """The Atlantic, Pacific and Indian sectors the band regions split by."""
    atl = (lon2 > -80) & (lon2 < 20)
    pac = (lon2 > 120) | (lon2 <= -80)
    ind = (lon2 >= 20) & (lon2 <= 120)
    # the Gulf of Mexico and the western Caribbean lie west of 80W but are the
    # Atlantic's (Mexico's Pacific coast is south of 18N west of 98W, Central
    # America's south of 10N east of 84W)
    caribbean = ((lon2 >= -98) & (lon2 <= -80) & (lat2 >= 18)) | (
        (lon2 >= -84) & (lon2 <= -80) & (lat2 >= 10) & (lat2 < 18)
    )
    return atl | caribbean, pac & ~caribbean, ind


def latbands(lat2, lon2):
    atl, pac, _ = basins(lat2, lon2)
    return [
        ("north_atlantic_20n", "North Atlantic (north of 20N)", (lat2 > 20) & atl),
        ("north_pacific_20n", "North Pacific (north of 20N)", (lat2 > 20) & pac),
        ("tropics", "Tropics (20S-20N)", np.abs(lat2) <= 20),
        ("southern_subtropics", "Southern subtropics (20S-40S)", (lat2 < -20) & (lat2 >= -40)),
        ("southern_ocean_40s", "Southern Ocean (south of 40S)", lat2 < -40),
    ]


def cmems(lat2, lon2):
    atl, pac, ind = basins(lat2, lon2)
    # the Black Sea (north of 41N, east of 28E) is left out
    med = (lat2 >= 30) & (lat2 <= 46) & (lon2 >= -5.5) & (lon2 <= 37) & ~((lon2 >= 28) & (lat2 >= 41))
    nordsea = (lat2 >= 51) & (lat2 <= 62) & (lon2 >= -4) & (lon2 <= 10)
    ibi = (lat2 >= 26) & (lat2 <= 56) & (lon2 >= -19) & (lon2 <= 5) & ~med & ~nordsea
    shelf_seas = med | nordsea | ibi
    return [
        ("glo", "Full Domain", np.ones_like(lat2, bool)),
        ("arc", "Arctic Ocean (ARC)", lat2 >= 66),
        ("nat", "North Atlantic (NAT)", (lat2 > 20) & (lat2 < 66) & atl & ~shelf_seas),
        ("tat", "Tropical Atlantic (TAT)", (np.abs(lat2) <= 20) & atl),
        ("sat", "South Atlantic (SAT)", (lat2 < -20) & (lat2 > -45) & atl),
        ("npa", "North Pacific (NPA)", (lat2 > 20) & (lat2 < 66) & pac),
        ("tpa", "Tropical Pacific (TPA)", (np.abs(lat2) <= 20) & pac),
        ("spa", "South Pacific (SPA)", (lat2 < -20) & (lat2 > -45) & pac),
        ("ind", "Indian Ocean (IND)", (lat2 > -45) & (lat2 < 30) & ind & ~med),
        ("acc", "Antarctic Circumpolar Current (ACC)", lat2 <= -45),
        ("med", "Mediterranean Sea (MED)", med),
        ("nordsea", "North Sea (NORDSEA)", nordsea),
        ("ibi", "Iberia-Biscay-Ireland (IBI)", ibi),
    ]


def biomes(lat2, lon2, path):
    ds = xr.open_dataset(path)
    codes = ds.MeanBiomes.transpose("lat", "lon")
    # onto the 1-degree grid of cell corners at whole degrees, by nearest neighbour
    lon_in = codes.lon.values
    lat_in = codes.lat.values
    i_lat = np.clip(np.searchsorted(lat_in, LAT) - 1, 0, len(lat_in) - 1)
    i_lon = np.clip(np.searchsorted(lon_in, LON) - 1, 0, len(lon_in) - 1)
    values = codes.values[np.ix_(i_lat, i_lon)]
    # check the codes are Table 2's order, by area on the file's own grid
    radius = 6371.0088
    band = np.radians(1) * radius**2 * (np.sin(np.radians(lat_in + 0.5)) - np.sin(np.radians(lat_in - 0.5)))
    area = band[:, None] * np.ones((1, len(lon_in)))
    for k, (_, name, table_area) in enumerate(BIOMES, start=1):
        found = area[codes.values == k].sum() / 1e6
        if abs(found - table_area) > 0.02 * table_area:
            raise ValueError(f"biome {k} ({name}) has area {found:.3f}, not {table_area} as in Table 2")
    return [(key, name, values == k) for k, (key, name, _) in enumerate(BIOMES, start=1)]


def write(regions, ocean, name, title, source):
    data = {}
    for key, long_name, inside in regions:
        values = np.where(inside & ocean, 1.0, np.nan).astype("float32")
        data[key] = xr.DataArray(values, dims=("lat", "lon"), attrs={"long_name": long_name})
    ds = xr.Dataset(data, coords={"lat": LAT, "lon": LON})
    ds.lat.attrs = {"units": "degrees_north", "standard_name": "latitude", "long_name": "latitude"}
    ds.lon.attrs = {"units": "degrees_east", "standard_name": "longitude", "long_name": "longitude"}
    ds.attrs = {"title": title, "source": source, "history": "made by scripts/make_region_files.py"}
    encoding = {key: {"_FillValue": -9999.0, "zlib": True, "complevel": 9} for key in data}
    out = os.path.join(DATA, f"{name}_subdomains.nc")
    ds.to_netcdf(out, encoding=encoding)
    print("wrote", out, list(data))


def main(biome_path):
    lat2, lon2 = np.meshgrid(LAT, LON, indexing="ij")
    ocean = ocean_mask(lat2, lon2)
    write(latbands(lat2, lon2), ocean, "latbands", "Latitude bands",
          "Latitude bands of Tjiputra et al. (2020) and Schwinger et al. (2016)")
    write(cmems(lat2, lon2), ocean, "cmems", "CMEMS global sub-regions",
          "Approximations of the sub-regions of Figure 1 of CMEMS QUID GLO-QUID-001-028")
    write(biomes(lat2, lon2, biome_path), ocean, "biomes", "Mean ocean biomes",
          "Fay, A. R. and McKinley, G. A. (2014), Earth Syst. Sci. Data, 6, 273-284, "
          "https://doi.org/10.5194/essd-6-273-2014; data https://doi.org/10.1594/PANGAEA.828650 (CC BY 4.0)")


if __name__ == "__main__":
    main(sys.argv[1])
