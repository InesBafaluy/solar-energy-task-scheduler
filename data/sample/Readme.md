# Jeu de données fictif (data/sample)

Ces fichiers sont **fictifs mais réalistes** : ils servent à faire tourner le code de l'agenda en attendant le dataset définitif. Les valeurs inventées sont à remplacer par les vraies données du refuge.

Ils sont générés par `src/predict/predictor_models/random_datasample_simulation.py`. Les données sont identiques à chaque exécution (graine aléatoire fixe).

## Fichiers

- `system.json` : limites du système électrique
- `forecast_sunny.csv` : prévision horaire d'une journée ensoleillée
- `forecast_cloudy.csv` : prévision horaire d'une journée nuageuse
- `tasks.csv` : liste des tâches du gardien

## forecast_sunny.csv et forecast_cloudy.csv

Une ligne par heure, de 0 à 23

- `hour` : heure de la journée
- `cloud_cover_pct` : couverture nuageuse, en %
- `pv_kw` : production photovoltaïque prévue, en kW
- `base_load_kw` : consommation de base (frigo, éclairage, radio…), en kW

## tasks.csv

Une ligne par tâche.

- `task_id` : identifiant (T01, T02…)
- `name` : nom de la tâche
- `priority` : de 1 (peu important) à 5 (indispensable)
- `duration_min` : durée exacte, en minutes
- `power_kw` : puissance consommée pendant la tâche, en kW
- `earliest_start` : heure de début au plus tôt (`HH:MM`)
- `deadline` : heure de fin au plus tard (`HH:MM`)
- `forced_start` : facultatif, heure à laquelle la tâche a déjà été réalisée (`HH:MM`)

**Paramètres de `system.json`** : `pv_peak_kwc`, `genset_derated_kw`, `battery_reserve_kwh`, `battery_capacity_kwh`, `battery_max_discharge_kw`, `inverter_max_kw`, `end_of_day_soc_target_kwh`, `genset_max_kwh_per_day` et `initial_soc_kwh` (niveau de batterie au début de la journée, par scénario).

## system.json

Paramètres du système électrique :

- `pv_peak_kwc` : puissance crête photovoltaïque (22 panneaux de 500 W = 11 kWc).
- `genset_nominal_kw`, `genset_altitude_m`, `genset_derated_kw` : groupe électrogène (20 kW nominal, 12 kW à 2 500 m).
- `genset_max_kwh_per_day` : budget carburant journalier (valeur fictive).
- `battery_reserve_kwh` : réserve minimale de la batterie (0,30 kWh).
- `battery_capacity_kwh`, `battery_max_discharge_kw` : capacité et puissance de décharge (valeurs fictives).
- `inverter_max_kw` : puissance maximale de l'onduleur (valeur fictive).
- `end_of_day_soc_target_kwh` : niveau de batterie souhaité en fin de journée.
- `initial_soc_kwh` : niveau de batterie au début de la journée, par scénario (`sunny`, `cloudy`).

Résultats dans `outputs/` :

- `agenda_<scénario>.csv` : l'agenda proposé.
- `hourly_<scénario>.csv` : le bilan électrique heure par heure.
- `agenda_<scénario>.png` : un graphique.

