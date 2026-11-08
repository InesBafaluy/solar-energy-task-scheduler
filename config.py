# config.py
# File storing all the global parameters used through the program files.


# SHELTER
SHELTER_NAME = "Refugi de l'Illa"
LATITUDE = 42.294134
LONGITUDE = 1.39229
ALTITUDE_M = 2480
TIMEZONE = "Europe/Andorra"


# SOLAR INSTALLATION
INSTALLED_CAPACITY_KWP = 62.5  # Deye Cloud - Plant Info - Capacity

# TEMPORARY ASSUMPTIONS - MODIFY WITH THE REAL VALUES
PANEL_TILT_DEG = 35
PANEL_AZIMUTH_DEG = 180  # Assumed south-facing
# Convention: 0=N, 90=E, 180=S, 270=W


# PANEL MODEL AND WIRING
# EXAMPLE VALUES - MODIFY WITH THE REAL VALUES
# These describe one example panel, not the 62.5 kWp installation.
MODULE_DATABASE_NAME = "Canadian_Solar_CS5P_220M___2009_"

MODULES_PER_STRING = 1
STRINGS_PER_INVERTER = 1

# Assumes an open mounting structure - MODIFY WITH THE REAL VALUE
TEMPERATURE_MODEL = "open_rack_glass_glass"


# INVERTER
INVERTER_AC_LIMIT_KW = 20  # Deye Cloud - Devices - Rated Power
NUMBER_OF_INVERTERS = 1

# EXAMPLE MODEL - MODIFY WITH THE REAL MODEL
# This ABB microinverter does not represent the 20 kW Deye inverter.
INVERTER_DATABASE_NAME = "ABB__MICRO_0_25_I_OUTD_US_208__208V_"


# BATTERY
BATTERY_TYPE = "lithium"

# DEMO ASSUMPTION ONLY - MODIFY WITH THE REAL USABLE CAPACITY
# This value cannot be inferred from the available DeyeCloud readings.
BATTERY_CAPACITY_KWH = 100


# WEATHER
WEATHER_PROVIDER = "open_meteo"
FORECAST_HOURS = 48

WEATHER_VARIABLES = [
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "temperature_2m",
    "wind_speed_10m",
    "snowfall",
    "snow_depth",
]


# DATA FILES
RAW_DATA_PATH = "data/private/energy.csv"
OUTPUT_DIR = "outputs"


# DATA INTERVALS AND PREDICTION TARGET
SOURCE_INTERVAL_MINUTES = 5
PREDICTION_INTERVAL = "1h"

# TEMPORARY CHOICE - VERIFY WITH THE REAL EXPORT DEFINITIONS
# Confirm that PV measures solar generation and check its units.
SOLAR_SOURCE_COLUMN = "PV"

TARGET_COLUMN = "generation_kwh"  # Energy per hourly interval


# COMPARISON BETWEEN PREDICTIVE METHODS
# Use the same chronological periods for all four methods.
# PROPOSED DATES - MODIFY AFTER CHECKING REAL DATA COVERAGE
# Suggested convention: training ends at TRAIN_END,
# validation follows until VALIDATION_END, then testing until TEST_END.
TRAIN_END = "2025-12-31"
VALIDATION_END = "2026-02-28"
TEST_END = "2026-05-20"

RANDOM_STATE = 42