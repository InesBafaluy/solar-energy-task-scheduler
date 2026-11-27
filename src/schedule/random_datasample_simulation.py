"""
Ce code génère un jeu de données FICTIF (mais réaliste) pour tester la partie Agenda, en attendant le dataset définitif.

Fichiers produits dans data/sample/ :
  - system.json            : limites du système électrique (PV, batterie, groupe électrogène…)
  - forecast_sunny.csv     : prévision horaire d'une journée ensoleillée
  - forecast_cloudy.csv    : prévision horaire d'une journée nuageuse
  - tasks.csv              : liste des tâches du gardien

ATTENTION : tout ce qui est marqué « FICTIF » est inventé et à remplacer par les
vraies valeurs du refuge dès qu'on les a.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

# On remonte les dossiers jusqu'à trouver la racine du dépôt (là où se trouve le README.md)
HERE = Path(__file__).resolve().parent
ROOT = next((p for p in [HERE, *HERE.parents] if (p / "README.md").exists()), HERE)
OUT = ROOT / "data" / "sample"
OUT.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(42)  # graine fixe -> les données sont reproductibles

# ---------------------------------------------------------------------------
# 1. Système électrique
# ---------------------------------------------------------------------------

N_PANELS, PANEL_W = 22, 500                      # donnée réelle
PV_KWC = N_PANELS * PANEL_W / 1000               # = 11 kWc (donnée réelle)
assert PV_KWC == 11.0

system = {
    # --- Données réelles ---
    "pv_peak_kwc": PV_KWC,
    "genset_nominal_kw": 20.0,        # puissance nominale du groupe électrogène
    "genset_altitude_m": 2500,
    "genset_derated_kw": 12.0,        # puissance réellement disponible à 2 500 m
    "battery_reserve_kwh": 0.30,      # minimum issu de la data
    # --- Données FICTIVES (à remplacer) ---
    "battery_capacity_kwh": 30.0,
    "battery_max_discharge_kw": 8.0,
    "inverter_max_kw": 15.0,          # puissance totale max que l'onduleur peut délivrer
    "end_of_day_soc_target_kwh": 10.0,  # niveau de batterie souhaité en fin de journée
    "genset_max_kwh_per_day": 12.0,   # budget carburant : énergie max produite par le groupe sur la journée
    # En production, ce sera la dernière mesure du site de monitoring.
    "initial_soc_kwh": {"sunny": 16.0, "cloudy": 6.0},
}
(OUT / "system.json").write_text(json.dumps(system, indent=2, ensure_ascii=False), encoding="utf-8")

# ---------------------------------------------------------------------------
# 2. Prévisions horaires (production PV + consommation de base)
# ---------------------------------------------------------------------------
SUNRISE, SUNSET = 7.2, 17.0   # FICTIF : journée de novembre en montagne (heure locale)
PERFORMANCE_RATIO = 0.72      # FICTIF : part de la puissance crête réellement obtenue par ciel clair


def clear_sky_kw(hour: int) -> float:
    """Production PV par ciel clair (kW), en cloche entre le lever et le coucher du soleil."""
    mid = hour + 0.5                              # milieu de l'heure
    if not (SUNRISE <= mid <= SUNSET):
        return 0.0
    x = (mid - SUNRISE) / (SUNSET - SUNRISE)      # 0 au lever, 1 au coucher
    return PV_KWC * PERFORMANCE_RATIO * np.sin(np.pi * x) ** 1.3


def base_load_kw(hour: int) -> float:
    """Consommation « essentielle » (frigo, éclairage, radio, circulateurs…) en kW."""
    load = 0.35                                   # socle permanent
    if 6 <= hour < 9:
        load += 0.5                               # petit-déjeuner des randonneurs
    if 11 <= hour < 14:
        load += 0.6                               # cuisine du midi
    if 18 <= hour < 22:
        load += 0.7                               # dîner + éclairage de la salle commune
    return load + rng.normal(0, 0.03)             # petit bruit réaliste


def make_forecast(cloud_low: int, cloud_high: int) -> pd.DataFrame:
    rows = []
    for h in range(24):
        cloud = int(rng.integers(cloud_low, cloud_high + 1))       # couverture nuageuse en %
        pv = max(0.0, clear_sky_kw(h) * (1 - 0.85 * cloud / 100))  # les nuages réduisent la production
        rows.append({
            "hour": h,
            "cloud_cover_pct": cloud,
            "pv_kw": round(pv, 3),
            "base_load_kw": round(max(0.30, base_load_kw(h)), 3),
        })
    return pd.DataFrame(rows)


make_forecast(0, 15).to_csv(OUT / "forecast_sunny.csv", index=False)    # journée ensoleillée
make_forecast(70, 95).to_csv(OUT / "forecast_cloudy.csv", index=False)  # journée nuageuse

# ---------------------------------------------------------------------------
# 3. Liste de tâches
#    priority : 1 (peu important) à 5 (indispensable) --> à adapter selon les besoins du refuge
#    forced_start : tâche DÉJÀ réalisée (ou imposée) à cette heure -> l'agenda doit en tenir compte
# ---------------------------------------------------------------------------
tasks = pd.DataFrame([
    # task_id, name,                                   prio, dur(min), power(kW), earliest, deadline, forced
    ("T01", "Remplissage de la citerne (pompe)",         5,  45, 1.2, "07:00", "20:00", "10:30"),  # déjà fait à 10h30
    ("T02", "Four électrique (préparation du dîner)",    5,  75, 3.5, "16:00", "19:30", None),
    ("T03", "Chauffe-eau (douches)",                     4, 120, 3.0, "09:00", "19:00", None),
    ("T04", "Lave-vaisselle",                            4,  60, 1.8, "13:00", "21:00", None),
    ("T05", "Lessive (draps)",                           3,  90, 2.0, "08:00", "18:00", None),
    ("T06", "Traitement UV de l'eau",                    3, 240, 0.4, "08:00", "20:00", None),
    ("T07", "Séchoir à chaussures",                      3, 180, 2.5, "14:00", "20:00", None),
    ("T08", "Aspirateur dortoirs",                       2,  60, 1.2, "09:30", "15:00", None),
    ("T09", "Recharge outils et ordinateurs",            1, 120, 0.3, "08:00", "22:00", None),
    ("T10", "Dégivrage du congélateur",                  2, 180, 0.5, "10:00", "17:00", None),
], columns=["task_id", "name", "priority", "duration_min", "power_kw", "earliest_start", "deadline", "forced_start"])
tasks.to_csv(OUT / "tasks.csv", index=False)

print(f"Jeu de données fictif écrit dans {OUT}")
for f in sorted(OUT.iterdir()):
    print("  -", f.name)
