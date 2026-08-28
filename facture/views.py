from pyexpat.errors import messages

from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.db import connection, transaction, connections, DatabaseError
from django.db.models import Prefetch, Q, Subquery, Sum, Value, DecimalField, F
from django.db.models.functions import Coalesce, ExtractMonth, ExtractYear
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from calendar import monthrange
from compte.models import ExerciceFinancier
import calendar
from types import SimpleNamespace
import re
import csv
import json
from io import TextIOWrapper
import chardet
from datetime import date, datetime, timedelta
from facture.constants import MONTH_LABELS_FR, MODE_CAP, MODE_CAR
from facture.models import Cheque, Client, Fournisseur, Tr_desc, Tr_detail, Source, Releve, RapportTaxes, CompteReleve, CompagnieSoldeDepart, Facture, SoldeFin, TransactionListe
from compte.models import Setting
from facture.forms import ChequeForm, ClientForm, FournisseurForm, TrDescForm, TrDetailFormSet
from facture.services.tax_report_enrich import enrich_report_with_calculations
from facture.services.tax_report_actions import remove_line_from_report, transmit_report, undo_transmit_report
from facture.helpers.dates import exercice_pour_working_period, prochaine_date_fin_exercice, verifier_exercice_modifiable
from facture.working_period import (
    COOKIE_MAX_AGE,
    get_working_period,
    set_working_period as save_working_period,
    working_period_cookie_name,
)
from facture.utils import (
    TAX_AUTHORITY_COMPANY_NAMES,
    ensure_tax_authority_companies,
    expert_required,
    get_setting,
    is_expert,
    parse_decimal,
    split_debit_credit,
    tax_target_mode_from_setting,
)
from compte.models import Compte, SoldeAuxLivres


from django.contrib import messages

from facture.services.dashboard_service import compute_dashboard_data
from facture.services.journal_service import build_journal_context
from facture.services.compte_mode_service import build_compte_mode_context
from facture.services.ledger_sql import ledger_db_alias
from facture.services.ledger_sql import fetch_or_create_monthly_tax_report
from facture.services.tax_report_fetch import fetch_report_with_details
from facture.services.taxes_service import (
    build_tax_blocks,
    construire_lignes_taxes,
    calculer_montants_mensuels,
    calculer_periode_mensuelle,
    construire_formulaire_taxes,
)




def _money(value):
    return (value or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


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


def _fetch_compte_solde(compte_id):
    if not compte_id:
        return Decimal('0')

    return _coerce_decimal(
        SoldeFin.objects.filter(compte_numero_id=compte_id)
        .values_list('solde_final', flat=True)
        .first()
    )


def _fetch_compte_mode_blocks_from_sql_view(mode, compte_id, compagnies):
    db_alias = ledger_db_alias()
    query = """
        SELECT
            compagnie_id,
            compagnie_nom,
            tr_date,
            source_nom,
            tr_description,
            debit,
            credit,
            solde_compagnie
        FROM facture_v_compagnie_ledger_lignes
        WHERE cap_ou_car = %s AND compte_id = %s
        ORDER BY compagnie_nom, tr_date, tr_desc_id, tr_detail_id
    """

    rows_by_company = {compagnie.id: [] for compagnie in compagnies}
    soldes_by_company = {compagnie.id: Decimal('0') for compagnie in compagnies}

    with connections[db_alias].cursor() as cursor:
        cursor.execute(query, [mode, compte_id])
        for (
            compagnie_id,
            _compagnie_nom,
            tr_date,
            source_nom,
            tr_description,
            debit,
            credit,
            solde_compagnie,
        ) in cursor.fetchall():
            if compagnie_id not in rows_by_company:
                continue

            rows_by_company[compagnie_id].append({
                'date': tr_date,
                'source': SimpleNamespace(nom=source_nom) if source_nom else None,
                'description': tr_description,
                'debit': _coerce_decimal(debit),
                'credit': _coerce_decimal(credit),
                'solde': _coerce_decimal(solde_compagnie),
            })
            soldes_by_company[compagnie_id] = _coerce_decimal(solde_compagnie)

    blocks = []
    total_des_soldes = Decimal('0')
    for compagnie in compagnies:
        company_solde = soldes_by_company.get(compagnie.id, Decimal('0'))
        total_des_soldes += company_solde
        blocks.append({
            'compagnie': compagnie,
            'rows': rows_by_company.get(compagnie.id, []),
            'solde_final': company_solde,
        })

    return blocks, total_des_soldes


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


def index(request):
    return render(request, "accueil/index.html", {
        "title": "Mon carnet comptable"
    })


@login_required
@require_POST
def update_working_period(request):
    period_value = save_working_period(request, request.POST.get('period'))

    if period_value:
        year, month = (int(part) for part in period_value.split('-'))
        reference_date = date(year, month, 1)

        if not ExerciceFinancier.objects.filter(
            date_debut__lte=reference_date,
            date_fin__gte=reference_date,
        ).exists():
            settings_instance = get_setting()
            dernier_exercice = ExerciceFinancier.objects.order_by('-date_fin').first()

            while dernier_exercice and not ExerciceFinancier.objects.filter(
                date_debut__lte=reference_date,
                date_fin__gte=reference_date,
            ).exists():
                nouvelle_date_fin = prochaine_date_fin_exercice(
                    dernier_exercice.date_fin + timedelta(days=1),
                    settings_instance,
                )
                dernier_exercice = ExerciceFinancier.creer_exercice_suivant(
                    dernier_exercice, nouvelle_date_fin
                )

    next_url = request.POST.get('next') or '/dashboard/'
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = '/dashboard/'

    response = redirect(next_url)
    if period_value:
        response.set_cookie(
            working_period_cookie_name(request),
            period_value,
            max_age=COOKIE_MAX_AGE,
            samesite='Lax',
        )
    return response





def dashboard(request):
    context = compute_dashboard_data(request)
    return render(request, "dashboard/index.html", context)


@expert_required
def administration(request):
    return render(request, "administration/index.html", {
        "title": "Administration",
    })


@expert_required
def exercices_financiers_page(request):
    if request.method == 'POST':
        exercices_audites_ids = set(request.POST.getlist('audite'))
        for exercice in ExerciceFinancier.objects.all():
            nouvelle_valeur = str(exercice.id) in exercices_audites_ids
            if exercice.est_audite != nouvelle_valeur:
                exercice.est_audite = nouvelle_valeur
                exercice.cloture_le = timezone.now() if nouvelle_valeur else None
                exercice.save(update_fields=['est_audite', 'cloture_le'])
        messages.success(request, "Les exercices financiers ont été mis à jour.")
        return redirect('exercices_financiers')

    exercices = ExerciceFinancier.objects.all().order_by('-date_debut')
    return render(request, "administration/exercices_financiers.html", {
        "title": "Exercices financiers",
        "exercices": exercices,
    })


@xframe_options_sameorigin
def journal_general(request):
    working_period = get_working_period(request)
    exercice = exercice_pour_working_period(working_period)
    context = build_journal_context(exercice)
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


def _next_no_ej(reference_date):
    exercice = ExerciceFinancier.objects.filter(
        date_debut__lte=reference_date,
        date_fin__gte=reference_date,
    ).order_by('-date_debut').first()

    if not exercice:
        raise ValueError(
            f"Aucun exercice financier ne couvre la date {reference_date}. "
            "Change la periode de travail pour creer le nouvel exercice avant d'inscrire cette ecriture."
        )

    last_tr_desc = Tr_desc.objects.filter(
        date__gte=exercice.date_debut,
        date__lte=exercice.date_fin,
    ).order_by('-id').first()

    if not last_tr_desc:
        return "EJ1"

    match = re.match(r'^EJ(\d+)$', last_tr_desc.no_ej or '')
    if not match:
        return "EJ1"

    return f"EJ{int(match.group(1)) + 1}"


def _company_invoices_queryset(company=None, company_type='client'):
    invoice_detail_ids = Facture.objects.values('transaction_id')
    queryset = Tr_desc.objects.filter(
        details__id__in=Subquery(invoice_detail_ids),
    )
    if company is not None:
        if company_type == 'fournisseur':
            queryset = queryset.filter(fournisseur=company)
        else:
            queryset = queryset.filter(client=company)

    return queryset.distinct().annotate(
        invoice_total=Coalesce(
            Sum('details__montant'),
            Value(0),
            output_field=DecimalField(max_digits=10, decimal_places=2)
        )
    ).prefetch_related('details__compte').order_by('-date', '-id')


def _serialize_invoice(tr):
    settings_instance = get_setting()
    company_mode = 'CAP' if tr.fournisseur_id else 'CAR' if tr.client_id else ''
    forced_compte_id = None
    if settings_instance:
        if company_mode == 'CAP' and settings_instance.cap:
            forced_compte_id = settings_instance.cap.pk
        elif company_mode == 'CAR' and settings_instance.car:
            forced_compte_id = settings_instance.car.pk

    details = []
    forced_amount = None
    max_abs_amount = Decimal('0')
    for detail in tr.details.all():
        detail_amount = detail.montant or Decimal('0')
        abs_amount = abs(detail_amount)
        if abs_amount > max_abs_amount:
            max_abs_amount = abs_amount

        if forced_compte_id and detail.compte_id == forced_compte_id:
            forced_amount = abs_amount

        details.append({
            'compteId': str(detail.compte_id or ''),
            'compteLabel': str(detail.compte.libelle or '') if detail.compte_id else '',
            'montant': f"{detail_amount:.2f}",
        })

    display_total = forced_amount if forced_amount is not None else max_abs_amount
    if tr.note_de_credit:
        display_total = -display_total

    return {
        'id': str(tr.id),
        'date': tr.date.isoformat() if tr.date else '',
        'numero': tr.desc_ctb or '',
        'noteDeCredit': bool(tr.note_de_credit),
        'total': f"{display_total:.2f}",
        'details': details,
    }


def _parse_facture_total(raw_value):
    value = parse_decimal(raw_value, strip_spaces=True)
    if value is None:
        return None
    return _money(value)


def facture(request):
    title = "Facture"
    company_type = (request.POST.get('company_type') or 'client').strip().lower()
    company_form_class = FournisseurForm if company_type == 'fournisseur' else ClientForm
    company_model_class = Fournisseur if company_type == 'fournisseur' else Client
    company_form = company_form_class(request.POST or None, prefix='company')
    settings_instance = Setting.objects.select_related(
        'compte_tps_percue',
        'compte_tps_payee',
        'compte_tvq_percue',
        'compte_tvq_payee',
        'compte_fr_retard',
    ).first()
    comptes_count = Compte.objects.count()
    working_period = get_working_period(request)
    invoice_detail_ids = Facture.objects.values('transaction_id')

    clients = Client.objects.prefetch_related(
        Prefetch('tr_desc', queryset=_company_invoices_queryset())
    ).order_by('nom')
    fournisseurs = Fournisseur.objects.prefetch_related(
        Prefetch('tr_desc', queryset=_company_invoices_queryset())
    ).order_by('nom')

    cards = sorted(
        [
            {'type': 'client', 'obj': c} for c in clients.filter(active=True, afficher_card=True)
        ] + [
            {'type': 'fournisseur', 'obj': f} for f in fournisseurs.filter(active=True, afficher_card=True)
        ],
        key=lambda item: item['obj'].nom.lower()
    )

    # Compagnies occasionnelles (afficher_card=False) : elles ne s'affichent pas en carte
    # sur la page d'accueil pour ne pas encombrer la vue, mais restent facturables via
    # "Gerer les compagnies" plus bas -- d'ou l'utilisation de la liste complete ici.
    gestion_compagnies = sorted(
        [{'type': 'client', 'obj': c} for c in clients] +
        [{'type': 'fournisseur', 'obj': f} for f in fournisseurs],
        key=lambda item: item['obj'].nom.lower()
    )



    comptes_queryset = Compte.objects.all()

    all_comptes = [
        {
            'id': compte.pk,
            'label': f"{compte.numero} - {compte.libelle}",
        }
        for compte in comptes_queryset.order_by('numero')
    ]

    companies_comptes = {}
    companies_factures = {}
    # Utilise gestion_compagnies (toutes les compagnies) plutot que cards
    # pour que les compagnies occasionnelles restent facturables meme si elles
    # ne sont pas affichees en carte sur la page d'accueil.
    for item in gestion_compagnies:
        compagnie = item['obj']
        item_type = item['type']
        company_key = f"{item_type}:{compagnie.pk}"
        comptes_company = [
            {
                'id': compte.pk,
                'label': f"{compte.numero} - {compte.libelle}",
            }
            for compte in compagnie.comptes.all().order_by('numero')
        ]

        # Injecte les comptes attendus selon le mode de compagnie.
        # Ces comptes sont forces en fin de liste pour apparaitre en bas du modal.
        tax_accounts = []
        company_mode = 'CAP' if item_type == 'fournisseur' else 'CAR'
        if settings_instance:
            if company_mode == 'CAP':
                tax_accounts = [
                    settings_instance.compte_tps_payee,
                    settings_instance.compte_tvq_payee,
                    settings_instance.compte_fr_retard,
                ]
            elif company_mode == 'CAR':
                tax_accounts = [
                    settings_instance.compte_tps_percue,
                    settings_instance.compte_tvq_percue,
                    settings_instance.compte_fr_retard,
                ]

        forced_ids = {
            account.pk
            for account in tax_accounts
            if account
        }

        # Retire les comptes forces de la liste de base pour les re-ajouter en bas.
        comptes_company = [
            item for item in comptes_company
            if item['id'] not in forced_ids
        ]

        existing_ids = {item['id'] for item in comptes_company}
        for tax_account in tax_accounts:
            if not tax_account or tax_account.pk in existing_ids:
                continue
            comptes_company.append({
                'id': tax_account.pk,
                'label': f"{tax_account.numero} - {tax_account.libelle}",
            })
            existing_ids.add(tax_account.pk)

        companies_comptes[company_key] = comptes_company
        company_invoices = []
        for tr in compagnie.tr_desc.all():
            serialized = _serialize_invoice(tr)
            company_invoices.append({
                'id': tr.id,
                'no_ej': tr.no_ej,
                'numero': tr.desc_ctb or '',
                'date': tr.date.isoformat() if tr.date else '',
                'noteDeCredit': serialized['noteDeCredit'],
                'total': float(serialized['total']),
                'details': serialized['details'],
            })
        companies_factures[company_key] = company_invoices

    tr_desc_form = TrDescForm(prefix='trdesc')
    tr_detail_formset = TrDetailFormSet(
        prefix='detail',
        form_kwargs={'comptes_queryset': comptes_queryset}
    )
    open_company_modal = False
    company_modal_action = 'add_company'
    editing_company_id = ''
    open_tr_modal = False
    selected_company_id = ''
    selected_company_name = ''
    editing_tr_desc_id = ''
    invoice_action_error = ''

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_company':
            if company_form.is_valid():
                company = company_form.save(commit=False)
                company.created_by_non_expert = not is_expert(request.user)
                company.save()
                company_form.save_m2m()
                return redirect('facture')
            open_company_modal = True

        elif action == 'edit_company':
            company_id = (request.POST.get('company_id') or '').strip()
            company = company_model_class.objects.filter(pk=company_id).first()
            company_form = company_form_class(request.POST, prefix='company', instance=company)
            company_modal_action = 'edit_company'
            editing_company_id = company_id

            if not company:
                company_form.add_error(None, "Compagnie introuvable.")
            elif company_form.is_valid():
                company = company_form.save(commit=False)
                company.save()
                company_form.save_m2m()
                return redirect('facture')

            open_company_modal = True

        elif action == 'delete_tr_desc':
            selected_company_id = request.POST.get('selected_company_id', '')
            selected_company_type = (request.POST.get('selected_company_type') or 'client').strip().lower()
            selected_company_model = Fournisseur if selected_company_type == 'fournisseur' else Client
            editing_tr_desc_id = (request.POST.get('editing_tr_desc_id') or '').strip()
            selected_company = selected_company_model.objects.filter(pk=selected_company_id).first()
            editing_tr_desc = None

            if selected_company:
                selected_company_name = selected_company.nom
                editing_tr_desc = _company_invoices_queryset(selected_company, selected_company_type).filter(
                    pk=editing_tr_desc_id,
                ).first()

            if not selected_company or not editing_tr_desc:
                invoice_action_error = "Facture introuvable pour cette compagnie."
                open_tr_modal = True
            elif Tr_detail.objects.filter(
                tr_desc=editing_tr_desc,
                rapport_taxes__transmis_le__isnull=False,
            ).exists():
                invoice_action_error = (
                    "Cette facture contient des lignes de taxes deja transmises. Elle ne peut pas etre supprimee."
                )
                open_tr_modal = True
            else:
                with transaction.atomic():
                    editing_tr_desc.delete()
                return redirect('facture')

        elif action == 'add_tr_desc':
            selected_company_id = request.POST.get('selected_company_id', '')
            selected_company_type = (request.POST.get('selected_company_type') or 'client').strip().lower()
            selected_company_model = Fournisseur if selected_company_type == 'fournisseur' else Client
            selected_company = selected_company_model.objects.filter(pk=selected_company_id).first()
            editing_tr_desc_id = (request.POST.get('editing_tr_desc_id') or '').strip()
            editing_tr_desc = None
            company_mode = 'CAP' if selected_company_type == 'fournisseur' else 'CAR'
            forced_compte = None

            try:
                facture_total_value = _parse_facture_total(request.POST.get('facture_total', '0'))
            except InvalidOperation:
                facture_total_value = None

            if selected_company and editing_tr_desc_id:
                editing_filter = {'fournisseur': selected_company} if selected_company_type == 'fournisseur' else {'client': selected_company}
                editing_tr_desc = Tr_desc.objects.filter(
                    pk=editing_tr_desc_id,
                    **editing_filter
                ).first()

            tr_desc_form = TrDescForm(request.POST, prefix='trdesc', instance=editing_tr_desc)
            tr_detail_formset = TrDetailFormSet(
                request.POST,
                prefix='detail',
                form_kwargs={'comptes_queryset': comptes_queryset}
            )

            if selected_company:
                selected_company_name = selected_company.nom
            else:
                tr_desc_form.add_error(None, "Compagnie invalide.")

            if selected_company:
                if company_mode == 'CAP':
                    if not settings_instance or not settings_instance.cap:
                        tr_desc_form.add_error(
                            None,
                            "Compte CAP non configure dans Setting. Configure le compte CAP avant d'enregistrer la facture."
                        )
                    else:
                        forced_compte = settings_instance.cap

                elif company_mode == 'CAR':
                    if not settings_instance or not settings_instance.car:
                        tr_desc_form.add_error(
                            None,
                            "Compte CAR non configure dans Setting. Configure le compte CAR avant d'enregistrer la facture."
                        )
                    else:
                        forced_compte = settings_instance.car

            if facture_total_value is None:
                tr_desc_form.add_error(None, "Total de facture invalide.")

            if editing_tr_desc_id and not editing_tr_desc and selected_company:
                tr_desc_form.add_error(None, "Facture introuvable pour cette compagnie.")

            if editing_tr_desc and Tr_detail.objects.filter(
                tr_desc=editing_tr_desc,
                rapport_taxes__transmis_le__isnull=False,
            ).exists():
                tr_desc_form.add_error(
                    None,
                    "Cette facture contient des lignes de taxes deja transmises. Elle ne peut plus etre modifiee."
                )

            if selected_company and tr_desc_form.is_valid() and tr_detail_formset.is_valid():
                detail_rows = []
                for form in tr_detail_formset:
                    cleaned_data = form.cleaned_data
                    if not cleaned_data:
                        continue
                    compte = cleaned_data.get('compte')
                    montant = cleaned_data.get('montant')
                    if compte and montant is not None:
                        detail_rows.append((compte, montant))

                balance_rows = [
                    (compte, montant)
                    for compte, montant in detail_rows
                    if not forced_compte or compte.pk != forced_compte.pk
                ]
                accounts_total = _money(sum(
                    (abs(montant) for _, montant in balance_rows),
                    Decimal('0'),
                ))
                invoice_total = _money(abs(facture_total_value))
                balance_difference = _money(invoice_total - accounts_total)

                if accounts_total != invoice_total:
                    tr_desc_form.add_error(
                        None,
                        "La somme des comptes doit correspondre au total de la facture. "
                        f"Comptes: {accounts_total:.2f}; total: {invoice_total:.2f}; "
                        f"ecart: {abs(balance_difference):.2f}."
                    )

            if selected_company and tr_desc_form.is_valid() and tr_detail_formset.is_valid():
                with transaction.atomic():
                    tr_desc = tr_desc_form.save(commit=False)
                    source_facture, _ = Source.objects.get_or_create(nom='Facture')
                    sign_multiplier = -1 if tr_desc.note_de_credit else 1
                    if selected_company_type == 'fournisseur':
                        tr_desc.fournisseur = selected_company
                        tr_desc.client = None
                    else:
                        tr_desc.client = selected_company
                        tr_desc.fournisseur = None
                    print("TYPE DE REQUEST:", type(request))
                    try:
                        verifier_exercice_modifiable(tr_desc.date)
                    except ValueError as exc:
                        messages.error(request, str(exc))
                        return redirect('facture')

                    if not tr_desc.no_ej:
                        tr_desc.no_ej = _next_no_ej(tr_desc.date)
                    if not tr_desc.source_id:
                        tr_desc.source = source_facture
                    tr_desc.save()

                    if editing_tr_desc:
                        Tr_detail.objects.filter(tr_desc=tr_desc).delete()

                    # En mode CAP/CAR, le compte de contrepartie vient toujours de Setting.
                    if forced_compte:
                        filtered_rows = [
                            (compte, abs(montant))
                            for (compte, montant) in detail_rows
                            if compte.pk != forced_compte.pk
                        ]

                        detail_sign = -1 if company_mode == 'CAR' else 1
                        filtered_rows = [
                            (compte, detail_sign * sign_multiplier * abs(montant))
                            for (compte, montant) in filtered_rows
                        ]

                        for compte, montant in filtered_rows:
                            Tr_detail.objects.create(
                                tr_desc=tr_desc,
                                compte=compte,
                                montant=montant,
                            )

                        forced_sign = -1 if company_mode == 'CAP' else 1
                        forced_amount = forced_sign * sign_multiplier * abs(facture_total_value)

                        Tr_detail.objects.create(
                            tr_desc=tr_desc,
                            compte=forced_compte,
                            montant=forced_amount,
                        )
                    else:
                        for compte, montant in detail_rows:
                            Tr_detail.objects.create(
                                tr_desc=tr_desc,
                                compte=compte,
                                montant=sign_multiplier * abs(montant),
                            )
                return redirect('facture')

            open_tr_modal = True

    return render(request, "factures/index.html", {
        'title': title,
        'company_form': company_form,
        'open_company_modal': open_company_modal,
        'company_modal_action': company_modal_action,
        'gestion_compagnies': gestion_compagnies,
        'editing_company_id': editing_company_id,
        'comptes_count': comptes_count,
        'clients': clients,
        'fournisseurs': fournisseurs,
        'cards': cards,
        'tr_desc_form': tr_desc_form,
        'tr_detail_formset': tr_detail_formset,
        'next_no_ej': _next_no_ej(date(working_period['year'], working_period['month'], 1)),
        'open_tr_modal': open_tr_modal,
        'selected_company_id': selected_company_id,
        'selected_company_name': selected_company_name,
        'editing_tr_desc_id': editing_tr_desc_id,
        'invoice_action_error': invoice_action_error,
        'all_comptes_json': json.dumps(all_comptes),
        'companies_comptes_json': json.dumps(companies_comptes),
        'companies_factures_json': json.dumps(companies_factures),
        'compte_cap_id': settings_instance.cap_id if settings_instance and settings_instance.cap_id else 0,
        'compte_car_id': settings_instance.car_id if settings_instance and settings_instance.car_id else 0,
        'compte_tps_percue_id': settings_instance.compte_tps_percue_id if settings_instance and settings_instance.compte_tps_percue_id else 0,
        'compte_tvq_percue_id': settings_instance.compte_tvq_percue_id if settings_instance and settings_instance.compte_tvq_percue_id else 0,
        'compte_tps_payee_id': settings_instance.compte_tps_payee_id if settings_instance and settings_instance.compte_tps_payee_id else 0,
        'compte_tvq_payee_id': settings_instance.compte_tvq_payee_id if settings_instance and settings_instance.compte_tvq_payee_id else 0,
        'compte_fr_retard_id': settings_instance.compte_fr_retard_id if settings_instance and settings_instance.compte_fr_retard_id else 0,
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



def _detecter_compte_csv(row):
    """
    Détecte le no_compte, nom_institut et type_compte à partir d'une ligne CSV.
    Format banque  : col[0]=institution, col[1]=no_compte, col[2]=type_compte (ex: EOP)
    Format VISA    : col[0]=no_compte (contient 'VISA'), col[1] et col[2] vides
    Retourne (no_compte, nom_institut, type_compte)
    """
    col0 = row[0].strip() if len(row) > 0 else ''
    col1 = row[1].strip() if len(row) > 1 else ''
    col2 = row[2].strip() if len(row) > 2 else ''

    if col2:
        # Format banque : col2 contient le type de compte (ex: EOP)
        return col1, col0, col2
    else:
        # Format VISA / autre : col0 est l'identifiant du compte
        return col0, '', ''


def _obtenir_ou_creer_compte_releve(no_compte, nom_institut, type_compte_csv):
    """
    Trouve ou crée un CompteReleve. Infère le type_onglet depuis type_compte_csv.
    """
    no_compte_upper = no_compte.upper()
    if 'VISA' in no_compte_upper or 'CC' in no_compte_upper:
        type_onglet = 'carte_credit'
        # Extraire les 4 derniers chiffres : "VISA**** **** **** 5011" → "Visa 5011"
        chiffres = ''.join(filter(str.isdigit, no_compte))
        nom_affichage = f"Visa {chiffres[-4:]}" if len(chiffres) >= 4 else no_compte
    elif 'MC' in no_compte_upper or 'MARGE' in no_compte_upper:
        type_onglet = 'marge_credit'
        nom_affichage = no_compte
    elif type_compte_csv:
        type_onglet = 'banque'
        nom_affichage = f"{no_compte} {type_compte_csv}"
    else:
        type_onglet = 'autre'
        nom_affichage = no_compte

    default_compte_comptable = None

    # Héritage intelligent pour les nouveaux comptes similaires:
    # - cartes Visa: si un seul compte comptable est deja utilise pour les cartes Visa,
    #   on le reprend automatiquement sur la nouvelle carte.
    # - marge de credit: meme principe pour les comptes de marge.
    # - banque: seulement si no_compte + type_compte trouvent deja un mapping (rare).
    if type_onglet == 'carte_credit' and 'VISA' in no_compte_upper:
        visa_compte_ids = list(
            CompteReleve.objects.filter(
                type_onglet='carte_credit',
                no_compte__icontains='VISA',
                compte_comptable__isnull=False,
            ).values_list('compte_comptable_id', flat=True).distinct()
        )
        if len(visa_compte_ids) == 1:
            default_compte_comptable = Compte.objects.filter(pk=visa_compte_ids[0]).first()
    elif type_onglet == 'marge_credit':
        marge_compte_ids = list(
            CompteReleve.objects.filter(
                type_onglet='marge_credit',
                compte_comptable__isnull=False,
            ).values_list('compte_comptable_id', flat=True).distinct()
        )
        if len(marge_compte_ids) == 1:
            default_compte_comptable = Compte.objects.filter(pk=marge_compte_ids[0]).first()

    compte, _ = CompteReleve.objects.get_or_create(
        no_compte=no_compte,
        type_compte=type_compte_csv,
        defaults={
            'nom_affichage': nom_affichage,
            'nom_institut': nom_institut,
            'type_onglet': type_onglet,
            'compte_comptable': default_compte_comptable,
        },
    )
    return compte


def _relink_releves_compte_type_mismatch():
    """Reassocie les lignes Releve au bon CompteReleve quand type_compte differe."""
    mismatches = Releve.objects.select_related('compte_releve').exclude(
        compte_releve__isnull=True
    ).exclude(
        type_compte=F('compte_releve__type_compte')
    )

    for releve in mismatches:
        corrected_compte = _obtenir_ou_creer_compte_releve(
            releve.no_compte,
            releve.nom_institut,
            releve.type_compte,
        )
        if releve.compte_releve_id != corrected_compte.id:
            releve.compte_releve = corrected_compte
            releve.save(update_fields=['compte_releve'])


def _suggest_compte_from_releve(releve):
    if releve.compte_releve_id and getattr(releve.compte_releve, 'compte_comptable_id', None):
        return releve.compte_releve.compte_comptable

    numero_compte = ''.join(ch for ch in (releve.no_compte or '') if ch.isdigit())
    if not numero_compte:
        return None

    candidates = [numero_compte]
    if len(numero_compte) > 4:
        candidates.extend([numero_compte[:4], numero_compte[-4:]])

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            compte = Compte.objects.filter(numero=int(candidate)).first()
        except (TypeError, ValueError):
            compte = None
        if compte:
            return compte
    return None


def _suggest_montant_from_releve(releve):
    if releve.depot is not None and releve.depot != 0:
        return abs(releve.depot)
    if releve.retrait is not None and releve.retrait != 0:
        return -abs(releve.retrait)
    return None


def _compte_releve_aliases(compte_releve):
    aliases = set()
    if not compte_releve:
        return aliases

    raw_values = [
        compte_releve.nom_affichage or '',
        compte_releve.no_compte or '',
        compte_releve.type_compte or '',
    ]

    for raw in raw_values:
        value = str(raw).strip().upper()
        if not value:
            continue
        aliases.add(value)
        for token in re.findall(r'[A-Z0-9]+', value):
            if len(token) >= 3:
                aliases.add(token)

    numero = ''.join(ch for ch in (compte_releve.no_compte or '') if ch.isdigit())
    if numero:
        aliases.add(numero)
        if len(numero) >= 4:
            aliases.add(numero[-4:])

    return aliases


def _description_alias_score(description, aliases):
    if not description or not aliases:
        return 0
    desc = str(description).upper()
    score = 0
    for alias in aliases:
        if alias and alias in desc:
            score = max(score, len(alias))
    return score


def _find_releve_counterpart(current_releve, compte_cible, montant_cible):
    """Trouve une ligne de releve contrepartie (montant oppose, meme date) sur le compte cible."""
    if not current_releve or not compte_cible or montant_cible is None:
        return None

    comptes_releves_cibles = list(CompteReleve.objects.filter(compte_comptable=compte_cible))
    if not comptes_releves_cibles:
        return None

    depot_present = current_releve.depot is not None and current_releve.depot != 0
    retrait_present = current_releve.retrait is not None and current_releve.retrait != 0
    if depot_present == retrait_present:
        return None

    base_qs = Releve.objects.filter(
        compte_releve__in=comptes_releves_cibles,
        date=current_releve.date,
    ).exclude(pk=current_releve.pk).select_related('compte_releve')

    if depot_present:
        # Ligne courante: depot. Contrepartie attendue: retrait du meme montant.
        base_qs = base_qs.filter(Q(retrait=montant_cible) | Q(retrait=-montant_cible))
    else:
        # Ligne courante: retrait. Contrepartie attendue: depot du meme montant.
        base_qs = base_qs.filter(Q(depot=montant_cible) | Q(depot=-montant_cible))

    candidates = list(base_qs.order_by('ecriture_creee', 'id'))
    if not candidates:
        return None

    # Validation douce par indice textuel (ex: EOP, ET2, VISA 5011) pour reduire les faux positifs.
    source_aliases = _compte_releve_aliases(getattr(current_releve, 'compte_releve', None))
    target_aliases = set()
    for compte_releve in comptes_releves_cibles:
        target_aliases.update(_compte_releve_aliases(compte_releve))

    scored = []
    for candidate in candidates:
        score_from_current_desc = _description_alias_score(current_releve.desc_releve, target_aliases)
        score_from_candidate_desc = _description_alias_score(candidate.desc_releve, source_aliases)
        combined_score = max(score_from_current_desc, score_from_candidate_desc)
        scored.append((combined_score, 0 if not candidate.ecriture_creee else 1, candidate.id, candidate))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))

    # S'il y a un indice descriptif, on le privilegie.
    if scored[0][0] > 0:
        return scored[0][3]

    # Regle stricte: s'il y a plusieurs candidates mais aucun indice textuel,
    # on ne choisit pas automatiquement pour eviter les faux positifs.
    if len(scored) > 1:
        return None

    # Sans indice, conserver le comportement precedent (premiere non transmise puis plus ancienne).
    return scored[0][3]


def _normalize_desc_releve(text):
    """Normalise une description de relevé pour la comparaison de similarité."""
    return re.sub(r'\s+', ' ', (text or '').strip().upper())


def _tr_desc_detail_rows_hors_compte_lie(tr_desc, compte_lie_id, montant_compte_lie):
    """Retourne les lignes Tr_detail d'une écriture, en excluant la ligne du compte lié
    (compte de banque/carte lui-même) quand elle est identifiable."""
    detail_rows = []
    ligne_compte_lie_ignoree = False
    for detail in tr_desc.details.all():
        if (
            not ligne_compte_lie_ignoree
            and compte_lie_id
            and montant_compte_lie is not None
            and detail.compte_id == compte_lie_id
            and detail.montant == montant_compte_lie
        ):
            ligne_compte_lie_ignoree = True
            continue
        detail_rows.append({
            'compte_id': detail.compte_id,
            'compte_label': str(detail.compte),
            'montant': str(abs(detail.montant or Decimal('0'))),
        })
    return detail_rows


def _date_moins_mois(value, months):
    target_month_index = value.year * 12 + value.month - 1 - months
    target_year, target_month_index = divmod(target_month_index, 12)
    target_month = target_month_index + 1
    target_day = min(value.day, monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def _dernier_jour_mois_precedent(value):
    """Retourne le dernier jour du mois qui précède celui de `value`, afin d'exclure
    le mois en cours de la recherche d'écritures similaires."""
    return date(value.year, value.month, 1) - timedelta(days=1)


def _candidats_ecriture_similaire(releve=None, max_candidats=500):
    """Precalcule une fois (par requete) la liste des lignes de relevé qui ont deja une
    ecriture, avec leurs lignes de detail hors compte lie. Les virements inter-relevés
    restent des modèles valides: leur contrepartie est reliée à la même écriture lors
    de la création, ce qui évite de comptabiliser le virement deux fois."""
    candidats_qs = Releve.objects.filter(
        ecriture_creee=True,
        ecriture_tr_desc__isnull=False,
    )
    if releve and releve.date:
        date_maximum = _dernier_jour_mois_precedent(releve.date)
        candidats_qs = candidats_qs.filter(
            date__gte=_date_moins_mois(releve.date, 18),
            date__lte=date_maximum,
        )

    candidats_qs = candidats_qs.select_related(
        'ecriture_tr_desc',
        'ecriture_tr_desc__client',
        'ecriture_tr_desc__fournisseur',
        'compte_releve',
        'compte_releve__compte_comptable',
    ).prefetch_related(
        Prefetch('ecriture_tr_desc__details', queryset=Tr_detail.objects.select_related('compte').order_by('id')),
    ).order_by('-date', '-id')[:max_candidats]

    enrichis = []
    for candidat in candidats_qs:
        tr_desc = candidat.ecriture_tr_desc
        if not tr_desc:
            continue

        compte_lie_id = None
        if candidat.compte_releve_id and candidat.compte_releve and candidat.compte_releve.compte_comptable_id:
            compte_lie_id = candidat.compte_releve.compte_comptable_id

        montant_compte_lie = None
        if candidat.depot is not None and candidat.depot != 0 and not (candidat.retrait is not None and candidat.retrait != 0):
            montant_compte_lie = abs(candidat.depot)
        elif candidat.retrait is not None and candidat.retrait != 0 and not (candidat.depot is not None and candidat.depot != 0):
            montant_compte_lie = -abs(candidat.retrait)

        details = _tr_desc_detail_rows_hors_compte_lie(tr_desc, compte_lie_id, montant_compte_lie)

        enrichis.append({
            'releve_pk': candidat.pk,
            'desc_releve_normalisee': _normalize_desc_releve(candidat.desc_releve),
            'releve_id': candidat.pk,
            'desc_releve': candidat.desc_releve,
            'date_releve': candidat.date.strftime('%Y-%m-%d') if candidat.date else '',
            'no_ej': tr_desc.no_ej,
            'desc_ctb': tr_desc.desc_ctb or '',
            'compagnie_id': (f"client:{tr_desc.client_id}" if tr_desc.client_id else (f"fournisseur:{tr_desc.fournisseur_id}" if tr_desc.fournisseur_id else '')),
            'compagnie_label': str(tr_desc.client or tr_desc.fournisseur or ''),
            'details': details,
        })
    return enrichis


def _meilleures_correspondances(releve, candidats_enrichis, limit=1):
    """Retourne les écritures dont la description normalisée est strictement identique."""
    if not releve or not releve.date or not releve.desc_releve or releve.ecriture_creee:
        return []

    cible = _normalize_desc_releve(releve.desc_releve)
    date_minimum = _date_moins_mois(releve.date, 18).isoformat()
    date_maximum = _dernier_jour_mois_precedent(releve.date).isoformat()
    correspondances = []
    for candidat in candidats_enrichis:
        if candidat['releve_pk'] == releve.pk:
            continue
        if not date_minimum <= candidat['date_releve'] <= date_maximum:
            continue
        if cible == candidat['desc_releve_normalisee']:
            correspondances.append(candidat)

    correspondances.sort(key=lambda candidat: -candidat['releve_pk'])
    return correspondances[:limit]


def releve_ecriture_similaire(request, releve_id):
    """Vue AJAX: retourne, pour une ligne de relevé donnée, les écritures déjà créées
    sur des lignes de relevé dont la description est semblable."""
    releve = Releve.objects.select_related('compte_releve').filter(pk=releve_id).first()
    if not releve:
        return JsonResponse({'error': "Ligne de relevé introuvable."}, status=404)

    candidats = _candidats_ecriture_similaire(releve)
    resultats = _meilleures_correspondances(releve, candidats, limit=1)
    return JsonResponse({'resultats': resultats})


def _import_releve_csv(csv_file):
    errors = []

    file_name = csv_file.name
    if Releve.objects.filter(fichier_source=file_name).exists():
        errors.append(f"⚠ Le fichier « {file_name} » a déjà été importé. Aucune ligne n'a été ajoutée.")
        return errors

    raw_data = csv_file.file.read(5000)
    csv_file.file.seek(0)
    detected = chardet.detect(raw_data)
    encoding = detected.get('encoding', 'utf-8') or 'utf-8'

    text_file = TextIOWrapper(csv_file.file, encoding=encoding)
    sample = text_file.read(1024)
    text_file.seek(0)

    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(text_file, dialect=dialect)
    releves = []
    compte_releve_cache = {}

    for row_num, row in enumerate(reader, 1):
        try:
            if not row or all(not cell.strip() for cell in row):
                continue

            if len(row) < 12:
                errors.append(f"Ligne {row_num}: {len(row)} colonnes trouvées. Données: {row[:3]}")
                continue

            no_compte, nom_institut, type_compte = _detecter_compte_csv(row)
            date_str = row[3].strip() if len(row) > 3 else ''
            no_ligne = row[4].strip() if len(row) > 4 else ''
            desc_releve = row[5].strip() if len(row) > 5 else ''

            if not all([no_compte, date_str, no_ligne, desc_releve]):
                errors.append(f"Ligne {row_num}: Données manquantes")
                continue

            try:
                date_obj = datetime.strptime(date_str, '%Y/%m/%d').date()
            except ValueError:
                errors.append(f"Ligne {row_num}: Format de date invalide ({date_str})")
                continue

            if type_compte:
                no_cheque = row[6].strip() if len(row) > 6 else ''
                retrait = parse_decimal(row[7] if len(row) > 7 else '', none_if_blank=True)
                depot = parse_decimal(row[8] if len(row) > 8 else '', none_if_blank=True)
                solde = parse_decimal(row[13] if len(row) > 13 else '', none_if_blank=False) or Decimal('0')
            else:
                no_cheque = ''
                charge = parse_decimal(row[11] if len(row) > 11 else '', none_if_blank=True)
                paiement = parse_decimal(row[12] if len(row) > 12 else '', none_if_blank=True)
                retrait = charge if charge and charge > 0 else None
                depot = abs(paiement) if paiement and paiement < 0 else None
                solde = Decimal('0')

            cache_key = (no_compte, type_compte)
            if cache_key not in compte_releve_cache:
                compte_releve_cache[cache_key] = _obtenir_ou_creer_compte_releve(
                    no_compte, nom_institut, type_compte
                )

            releve_data = {
                'compte_releve': compte_releve_cache[cache_key],
                'fichier_source': file_name,
                'nom_institut': nom_institut,
                'no_compte': no_compte,
                'type_compte': type_compte,
                'date': date_obj,
                'no_ligne': no_ligne,
                'desc_releve': desc_releve,
                'desc_ctb': desc_releve[:40],
                'no_cheque': no_cheque,
                'retrait': retrait,
                'depot': depot,
                'solde': solde,
                'ecriture_creee': False,
            }

            releves.append(releve_data)

        except Exception as exc:
            errors.append(f"Ligne {row_num}: Erreur lors du parsing ({str(exc)})")
            continue

    if errors:
        errors.insert(0, "Le fichier est invalide. Aucune ligne n'a été ajoutée.")
    elif releves:
        try:
            with transaction.atomic():
                for data in releves:
                    Releve.objects.create(**data)
            errors.insert(0, f"✓ {len(releves)} ligne(s) ajoutée(s) à la base de données avec succès!")
        except Exception as exc:
            errors.append(f"Erreur lors de l'insertion: {str(exc)}")
    else:
        errors.append("Le fichier ne contient aucune ligne de relevé valide.")

    return errors


def releve_bancaire(request):
    releves = []
    errors = []
    open_releve_modal = False
    modal_releve_id = ''
    selected_compagnie_id = ''
    comptes_queryset = Compte.objects.all().order_by('numero')
    compagnies = sorted(
        [{'type': 'client', 'obj': c, 'key': f'client:{c.pk}'} for c in Client.objects.filter(active=True)] +
        [{'type': 'fournisseur', 'obj': f, 'key': f'fournisseur:{f.pk}'} for f in Fournisseur.objects.filter(active=True)],
        key=lambda item: item['obj'].nom.lower()
    )
    settings_instance = get_setting()
    company_required_account_ids = {
        account_id
        for account_id in (
            settings_instance.cap_id if settings_instance else None,
            settings_instance.car_id if settings_instance else None,
        )
        if account_id is not None
    }

    tr_desc_form = TrDescForm(prefix='trdesc_releve')
    tr_detail_formset = TrDetailFormSet(
        prefix='detail_releve',
        form_kwargs={'comptes_queryset': comptes_queryset}
    )

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'create_ecriture':
            releve_id = (request.POST.get('releve_id') or '').strip()
            selected_compagnie_raw = (request.POST.get('compagnie_id') or '').strip()
            if ':' in selected_compagnie_raw:
                selected_compagnie_type, selected_compagnie_id = selected_compagnie_raw.split(':', 1)
                selected_compagnie_type = selected_compagnie_type.strip().lower()
            else:
                selected_compagnie_type = 'client'
                selected_compagnie_id = selected_compagnie_raw
            modal_releve_id = releve_id
            open_releve_modal = True
            releve = Releve.objects.select_related('ecriture_tr_desc', 'compte_releve', 'compte_releve__compte_comptable').filter(pk=releve_id).first()
            existing_tr_desc = releve.ecriture_tr_desc if releve and releve.ecriture_creee and releve.ecriture_tr_desc_id else None
            selected_compagnie_model = Fournisseur if selected_compagnie_type == 'fournisseur' else Client
            selected_compagnie = None
            if selected_compagnie_id:
                selected_compagnie = selected_compagnie_model.objects.filter(pk=selected_compagnie_id).first()
                if not selected_compagnie:
                    errors.append("Compagnie invalide.")

            tr_desc_form = TrDescForm(request.POST, prefix='trdesc_releve', instance=existing_tr_desc)
            tr_detail_formset = TrDetailFormSet(
                request.POST,
                prefix='detail_releve',
                form_kwargs={'comptes_queryset': comptes_queryset}
            )

            if not releve:
                errors.append("Ligne de relevé introuvable.")
            else:
                depot_present = releve.depot is not None and releve.depot != 0
                retrait_present = releve.retrait is not None and releve.retrait != 0

                if depot_present and retrait_present:
                    errors.append("La ligne de relevé contient dépôt et retrait en même temps; impossible de déterminer le sens.")
                elif not depot_present and not retrait_present:
                    errors.append("La ligne de relevé ne contient ni dépôt ni retrait.")

                compte_lie = None
                if releve.compte_releve_id and releve.compte_releve.compte_comptable_id:
                    compte_lie = releve.compte_releve.compte_comptable
                else:
                    compte_lie = _suggest_compte_from_releve(releve)

                if not compte_lie:
                    errors.append(
                        "Aucun compte de grand livre lié au compte de relevé. Configure `compte_comptable` sur ce compte de relevé."
                    )

                if not errors and tr_desc_form.is_valid() and tr_detail_formset.is_valid():
                    detail_rows = []
                    for detail_form in tr_detail_formset:
                        cleaned_data = detail_form.cleaned_data
                        if not cleaned_data:
                            continue
                        compte = cleaned_data.get('compte')
                        montant = cleaned_data.get('montant')
                        if compte and montant is not None:
                            detail_rows.append((compte, abs(montant)))

                    if not detail_rows:
                        errors.append("Ajoute au moins une ligne Tr_detail (compte + montant).")
                    else:
                        detail_compte_ids = [compte.pk for compte, _ in detail_rows if getattr(compte, 'pk', None) is not None]
                        compte_ids_releves = set(
                            CompteReleve.objects.filter(compte_comptable_id__in=detail_compte_ids)
                            .values_list('compte_comptable_id', flat=True)
                        )
                        is_virement_inter_releves = any(compte.pk in compte_ids_releves for compte, _ in detail_rows)
                        used_account_ids = {compte.pk for compte, _ in detail_rows}
                        if compte_lie:
                            used_account_ids.add(compte_lie.pk)
                        company_is_required = bool(used_account_ids & company_required_account_ids)
                        if company_is_required and selected_compagnie is None:
                            errors.append(
                                "Une compagnie est obligatoire lorsqu'une ligne utilise le compte CAP ou CAR."
                            )
                        compagnie_ecriture = selected_compagnie if company_is_required else (
                            None if is_virement_inter_releves else selected_compagnie
                        )

                        montant_releve = abs(releve.depot) if depot_present else abs(releve.retrait)
                        total_contrepartie = sum((montant for _, montant in detail_rows), Decimal('0'))
                        try:
                            verifier_exercice_modifiable(releve.date)
                            exercice_error = None
                        except ValueError as exc:
                            exercice_error = str(exc)

                        if exercice_error:
                            errors.append(exercice_error)
                            return_open = True
                        elif company_is_required and selected_compagnie is None:
                            return_open = True
                        elif total_contrepartie != montant_releve:
                            errors.append(
                                f"La somme des lignes Tr_detail ({total_contrepartie:.2f}) doit egaler le montant du relevé ({montant_releve:.2f})."
                            )
                            return_open = True
                        else:
                            return_open = False

                        if return_open:
                            pass
                        else:
                            # Sens comptable:
                            # - Depot => compte lie au debit (+), lignes modal au credit (-)
                            # - Retrait => compte lie au credit (-), lignes modal au debit (+)
                            montant_compte_lie = montant_releve if depot_present else -montant_releve
                            signe_contrepartie = Decimal('-1') if depot_present else Decimal('1')

                            with transaction.atomic():
                                source_nom = ''
                                if releve.compte_releve_id and releve.compte_releve and releve.compte_releve.nom_affichage:
                                    source_nom = releve.compte_releve.nom_affichage.strip()
                                if not source_nom:
                                    source_nom = f"{(releve.no_compte or '').strip()} {(releve.type_compte or '').strip()}".strip()
                                if not source_nom:
                                    source_nom = '0024883 EOP'

                                source_releve, _ = Source.objects.get_or_create(nom=source_nom[:15])
                                tr_desc = tr_desc_form.save(commit=False)
                                if not tr_desc.no_ej:
                                    tr_desc.no_ej = _next_no_ej(tr_desc.date)
                                tr_desc.desc_releve = tr_desc.desc_releve or releve.desc_releve or ''
                                tr_desc.source = source_releve
                                if selected_compagnie_type == 'fournisseur':
                                    tr_desc.fournisseur = compagnie_ecriture
                                    tr_desc.client = None
                                else:
                                    tr_desc.client = compagnie_ecriture
                                    tr_desc.fournisseur = None
                                tr_desc.save()

                                releve.desc_ctb = tr_desc.desc_ctb or releve.desc_releve

                                Tr_detail.objects.filter(tr_desc=tr_desc).delete()

                                Tr_detail.objects.create(
                                    tr_desc=tr_desc,
                                    compte=compte_lie,
                                    montant=montant_compte_lie,
                                )

                                for compte, montant in detail_rows:
                                    Tr_detail.objects.create(
                                        tr_desc=tr_desc,
                                        compte=compte,
                                        montant=signe_contrepartie * montant,
                                    )

                                if releve.compte_releve_id and not releve.compte_releve.compte_comptable_id:
                                    releve.compte_releve.compte_comptable = compte_lie
                                    releve.compte_releve.save(update_fields=['compte_comptable'])

                                releve.ecriture_creee = True
                                releve.ecriture_tr_desc = tr_desc
                                releve.save(update_fields=['desc_ctb', 'ecriture_creee', 'ecriture_tr_desc'])

                                lignes_liees = []
                                for compte, montant in detail_rows:
                                    counterpart = _find_releve_counterpart(releve, compte, montant)
                                    if not counterpart:
                                        continue
                                    if counterpart.ecriture_tr_desc_id and counterpart.ecriture_tr_desc_id != tr_desc.id:
                                        continue

                                    counterpart.ecriture_creee = True
                                    counterpart.ecriture_tr_desc = tr_desc
                                    counterpart.save(update_fields=['ecriture_creee', 'ecriture_tr_desc'])
                                    lignes_liees.append(str(counterpart.no_ligne or counterpart.id))

                            if existing_tr_desc:
                                msg = f"✓ Écriture {tr_desc.no_ej} mise à jour pour la ligne #{releve.no_ligne}."
                            else:
                                msg = f"✓ Écriture {tr_desc.no_ej} créée pour la ligne #{releve.no_ligne}."
                            if is_virement_inter_releves:
                                msg += " Virement inter-relevés: compagnie laissée vide."
                            if lignes_liees:
                                msg += f" Contrepartie reliée: ligne(s) {', '.join(lignes_liees)}."
                            errors.insert(0, msg)
                            open_releve_modal = False
                            modal_releve_id = ''
                            selected_compagnie_id = ''
                            tr_desc_form = TrDescForm(prefix='trdesc_releve')
                            tr_detail_formset = TrDetailFormSet(
                                prefix='detail_releve',
                                form_kwargs={'comptes_queryset': comptes_queryset}
                            )

        elif request.FILES.get('csv_file'):
            csv_file = request.FILES['csv_file']

            try:
                with transaction.atomic():
                    import_messages = _import_releve_csv(csv_file)
                    if any(not message.startswith("✓") for message in import_messages):
                        transaction.set_rollback(True)
                    errors.extend(import_messages)
            except Exception as e:
                errors.append(f"Erreur lors de la lecture du fichier: {str(e)}")

    _relink_releves_compte_type_mismatch()

    working_period = get_working_period(request)
    selected_periode = working_period['value']
    mois_selectionne = str(working_period['month'])
    annee_selectionnee = str(working_period['year'])
    periode_label = working_period['label']

    comptes_releves = CompteReleve.objects.order_by('type_onglet', 'nom_affichage')

    # Construire les données par compte pour l'affichage dans les onglets
    releves_qs = Releve.objects.select_related(
        'compte_releve',
        'compte_releve__compte_comptable',
        'ecriture_tr_desc',
        'ecriture_tr_desc__client',
        'ecriture_tr_desc__fournisseur',
    ).prefetch_related(
        Prefetch('ecriture_tr_desc__details', queryset=Tr_detail.objects.select_related('compte').order_by('id')),
    ).order_by('date', 'no_ligne')
    if annee_selectionnee.isdigit():
        releves_qs = releves_qs.filter(date__year=int(annee_selectionnee))
    if mois_selectionne.isdigit() and 1 <= int(mois_selectionne) <= 12:
        releves_qs = releves_qs.filter(date__month=int(mois_selectionne))

    compte_releve_ids_with_lines = set(
        releves_qs.values_list('compte_releve_id', flat=True).distinct()
    )
    unlinked_comptes_with_lines = [
        compte for compte in comptes_releves
        if compte.compte_comptable_id is None and compte.pk in compte_releve_ids_with_lines
    ]

    # Precalcule une seule fois (pas par ligne) les ecritures existantes utilisables comme
    # modele, pour la suggestion automatique "ecriture similaire" affichee dans le tableau.
    candidats_ecriture_similaire = _candidats_ecriture_similaire()

    releves_par_compte = {}
    for compte in comptes_releves:
        releves_list = list(releves_qs.filter(compte_releve=compte))

        for releve in releves_list:
            suggested_compte = _suggest_compte_from_releve(releve)
            suggested_montant = _suggest_montant_from_releve(releve)
            releve.suggested_compte_id = suggested_compte.pk if suggested_compte else ''
            releve.suggested_compte_label = str(suggested_compte) if suggested_compte else ''
            releve.compte_lie_id = (
                releve.compte_releve.compte_comptable_id
                if releve.compte_releve_id and releve.compte_releve
                else releve.suggested_compte_id
            )
            releve.suggested_montant = suggested_montant
            releve.ecriture_date = ''
            releve.ecriture_description = ''
            releve.ecriture_compagnie_id = ''
            releve.ecriture_details_json = '[]'
            releve.similaire_disponible = False
            releve.similaire_no_ej = ''
            releve.similaire_desc_ctb = ''
            releve.similaire_compagnie_id = ''
            releve.similaire_details_json = '[]'

            if not releve.ecriture_creee:
                meilleures = _meilleures_correspondances(releve, candidats_ecriture_similaire, limit=1)
                if meilleures:
                    meilleure = meilleures[0]
                    releve.similaire_disponible = True
                    releve.similaire_no_ej = meilleure['no_ej']
                    releve.similaire_desc_ctb = meilleure['desc_ctb']
                    releve.similaire_compagnie_id = meilleure['compagnie_id']
                    releve.similaire_details_json = json.dumps(meilleure['details'])

            tr_desc = releve.ecriture_tr_desc
            if tr_desc:
                releve.ecriture_date = tr_desc.date.strftime('%Y-%m-%d') if tr_desc.date else ''
                releve.ecriture_description = tr_desc.desc_ctb or ''
                if tr_desc.fournisseur_id:
                    releve.ecriture_compagnie_id = f"fournisseur:{tr_desc.fournisseur_id}"
                elif tr_desc.client_id:
                    releve.ecriture_compagnie_id = f"client:{tr_desc.client_id}"
                else:
                    releve.ecriture_compagnie_id = ''

                montant_releve = abs(releve.depot) if (releve.depot is not None and releve.depot != 0) else abs(releve.retrait) if (releve.retrait is not None and releve.retrait != 0) else None
                montant_compte_lie = None
                if montant_releve is not None:
                    if releve.depot is not None and releve.depot != 0 and not (releve.retrait is not None and releve.retrait != 0):
                        montant_compte_lie = montant_releve
                    elif releve.retrait is not None and releve.retrait != 0 and not (releve.depot is not None and releve.depot != 0):
                        montant_compte_lie = -montant_releve

                compte_lie_id = None
                if releve.compte_releve_id and releve.compte_releve and releve.compte_releve.compte_comptable_id:
                    compte_lie_id = releve.compte_releve.compte_comptable_id

                detail_rows = _tr_desc_detail_rows_hors_compte_lie(tr_desc, compte_lie_id, montant_compte_lie)
                releve.ecriture_details_json = json.dumps(detail_rows)
        
        # Calculer le solde cumulatif pour les cartes de crédit
        if compte.type_onglet in ['carte_credit', 'marge_credit']:
            solde_cumulatif = Decimal('0')
            for releve in releves_list:
                # Pour les cartes: solde = solde_precedent + depot - retrait
                if releve.depot:
                    solde_cumulatif += abs(releve.depot)
                if releve.retrait:
                    solde_cumulatif -= abs(releve.retrait)
                # On met à jour le solde de l'objet (pour l'affichage seulement)
                releve.solde = solde_cumulatif
        
        releves_par_compte[compte.pk] = releves_list

    # Fichiers source distincts par compte
    fichiers_par_compte = {
        compte.pk: list(
            releves_qs.filter(compte_releve=compte)
            .order_by('fichier_source')
            .values_list('fichier_source', flat=True)
            .distinct()
        )
        for compte in comptes_releves
    }

    # Grouper les comptes par type_onglet pour les 4 onglets fixes
    types_onglets = [
        ('banque',        'Banque'),
        ('carte_credit',  'Carte de crédit'),
        ('marge_credit',  'Marge de crédit'),
        ('autre',         'Autre'),
    ]
    groupes = [
        {
            'type_onglet': type_val,
            'label': label,
            'comptes': [c for c in comptes_releves if c.type_onglet == type_val],
        }
        for type_val, label in types_onglets
    ]

    response = render(request, "releves/index.html", {
        'title': "Relevé bancaire",
        'errors': errors,
        'unlinked_comptes_with_lines': unlinked_comptes_with_lines,
        'open_releve_modal': open_releve_modal,
        'modal_releve_id': modal_releve_id,
        'selected_compagnie_id': selected_compagnie_id,
        # 'compagnies': compagnies,
        'compte_cap_id': settings_instance.cap_id if settings_instance else '',
        'compte_car_id': settings_instance.car_id if settings_instance else '',
        'selected_periode': selected_periode,
        'mois_selectionne': mois_selectionne,
        'annee_selectionnee': annee_selectionnee,
        'periode_label': periode_label,
        'tr_desc_form': tr_desc_form,
        'tr_detail_formset': tr_detail_formset,
        'groupes': groupes,
        'releves_par_compte': releves_par_compte,
        'fichiers_par_compte': fichiers_par_compte,
    })

    return response


@login_required
@require_POST
def creer_cheque(request):
    if request.POST.get('annule') == '1':
        no_cheque = (request.POST.get('no_cheque') or '').strip()
        if not no_cheque:
            return JsonResponse({'error': "Numéro de chèque manquant."}, status=400)
        if Cheque.objects.filter(no_cheque=no_cheque).exists():
            return JsonResponse({'error': f"Le numéro {no_cheque} est déjà utilisé."}, status=400)

        cheque = Cheque.objects.create(no_cheque=no_cheque, date_emission=timezone.now().date(), annule=True)
        return JsonResponse({'ok': True, 'cheque_id': cheque.id, 'annule': True})

    settings_instance = get_setting()
    if not settings_instance or not settings_instance.compte_cheques:
        return JsonResponse(
            {'error': "Configure le compte chèques dans Setting avant d'inscrire un chèque."},
            status=400,
        )

    form = ChequeForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'error': "Formulaire invalide.", 'errors': form.errors}, status=400)

    fournisseur = form.cleaned_data.get('fournisseur')
    client = form.cleaned_data.get('client')

    if not fournisseur and not client:
        return JsonResponse({'error': "Choisis un fournisseur ou un client pour ce chèque."}, status=400)
    if fournisseur and client:
        return JsonResponse({'error': "Choisis soit un fournisseur, soit un client, pas les deux."}, status=400)

    if fournisseur and not settings_instance.cap:
        return JsonResponse({'error': "Configure le compte CAP dans Setting avant d'inscrire un chèque à un fournisseur."}, status=400)
    if client and not settings_instance.car:
        return JsonResponse({'error': "Configure le compte CAR dans Setting avant d'inscrire un chèque à un client."}, status=400)

    with transaction.atomic():
        no_cheque = form.cleaned_data['no_cheque']
        source_cheque, _ = Source.objects.get_or_create(nom=f"Ch # {no_cheque}")

        try:
            verifier_exercice_modifiable(form.cleaned_data['date_emission'])
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=400)

        tr_desc_kwargs = {
            'no_ej': _next_no_ej(form.cleaned_data['date_emission']),
            'date': form.cleaned_data['date_emission'],
            'desc_ctb': form.cleaned_data['description'],
            'source': source_cheque,
        }
        if fournisseur:
            tr_desc_kwargs['fournisseur'] = fournisseur
        else:
            tr_desc_kwargs['client'] = client

        tr_desc = Tr_desc.objects.create(**tr_desc_kwargs)

        montant = form.cleaned_data['montant']
        compte_contrepartie = settings_instance.cap if fournisseur else settings_instance.car
        Tr_detail.objects.create(tr_desc=tr_desc, compte=compte_contrepartie, montant=montant)
        Tr_detail.objects.create(tr_desc=tr_desc, compte=settings_instance.compte_cheques, montant=-montant)

        cheque = form.save(commit=False)
        cheque.tr_desc = tr_desc
        cheque.save()

    return JsonResponse({'ok': True, 'no_ej': tr_desc.no_ej, 'cheque_id': cheque.id})


def cheques(request):
    cheques_list = Cheque.objects.select_related('client', 'fournisseur').order_by('-date_emission', '-id')
    return render(request, "cheques/index.html", {
        'title': "Chèques",
        'cheques': cheques_list,
    })