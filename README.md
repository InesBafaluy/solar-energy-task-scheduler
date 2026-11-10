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

- Déclarer, pour chaque tâche, un créneau de départ à choisir, puis les contraintes : fenêtre horaire, durée, puissance totale ≤ puissance disponible à chaque heure, réserve de batterie.
- Ajouter un objectif, par exemple maximiser la somme des priorités des tâches planifiées. Les tâches qui ne rentrent pas ressortent alors comme non planifiées.

### 2. PuLP ou Pyomo (programmation linéaire en nombres entiers)

Plus facile à utiliser que OR-Tools.

- Définir une variable binaire « la tâche *i* démarre à l'heure *t* » et écrire les contraintes sous forme d'inégalités.

### 3. python-mip ou SciPy `milp`

- Même principe que PuLP, avec moins de dépendances.

### 4. Sans librairie : heuristique gloutonne

Une première version simple du modèle.

- Trier les tâches par priorité, puis les placer dans le premier créneau faisable.
- Code rapide à écrire avec pandas.

### Piste retenue

Commencer par coder le point 4, puis passer à OR-Tools (point 1) et comparer les deux agendas.

## Questions à trancher 

- [ ] **Interruptibilité** : les tâches sont-elles interruptibles (on peut les couper et les reprendre) ou non ?
- [ ] **Batterie** : est-ce un simple seuil minimal à respecter, ou faut-il simuler son niveau heure par heure ? Exemple de seuil : garder toujours au moins X % de batterie en réserve pour le frigo, l'éclairage, la radio…
- [ ] **Pas de temps** : à quel rythme découpe-t-on la journée ? Créneaux d'1 h (24 créneaux par jour), de 30 min, d'1 h 30… ?
>>>>>>> Stashed changes
