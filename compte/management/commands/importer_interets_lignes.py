"""Importe des lignes dans le registre des intérêts (page 'Intérêts') pour un prêteur/année donnés.

Réutilisable : chaque nouvelle série de chiffres tirés d'un relevé ou du
grand livre peut être mise dans un fichier CSV (colonnes : date_montant,
montant, date_remboursement, remboursement — une des deux paires par
ligne, l'autre vide) puis importée avec cette commande. Les lignes déjà
présentes (mêmes dates/montants) pour l'année ne sont pas dupliquées.

Exemple :

    python manage.py importer_interets_lignes --tenant anonymus \\
        --preteur "Claude Bernatchez" --annee 2026 --solde-initial 186200 \\
        --csv imports/interets_claude_bernatchez_2026.csv

`--taux` et `--solde-initial` ne sont nécessaires que pour créer l'année ou
mettre à jour ces valeurs ; sans eux, une année déjà existante n'est pas
modifiée sur ces deux champs.
"""

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from compte.models import InteretAnnee, InteretLigne, Preteur


def _parse_decimal(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return Decimal(raw.replace(' ', '').replace(',', '.'))
    except InvalidOperation:
        return None


def _parse_date(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    return datetime.strptime(raw, '%Y-%m-%d').date()


class Command(BaseCommand):
    help = "Importe des lignes (date/montant/date/remboursement) dans le registre des intérêts, pour un prêteur et une année donnés, à partir d'un CSV."

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help="Alias de la base tenant (ex.: anonymus).")
        parser.add_argument('--preteur', required=True, help="Nom du prêteur (créé s'il n'existe pas).")
        parser.add_argument('--annee', required=True, type=int)
        parser.add_argument('--csv', required=True, help="Chemin du fichier CSV (colonnes: date_montant,montant,date_remboursement,remboursement).")
        parser.add_argument('--solde-initial', dest='solde_initial', default=None, help="Solde reporté au 1er janvier de cette année, pour ce prêteur.")
        parser.add_argument('--taux', default=None, help="Taux annuel (%%) pour cette année.")

    def handle(self, *args, **options):
        alias = options['tenant']
        annee_num = options['annee']

        preteur, cree = Preteur.objects.using(alias).get_or_create(nom=options['preteur'])
        if cree:
            self.stdout.write(self.style.SUCCESS(f"Prêteur créé : {preteur.nom}"))

        defaults = {}
        if options['solde_initial'] is not None:
            defaults['solde_initial'] = _parse_decimal(options['solde_initial']) or Decimal('0')
        if options['taux'] is not None:
            defaults['taux'] = _parse_decimal(options['taux']) or Decimal('0')

        annee_obj, cree = InteretAnnee.objects.using(alias).get_or_create(
            preteur=preteur, annee=annee_num,
            defaults={
                'solde_initial': defaults.get('solde_initial', Decimal('0')),
                'taux': defaults.get('taux', Decimal('0')),
            },
        )
        if not cree and defaults:
            for champ, valeur in defaults.items():
                setattr(annee_obj, champ, valeur)
            annee_obj.save()
            self.stdout.write(f"Année {annee_num} ({preteur.nom}) mise à jour : {defaults}")
        elif cree:
            self.stdout.write(self.style.SUCCESS(f"Année {annee_num} créée pour {preteur.nom} (solde initial {annee_obj.solde_initial} $, taux {annee_obj.taux} %)."))

        existantes = set(
            annee_obj.lignes.using(alias).values_list('date_montant', 'montant', 'date_remboursement', 'remboursement')
        )

        prochain_ordre = (
            annee_obj.lignes.using(alias).order_by('-ordre').values_list('ordre', flat=True).first() or 0
        ) + 1

        ajoutees = 0
        ignorees = 0
        with open(options['csv'], newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                d1 = _parse_date(row.get('date_montant'))
                m = _parse_decimal(row.get('montant'))
                d2 = _parse_date(row.get('date_remboursement'))
                r = _parse_decimal(row.get('remboursement'))
                if not d1 and m is None and not d2 and r is None:
                    continue
                cle = (d1, m, d2, r)
                if cle in existantes:
                    ignorees += 1
                    continue
                InteretLigne.objects.using(alias).create(
                    annee=annee_obj, ordre=prochain_ordre,
                    date_montant=d1, montant=m, date_remboursement=d2, remboursement=r,
                )
                existantes.add(cle)
                prochain_ordre += 1
                ajoutees += 1

        self.stdout.write(self.style.SUCCESS(
            f"{ajoutees} ligne(s) ajoutée(s), {ignorees} déjà présente(s) ignorée(s), pour {preteur.nom} — {annee_num} ({alias})."
        ))
