"""Vues et helpers pour les rapports comptables : journal général, grand
livre, balance de vérification, état des revenus et dépenses, bilan,
comptes à payer/recevoir et rapport de taxes.

Extrait de facture/views.py pour alléger ce fichier.
"""

from django.shortcuts import render
from django.db import transaction, connections, DatabaseError
from django.db.models import Sum
from django.views.decorators.clickjacking import xframe_options_sameorigin

from decimal import Decimal, ROUND_HALF_UP
from calendar import monthrange
from types import SimpleNamespace
from datetime import date

from facture.constants import MONTH_LABELS_FR, MODE_CAP, MODE_CAR
from facture.models import Tr_desc, Tr_detail, RapportTaxes, SoldeFin
from compte.models import Compte, Setting, SoldeAuxLivres

from facture.services.tax_report_enrich import enrich_report_with_calculations
from facture.services.tax_report_actions import remove_line_from_report, transmit_report, undo_transmit_report
from facture.helpers.dates import exercice_pour_working_period
from facture.working_period import get_working_period
from facture.utils import get_setting, is_expert, split_debit_credit
from facture.services.journal_service import build_journal_context
from facture.services.compte_mode_service import build_compte_mode_context
from facture.services.ledger_sql import ledger_db_alias, fetch_or_create_monthly_tax_report
from facture.services.tax_report_fetch import fetch_report_with_details
from facture.services.taxes_service import (
    construire_lignes_taxes,
    calculer_montants_mensuels,
    calculer_periode_mensuelle,
    construire_formulaire_taxes,
)


def _solde_depart_par_compte(exercice=None):
    base = {
        row['compte_id']: (row['solde_depart'] or Decimal('0'))
        for row in SoldeAuxLivres.objects.values('compte_id', 'solde_depart')
    }
    if not exercice:
        return base

    mouvements_avant = (
        Tr_detail.objects
        .filter(tr_desc__date__lt=exercice.date_debut)
        .values('compte_id')
        .annotate(total=Sum('montant'))
    )
    for row in mouvements_avant:
        base[row['compte_id']] = base.get(row['compte_id'], Decimal('0')) + (row['total'] or Decimal('0'))

    return base


def _coerce_decimal(value):
    if value is None or value == '':
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


from tenancy.db_context import get_current_tenant_alias
from django.db import connections

def _fetch_balance_rows(exercice):
    alias = get_current_tenant_alias() or 'default'
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT * FROM solde_fin_pour_exercice(%s::bigint)", [exercice.id])
        columns = [col[0] for col in cursor.description]
        rows_raw = [dict(zip(columns, row)) for row in cursor.fetchall()]

    comptes = {c.pk: c for c in Compte.objects.all()}

    rows = []
    for raw in rows_raw:
        compte = comptes.get(raw['compte_numero'])
        if not compte:
            continue
        solde_final = _coerce_decimal(raw['solde_final'])
        solde_depart = _coerce_decimal(raw['solde_depart'])
        if solde_final == Decimal('0') and solde_depart == Decimal('0'):
            continue
        rows.append({
            'compte': compte,
            'debit': solde_final if solde_final >= 0 else Decimal('0'),
            'credit': abs(solde_final) if solde_final < 0 else Decimal('0'),
            'solde_depart': solde_depart,
        })

    total_debit = sum((row['debit'] for row in rows), Decimal('0'))
    total_credit = sum((row['credit'] for row in rows), Decimal('0'))
    return rows, total_debit, total_credit


def _fetch_resultat_rows(exercice):
    """Regroupe les comptes de resultat (numero >= 4000) en Revenus et Depenses
    pour l'etat de revenus et depenses, en suivant le meme modele SQL que
    la balance de verification (fonction solde_fin_pour_exercice)."""
    alias = get_current_tenant_alias() or 'default'
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT * FROM solde_fin_pour_exercice(%s::bigint)", [exercice.id])
        columns = [col[0] for col in cursor.description]
        rows_raw = [dict(zip(columns, row)) for row in cursor.fetchall()]

    comptes = {
        c.pk: c for c in Compte.objects.select_related('no_total').filter(numero__gte=4000)
    }

    revenus_par_categorie = {}
    depenses_par_categorie = {}
    total_revenus = Decimal('0')
    total_depenses = Decimal('0')

    for raw in rows_raw:
        compte = comptes.get(raw['compte_numero'])
        if not compte:
            continue

        solde_final = _coerce_decimal(raw['solde_final'])
        if solde_final == Decimal('0'):
            continue

        categorie = compte.no_total.desc if compte.no_total_id else "Autres"

        if solde_final < 0:
            # Solde crediteur : compte de revenu.
            montant = abs(solde_final)
            groupe = revenus_par_categorie.setdefault(
                categorie, {'comptes': [], 'sous_total': Decimal('0')}
            )
            groupe['comptes'].append({'compte': compte, 'montant': montant})
            groupe['sous_total'] += montant
            total_revenus += montant
        else:
            # Solde debiteur : compte de depense.
            montant = solde_final
            groupe = depenses_par_categorie.setdefault(
                categorie, {'comptes': [], 'sous_total': Decimal('0')}
            )
            groupe['comptes'].append({'compte': compte, 'montant': montant})
            groupe['sous_total'] += montant
            total_depenses += montant

    resultat_net = total_revenus - total_depenses

    return {
        'revenus': revenus_par_categorie,
        'depenses': depenses_par_categorie,
        'total_revenus': total_revenus,
        'total_depenses': total_depenses,
        'resultat_net': resultat_net,
    }



def _fetch_bilan_rows(exercice):
    """Regroupe les comptes de bilan (numero entre 1000 et 3999) en Actif
    (1000-1999), Passif (2000-2999) et Avoir (3000-3999) pour le Bilan, en
    suivant le meme modele SQL que la balance de verification (fonction
    solde_fin_pour_exercice). Le solde est cumulatif depuis l'ouverture (pas
    seulement la periode), comme pour la balance de verification."""
    alias = get_current_tenant_alias() or 'default'
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT * FROM solde_fin_pour_exercice(%s::bigint)", [exercice.id])
        columns = [col[0] for col in cursor.description]
        rows_raw = [dict(zip(columns, row)) for row in cursor.fetchall()]

    comptes = {
        c.pk: c for c in Compte.objects.select_related('no_total').filter(numero__gte=1000, numero__lte=3999)
    }

    actif_par_categorie = {}
    passif_par_categorie = {}
    avoir_par_categorie = {}
    total_actif = Decimal('0')
    total_passif = Decimal('0')
    total_avoir = Decimal('0')

    for raw in rows_raw:
        compte = comptes.get(raw['compte_numero'])
        if not compte:
            continue

        solde_final = _coerce_decimal(raw['solde_final'])
        if solde_final == Decimal('0'):
            continue

        categorie = compte.no_total.desc if compte.no_total_id else "Autres"
        numero = compte.numero

        if 1000 <= numero <= 1999:
            # Actif : solde normalement debiteur.
            montant = solde_final
            section = actif_par_categorie
        elif 2000 <= numero <= 2999:
            # Passif : solde normalement crediteur, affiche en positif.
            montant = -solde_final
            section = passif_par_categorie
        else:
            # Avoir (3000-3999) : solde normalement crediteur, affiche en positif.
            montant = -solde_final
            section = avoir_par_categorie

        groupe = section.setdefault(
            categorie, {'comptes': [], 'sous_total': Decimal('0')}
        )
        groupe['comptes'].append({'compte': compte, 'montant': montant})
        groupe['sous_total'] += montant

        if 1000 <= numero <= 1999:
            total_actif += montant
        elif 2000 <= numero <= 2999:
            total_passif += montant
        else:
            total_avoir += montant

    # Le BNR (benefices non repartis) au compte 3000-3999 ne contient que le
    # resultat des exercices deja clotures (voir clore_exercice). Tant que
    # l'exercice en cours n'est pas audite, son resultat net (revenus moins
    # depenses, comptes >= 4000) n'a pas encore ete verse au compte BNR : on
    # l'ajoute donc ici comme benefice (ou perte) non affecte de l'exercice en
    # cours, pour que le bilan reste equilibre avant la cloture officielle.
    if not exercice.est_audite:
        resultat_exercice = _fetch_resultat_rows(exercice)
        resultat_net_exercice = resultat_exercice['resultat_net']
        if resultat_net_exercice != Decimal('0'):
            categorie = "Bénéfices non répartis - résultat de l'exercice en cours (non audité)"
            pseudo_compte = SimpleNamespace(
                numero='',
                libelle="Résultat net de l'exercice en cours",
            )
            groupe = avoir_par_categorie.setdefault(
                categorie, {'comptes': [], 'sous_total': Decimal('0')}
            )
            groupe['comptes'].append({'compte': pseudo_compte, 'montant': resultat_net_exercice})
            groupe['sous_total'] += resultat_net_exercice
            total_avoir += resultat_net_exercice

    total_passif_avoir = total_passif + total_avoir

    is_balanced = total_actif.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) == total_passif_avoir.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return {
        'actif': actif_par_categorie,
        'passif': passif_par_categorie,
        'avoir': avoir_par_categorie,
        'total_actif': total_actif,
        'total_passif': total_passif,
        'total_avoir': total_avoir,
        'total_passif_avoir': total_passif_avoir,
        'is_balanced': is_balanced,
    }



def _fetch_grand_livre_from_sql_view():
    db_alias = ledger_db_alias()
    solde_depart_par_compte = _solde_depart_par_compte()
    query = """
        SELECT
            compte_id,
            compte_numero,
            compte_libelle,
            tr_date,
            no_ej,
            compagnie_nom,
            tr_description,
            source_nom,
            debit,
            credit,
            solde
        FROM facture_v_grand_livre_lignes
        ORDER BY compte_numero, tr_date, no_ej, tr_desc_id, tr_detail_id
    """

    comptes = []
    grand_total_debit = Decimal('0')
    grand_total_credit = Decimal('0')
    current_compte_id = None
    current_block = None
    comptes_with_entries = set()

    with connections[db_alias].cursor() as cursor:
        cursor.execute(query)
        for (
            compte_id,
            compte_numero,
            compte_libelle,
            tr_date,
            no_ej,
            compagnie_nom,
            tr_description,
            source_nom,
            debit,
            credit,
            solde,
        ) in cursor.fetchall():
            comptes_with_entries.add(compte_id)
            if current_compte_id != compte_id:
                if current_block is not None:
                    comptes.append(current_block)

                numero = compte_numero or 0
                current_compte_id = compte_id
                current_block = {
                    'compte': SimpleNamespace(
                        pk=compte_id,
                        numero=compte_numero,
                        libelle=compte_libelle,
                    ),
                    'is_bilan': 1000 <= numero <= 3999,
                    'entries': [],
                    'total_debit': Decimal('0'),
                    'total_credit': Decimal('0'),
                    'solde': Decimal('0'),
                    'solde_depart': _coerce_decimal(solde_depart_par_compte.get(compte_id, Decimal('0'))),
                }

                solde_depart = current_block['solde_depart']
                if current_block['is_bilan']:
                    current_block['entries'].append({
                        'date': None,
                        'no_ej': '',
                        'compagnie': None,
                        'description': 'Solde de depart',
                        'source': None,
                        'debit': Decimal('0'),
                        'credit': Decimal('0'),
                        'solde': solde_depart,
                        'is_solde_depart': True,
                    })
                    current_block['solde'] = solde_depart

            debit = _coerce_decimal(debit)
            credit = _coerce_decimal(credit)
            solde = _coerce_decimal(solde)
            solde_depart = current_block.get('solde_depart', Decimal('0'))
            solde_avec_depart = solde_depart + solde

            current_block['entries'].append({
                'date': tr_date,
                'no_ej': no_ej,
                'compagnie': SimpleNamespace(nom=compagnie_nom) if compagnie_nom else None,
                'description': tr_description,
                'source': SimpleNamespace(nom=source_nom) if source_nom else None,
                'debit': debit,
                'credit': credit,
                'solde': solde_avec_depart,
            })
            current_block['total_debit'] += debit
            current_block['total_credit'] += credit
            current_block['solde'] = solde_avec_depart
            grand_total_debit += debit
            grand_total_credit += credit

    if current_block is not None:
        comptes.append(current_block)

    # Ajoute les comptes de bilan sans mouvements mais avec solde de depart non nul.
    missing_opening_ids = [
        compte_id
        for compte_id, solde_depart in solde_depart_par_compte.items()
        if compte_id not in comptes_with_entries and _coerce_decimal(solde_depart) != Decimal('0')
    ]
    if missing_opening_ids:
        comptes_map = {
            compte.pk: compte
            for compte in Compte.objects.filter(pk__in=missing_opening_ids)
        }
        for compte_id in missing_opening_ids:
            compte = comptes_map.get(compte_id)
            if not compte:
                continue

            numero = getattr(compte, 'numero', None) or 0
            is_bilan = 1000 <= numero <= 3999
            if not is_bilan:
                continue

            solde_depart = _coerce_decimal(solde_depart_par_compte.get(compte_id, Decimal('0')))
            comptes.append({
                'compte': compte,
                'is_bilan': True,
                'entries': [{
                    'date': None,
                    'no_ej': '',
                    'compagnie': None,
                    'description': 'Solde de depart',
                    'source': None,
                    'debit': Decimal('0'),
                    'credit': Decimal('0'),
                    'solde': solde_depart,
                    'is_solde_depart': True,
                }],
                'total_debit': Decimal('0'),
                'total_credit': Decimal('0'),
                'solde': solde_depart,
                'solde_depart': solde_depart,
            })
    comptes.sort(key=lambda bloc: ((getattr(bloc['compte'], 'numero', None) or 0), bloc['compte'].pk or 0))

    grand_total_solde = sum((bloc['solde'] for bloc in comptes), Decimal('0'))
    is_balanced = grand_total_debit.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) == grand_total_credit.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return comptes, grand_total_debit, grand_total_credit, grand_total_solde, is_balanced


def _closing_date_label(reference_date, settings_instance=None):
    if not reference_date:
        return None

    closing_month = 12
    closing_day = 31
    if settings_instance:
        if settings_instance.fin_annee_mois:
            closing_month = settings_instance.fin_annee_mois
        if settings_instance.fin_annee_jour:
            closing_day = settings_instance.fin_annee_jour

    closing_year = reference_date.year
    if (reference_date.month, reference_date.day) > (closing_month, closing_day):
        closing_year += 1

    closing_day = min(closing_day, monthrange(closing_year, closing_month)[1])
    month_label = MONTH_LABELS_FR[closing_month].lower()
    return f"Pour l'année au {closing_day} {month_label} {closing_year}"


@xframe_options_sameorigin
def journal_general(request):
    working_period = get_working_period(request)
    exercice = exercice_pour_working_period(working_period)
    context = build_journal_context(exercice)
    context['can_edit_journal'] = is_expert(request.user)
    return render(request, "rapports/journal_general.html", context)


@xframe_options_sameorigin
def grand_livre(request):
    settings_instance = get_setting()
    working_period = get_working_period(request)
    exercice = exercice_pour_working_period(working_period)
    solde_depart_par_compte = _solde_depart_par_compte(exercice)
    report_date = exercice.date_fin if exercice else Tr_desc.objects.order_by('-date').values_list('date', flat=True).first()
    report_year_label = _closing_date_label(report_date, settings_instance)
    try:
        with transaction.atomic(using=ledger_db_alias()):
            comptes, grand_total_debit, grand_total_credit, grand_total_solde, is_balanced = _fetch_grand_livre_from_sql_view()
    except DatabaseError:
        # Fallback temporaire tant que la migration SQL view n'est pas appliquee.
        def is_bilan_account(compte):
            numero = getattr(compte, 'numero', None)
            return numero is not None and 1000 <= numero <= 3999

        details = Tr_detail.objects.select_related(
            'compte',
            'tr_desc__client',
            'tr_desc__fournisseur',
            'tr_desc__source'
        )
        if exercice:
            details = details.filter(
                tr_desc__date__gte=exercice.date_debut,
                tr_desc__date__lte=exercice.date_fin,
            )
        details = details.order_by('compte_id', 'tr_desc__date', 'tr_desc_id', 'id')

        comptes = []
        grand_total_debit = Decimal('0')
        grand_total_credit = Decimal('0')
        current_compte_id = None
        current_compte = None
        current_entries = []
        total_debit = Decimal('0')
        total_credit = Decimal('0')
        solde = Decimal('0')

        for detail in details:
            if current_compte_id is None:
                current_compte_id = detail.compte_id
                current_compte = detail.compte
                solde_depart = _coerce_decimal(solde_depart_par_compte.get(current_compte_id, Decimal('0')))
                if is_bilan_account(current_compte):
                    current_entries.append({
                        'date': None,
                        'no_ej': '',
                        'compagnie': None,
                        'description': 'Solde de depart',
                        'source': None,
                        'debit': Decimal('0'),
                        'credit': Decimal('0'),
                        'solde': solde_depart,
                        'is_solde_depart': True,
                    })
                solde = solde_depart

            if detail.compte_id != current_compte_id:
                comptes.append({
                    'compte': current_compte,
                    'is_bilan': is_bilan_account(current_compte),
                    'entries': current_entries,
                    'total_debit': total_debit,
                    'total_credit': total_credit,
                    'solde': solde,
                })

                current_compte_id = detail.compte_id
                current_compte = detail.compte
                current_entries = []
                total_debit = Decimal('0')
                total_credit = Decimal('0')
                solde_depart = _coerce_decimal(solde_depart_par_compte.get(current_compte_id, Decimal('0')))
                if is_bilan_account(current_compte):
                    current_entries.append({
                        'date': None,
                        'no_ej': '',
                        'compagnie': None,
                        'description': 'Solde de depart',
                        'source': None,
                        'debit': Decimal('0'),
                        'credit': Decimal('0'),
                        'solde': solde_depart,
                        'is_solde_depart': True,
                    })
                solde = solde_depart

            montant = detail.montant or Decimal('0')
            debit, credit = split_debit_credit(montant)

            total_debit += debit
            total_credit += credit
            solde += montant
            grand_total_debit += debit
            grand_total_credit += credit

            if not (detail.tr_desc.source and detail.tr_desc.source.nom == 'Cloture exercice'):
                current_entries.append({
                    'date': detail.tr_desc.date,
                    'no_ej': detail.tr_desc.no_ej,
                    'compagnie': detail.tr_desc.client or detail.tr_desc.fournisseur,
                    'description': detail.tr_desc.desc_ctb,
                    'source': detail.tr_desc.source,
                    'debit': debit,
                    'credit': credit,
                    'solde': solde,
                })

        if current_compte_id is not None:
            comptes.append({
                'compte': current_compte,
                'is_bilan': is_bilan_account(current_compte),
                'entries': current_entries,
                'total_debit': total_debit,
                'total_credit': total_credit,
                'solde': solde,
            })

        comptes_with_entries = {
            bloc['compte'].pk
            for bloc in comptes
            if bloc.get('compte') and getattr(bloc['compte'], 'pk', None)
        }
        missing_opening_ids = [
            compte_id
            for compte_id, solde_depart in solde_depart_par_compte.items()
            if compte_id not in comptes_with_entries and _coerce_decimal(solde_depart) != Decimal('0')
        ]
        if missing_opening_ids:
            comptes_map = {
                compte.pk: compte
                for compte in Compte.objects.filter(pk__in=missing_opening_ids)
            }
            for compte_id in missing_opening_ids:
                compte = comptes_map.get(compte_id)
                if not compte or not is_bilan_account(compte):
                    continue

                solde_depart = _coerce_decimal(solde_depart_par_compte.get(compte_id, Decimal('0')))
                comptes.append({
                    'compte': compte,
                    'is_bilan': True,
                    'entries': [{
                        'date': None,
                        'no_ej': '',
                        'compagnie': None,
                        'description': 'Solde de depart',
                        'source': None,
                        'debit': Decimal('0'),
                        'credit': Decimal('0'),
                        'solde': solde_depart,
                        'is_solde_depart': True,
                    }],
                    'total_debit': Decimal('0'),
                    'total_credit': Decimal('0'),
                    'solde': solde_depart,
                })
        comptes.sort(key=lambda bloc: ((getattr(bloc['compte'], 'numero', None) or 0), bloc['compte'].pk or 0))

        grand_total_solde = sum((bloc['solde'] for bloc in comptes), Decimal('0'))
        is_balanced = grand_total_debit.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) == grand_total_credit.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return render(request, "rapports/grand_livre.html", {
        'title': "Grand livre",
        'comptes': comptes,
        'grand_total_debit': grand_total_debit,
        'grand_total_credit': grand_total_credit,
        'grand_total_solde': grand_total_solde,
        'is_balanced': is_balanced,
        'report_year_label': report_year_label,
    })


@xframe_options_sameorigin
def balance_de_verification(request):
    settings_instance = get_setting()
    working_period = get_working_period(request)
    exercice = exercice_pour_working_period(working_period)

    if not exercice:
        return render(request, "rapports/balance_de_verification.html", {
            'title': "Balance de vérification",
            'rows': [],
            'total_debit': Decimal('0'),
            'total_credit': Decimal('0'),
            'is_balanced': True,
            'report_year_label': "Aucun exercice financier configuré pour cette période.",
        })

    report_date = exercice.date_fin
    report_year_label = _closing_date_label(report_date, settings_instance)
    rows, total_debit, total_credit = _fetch_balance_rows(exercice)

    is_balanced = total_debit.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) == total_credit.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return render(request, "rapports/balance_de_verification.html", {
        'title': "Balance de vérification",
        'rows': rows,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'is_balanced': is_balanced,
        'report_year_label': report_year_label,
    })


@xframe_options_sameorigin
def etat_revenus_depenses(request):
    settings_instance = get_setting()
    working_period = get_working_period(request)
    exercice = exercice_pour_working_period(working_period)

    if not exercice:
        return render(request, "rapports/etat_revenus_depenses.html", {
            'title': "État des résultats",
            'revenus': {},
            'depenses': {},
            'total_revenus': Decimal('0'),
            'total_depenses': Decimal('0'),
            'resultat_net': Decimal('0'),
            'report_year_label': "Aucun exercice financier configuré pour cette période.",
        })

    report_date = exercice.date_fin
    report_year_label = _closing_date_label(report_date, settings_instance)
    resultat = _fetch_resultat_rows(exercice)

    return render(request, "rapports/etat_revenus_depenses.html", {
        'title': "État des résultats",
        'revenus': resultat['revenus'],
        'depenses': resultat['depenses'],
        'total_revenus': resultat['total_revenus'],
        'total_depenses': resultat['total_depenses'],
        'resultat_net': resultat['resultat_net'],
        'report_year_label': report_year_label,
    })


@xframe_options_sameorigin
def bilan(request):
    settings_instance = get_setting()
    working_period = get_working_period(request)
    exercice = exercice_pour_working_period(working_period)

    if not exercice:
        return render(request, "rapports/bilan.html", {
            'title': "Bilan",
            'actif': {},
            'passif': {},
            'avoir': {},
            'total_actif': Decimal('0'),
            'total_passif': Decimal('0'),
            'total_avoir': Decimal('0'),
            'total_passif_avoir': Decimal('0'),
            'is_balanced': True,
            'report_year_label': "Aucun exercice financier configuré pour cette période.",
        })

    report_date = exercice.date_fin
    report_year_label = _closing_date_label(report_date, settings_instance)
    bilan_data = _fetch_bilan_rows(exercice)

    return render(request, "rapports/bilan.html", {
        'title': "Bilan",
        'actif': bilan_data['actif'],
        'passif': bilan_data['passif'],
        'avoir': bilan_data['avoir'],
        'total_actif': bilan_data['total_actif'],
        'total_passif': bilan_data['total_passif'],
        'total_avoir': bilan_data['total_avoir'],
        'total_passif_avoir': bilan_data['total_passif_avoir'],
        'is_balanced': bilan_data['is_balanced'],
        'report_year_label': report_year_label,
    })


@xframe_options_sameorigin
def compte_a_payer(request):
    settings_instance = get_setting()
    working_period = get_working_period(request)
    exercice = exercice_pour_working_period(working_period)
    context = build_compte_mode_context(MODE_CAP, settings_instance, exercice=exercice)
    context['title'] = "Comptes à payer"
    return render(request, "rapports/compte_mode.html", context)



@xframe_options_sameorigin
def compte_a_recevoir(request):
    settings_instance = get_setting()
    working_period = get_working_period(request)
    exercice = exercice_pour_working_period(working_period)
    context = build_compte_mode_context(MODE_CAR, settings_instance, exercice=exercice)
    context['title'] = "Comptes à recevoir"
    return render(request, "rapports/compte_mode.html", context)



@xframe_options_sameorigin
def rapport_de_taxes(request):
    from facture.views import _next_no_ej  # import tardif : evite l'import circulaire avec facture.views
    settings_instance = get_setting()
    feedback = []
    error_messages = []

    # 1. Récupération de la période de travail
    working_period = get_working_period(request)
    selected_year = working_period['year']
    selected_month = working_period['month']
    selected_month_value = working_period['value']

    # 2. Récupération des comptes TPS/TVQ
    tps_percue_id = settings_instance.compte_tps_percue_id if settings_instance else None
    tps_payee_id = settings_instance.compte_tps_payee_id if settings_instance else None
    tvq_percue_id = settings_instance.compte_tvq_percue_id if settings_instance else None
    tvq_payee_id = settings_instance.compte_tvq_payee_id if settings_instance else None

    tax_account_ids = [
        account_id for account_id in [
            tps_percue_id,
            tps_payee_id,
            tvq_percue_id,
            tvq_payee_id,
        ] if account_id
    ]

    # 3. Récupération ou création du rapport + lignes du mois
    selected_report, month_tax_details = fetch_or_create_monthly_tax_report(
        selected_year,
        selected_month,
        tax_account_ids,
    )

    # 4. Gestion des actions POST
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if not tax_account_ids:
            error_messages.append(
                "Configure les comptes TPS/TVQ dans Setting avant de creer ou modifier un rapport de taxes."
            )
        elif action == 'remove_line':
            report_id = request.POST.get('report_id')
            detail_id = request.POST.get('detail_id')
            report = RapportTaxes.objects.filter(pk=report_id).first()

            if not report:
                error_messages.append("Rapport de taxes introuvable.")
            elif report.est_transmis:
                error_messages.append("Ce rapport est deja transmis et ne peut plus etre modifie.")
            else:
                removed_count = remove_line_from_report(report, detail_id)
                if removed_count == 0:
                    error_messages.append("La ligne de taxes n'a pas pu etre retiree du rapport.")
                else:
                    feedback.append("Ligne de taxes retiree du rapport.")

        elif action == 'transmit_report':
            report_id = request.POST.get('report_id')
            report = RapportTaxes.objects.filter(pk=report_id).first()

            if not report:
                error_messages.append("Rapport de taxes introuvable.")
            elif report.est_transmis:
                error_messages.append("Ce rapport est deja transmis.")
            elif not report.details_taxes.exists():
                error_messages.append("Impossible de transmettre un rapport sans ligne de taxes.")
            else:
                try:
                    posted_count, mode_label = transmit_report(report, settings_instance, _next_no_ej)
                except ValueError as exc:
                    error_messages.append(str(exc))
                else:
                    if posted_count:
                        feedback.append(
                            f"Rapport transmis. {posted_count} ecriture(s) de report creee(s) vers {mode_label}."
                        )
                    else:
                        feedback.append(
                            "Rapport transmis. Aucun montant net TPS/TVQ a reporter pour cette periode."
                        )

        elif action == 'undo_transmit_report':
            report_id = request.POST.get('report_id')
            report = RapportTaxes.objects.filter(pk=report_id).first()

            if not report:
                error_messages.append("Rapport de taxes introuvable.")
            elif not report.est_transmis:
                error_messages.append("Ce rapport est deja en brouillon.")
            else:
                deleted_entries = undo_transmit_report(report)
                feedback.append(
                    f"Transmission annulee. Rapport remis en brouillon ({deleted_entries} ecriture(s) supprimee(s))."
                )

        elif action:
            error_messages.append("Action inconnue sur le rapport de taxes.")


    # 5. Recharger le rapport avec prefetch
    selected_report = fetch_report_with_details(
        selected_year,
        selected_month,
        tax_account_ids,
    )



    if selected_report:
        enrich_report_with_calculations(
            selected_report,
            tps_percue_id,
            tps_payee_id,
            tvq_percue_id,
            tvq_payee_id,
        )


        selected_report.formulaire = construire_formulaire_taxes(
            selected_report.tax_blocks['TPS'],
            selected_report.tax_blocks['TVQ'],
)

        # 7. Fusion des lignes TPS/TVQ
        selected_report.merged_rows = construire_lignes_taxes(
            selected_report.details_taxes.all(),
            tps_percue_id,
            tps_payee_id,
            tvq_percue_id,
            tvq_payee_id,
        )

        # 8. Calcul des montants mensuels (101, 300)
        montants = calculer_montants_mensuels(
            selected_report.tax_blocks['TPS'],
            selected_report.tax_blocks['TVQ'],
        )
        selected_report.montant_101 = montants['montant_101']
        selected_report.solde_300 = montants['solde_300']

        # 9. Calcul de la période mensuelle
        selected_report.periode_debut, selected_report.periode_fin = calculer_periode_mensuelle(
            selected_report.annee,
            selected_report.mois,
        )

    return render(request, "rapports/rapport_de_taxe.html", {
        'title': "Rapport de taxes",
        'selected_report': selected_report,
        'tax_accounts_configured': bool(tax_account_ids),
        'feedback': feedback,
        'error_messages': error_messages,
        'selected_month_value': selected_month_value,
        'report_year_label': None,
    })



