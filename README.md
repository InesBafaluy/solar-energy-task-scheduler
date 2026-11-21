=======
# Solar Energy Task Scheduler

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
│   ├── predict.py         # Estimate next-day production and consumption
│   └── schedule.py        # Turn available electricity and tasks into an agenda
├── data/
│   ├── sample/            # Synthetic examples that can be shared on GitHub
│   └── private/           # Real monitoring exports and task data; excluded from Git
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


## Construction de l'agenda : choix de la méthode

La construction de l'agenda relève de l'**ordonnancement sous contraintes** (*constraint scheduling*). Plusieurs librairies Python sont envisageables.

### 1. OR-Tools (CP-SAT), de Google

Très rapide : résout le problème en quelques secondes.

- Déclarer, pour chaque tâche, un créneau de départ à choisir, puis les contraintes : fenêtre horaire, durée, puissance totale ≤ puissance disponible à chaque heure, réserve de batterie. Contrainte supplémentaire potentielle : Cette tâche a été faite à ce moment même si c'était pas le bon moment préconisé, le prendre en compte. 
- Ajouter un objectif, par exemple maximiser la somme des priorités des tâches planifiées. Les tâches qui ne rentrent pas ressortent alors comme non planifiées.

### 2. PuLP ou Pyomo (programmation linéaire en nombres entiers)

Plus facile à utiliser que OR-Tools.

- Définir une variable binaire « la tâche *i* démarre à l'heure *t* » et écrire les contraintes sous forme d'inégalités.

### 3. python-mip ou SciPy `milp`

- Même principe que PuLP, avec moins de dépendances.

### 4. Sans librairie : 

Une première version simple du modèle.

- Trier les tâches par priorité, puis les placer dans le premier créneau faisable.
- Code rapide à écrire avec pandas.

### Piste retenue

Commencer par coder le point 4, puis passer à OR-Tools (point 1) et comparer les deux agendas.

### Décisions prises

- **Tâches non interruptibles** : une tâche est un bloc continu de durée fixe.
- **Pas de découpage en créneaux** : le modèle travaille à la minute et utilise la durée exacte de chaque tâche.
- **Batterie** : le niveau est suivi heure par heure et ne descend jamais sous la réserve minimale de **0,30 kWh** (valeur issue de la data). Une cible de niveau en fin de journée est ajustable dans `system.json`.
- **Tâche déjà réalisée** : si une tâche a déjà été faite à une heure donnée, même hors du moment préconisé, cet horaire est imposé (colonne `forced_start`) et le reste de l'agenda s'organise autour.
- **Objectif** : planifier le plus de priorité possible, puis utiliser le moins possible le groupe électrogène, puis démarrer les tâches tôt. Les tâches qui ne rentrent pas sont listées comme **non planifiées**.

### Système électrique du refuge

- **Installation photovoltaïque** : 22 panneaux de 500 W, soit **11 kWc**
- **Groupe électrogène** (recharge des batteries par mauvais temps) : **20 kW nominal, réduit à 12 kW à 2 500 m d'altitude**. Le modèle utilise 12 kW
- **Réserve minimale de la batterie** : 0,30 kWh

### Limites connues du modèle

- La puissance de charge de la batterie n'est pas limitée.
- Les priorités comptent linéairement : plusieurs petites tâches peuvent passer avant une grosse tâche de priorité élevée. À ajuster si certaines tâches sont indispensables.
- Le budget carburant du groupe électrogène (`genset_max_kwh_per_day`) est une valeur fictive à confirmer.
>>>>>>> Stashed changes

