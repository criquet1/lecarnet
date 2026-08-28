MONTH_LABELS_FR = {
    1: 'Janvier',
    2: 'Fevrier',
    3: 'Mars',
    4: 'Avril',
    5: 'Mai',
    6: 'Juin',
    7: 'Juillet',
    8: 'Aout',
    9: 'Septembre',
    10: 'Octobre',
    11: 'Novembre',
    12: 'Décembre',
}

MONTH_CHOICES_FR = [(str(month), label) for month, label in MONTH_LABELS_FR.items()]

# Index 0 = lundi ... 6 = dimanche, aligné sur date.weekday() de Python.
WEEKDAY_LABELS_FR = {
    0: 'Lundi',
    1: 'Mardi',
    2: 'Mercredi',
    3: 'Jeudi',
    4: 'Vendredi',
    5: 'Samedi',
    6: 'Dimanche',
}

WEEKDAY_CHOICES_FR = [(str(day), label) for day, label in WEEKDAY_LABELS_FR.items()]

MODE_CAP = 'CAP'
MODE_CAR = 'CAR'
MODE_AUTRE = 'AUTRE'
