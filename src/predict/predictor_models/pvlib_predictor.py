# Goal : Estimate solar production from weather data.
# The function returns results without downloading or plotting anything.

import pandas as pd
import pvlib
import config


def predict_pvlib(weather, interval_minutes=60):
    """
    Goal : Predict solar production from weather using pvlib.
    Input : Weather DataFrame, with 15-minute or hourly intervals.
    Output : Power (kW), energy and inverter standby consumption (kWh).
    Config : Installation settings from config.py.
    Structure : Checks → Get data → Calculate production → Return results.
    """

    ### GENERAL CHECKS : weather and installation

    # CHECK WEATHER
    required_columns = [
        "ghi",
        "dni",
        "dhi",
        "temp_air",
        "wind_speed",
    ]

    missing_columns = set(required_columns) - set(weather.columns)

    # ERROR MESSAGES in case of missing data
    if missing_columns:
        raise ValueError(f"Missing weather columns: {missing_columns}")

    if weather.empty:
        raise ValueError("The weather data is empty.")

    if not isinstance(weather.index, pd.DatetimeIndex):
        raise ValueError("Weather must have a DatetimeIndex.")

    if weather.index.tz is None:
        raise ValueError("Weather timestamps must include a timezone.")

    if not weather.index.is_unique:
        raise ValueError("There are duplicate weather timestamps.")

    weather = weather.sort_index().copy()

    if weather[required_columns].isna().any().any():
        raise ValueError("There are missing weather values.")

    # CHECK INTERVAL
    # We need the interval to calculate energy correctly.
    if interval_minutes not in (15, 60):
        raise ValueError("Choose interval_minutes=15 or 60.")

    interval = pd.Timedelta(minutes=interval_minutes)
    intervals = weather.index.to_series().diff().dropna()

    # Allow gaps between separately requested historical days.
    # Each gap must still be a multiple of the chosen interval.
    if not (intervals % interval == pd.Timedelta(0)).all():
        raise ValueError("Weather timestamps do not match the interval.")

    # CHECK INSTALLATION
    required_settings = [
        config.LATITUDE,
        config.LONGITUDE,
        config.ALTITUDE_M,
        config.PANEL_TILT_DEG,
        config.PANEL_AZIMUTH_DEG,
        config.MODULE_DATABASE_NAME,
        config.INVERTER_DATABASE_NAME,
        config.MODULES_PER_STRING,
        config.STRINGS_PER_INVERTER,
        config.NUMBER_OF_INVERTERS,
        config.TEMPERATURE_MODEL,
    ]

    if any(value is None for value in required_settings):
        raise ValueError("Fill in the missing installation settings.")




    ### GET DATA : panel, inverter, pressure, sun position, air mass...

    # GET PANEL AND INVERTER INFO
    modules = pvlib.pvsystem.retrieve_sam("SandiaMod")
    inverters = pvlib.pvsystem.retrieve_sam("cecinverter")

    module = modules[config.MODULE_DATABASE_NAME]
    inverter = inverters[config.INVERTER_DATABASE_NAME]

    temperature_parameters = (
        pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]
        [config.TEMPERATURE_MODEL]
    )

    # GET PRESSURE
    # Use weather pressure if available.
    # Otherwise estimate it from the altitude.
    if "pressure" in weather.columns:
        pressure = weather["pressure"]

        if pressure.isna().any() or (pressure <= 0).any():
            raise ValueError("Invalid pressure values.")
    else:
        pressure = pvlib.atmosphere.alt2pres(config.ALTITUDE_M)

    # GET SUN POSITION
    # Radiation represents the preceding interval.
    # Use the middle of that interval for the solar geometry.
    calculation_times = weather.index - interval / 2

    # Keep the original index so all calculated series stay aligned.
    solpos = pvlib.solarposition.get_solarposition(
        time=calculation_times,
        latitude=config.LATITUDE,
        longitude=config.LONGITUDE,
        altitude=config.ALTITUDE_M,
        temperature=weather["temp_air"].to_numpy(),
        pressure=(
            pressure.to_numpy()
            if isinstance(pressure, pd.Series)
            else pressure
        ),
    )

    solpos.index = weather.index

    # GET AIR MASS
    relative_airmass = pvlib.atmosphere.get_relative_airmass(
        solpos["apparent_zenith"]
    )

    absolute_airmass = pvlib.atmosphere.get_absolute_airmass(
        relative_airmass,
        pressure,
    )

    # GET ANGLE OF INCIDENCE
    aoi = pvlib.irradiance.aoi(
        config.PANEL_TILT_DEG,
        config.PANEL_AZIMUTH_DEG,
        solpos["apparent_zenith"],
        solpos["azimuth"],
    )

    # GET RADIATION REACHING THE PANELS
    dni_extra = pvlib.irradiance.get_extra_radiation(
        calculation_times
    )
    dni_extra.index = weather.index

    irradiance = pvlib.irradiance.get_total_irradiance(
        surface_tilt=config.PANEL_TILT_DEG,
        surface_azimuth=config.PANEL_AZIMUTH_DEG,
        solar_zenith=solpos["apparent_zenith"],
        solar_azimuth=solpos["azimuth"],
        dni=weather["dni"],
        ghi=weather["ghi"],
        dhi=weather["dhi"],
        dni_extra=dni_extra,
        model="haydavies",
    )

    # GET PANEL TEMPERATURE
    cell_temperature = pvlib.temperature.sapm_cell(
        irradiance["poa_global"],
        weather["temp_air"],
        weather["wind_speed"],
        **temperature_parameters,
    )

    # GET EFFECTIVE RADIATION
    effective_irradiance = (
        pvlib.pvsystem.sapm_effective_irradiance(
            irradiance["poa_direct"],
            irradiance["poa_diffuse"],
            absolute_airmass,
            aoi,
            module,
        )
    )

    # GET DC ELECTRICITY FROM ONE PANEL
    dc = pvlib.pvsystem.sapm(
        effective_irradiance,
        cell_temperature,
        module,
    )




    ### PREPARE DATA : scale, convert to right units of measure

    # IMPORTANT
    # Production is scaled using the module model and string counts.
    # INSTALLED_CAPACITY_KWP is not used by this calculation.
    # Example equipment therefore gives example-system results,
    # not the actual shelter's production.

    # SCALE TO THE CONFIGURED INSTALLATION
    # Assumes identical panels and identical inverter arrangements.
    voltage = dc["v_mp"] * config.MODULES_PER_STRING

    power = (
        dc["p_mp"]
        * config.MODULES_PER_STRING
        * config.STRINGS_PER_INVERTER
    )

    # CONVERT DC TO AC
    ac = pvlib.inverter.sandia(voltage, power, inverter)
    ac = ac * config.NUMBER_OF_INVERTERS

    # CHECK THE OUTPUT
    # Only replace missing values when there is no input sunlight.
    has_sunlight = weather["ghi"] > 0

    if ac.loc[has_sunlight].isna().any():
        raise ValueError("Missing predictions during sunlight hours.")

    ac = ac.copy()
    ac.loc[~has_sunlight] = ac.loc[~has_sunlight].fillna(0)

    
    
    
    ### GET ENERGY
    # Separate generation from inverter standby consumption.
    generation_w = ac.clip(lower=0)
    standby_w = -ac.clip(upper=0)

    # CONVERT THE INTERVAL INTO HOURS
    # 60 minutes = 1 hour; 15 minutes = 0.25 hours.
    interval_hours = interval_minutes / 60



    ### RETURN RESULTS
    # Keep the original interval-ending timestamps.
    results = pd.DataFrame({
        "generation_kw": generation_w / 1000,
        "generation_kwh": generation_w * interval_hours / 1000,
        "inverter_standby_kwh": standby_w * interval_hours / 1000,
    })

    results.index = results.index.tz_convert(config.TIMEZONE)
    results.index.name = "time"

    return results