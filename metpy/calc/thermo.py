# Copyright (c) 2008-2015 MetPy Developers.
# Distributed under the terms of the BSD 3-Clause License.
# SPDX-License-Identifier: BSD-3-Clause
"""Contains a collection of thermodynamic calculations."""

from __future__ import division

import numpy as np
import scipy.integrate as si
import scipy.optimize as so

from .tools import find_intersections
from ..constants import Cp_d, epsilon, kappa, Lv, P0, Rd
from ..package_tools import Exporter
from ..units import atleast_1d, check_units, concatenate, units

exporter = Exporter(globals())

sat_pressure_0c = 6.112 * units.millibar


@exporter.export
@check_units('[pressure]', '[temperature]')
def potential_temperature(pressure, temperature):
    r"""Calculate the potential temperature.

    Uses the Poisson equation to calculation the potential temperature
    given `pressure` and `temperature`.

    Parameters
    ----------
    pressure : `pint.Quantity`
        The total atmospheric pressure
    temperature : `pint.Quantity`
        The temperature

    Returns
    -------
    `pint.Quantity`
        The potential temperature corresponding to the the temperature and
        pressure.

    See Also
    --------
    dry_lapse

    Notes
    -----
    Formula:

    .. math:: \Theta = T (P_0 / P)^\kappa

    Examples
    --------
    >>> from metpy.units import units
    >>> metpy.calc.potential_temperature(800. * units.mbar, 273. * units.kelvin)
    <Quantity(290.96653180346203, 'kelvin')>

    """
    return temperature * (P0 / pressure).to('dimensionless')**kappa


@exporter.export
@check_units('[pressure]', '[temperature]')
def dry_lapse(pressure, temperature):
    r"""Calculate the temperature at a level assuming only dry processes.

    This function lifts a parcel starting at `temperature`, conserving
    potential temperature. The starting pressure should be the first item in
    the `pressure` array.

    Parameters
    ----------
    pressure : `pint.Quantity`
        The atmospheric pressure level(s) of interest
    temperature : `pint.Quantity`
        The starting temperature

    Returns
    -------
    `pint.Quantity`
       The resulting parcel temperature at levels given by `pressure`

    See Also
    --------
    moist_lapse : Calculate parcel temperature assuming liquid saturation
                  processes
    parcel_profile : Calculate complete parcel profile
    potential_temperature

    """
    return temperature * (pressure / pressure[0])**kappa


@exporter.export
@check_units('[pressure]', '[temperature]')
def moist_lapse(pressure, temperature):
    r"""Calculate the temperature at a level assuming liquid saturation processes.

    This function lifts a parcel starting at `temperature`. The starting
    pressure should be the first item in the `pressure` array. Essentially,
    this function is calculating moist pseudo-adiabats.

    Parameters
    ----------
    pressure : `pint.Quantity`
        The atmospheric pressure level(s) of interest
    temperature : `pint.Quantity`
        The starting temperature

    Returns
    -------
    `pint.Quantity`
       The temperature corresponding to the the starting temperature and
       pressure levels.

    See Also
    --------
    dry_lapse : Calculate parcel temperature assuming dry adiabatic processes
    parcel_profile : Calculate complete parcel profile

    Notes
    -----
    This function is implemented by integrating the following differential
    equation:

    .. math:: \frac{dT}{dP} = \frac{1}{P} \frac{R_d T + L_v r_s}
                                {C_{pd} + \frac{L_v^2 r_s \epsilon}{R_d T^2}}

    This equation comes from [Bakhshaii2013]_.

    """
    def dt(t, p):
        t = units.Quantity(t, temperature.units)
        p = units.Quantity(p, pressure.units)
        rs = saturation_mixing_ratio(p, t)
        frac = ((Rd * t + Lv * rs) /
                (Cp_d + (Lv * Lv * rs * epsilon / (Rd * t * t)))).to('kelvin')
        return frac / p
    return units.Quantity(si.odeint(dt, atleast_1d(temperature).squeeze(),
                                    pressure.squeeze()).T.squeeze(), temperature.units)


@exporter.export
@check_units('[pressure]', '[temperature]', '[temperature]')
def lcl(pressure, temperature, dewpt, max_iters=50, eps=1e-5):
    r"""Calculate the lifted condensation level (LCL) using from the starting point.

    The starting state for the parcel is defined by `temperature`, `dewpt`,
    and `pressure`.

    Parameters
    ----------
    pressure : `pint.Quantity`
        The starting atmospheric pressure
    temperature : `pint.Quantity`
        The starting temperature
    dewpt : `pint.Quantity`
        The starting dew point

    Returns
    -------
    `(pint.Quantity, pint.Quantity)`
        The LCL pressure and temperature

    Other Parameters
    ----------------
    max_iters : int, optional
        The maximum number of iterations to use in calculation, defaults to 50.
    eps : float, optional
        The desired relative error in the calculated value, defaults to 1e-5.

    See Also
    --------
    parcel_profile

    Notes
    -----
    This function is implemented using an iterative approach to solve for the
    LCL. The basic algorithm is:

    1. Find the dew point from the LCL pressure and starting mixing ratio
    2. Find the LCL pressure from the starting temperature and dewpoint
    3. Iterate until convergence

    The function is guaranteed to finish by virtue of the `max_iters` counter.

    """
    def _lcl_iter(p, p0, w, t):
        td = dewpoint(vapor_pressure(units.Quantity(p, pressure.units), w))
        return (p0 * (td / t) ** (1. / kappa)).m

    w = mixing_ratio(saturation_vapor_pressure(dewpt), pressure)
    fp = so.fixed_point(_lcl_iter, pressure.m, args=(pressure.m, w, temperature),
                        xtol=eps, maxiter=max_iters)
    lcl_p = units.Quantity(fp, pressure.units)
    return lcl_p, dewpoint(vapor_pressure(lcl_p, w))


@exporter.export
@check_units('[pressure]', '[temperature]', '[temperature]')
def lfc(pressure, temperature, dewpt):
    r"""Calculate the level of free convection (LFC).

    This works by finding the first intersection of the ideal parcel path and
    the measured parcel temperature.

    Parameters
    ----------
    pressure : `pint.Quantity`
        The atmospheric pressure
    temperature : `pint.Quantity`
        The temperature at the levels given by `pressure`
    dewpt : `pint.Quantity`
        The dew point at the levels given by `pressure`

    Returns
    -------
    `pint.Quantity`
        The LFC pressure and temperature

    See Also
    --------
    parcel_profile

    """
    ideal_profile = parcel_profile(pressure, temperature[0], dewpt[0]).to('degC')

    # The parcel profile and data have the same first data point, so we ignore
    # that point to get the real first intersection for the LFC calculation.
    x, y = find_intersections(pressure[1:], ideal_profile[1:], temperature[1:],
                              direction='increasing')
    # Two possible cases here: LFC = LCL, or LFC doesn't exist
    if len(x) == 0:
        if np.any(ideal_profile > temperature):
            # LFC = LCL
            x, y = lcl(pressure[0], temperature[0], dewpt[0])
            return x, y
        # LFC doesn't exist
        else:
            return np.nan * pressure.units, np.nan * temperature.units
    else:
        return x[0], y[0]


@exporter.export
@check_units('[pressure]', '[temperature]', '[temperature]')
def el(pressure, temperature, dewpt):
    r"""Calculate the equilibrium level.

    This works by finding the last intersection of the ideal parcel path and
    the measured environmental temperature. If there is one or fewer intersections, there is
    no equilibrium level.

    Parameters
    ----------
    pressure : `pint.Quantity`
        The atmospheric pressure
    temperature : `pint.Quantity`
        The temperature at the levels given by `pressure`
    dewpt : `pint.Quantity`
        The dew point at the levels given by `pressure`

    Returns
    -------
    `pint.Quantity, pint.Quantity`
        The EL pressure and temperature

    See Also
    --------
    parcel_profile

    """
    ideal_profile = parcel_profile(pressure, temperature[0], dewpt[0]).to('degC')
    x, y = find_intersections(pressure[1:], ideal_profile[1:], temperature[1:])

    # If there is only one intersection, there are two possibilities:
    # the dataset does not contain the EL, or the LFC = LCL.
    if len(x) <= 1:
        if (ideal_profile[-1] < temperature[-1]) and (len(x) == 1):
            # Profile top colder than environment with one
            # intersection, EL exists and LFC = LCL
            return x[-1], y[-1]
        else:
            # The EL does not exist, either due to incomplete data
            # or no intersection occurring.
            return np.nan * pressure.units, np.nan * temperature.units
    else:
        return x[-1], y[-1]


@exporter.export
@check_units('[pressure]', '[temperature]', '[temperature]')
def parcel_profile(pressure, temperature, dewpt):
    r"""Calculate the profile a parcel takes through the atmosphere.

    The parcel starts at `temperature`, and `dewpt`, lifted up
    dry adiabatically to the LCL, and then moist adiabatically from there.
    `pressure` specifies the pressure levels for the profile.

    Parameters
    ----------
    pressure : `pint.Quantity`
        The atmospheric pressure level(s) of interest. The first entry should be the starting
        point pressure.
    temperature : `pint.Quantity`
        The starting temperature
    dewpt : `pint.Quantity`
        The starting dew point

    Returns
    -------
    `pint.Quantity`
        The parcel temperatures at the specified pressure levels.

    See Also
    --------
    lcl, moist_lapse, dry_lapse

    """
    # Find the LCL
    l = lcl(pressure[0], temperature, dewpt)[0].to(pressure.units)

    # Find the dry adiabatic profile, *including* the LCL. We need >= the LCL in case the
    # LCL is included in the levels. It's slightly redundant in that case, but simplifies
    # the logic for removing it later.
    press_lower = concatenate((pressure[pressure >= l], l))
    t1 = dry_lapse(press_lower, temperature)

    # Find moist pseudo-adiabatic profile starting at the LCL
    press_upper = concatenate((l, pressure[pressure < l]))
    t2 = moist_lapse(press_upper, t1[-1]).to(t1.units)

    # Return LCL *without* the LCL point
    return concatenate((t1[:-1], t2[1:]))


@exporter.export
@check_units('[pressure]', '[dimensionless]')
def vapor_pressure(pressure, mixing):
    r"""Calculate water vapor (partial) pressure.

    Given total `pressure` and water vapor `mixing` ratio, calculates the
    partial pressure of water vapor.

    Parameters
    ----------
    pressure : `pint.Quantity`
        total atmospheric pressure
    mixing : `pint.Quantity`
        dimensionless mass mixing ratio

    Returns
    -------
    `pint.Quantity`
        The ambient water vapor (partial) pressure in the same units as
        `pressure`.

    Notes
    -----
    This function is a straightforward implementation of the equation given in many places,
    such as [Hobbs1977]_ pg.71:

    .. math:: e = p \frac{r}{r + \epsilon}

    See Also
    --------
    saturation_vapor_pressure, dewpoint

    """
    return pressure * mixing / (epsilon + mixing)


@exporter.export
@check_units('[temperature]')
def saturation_vapor_pressure(temperature):
    r"""Calculate the saturation water vapor (partial) pressure.

    Parameters
    ----------
    temperature : `pint.Quantity`
        The temperature

    Returns
    -------
    `pint.Quantity`
        The saturation water vapor (partial) pressure

    See Also
    --------
    vapor_pressure, dewpoint

    Notes
    -----
    Instead of temperature, dewpoint may be used in order to calculate
    the actual (ambient) water vapor (partial) pressure.

    The formula used is that from [Bolton1980]_ for T in degrees Celsius:

    .. math:: 6.112 e^\frac{17.67T}{T + 243.5}

    """
    # Converted from original in terms of C to use kelvin. Using raw absolute values of C in
    # a formula plays havoc with units support.
    return sat_pressure_0c * np.exp(17.67 * (temperature - 273.15 * units.kelvin) /
                                    (temperature - 29.65 * units.kelvin))


@exporter.export
@check_units('[temperature]', '[dimensionless]')
def dewpoint_rh(temperature, rh):
    r"""Calculate the ambient dewpoint given air temperature and relative humidity.

    Parameters
    ----------
    temperature : `pint.Quantity`
        Air temperature
    rh : `pint.Quantity`
        Relative humidity expressed as a ratio in the range [0, 1]

    Returns
    -------
    `pint.Quantity`
        The dew point temperature

    See Also
    --------
    dewpoint, saturation_vapor_pressure

    """
    return dewpoint(rh * saturation_vapor_pressure(temperature))


@exporter.export
@check_units('[pressure]')
def dewpoint(e):
    r"""Calculate the ambient dewpoint given the vapor pressure.

    Parameters
    ----------
    e : `pint.Quantity`
        Water vapor partial pressure

    Returns
    -------
    `pint.Quantity`
        Dew point temperature

    See Also
    --------
    dewpoint_rh, saturation_vapor_pressure, vapor_pressure

    Notes
    -----
    This function inverts the [Bolton1980]_ formula for saturation vapor
    pressure to instead calculate the temperature. This yield the following
    formula for dewpoint in degrees Celsius:

    .. math:: T = \frac{243.5 log(e / 6.112)}{17.67 - log(e / 6.112)}

    """
    val = np.log(e / sat_pressure_0c)
    return 0. * units.degC + 243.5 * units.delta_degC * val / (17.67 - val)


@exporter.export
@check_units('[pressure]', '[pressure]', '[dimensionless]')
def mixing_ratio(part_press, tot_press, molecular_weight_ratio=epsilon):
    r"""Calculate the mixing ratio of a gas.

    This calculates mixing ratio given its partial pressure and the total pressure of
    the air. There are no required units for the input arrays, other than that
    they have the same units.

    Parameters
    ----------
    part_press : `pint.Quantity`
        Partial pressure of the constituent gas
    tot_press : `pint.Quantity`
        Total air pressure
    molecular_weight_ratio : `pint.Quantity` or float, optional
        The ratio of the molecular weight of the constituent gas to that assumed
        for air. Defaults to the ratio for water vapor to dry air
        (:math:`\epsilon\approx0.622`).

    Returns
    -------
    `pint.Quantity`
        The (mass) mixing ratio, dimensionless (e.g. Kg/Kg or g/g)

    Notes
    -----
    This function is a straightforward implementation of the equation given in many places,
    such as [Hobbs1977]_ pg.73:

    .. math:: r = \epsilon \frac{e}{p - e}

    See Also
    --------
    saturation_mixing_ratio, vapor_pressure

    """
    return molecular_weight_ratio * part_press / (tot_press - part_press)


@exporter.export
@check_units('[pressure]', '[temperature]')
def saturation_mixing_ratio(tot_press, temperature):
    r"""Calculate the saturation mixing ratio of water vapor.

    This calculation is given total pressure and the temperature. The implementation
    uses the formula outlined in [Hobbs1977]_ pg.73.

    Parameters
    ----------
    tot_press: `pint.Quantity`
        Total atmospheric pressure
    temperature: `pint.Quantity`
        The temperature

    Returns
    -------
    `pint.Quantity`
        The saturation mixing ratio, dimensionless

    """
    return mixing_ratio(saturation_vapor_pressure(temperature), tot_press)


@exporter.export
@check_units('[pressure]', '[temperature]')
def equivalent_potential_temperature(pressure, temperature):
    r"""Calculate equivalent potential temperature.

    This calculation must be given an air parcel's pressure and temperature.
    The implementation uses the formula outlined in [Hobbs1977]_ pg.78-79.

    Parameters
    ----------
    pressure: `pint.Quantity`
        Total atmospheric pressure
    temperature: `pint.Quantity`
        The temperature

    Returns
    -------
    `pint.Quantity`
        The corresponding equivalent potential temperature of the parcel

    Notes
    -----
    .. math:: \Theta_e = \Theta e^\frac{L_v r_s}{C_{pd} T}

    """
    pottemp = potential_temperature(pressure, temperature)
    smixr = saturation_mixing_ratio(pressure, temperature)
    return pottemp * np.exp(Lv * smixr / (Cp_d * temperature))


@exporter.export
@check_units('[temperature]', '[dimensionless]', '[dimensionless]')
def virtual_temperature(temperature, mixing, molecular_weight_ratio=epsilon):
    r"""Calculate virtual temperature.

    This calculation must be given an air parcel's temperature and mixing ratio.
    The implementation uses the formula outlined in [Hobbs2006]_ pg.80.

    Parameters
    ----------
    temperature: `pint.Quantity`
        The temperature
    mixing : `pint.Quantity`
        dimensionless mass mixing ratio
    molecular_weight_ratio : `pint.Quantity` or float, optional
        The ratio of the molecular weight of the constituent gas to that assumed
        for air. Defaults to the ratio for water vapor to dry air
        (:math:`\epsilon\approx0.622`).

    Returns
    -------
    `pint.Quantity`
        The corresponding virtual temperature of the parcel

    Notes
    -----
    .. math:: T_v = T \frac{\text{w} + \epsilon}{\epsilon\,(1 + \text{w})}

    """
    return temperature * ((mixing + molecular_weight_ratio) /
                          (molecular_weight_ratio * (1 + mixing)))


@exporter.export
@check_units('[pressure]', '[temperature]', '[dimensionless]', '[dimensionless]')
def virtual_potential_temperature(pressure, temperature, mixing,
                                  molecular_weight_ratio=epsilon):
    r"""Calculate virtual potential temperature.

    This calculation must be given an air parcel's pressure, temperature, and mixing ratio.
    The implementation uses the formula outlined in [Markowski2010]_ pg.13.

    Parameters
    ----------
    pressure: `pint.Quantity`
        Total atmospheric pressure
    temperature: `pint.Quantity`
        The temperature
    mixing : `pint.Quantity`
        dimensionless mass mixing ratio
    molecular_weight_ratio : `pint.Quantity` or float, optional
        The ratio of the molecular weight of the constituent gas to that assumed
        for air. Defaults to the ratio for water vapor to dry air
        (:math:`\epsilon\approx0.622`).

    Returns
    -------
    `pint.Quantity`
        The corresponding virtual potential temperature of the parcel

    Notes
    -----
    .. math:: \Theta_v = \Theta \frac{\text{w} + \epsilon}{\epsilon\,(1 + \text{w})}

    """
    pottemp = potential_temperature(pressure, temperature)
    return virtual_temperature(pottemp, mixing, molecular_weight_ratio)


@exporter.export
@check_units('[pressure]', '[temperature]', '[dimensionless]', '[dimensionless]')
def density(pressure, temperature, mixing, molecular_weight_ratio=epsilon):
    r"""Calculate density.

    This calculation must be given an air parcel's pressure, temperature, and mixing ratio.
    The implementation uses the formula outlined in [Hobbs2006]_ pg.67.

    Parameters
    ----------
    temperature: `pint.Quantity`
        The temperature
    pressure: `pint.Quantity`
        Total atmospheric pressure
    mixing : `pint.Quantity`
        dimensionless mass mixing ratio
    molecular_weight_ratio : `pint.Quantity` or float, optional
        The ratio of the molecular weight of the constituent gas to that assumed
        for air. Defaults to the ratio for water vapor to dry air
        (:math:`\epsilon\approx0.622`).

    Returns
    -------
    `pint.Quantity`
        The corresponding density of the parcel

    Notes
    -----
    .. math:: \rho = \frac{p}{R_dT_v}

    """
    virttemp = virtual_temperature(temperature, mixing, molecular_weight_ratio)
    return (pressure / (Rd * virttemp)).to(units.kilogram / units.meter ** 3)


@exporter.export
@check_units('[temperature]', '[temperature]', '[pressure]')
def relative_humidity_wet_psychrometric(dry_bulb_temperature, web_bulb_temperature,
                                        pressure, **kwargs):
    r"""Calculate the relative humidity with wet bulb and dry bulb temperatures.

    This uses a psychrometric relationship as outlined in [WMO8-2008]_, with
    coefficients from [Fan1987]_.

    Parameters
    ----------
    dry_bulb_temperature: `pint.Quantity`
        Dry bulb temperature
    web_bulb_temperature: `pint.Quantity`
        Wet bulb temperature
    pressure: `pint.Quantity`
        Total atmospheric pressure

    Returns
    -------
    `pint.Quantity`
        Relative humidity

    Notes
    -----
    .. math:: RH = 100 \frac{e}{e_s}

    * :math:`RH` is relative humidity
    * :math:`e` is vapor pressure from the wet psychrometric calculation
    * :math:`e_s` is the saturation vapor pressure

    See Also
    --------
    psychrometric_vapor_pressure_wet, saturation_vapor_pressure

    """
    return (100 * units.percent * psychrometric_vapor_pressure_wet(dry_bulb_temperature,
            web_bulb_temperature, pressure, **kwargs) /
            saturation_vapor_pressure(dry_bulb_temperature))


@exporter.export
@check_units('[temperature]', '[temperature]', '[pressure]')
def psychrometric_vapor_pressure_wet(dry_bulb_temperature, wet_bulb_temperature, pressure,
                                     psychrometer_coefficient=6.21e-4 / units.kelvin):
    r"""Calculate the vapor pressure with wet bulb and dry bulb temperatures.

    This uses a psychrometric relationship as outlined in [WMO8-2008]_, with
    coefficients from [Fan1987]_.

    Parameters
    ----------
    dry_bulb_temperature: `pint.Quantity`
        Dry bulb temperature
    wet_bulb_temperature: `pint.Quantity`
        Wet bulb temperature
    pressure: `pint.Quantity`
        Total atmospheric pressure
    psychrometer_coefficient: `pint.Quantity`
        Psychrometer coefficient

    Returns
    -------
    `pint.Quantity`
        Vapor pressure

    Notes
    -----
    .. math:: e' = e'_w(T_w) - A p (T - T_w)

    * :math:`e'` is vapor pressure
    * :math:`e'_w(T_w)` is the saturation vapor pressure with respect to water at temperature
      :math:`T_w`
    * :math:`p` is the pressure of the wet bulb
    * :math:`T` is the temperature of the dry bulb
    * :math:`T_w` is the temperature of the wet bulb
    * :math:`A` is the psychrometer coefficient

    Psychrometer coefficient depends on the specific instrument being used and the ventilation
    of the instrument.

    See Also
    --------
    saturation_vapor_pressure

    """
    return (saturation_vapor_pressure(wet_bulb_temperature) - psychrometer_coefficient *
            pressure * (dry_bulb_temperature - wet_bulb_temperature).to('kelvin'))


@exporter.export
@check_units('[dimensionless]', '[temperature]', '[pressure]')
def relative_humidity_from_mixing_ratio(mixing_ratio, temperature, pressure):
    r"""Calculate the relative humidity from mixing ratio, temperature, and pressure.

    Parameters
    ----------
    mixing_ratio: `pint.Quantity`
        Dimensionless mass mixing ratio
    temperature: `pint.Quantity`
        Air temperature
    pressure: `pint.Quantity`
        Total atmospheric pressure

    Returns
    -------
    `pint.Quantity`
        Relative humidity

    Notes
    -----
    Formula from [Hobbs1977]_ pg. 74.

    .. math:: RH = 100 \frac{w}{w_s}

    * :math:`RH` is relative humidity
    * :math:`w` is mxing ratio
    * :math:`w_s` is the saturation mixing ratio

    See Also
    --------
    saturation_mixing_ratio

    """
    return (100 * units.percent *
            mixing_ratio / saturation_mixing_ratio(pressure, temperature))


@exporter.export
@check_units('[dimensionless]')
def mixing_ratio_from_specific_humidity(specific_humidity):
    r"""Calculate the mixing ratio from specific humidity.

    Parameters
    ----------
    specific_humidity: `pint.Quantity`
        Specific humidity of air

    Returns
    -------
    `pint.Quantity`
        Mixing ratio

    Notes
    -----
    Formula from [Salby1996]_ pg. 118.

    .. math:: w = \frac{q}{1-q}

    * :math:`w` is mxing ratio
    * :math:`q` is the specific humidity

    See Also
    --------
    mixing_ratio

    """
    return specific_humidity / (1 - specific_humidity)


@exporter.export
@check_units('[dimensionless]', '[temperature]', '[pressure]')
def relative_humidity_from_specific_humidity(specific_humidity, temperature, pressure):
    r"""Calculate the relative humidity from specific humidity, temperature, and pressure.

    Parameters
    ----------
    specific_humidity: `pint.Quantity`
        Specific humidity of air
    temperature: `pint.Quantity`
        Air temperature
    pressure: `pint.Quantity`
        Total atmospheric pressure

    Returns
    -------
    `pint.Quantity`
        Relative humidity

    Notes
    -----
    Formula from [Hobbs1977]_ pg. 74. and [Salby1996]_ pg. 118.

    .. math:: RH = 100 \frac{q}{(1-q)w_s}

    * :math:`RH` is relative humidity
    * :math:`q` is specific humidity
    * :math:`w_s` is the saturation mixing ratio

    See Also
    --------
    relative_humidity_from_mixing_ratio

    """
    return (100 * units.percent *
            mixing_ratio_from_specific_humidity(specific_humidity) /
            saturation_mixing_ratio(pressure, temperature))


@exporter.export
@check_units('[pressure]', '[temperature]', '[temperature]', '[temperature]')
def cape_cin(pressure, temperature, dewpt, parcel_profile):
    r"""Calculate CAPE and CIN.

    Calculate the convective available potential energy (CAPE) and convective inhibition (CIN)
    of a given upper air profile and parcel path. CIN is integrated between the surface and
    LFC, CAPE is integrated between the LFC and EL (or top of sounding). Intersection points of
    the measured temperature profile and parcel profile are linearly interpolated.

    Parameters
    ----------
    pressure : `pint.Quantity`
        The atmospheric pressure level(s) of interest. The first entry should be the starting
        point pressure.
    temperature : `pint.Quantity`
        The starting temperature
    dewpt : `pint.Quantity`
        The starting dew point
    parcel_profile : `pint.Quantity`
        The temperature profile of the parcel

    Returns
    -------
    `pint.Quantity`
        Convective available potential energy (CAPE).
    `pint.Quantity`
        Convective inhibition (CIN).

    Notes
    -----
    Formula adopted from [Hobbs1977]_.

    .. math:: \text{CAPE} = -R_d \int_{LFC}^{EL} (T_{parcel} - T_{env}) d\text{ln}(p)

    .. math:: \text{CIN} = -R_d \int_{SFC}^{LFC} (T_{parcel} - T_{env}) d\text{ln}(p)


    * :math:`CAPE` Convective available potential energy
    * :math:`CIN` Convective inhibition
    * :math:`LFC` Pressure of the level of free convection
    * :math:`EL` Pressure of the equilibrium level
    * :math:`SFC` Level of the surface or beginning of parcel path
    * :math:`R_d` Gas constant
    * :math:`g` Gravitational acceleration
    * :math:`T_{parcel}` Parcel temperature
    * :math:`T_{env}` Environment temperature
    * :math:`p` Atmospheric pressure

    See Also
    --------
    lfc, el

    """
    # Calculate LFC limit of integration
    lfc_pressure = lfc(pressure, temperature, dewpt)[0]

    # If there is no LFC, no need to proceed.
    if np.isnan(lfc_pressure):
        return 0 * units('J/kg'), 0 * units('J/kg')
    else:
        lfc_pressure = lfc_pressure.magnitude

    # Calculate the EL limit of integration
    el_pressure = el(pressure, temperature, dewpt)[0]

    # No EL and we use the top reading of the sounding.
    if np.isnan(el_pressure):
        el_pressure = pressure[-1].magnitude
    else:
        el_pressure = el_pressure.magnitude

    # Difference between the parcel path and measured temperature profiles
    y = (parcel_profile - temperature).to(units.degK)

    # Estimate zero crossings
    x, y = _find_append_zero_crossings(np.copy(pressure), y)

    # CAPE (temperature parcel < temperature environment)
    # Only use data between the LFC and EL for calculation
    p_mask = (x <= lfc_pressure) & (x >= el_pressure)
    x_clipped = x[p_mask]
    y_clipped = y[p_mask]

    y_clipped[y_clipped <= 0 * units.degK] = 0 * units.degK
    cape = (Rd * (np.trapz(y_clipped, np.log(x_clipped)) * units.degK)).to(units('J/kg'))

    # CIN (temperature parcel < temperature environment)
    # Only use data between the surface and LFC for calculation
    p_mask = (x >= lfc_pressure)
    x_clipped = x[p_mask]
    y_clipped = y[p_mask]

    y_clipped[y_clipped >= 0 * units.degK] = 0 * units.degK
    cin = (Rd * (np.trapz(y_clipped, np.log(x_clipped)) * units.degK)).to(units('J/kg'))

    return cape, cin


def _find_append_zero_crossings(x, y):
    r"""
    Find and interpolate zero crossings.

    Estimate the zero crossings of an x,y series and add estimated crossings to series,
    returning a sorted array with no duplicate values.

    Parameters
    ----------
    x : `pint.Quantity`
        x values of data
    y : `pint.Quantity`
        y values of data

    Returns
    -------
    x : `pint.Quantity`
        x values of data
    y : `pint.Quantity`
        y values of data

    """
    # Find and append crossings to the data
    crossings = find_intersections(x[1:], y[1:], np.zeros_like(y[1:]) * y.units)
    x = concatenate((x, crossings[0]))
    y = concatenate((y, crossings[1]))

    # Resort so that data are in order
    sort_idx = np.argsort(x)
    x = x[sort_idx]
    y = y[sort_idx]

    # Remove duplicate data points if there are any
    keep_idx = np.ediff1d(x, to_end=[1]) > 0
    x = x[keep_idx]
    y = y[keep_idx]
    return x, y
