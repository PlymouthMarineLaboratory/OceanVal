"""The stratification measures of casts (oceanval.stratification), checked
against values worked out by hand: a two-layer cast's dt, the potential energy
anomaly of a linear density profile (g H drho / 12), EOS-80 density, and the
mixed layer depth's crossing."""

import numpy as np
import pandas as pd
import pytest

from oceanval import stratification as st


def _eos80_sigma_t(temperature, salinity):
    """EOS-80's density at one atmosphere (UNESCO 1981, Millero and Poisson),
    less 1000: written out here so the density is checked against something
    other than gsw."""
    t, s = temperature, salinity
    rho_w = (
        999.842594 + 6.793952e-2 * t - 9.095290e-3 * t**2 + 1.001685e-4 * t**3
        - 1.120083e-6 * t**4 + 6.536332e-9 * t**5
    )
    a = 8.24493e-1 - 4.0899e-3 * t + 7.6438e-5 * t**2 - 8.2467e-7 * t**3 + 5.3875e-9 * t**4
    b = -5.72466e-3 + 1.0227e-4 * t - 1.6546e-6 * t**2
    c = 4.8314e-4
    return rho_w + a * s + b * s**1.5 + c * s**2 - 1000


@pytest.mark.parametrize("temperature, salinity", [(5.0, 35.0), (12.0, 34.5), (18.0, 35.5), (10.0, 30.0)])
def test_density_is_eos80_potential_density(temperature, salinity):
    # TEOS-10 and EOS-80 potential densities differ by less than 0.02 kg m-3 in
    # these waters (McDougall and Barker, 2011)
    rho = st.density([temperature], salinity, [50.0], -5.0, 50.0)[0]
    assert rho - 1000 == pytest.approx(_eos80_sigma_t(temperature, salinity), abs=0.02)


def test_pea_of_linear_density_is_g_h_drho_over_12():
    # rho = rho0 + drho d / H, so the mean is rho0 + drho / 2 and
    # phi = g / H * integral (drho (d / H - 1 / 2)) d dd = g drho H / 12
    water_depth, drho = 80.0, 1.5
    depths = np.linspace(0.0, water_depth, 9)
    rho = 1025.0 + drho * depths / water_depth
    assert st.pea_from_density(depths, rho, water_depth) == pytest.approx(st.G * drho * water_depth / 12)


def test_pea_of_a_mixed_column_is_zero():
    assert st.pea_from_density([2.0, 40.0, 78.0], [1026.0, 1026.0, 1026.0], 80.0) == pytest.approx(0.0, abs=1e-9)


def test_pea_of_two_layers():
    # light water of rho1 over the top half, rho2 below, as samples at the centres
    # of the top and bottom cells, with the density linear between them
    water_depth, rho1, rho2 = 100.0, 1025.0, 1026.0
    depths = np.array([0.5, 49.9, 50.1, 99.5])
    rho = np.array([rho1, rho1, rho2, rho2])
    expected_layers = st.G * (rho2 - rho1) * water_depth / 8
    # the jump between 49.9 and 50.1 m is linear, which makes no visible difference
    assert st.pea_from_density(depths, rho, water_depth) == pytest.approx(expected_layers, rel=1e-3)


def _cast(depths, temperature, salinity=None, **extra):
    frame = pd.DataFrame({"lon": -5.0, "lat": 50.0, "year": 2012, "month": 7, "day": 15,
                          "depth": depths, "observation": temperature, "model": temperature})
    if salinity is not None:
        frame = frame.assign(salinity=salinity, model_salinity=salinity)
    return frame.assign(**extra)


def _info(surface, bottom, water, t_surface, t_bottom, s_surface=np.nan, s_bottom=np.nan, **extra):
    row = {"lon": -5.0, "lat": 50.0, "year": 2012, "month": 7, "day": 15,
           "surface_depth": surface, "bottom_depth": bottom, "water_depth": water,
           "model_surface": t_surface, "model_bottom": t_bottom,
           "model_surface_salinity": s_surface, "model_bottom_salinity": s_bottom}
    row.update(extra)
    return pd.DataFrame([row])


def test_dt_is_surface_minus_bottom_at_the_model_cell_centres():
    # a thermocline at 20-30 m; the model's top and bottom cell centres are at
    # 1 m and 79 m of an 80 m water column
    depths = np.array([0.0, 10.0, 20.0, 30.0, 50.0, 78.0])
    temperature = np.array([15.0, 15.0, 15.0, 10.0, 10.0, 10.0])
    table = st.measures(_cast(depths, temperature), _info(1.0, 79.0, 80.0, 15.0, 10.0))
    dt = table.query("measure == 'dt'")
    assert dt.observation.iloc[0] == pytest.approx(5.0)
    assert dt.model.iloc[0] == pytest.approx(5.0)
    assert dt.water_depth.iloc[0] == 80.0
    assert dt.n_samples.iloc[0] == 6


def test_observed_cast_is_interpolated_to_the_surface_and_bottom():
    # samples at 0, 4 and 60 m: the surface (2 m) is between the first two, and
    # the bottom (64 m) is 4 m below the last, within the tolerance
    depths = np.array([0.0, 4.0, 60.0])
    temperature = np.array([16.0, 14.0, 10.0])
    table = st.measures(_cast(depths, temperature), _info(2.0, 64.0, 66.0, 15.0, 10.0))
    dt = table.query("measure == 'dt'").observation.iloc[0]
    assert dt == pytest.approx(15.0 - 10.0)


def test_cast_short_of_the_bottom_has_no_dt():
    # its deepest sample is 30 m above a bottom of 100 m (tolerance 10 m)
    depths = np.array([0.0, 20.0, 70.0])
    temperature = np.array([15.0, 12.0, 10.0])
    table = st.measures(_cast(depths, temperature), _info(1.0, 100.0, 102.0, 15.0, 10.0))
    assert "dt" not in set(table.measure)


def test_cast_with_too_few_samples_or_no_surface_sample_is_left_out():
    few = st.measures(_cast(np.array([0.0, 40.0]), np.array([15.0, 10.0])), _info(1.0, 39.0, 40.0, 15.0, 10.0))
    assert len(few) == 0
    deep = st.measures(
        _cast(np.array([12.0, 20.0, 40.0]), np.array([15.0, 12.0, 10.0])), _info(1.0, 39.0, 40.0, 15.0, 10.0)
    )
    assert len(deep) == 0


def test_mld_is_where_the_temperature_first_leaves_its_10_m_value_by_0_2():
    # 14 degrees down to 20 m, then 0.1 degrees colder each metre: the
    # threshold of 0.2 is crossed 2 m below 20 m
    depths = np.array([0.0, 5.0, 10.0, 20.0, 21.0, 22.0, 23.0, 40.0])
    temperature = np.array([14.0, 14.0, 14.0, 14.0, 13.9, 13.8, 13.7, 12.0])
    out = st.profile_measures(depths, temperature, None, 1.0, 39.5, 40.0, -5.0, 50.0)
    assert out["mld"] == pytest.approx(22.0)


def test_mld_of_a_column_mixed_to_the_bed_is_the_water_depth():
    depths = np.array([0.0, 10.0, 30.0, 59.0])
    out = st.profile_measures(depths, np.full(4, 11.0), None, 1.0, 59.0, 60.0, -5.0, 50.0)
    assert out["mld"] == pytest.approx(60.0)
    assert out["dt"] == pytest.approx(0.0)
    assert out["pea_t"] == pytest.approx(0.0, abs=1e-6)


def test_thermal_pea_matches_the_linear_case_with_fixed_salinity():
    # temperature linear in depth makes the density nearly linear, so the
    # anomaly is close to g H drho / 12 with drho from the end densities
    depths = np.linspace(0.5, 59.5, 60)
    temperature = 16.0 - 6.0 * depths / 60.0
    out = st.profile_measures(depths, temperature, None, 0.5, 59.5, 60.0, -5.0, 50.0)
    ends = st.density([16.0, 10.0], st.FIXED_SALINITY, [0.0, 60.0], -5.0, 50.0)
    assert out["pea_t"] == pytest.approx(st.G * (ends[1] - ends[0]) * 60.0 / 12, rel=0.03)
    assert np.isnan(out["pea"])


def test_full_pea_uses_salinity_when_it_is_there():
    # fresher on top makes the full anomaly larger than the thermal one
    depths = np.array([0.5, 10.0, 20.0, 30.0, 39.5])
    temperature = np.array([14.0, 14.0, 12.0, 11.0, 11.0])
    salinity = np.array([34.0, 34.2, 35.0, 35.2, 35.2])
    out = st.profile_measures(depths, temperature, salinity, 0.5, 39.5, 40.0, -5.0, 50.0)
    assert out["pea"] > out["pea_t"] > 0


def test_pea_goes_to_200_m_at_most():
    depths = np.array([0.0, 50.0, 100.0, 199.0, 250.0])
    temperature = np.array([14.0, 12.0, 11.0, 10.0, 9.0])
    deep = st.profile_measures(depths, temperature, None, 1.0, 495.0, 500.0, -12.0, 48.0)
    shallow = st.profile_measures(depths[:4], temperature[:4], None, 1.0, 199.0, 200.0, -12.0, 48.0)
    assert deep["pea_t"] == pytest.approx(shallow["pea_t"], rel=0.02)
    # a cast reaching 250 m of a 500 m column is far short of the bottom
    assert np.isnan(deep["dt"])


def test_measures_are_one_row_per_cast_and_measure():
    a = _cast(np.array([0.0, 20.0, 40.0, 59.0]), np.array([15.0, 14.0, 10.0, 10.0]))
    b = _cast(np.array([0.0, 20.0, 40.0, 59.0]), np.array([12.0, 12.0, 12.0, 12.0])).assign(day=16)
    casts = pd.concat([_info(1.0, 59.0, 60.0, 15.0, 10.0), _info(1.0, 59.0, 60.0, 12.0, 12.0).assign(day=16)])
    table = st.measures(pd.concat([a, b]), casts)
    assert sorted(set(table.day)) == [15, 16]
    assert set(table.measure) == {"dt", "pea_t", "mld"}
    assert list(table.columns[:5]) == ["lon", "lat", "year", "month", "day"]


def test_agreement_counts_stratified_and_mixed_casts():
    frame = pd.DataFrame({
        "measure": ["dt", "dt", "dt", "dt", "mld"],
        "observation": [2.0, 2.0, 0.1, 0.1, 20.0],
        "model": [3.0, 0.2, 1.0, 0.0, 30.0],
    })
    table = st.agreement(frame)
    row = table.query("measure == 'dt'").iloc[0]
    assert (row["both stratified"], row["observed only"], row["modelled only"], row["both mixed"]) == (1, 1, 1, 1)
    assert row["agreement"] == pytest.approx(0.5)
    assert "mld" not in set(table.measure)
