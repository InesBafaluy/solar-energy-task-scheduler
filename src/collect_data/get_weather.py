# Goal : Get forecast or historical weather from Open-Meteo.

from datetime import date, timedelta

import pandas as pd
import requests
import config


def get_weather_forecast(
    params=None,
    variables=None,
    interval="1h",
    forecast_hours=None,
    rename_for_pvlib=False,
):
    """Get upcoming weather, starting around the current time."""

    # GET DEFAULT VALUES
    if variables is None:
        variables = config.WEATHER_VARIABLES

    if forecast_hours is None:
        forecast_hours = config.FORECAST_HOURS

    if isinstance(variables, str) or not variables:
        raise ValueError("variables must be a non-empty list.")

    if (
        isinstance(forecast_hours, bool)
        or not isinstance(forecast_hours, int)
        or forecast_hours <= 0
    ):
        raise ValueError("forecast_hours must be a positive integer.")

    # SELECT INTERVAL
    if interval == "1h":
        data_key = "hourly"
        length_key = "forecast_hours"
        records = forecast_hours
    elif interval == "15min":
        data_key = "minutely_15"
        length_key = "forecast_minutely_15"
        records = forecast_hours * 4
    else:
        raise ValueError("Choose '1h' or '15min'.")

    # DEFINE PARAMETERS
    request_params = {
        "latitude": config.LATITUDE,
        "longitude": config.LONGITUDE,
        "elevation": config.ALTITUDE_M,
    }

    if params is not None:
        request_params.update(params)

    # Use function arguments to control dates and variables.
    for key in [
        "hourly", "minutely_15", "daily", "current",
        "forecast_days", "forecast_hours", "forecast_minutely_15",
        "past_days", "past_hours", "past_minutely_15",
        "start_date", "end_date", "start_hour", "end_hour",
        "start_minutely_15", "end_minutely_15",
    ]:
        request_params.pop(key, None)

    request_params.update({
        data_key: ",".join(variables),
        length_key: records,
        "timezone": "UTC",
        "timeformat": "iso8601",
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
    })

    # GET WEATHER
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params=request_params,
        timeout=60,
    )
    response.raise_for_status()

    data = response.json()

    # CREATE THE DATAFRAME
    weather = pd.DataFrame(data[data_key])

    if weather.empty:
        raise ValueError("No weather records were returned.")

    weather["time"] = pd.to_datetime(weather["time"], utc=True)
    weather = weather.set_index("time")[variables].sort_index()

    # RENAME FOR PVLIB IF REQUESTED
    if rename_for_pvlib:
        weather = weather.rename(columns={
            "shortwave_radiation": "ghi",
            "direct_normal_irradiance": "dni",
            "diffuse_radiation": "dhi",
            "temperature_2m": "temp_air",
            "wind_speed_10m": "wind_speed",
        })

    return weather


def get_weather_history(
    start_date=None,
    end_date=None,
    days=None,
    params=None,
    variables=None,
    rename_for_pvlib=False,
):
    """
    Get hourly historical weather.

    Use start_date/end_date OR a list of days.
    Dates are inclusive and refer to UTC calendar days.
    """

    # GET DEFAULT VARIABLES
    if variables is None:
        variables = config.WEATHER_VARIABLES

    if isinstance(variables, str) or not variables:
        raise ValueError("variables must be a non-empty list.")

    # SELECT DATES
    if days is not None:
        if start_date is not None or end_date is not None:
            raise ValueError("Use a period OR a list of days.")

        if isinstance(days, str) or not days:
            raise ValueError("days must be a non-empty list.")

        selected_days = sorted({
            date.fromisoformat(day) for day in days
        })

        first_day = selected_days[0]
        last_day = selected_days[-1]

    else:
        if start_date is None or end_date is None:
            raise ValueError("Provide start_date and end_date.")

        first_day = date.fromisoformat(start_date)
        last_day = date.fromisoformat(end_date)

        if first_day > last_day:
            raise ValueError("start_date must be before end_date.")

        selected_days = None

    # DOWNLOAD IN MONTH-SIZED PARTS
    # This also works for a long training period.
    frames = []
    current_day = first_day

    while current_day <= last_day:
        next_month = (
            current_day.replace(day=28) + timedelta(days=4)
        ).replace(day=1)

        period_end = min(
            last_day,
            next_month - timedelta(days=1),
        )

        # Skip months with no requested days.
        if selected_days is not None and not any(
            current_day <= day <= period_end
            for day in selected_days
        ):
            current_day = next_month
            continue

        request_params = {
            "latitude": config.LATITUDE,
            "longitude": config.LONGITUDE,
            "elevation": config.ALTITUDE_M,
        }

        if params is not None:
            allowed = {
                "latitude", "longitude", "elevation",
                "models", "cell_selection",
            }

            if set(params) - allowed:
                raise ValueError(
                    f"Allowed historical parameters: {allowed}"
                )

            request_params.update(params)

        request_params.update({
            "start_date": current_day.isoformat(),
            "end_date": period_end.isoformat(),
            "hourly": ",".join(variables),
            "timezone": "UTC",
            "timeformat": "iso8601",
            "temperature_unit": "celsius",
            "wind_speed_unit": "ms",
            "precipitation_unit": "mm",
        })

        # GET WEATHER
        response = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params=request_params,
            timeout=120,
        )
        response.raise_for_status()

        frame = pd.DataFrame(response.json()["hourly"])

        if frame.empty:
            raise ValueError("No historical records were returned.")

        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frame = frame.set_index("time")[variables]

        frames.append(frame)
        current_day = next_month

    # COMBINE RESULTS
    weather = pd.concat(frames).sort_index()

    # Keep only the requested days when a list was provided.
    if selected_days is not None:
        weather = weather.loc[
            pd.Index(weather.index.date).isin(selected_days)
        ]

    # RENAME FOR PVLIB IF REQUESTED
    if rename_for_pvlib:
        weather = weather.rename(columns={
            "shortwave_radiation": "ghi",
            "direct_normal_irradiance": "dni",
            "diffuse_radiation": "dhi",
            "temperature_2m": "temp_air",
            "wind_speed_10m": "wind_speed",
        })

    return weather