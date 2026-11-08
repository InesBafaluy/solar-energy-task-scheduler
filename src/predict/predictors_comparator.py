# predictors_comparator
# Goal : Compare the 4 prediction models used to forecast the energy production

from pathlib import Path

import matplotlib.pyplot as plt

import config
from src.collect_data.get_weather import get_weather_forecast
from predict.predictor_models.pvlib_predictor import predict_pvlib


def main():



    ############## Prediction pvlib

    # GET WEATHER
    # Hourly data : specific for pvlib (the predictor expects it)
    # Renaming the variables to the names used by pvlib
    weather = get_weather_forecast(
        params={"wind_speed_unit": "ms"},
        variables=[
            "shortwave_radiation",
            "direct_normal_irradiance",
            "diffuse_radiation",
            "temperature_2m",
            "wind_speed_10m",
        ],
        interval="1h",
        forecast_hours=config.FORECAST_HOURS,
        rename_for_pvlib=True,
    )

    # PREDICT SOLAR PRODUCTION
    # Give the weather DataFrame directly to the predictor
    predictions = predict_pvlib(weather)

    # SAVE RESULTS
    output_folder = Path(config.OUTPUT_DIR)
    output_folder.mkdir(parents=True, exist_ok=True)

    weather.to_csv(output_folder / "weather_forecast.csv")
    predictions.to_csv(output_folder / "pvlib_prediction.csv")

    # SHOW TOTAL ENERGY
    # The total covers the returned forecast intervals.
    total_energy = predictions["generation_kwh"].sum()

    print(f"Shelter: {config.SHELTER_NAME}")
    print(f"Forecast intervals: {len(predictions)} hours")
    print(f"Predicted solar generation: {total_energy:.2f} kWh")
    print(predictions.head())

    # SHOW HOURLY POWER
    predictions["generation_kw"].plot(figsize=(12, 4))

    plt.title(f"{config.SHELTER_NAME} - solar production forecast")
    plt.xlabel(f"Time ({config.TIMEZONE})")
    plt.ylabel("Predicted power (kW)")
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()