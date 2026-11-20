"""EN4 profiles (oceanval.en4): its quality control, box and dates read from a
small file laid out as EN4's are, its addresses, and the sizes the window
gives. No network."""

import zipfile

import netCDF4
import numpy as np
import pytest

from oceanval import en4


def _write_month(path, profiles):
    """A monthly profile file laid out as EN4's, with profiles a list of dicts
    of lon, lat, days (since 1950), depth, temperature, salinity (lists over
    the levels) and the QC characters (position, potm, psal, deph, the level
    potm_levels/psal_levels strings) and the flags."""
    n_levels = max(len(x["depth"]) for x in profiles)
    with netCDF4.Dataset(path, "w") as f:
        f.createDimension("N_PROF", len(profiles))
        f.createDimension("N_LEVELS", n_levels)
        for name, dtype, dims in [
            ("LONGITUDE", "f8", ("N_PROF",)), ("LATITUDE", "f8", ("N_PROF",)), ("JULD", "f8", ("N_PROF",)),
            ("QC_FLAGS_PROFILES", "i4", ("N_PROF",)),
        ]:
            f.createVariable(name, dtype, dims)
        for name in ["POSITION_QC", "PROFILE_POTM_QC", "PROFILE_PSAL_QC", "PROFILE_DEPH_QC"]:
            f.createVariable(name, "S1", ("N_PROF",))
        for name in ["DEPH_CORRECTED", "POTM_CORRECTED", "PSAL_CORRECTED"]:
            f.createVariable(name, "f4", ("N_PROF", "N_LEVELS"), fill_value=99999.0)
        for name in ["DEPH_CORRECTED_QC", "POTM_CORRECTED_QC", "PSAL_CORRECTED_QC"]:
            f.createVariable(name, "S1", ("N_PROF", "N_LEVELS"))
        f["JULD"].units = "days since 1950-01-01 00:00:00 utc"
        for i, p in enumerate(profiles):
            f["LONGITUDE"][i] = p["lon"]
            f["LATITUDE"][i] = p["lat"]
            f["JULD"][i] = p["days"]
            f["QC_FLAGS_PROFILES"][i] = p.get("flags", 0)
            f["POSITION_QC"][i] = p.get("position", "1")
            f["PROFILE_POTM_QC"][i] = p.get("potm", "1")
            f["PROFILE_PSAL_QC"][i] = p.get("psal", "1")
            f["PROFILE_DEPH_QC"][i] = p.get("deph", "1")
            n = len(p["depth"])
            f["DEPH_CORRECTED"][i, :n] = p["depth"]
            f["POTM_CORRECTED"][i, :n] = p["temperature"]
            f["PSAL_CORRECTED"][i, :n] = p["salinity"]
            levels = lambda key: np.array(list(p.get(key, "1" * n).ljust(n_levels, "0")), dtype="S1")
            f["DEPH_CORRECTED_QC"][i, :] = levels("deph_levels")
            f["POTM_CORRECTED_QC"][i, :] = levels("potm_levels")
            f["PSAL_CORRECTED_QC"][i, :] = levels("psal_levels")


def _profile(lon=-5.0, lat=50.0, days=22841.5, **extra):
    # 22841.5 days after 1950-01-01 is 2012-07-15 12:00
    p = {"lon": lon, "lat": lat, "days": days, "depth": [1.0, 20.0, 60.0],
         "temperature": [15.0, 12.0, 10.0], "salinity": [35.0, 35.1, 35.2]}
    p.update(extra)
    return p


def test_accepted_levels_are_read_with_their_date(tmp_path):
    path = tmp_path / "month.nc"
    _write_month(path, [_profile()])
    df = en4.read_month(str(path))
    assert list(df.columns) == en4.COLUMNS
    assert len(df) == 3
    assert (df.year.unique().tolist(), df.month.unique().tolist(), df.day.unique().tolist()) == ([2012], [7], [15])
    assert df.depth.tolist() == [1.0, 20.0, 60.0]
    assert df.observation.tolist() == pytest.approx([15.0, 12.0, 10.0])
    assert df.salinity.tolist() == pytest.approx([35.0, 35.1, 35.2])


def test_rejected_profiles_and_duplicates_are_left_out(tmp_path):
    path = tmp_path / "month.nc"
    _write_month(path, [
        _profile(lon=-1.0, position="4"),
        _profile(lon=-2.0, potm="4"),
        _profile(lon=-3.0, flags=en4.DUPLICATE_BIT),
        _profile(lon=-4.0, deph="4"),
        _profile(lon=-6.0),
    ])
    df = en4.read_month(str(path))
    assert df.lon.unique().tolist() == [-6.0]


def test_rejected_levels_and_salinity(tmp_path):
    path = tmp_path / "month.nc"
    _write_month(path, [
        # the second level's temperature is rejected, and the third's depth
        _profile(lon=-5.0, potm_levels="141", deph_levels="114"),
        # the salinity profile is rejected: its levels stay, without salinity
        _profile(lon=-6.0, psal="4"),
        # one salinity level is rejected
        _profile(lon=-7.0, psal_levels="411"),
    ])
    df = en4.read_month(str(path))
    assert df.query("lon == -5").depth.tolist() == [1.0]
    assert df.query("lon == -6").salinity.isna().all() and len(df.query("lon == -6")) == 3
    assert np.isnan(df.query("lon == -7").salinity.iloc[0])
    assert df.query("lon == -7").salinity.iloc[1:].tolist() == pytest.approx([35.1, 35.2])


def test_box_and_longitudes_over_180(tmp_path):
    path = tmp_path / "month.nc"
    _write_month(path, [_profile(lon=355.0), _profile(lon=30.0), _profile(lat=70.0)])
    df = en4.read_month(str(path), lon_lim=(-20, 13), lat_lim=(40, 65))
    assert df.lon.unique().tolist() == [-5.0]


def test_a_year_is_read_from_its_zip(tmp_path):
    for month in (7, 8):
        _write_month(tmp_path / f"EN.4.2.2.f.profiles.g10.2012{month:02d}.nc", [_profile(days=22841.5 + 31 * (month - 7))])
    zipped = tmp_path / "en4_2012.zip"
    with zipfile.ZipFile(zipped, "w") as archive:
        for month in (7, 8):
            name = f"EN.4.2.2.f.profiles.g10.2012{month:02d}.nc"
            archive.write(tmp_path / name, name)
    told = []
    df = en4.read_zip(str(zipped), on_month=lambda done, total: told.append((done, total)))
    assert sorted(df.month.unique().tolist()) == [7, 8]
    assert told == [(1, 2), (2, 2)]


def test_addresses_before_and_after_2021():
    assert en4.url(2012) == "https://www.metoffice.gov.uk/hadobs/en4/data/en4-2-1/EN.4.2.2/EN.4.2.2.profiles.g10.2012.zip"
    assert en4.url(2021) == "https://www.metoffice.gov.uk/hadobs/en4/data/en4-2-1/EN.4.2.2.profiles.g10.2021.zip"


def test_sizes_for_the_window():
    assert en4.total_gb(2012, 2014) == pytest.approx((528 + 692 + 780) / 1000)
    assert en4.total_gb(2014, 2012) == 0.0
    # a year after the last measured is taken to be as big as it
    assert en4.year_mb(2030) == en4.MB_PER_YEAR[max(en4.MB_PER_YEAR)]
    assert en4.sizes()["2012"] == 528
