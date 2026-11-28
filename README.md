=======
## Solar Energy Task Scheduler

A platform that helps the keeper of one mountain shelter decide when to run daily tasks based on expected electricity availability.

## Two goals

1. **Electricity forecast:** show predicted hourly solar production and consumption for the next day, using historical monitoring data and the OpenWeather forecast. Show the electricity available for tasks, accounting for essential needs and battery reserves where relevant.
2. **Daily agenda:** suggest when to run each task based on its priority, electricity needs, duration and allowed time window. Flag tasks that cannot fit.

Solar production and consumption are different: weather helps predict production, while past usage and planned tasks help estimate consumption. Check which measurements the monitoring website provides before building the prediction.

## Three inputs

- **OpenWeather:** weather forecast for the shelter.
- **Monitoring website:** historical solar production, electricity consumption and battery data, where available. We start with a downloaded CSV, then potentially an automate access later.
- **Task list:** task name, priority, duration, power/energy use, earliest start and deadline. We start with a CSV.

## Repository structure

```text
solar-energy-task-scheduler/
├── README.md              # Project goal and build steps
├── requirements.txt       # Python dependencies
├── .gitignore             # Exclude private data and credentials
├── app.py                 # Platform: electricity forecast and daily agenda
├── config.example.json    # Example location and electrical system limits
├── src/
│   ├── collect_data.py    # Read OpenWeather, monitoring history and tasks
│   │   └── get_weather.py # OpenWeather forecast
│   ├── predict.py         # Estimate next-day production and consumption
│   │   ├── predictor_models/          # pvlib, linear, random forest and gradient boosting models
│   │   └── predictors_comparator.py   # Compare the models against measurements
│   └── schedule.py        # Turn available electricity and tasks into an agenda
├── data/
│   ├── sample/            # Synthetic examples that can be shared on GitHub
│   └── private/           # Real monitoring exports and task data; excluded from Git
├── outputs/               # Results (CSV and charts)
└── LICENSE                # MIT license
```

## Build steps

1. **Collect a working example.** Obtain one shelter's monitoring export, equipment limits and task list; retrieve its weather forecast.
2. **Prepare the inputs.** Align dates, hours and units; identify missing data. Describe the columns each file needs.
3. **Build the electricity forecast.** Start with a simple prediction based on historical patterns and weather. Compare predictions with actual measurements on days not used to build the model.
4. **Build the agenda.** Place priority tasks in suitable time slots, respecting deadlines, simultaneous power limits and battery reserves. Avoid counting task consumption twice.
5. **Connect the platform.** Display an hourly electricity chart and a task agenda. Let the keeper review the proposed schedule and test it on a sunny day and a cloudy day.

## MVP

For one shelter and one forecast day, the platform can load the three inputs, display an electricity forecast and produce a feasible agenda with a clear list of any unscheduled tasks.

We start with manual data uploads and a simple interface. Automatic monitoring imports, multiple shelters and equipment control can come later.


## Building the agenda: choice of method

Building the agenda is a **constraint scheduling** problem. Several Python libraries are possible.
- **OR-Tools (CP-SAT)**: **selected**. Fast (a few seconds), handles cumulative power constraints well.
- **PuLP or Pyomo** (integer linear programming): easier to get started with, but less suited to a minute-level schedule.
- **python-mip or SciPy `milp`**: same principle as PuLP, with fewer dependencies.
- No library: sort the tasks by priority and place each one in the first feasible slot. A possible way to compare schedules, not implemented for now.


## Decisions made

- **Non-interruptible tasks**: a task is a continuous block of fixed duration.
- **No time slots**: the model works at the minute level and uses the exact duration of each task.
- **Battery**: the level is tracked hour by hour and never drops below the minimum reserve of **0.30 kWh** (value taken from the data). An end-of-day level target can be adjusted in `system.json`.
- **Task already done**: if a task has already been carried out at a given time, even outside the recommended time, that time is imposed (`forced_start` column) and the rest of the agenda is organised around it.
- **Objective**: schedule as much priority as possible, then use the generator as little as possible, then start tasks as early as possible. Tasks that do not fit are listed as **unscheduled**.

## Electrical system of the shelter

- **Photovoltaic installation**: 22 panels of 500 W, i.e. **11 kWp**
- **Generator** (recharges the batteries in bad weather): **20 kW nominal, derated to 12 kW at 2,500 m altitude**. The model uses 12 kW
- **Minimum battery reserve**: 0.30 kWh

## Known limitations of the model

- The battery charging power is not limited.
- Priorities count linearly: several small tasks can be scheduled ahead of one large high-priority task. To be adjusted if some tasks are essential.
- The generator fuel budget (`genset_max_kwh_per_day`) is a placeholder value to be confirmed.