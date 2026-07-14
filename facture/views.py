from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.core.exceptions import ValidationError
from django.db import transaction, connections, DatabaseError
from django.db.models import Prefetch, Sum, Value, DecimalField, Case, When, IntegerField, F
from django.db.models.functions import Coalesce
from django.utils import timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from calendar import monthrange
from types import SimpleNamespace
import re
import csv
import json
from io import TextIOWrapper
import chardet
from datetime import date, datetime
from facture.constants import MONTH_LABELS_FR
from facture.models import Compagnie, Tr_desc, Tr_detail, Source, Releve, RapportTaxes, CompteReleve, CompagnieSoldeDepart
from compte.models import Setting
from facture.context_processors import build_fiscal_period_options
from facture.forms import CompagnieForm, TrDescForm, TrDetailFormSet
from facture.utils import (
    TAX_AUTHORITY_COMPANY_NAMES,
    ensure_tax_authority_companies,
    expert_required,
    get_setting,
    parse_decimal,
    split_debit_credit,
    tax_target_mode_from_setting,
)
from compte.models import Compte, SoldeAuxLivres


def _money(value):
    return (value or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _solde_depart_par_compte():
    return {
        row['compte_id']: (row['solde_depart'] or Decimal('0'))
        for row in SoldeAuxLivres.objects.values('compte_id', 'solde_depart')
    }


def _aggregate_debit_credit(details_qs):
    total_debit = details_qs.filter(montant__gte=0).aggregate(
        total=Coalesce(
            Sum('montant'),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    ).get('total') or Decimal('0')

    total_credit_raw = details_qs.filter(montant__lt=0).aggregate(
        total=Coalesce(
            Sum('montant'),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    ).get('total') or Decimal('0')

    return total_debit, abs(total_credit_raw)


def _ledger_db_alias():
    return Tr_detail.objects.all().db


def _coerce_decimal(value):
    if value is None or value == '':
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _fetch_balance_rows_from_sql_view():
    db_alias = _ledger_db_alias()
    query = """
        SELECT
            compte_id,
            compte_numero,
            compte_libelle,
            solde_depart,
            debit,
            credit
        FROM facture_v_balance_verification
        ORDER BY compte_numero
    """

    rows = []
    with connections[db_alias].cursor() as cursor:
        cursor.execute(query)
        for compte_id, compte_numero, compte_libelle, solde_depart, debit, credit in cursor.fetchall():
            rows.append({
                'compte': SimpleNamespace(
                    pk=compte_id,
                    numero=compte_numero,
                    libelle=compte_libelle,
                ),
                'debit': _coerce_decimal(debit),
                'credit': _coerce_decimal(credit),
                'solde_depart': _coerce_decimal(solde_depart),
            })

    total_debit = sum((row['debit'] for row in rows), Decimal('0'))
    total_credit = sum((row['credit'] for row in rows), Decimal('0'))
    return rows, total_debit, total_credit


def _fetch_grand_livre_from_sql_view():
    db_alias = _ledger_db_alias()
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
                        'debit': solde_depart if solde_depart >= 0 else Decimal('0'),
                        'credit': abs(solde_depart) if solde_depart < 0 else Decimal('0'),
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

    grand_total_solde = grand_total_debit - grand_total_credit
    is_balanced = (grand_total_debit == grand_total_credit) and (grand_total_solde == Decimal('0'))

    return comptes, grand_total_debit, grand_total_credit, grand_total_solde, is_balanced


def _fetch_compte_solde_from_balance_view(compte_id):
    if not compte_id:
        return Decimal('0')

    db_alias = _ledger_db_alias()
    query = """
        SELECT solde
        FROM facture_v_balance_verification
        WHERE compte_id = %s
    """

    with connections[db_alias].cursor() as cursor:
        cursor.execute(query, [compte_id])
        row = cursor.fetchone()

    return _coerce_decimal(row[0]) if row else Decimal('0')


def _fetch_compte_mode_blocks_from_sql_view(mode, compte_id, compagnies):
    db_alias = _ledger_db_alias()
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
    return render(request, "facture/index.html", {'title': "Le carnet à Bibi"})


def journal_general(request):
    details_queryset = Tr_detail.objects.select_related('compte').annotate(
        debit_first=Case(
            When(montant__gte=0, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by('debit_first', 'compte_id', 'id')

    total_debit, total_credit = _aggregate_debit_credit(Tr_detail.objects.all())

    journal_entries = list(Tr_desc.objects.select_related(
        'compagnie',
        'source'
    ).prefetch_related(
        Prefetch('details', queryset=details_queryset),
        'releves_sources',
    ))

    for entry in journal_entries:
        releve_source = entry.releves_sources.all().first()
        if releve_source and releve_source.desc_ctb:
            entry.description = releve_source.desc_ctb

    def no_ej_sort_value(entry):
        match = re.match(r'^EJ(\d+)$', entry.no_ej or '')
        if match:
            return int(match.group(1))
        return -1

    journal_entries.sort(key=no_ej_sort_value, reverse=True)

    settings_instance = get_setting()
    report_date = max((entry.date for entry in journal_entries if entry.date), default=None)
    report_year_label = _closing_date_label(report_date, settings_instance)

    return render(request, "facture/journal_general.html", {
        'title': "Journal général",
        'journal_entries': journal_entries,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'report_year_label': report_year_label,
    })


def grand_livre(request):
    settings_instance = get_setting()
    solde_depart_par_compte = _solde_depart_par_compte()
    report_date = Tr_desc.objects.order_by('-date').values_list('date', flat=True).first()
    report_year_label = _closing_date_label(report_date, settings_instance)
    try:
        comptes, grand_total_debit, grand_total_credit, grand_total_solde, is_balanced = _fetch_grand_livre_from_sql_view()
    except DatabaseError:
        # Fallback temporaire tant que la migration SQL view n'est pas appliquee.
        def is_bilan_account(compte):
            numero = getattr(compte, 'numero', None)
            return numero is not None and 1000 <= numero <= 3999

        details = Tr_detail.objects.select_related(
            'compte',
            'tr_desc__compagnie',
            'tr_desc__source'
        ).order_by('compte_id', 'tr_desc__date', 'tr_desc_id', 'id')

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
                        'debit': solde_depart if solde_depart >= 0 else Decimal('0'),
                        'credit': abs(solde_depart) if solde_depart < 0 else Decimal('0'),
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
                        'debit': solde_depart if solde_depart >= 0 else Decimal('0'),
                        'credit': abs(solde_depart) if solde_depart < 0 else Decimal('0'),
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

            current_entries.append({
                'date': detail.tr_desc.date,
                'no_ej': detail.tr_desc.no_ej,
                'compagnie': detail.tr_desc.compagnie,
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

        grand_total_solde = grand_total_debit - grand_total_credit
        is_balanced = (grand_total_debit == grand_total_credit) and (grand_total_solde == Decimal('0'))

    return render(request, "facture/grand_livre.html", {
        'title': "Grand livre",
        'comptes': comptes,
        'grand_total_debit': grand_total_debit,
        'grand_total_credit': grand_total_credit,
        'grand_total_solde': grand_total_solde,
        'is_balanced': is_balanced,
        'report_year_label': report_year_label,
    })


def _next_no_ej():
    last_tr_desc = Tr_desc.objects.order_by('-id').first()
    if not last_tr_desc:
        return "EJ1"

    match = re.match(r'^EJ(\d+)$', last_tr_desc.no_ej or '')
    if not match:
        return "EJ1"

    return f"EJ{int(match.group(1)) + 1}"


def _company_invoices_queryset(company):
    return Tr_desc.objects.filter(compagnie=company).annotate(
        invoice_total=Coalesce(
            Sum('details__montant'),
            Value(0),
            output_field=DecimalField(max_digits=10, decimal_places=2)
        )
    ).prefetch_related('details__compte').order_by('-date', '-id')


def _serialize_invoice(tr):
    settings_instance = get_setting()
    company_mode = (getattr(tr.compagnie, 'cap_ou_car', '') or '').strip().upper()
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


def company_invoices_api(request, company_id):
    company = Compagnie.objects.filter(pk=company_id).first()
    if not company:
        return JsonResponse({'invoices': []}, status=404)

    invoices = [_serialize_invoice(tr) for tr in _company_invoices_queryset(company)]
    return JsonResponse({'invoices': invoices})


def facture(request):
    title = "Facture"
    company_form = CompagnieForm(request.POST or None, prefix='company')
    settings_instance = Setting.objects.select_related(
        'compte_tps_percue',
        'compte_tps_payee',
        'compte_tvq_percue',
        'compte_tvq_payee',
        'compte_fr_retard',
    ).first()
    comptes_count = Compte.objects.count()
    compagnies = Compagnie.objects.prefetch_related(
        'comptes',
        Prefetch(
            'tr_desc',
            queryset=Tr_desc.objects.annotate(
                invoice_total=Coalesce(
                    Sum('details__montant'),
                    Value(0),
                    output_field=DecimalField(max_digits=10, decimal_places=2)
                )
            ).prefetch_related('details__compte').order_by('-date', '-id')
        )
    ).exclude(nom__in=TAX_AUTHORITY_COMPANY_NAMES).all()
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
    for compagnie in compagnies:
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
        company_mode = (compagnie.cap_ou_car or '').strip().upper()
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

        companies_comptes[str(compagnie.pk)] = comptes_company
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
        companies_factures[str(compagnie.pk)] = company_invoices

    tr_desc_form = TrDescForm(prefix='trdesc')
    tr_detail_formset = TrDetailFormSet(
        prefix='detail',
        form_kwargs={'comptes_queryset': comptes_queryset}
    )
    open_tr_modal = False
    selected_company_id = ''
    selected_company_name = ''
    editing_tr_desc_id = ''

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_company':
            if company_form.is_valid():
                company_form.save()
                return redirect('facture')

        elif action == 'add_tr_desc':
            selected_company_id = request.POST.get('selected_company_id', '')
            selected_company = Compagnie.objects.filter(pk=selected_company_id).first()
            editing_tr_desc_id = (request.POST.get('editing_tr_desc_id') or '').strip()
            editing_tr_desc = None
            company_mode = ''
            forced_compte = None

            try:
                facture_total_value = _parse_facture_total(request.POST.get('facture_total', '0'))
            except InvalidOperation:
                facture_total_value = None

            if selected_company and editing_tr_desc_id:
                editing_tr_desc = Tr_desc.objects.filter(
                    pk=editing_tr_desc_id,
                    compagnie=selected_company
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
                company_mode = (selected_company.cap_ou_car or '').strip().upper()

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
                with transaction.atomic():
                    tr_desc = tr_desc_form.save(commit=False)
                    source_facture, _ = Source.objects.get_or_create(nom='Facture')
                    sign_multiplier = -1 if tr_desc.note_de_credit else 1
                    tr_desc.compagnie = selected_company
                    if not tr_desc.no_ej:
                        tr_desc.no_ej = _next_no_ej()
                    if not tr_desc.source_id:
                        tr_desc.source = source_facture
                    tr_desc.save()

                    if editing_tr_desc:
                        Tr_detail.objects.filter(tr_desc=tr_desc).delete()

                    detail_rows = []
                    for form in tr_detail_formset:
                        cleaned_data = form.cleaned_data
                        if not cleaned_data:
                            continue
                        compte = cleaned_data.get('compte')
                        montant = cleaned_data.get('montant')
                        if compte and montant is not None:
                            detail_rows.append((compte, montant))

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

    return render(request, "facture/facture.html", {
        'title': title,
        'company_form': company_form,
        'comptes_count': comptes_count,
        'compagnies': compagnies,
        'companies': compagnies,
        'tr_desc_form': tr_desc_form,
        'tr_detail_formset': tr_detail_formset,
        'next_no_ej': _next_no_ej(),
        'open_tr_modal': open_tr_modal,
        'selected_company_id': selected_company_id,
        'selected_company_name': selected_company_name,
        'editing_tr_desc_id': editing_tr_desc_id,
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


def balance_de_verification(request):
    settings_instance = get_setting()
    report_date = Tr_desc.objects.order_by('-date').values_list('date', flat=True).first()
    report_year_label = _closing_date_label(report_date, settings_instance)
    try:
        rows, total_debit, total_credit = _fetch_balance_rows_from_sql_view()
    except DatabaseError:
        # Fallback temporaire tant que la migration SQL view n'est pas appliquee.
        details_qs = Tr_detail.objects.all()
        solde_depart_par_compte = _solde_depart_par_compte()
        total_par_compte = dict(
            details_qs.values('compte_id').annotate(
                total=Coalesce(
                    Sum('montant'),
                    Value(0),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            ).values_list('compte_id', 'total')
        )

        rows = []
        total_debit = Decimal('0')
        total_credit = Decimal('0')

        for compte in Compte.objects.select_related('no_total').order_by('numero'):
            solde_depart = solde_depart_par_compte.get(compte.pk, Decimal('0'))
            total_mouvements = total_par_compte.get(compte.pk, Decimal('0'))

            if total_mouvements == Decimal('0') and solde_depart == Decimal('0'):
                continue

            solde = solde_depart + total_mouvements

            debit = solde if solde >= 0 else Decimal('0')
            credit = abs(solde) if solde < 0 else Decimal('0')
            rows.append({
                'compte': compte,
                'debit': debit,
                'credit': credit,
                'solde_depart': solde_depart,
            })
            total_debit += debit
            total_credit += credit

    is_balanced = total_debit.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) == total_credit.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return render(request, "facture/balance_de_verification.html", {
        'title': "Balance de vérification",
        'rows': rows,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'is_balanced': is_balanced,
        'report_year_label': report_year_label,
    })


def _build_compte_mode_context(mode, settings_instance):
    if mode not in {Compagnie.MODE_CAP, Compagnie.MODE_CAR}:
        raise ValueError("Mode invalide pour compte mode")

    is_cap = mode == Compagnie.MODE_CAP
    compte_id = settings_instance.cap_id if (settings_instance and is_cap) else settings_instance.car_id if settings_instance else None
    compagnies = Compagnie.objects.filter(cap_ou_car=mode).order_by('nom')

    report_date = Tr_desc.objects.filter(
        compagnie__cap_ou_car=mode,
    ).order_by('-date').values_list('date', flat=True).first()
    report_year_label = _closing_date_label(report_date, settings_instance)

    compte_solde_grand_livre = Decimal('0')
    blocks = []
    total_des_soldes = Decimal('0')
    soldes_reportes_map = {
        row.compagnie_id: (row.montant or Decimal('0'))
        for row in CompagnieSoldeDepart.objects.filter(compagnie__cap_ou_car=mode)
    }

    if compte_id:
        try:
            compte_solde_grand_livre = _fetch_compte_solde_from_balance_view(compte_id)
            blocks, total_des_soldes = _fetch_compte_mode_blocks_from_sql_view(
                mode,
                compte_id,
                compagnies,
            )
        except DatabaseError:
            compte_solde_grand_livre = Tr_detail.objects.filter(
                compte_id=compte_id,
            ).aggregate(total=Sum('montant')).get('total') or Decimal('0')

            details = Tr_detail.objects.select_related(
                'tr_desc__compagnie',
                'tr_desc__source',
            ).filter(
                tr_desc__compagnie__cap_ou_car=mode,
                compte_id=compte_id,
            ).order_by(
                'tr_desc__compagnie__nom',
                'tr_desc__date',
                'tr_desc_id',
                'id',
            )

            rows_by_company = {compagnie.id: [] for compagnie in compagnies}
            soldes_by_company = {compagnie.id: Decimal('0') for compagnie in compagnies}

            for detail in details:
                compagnie_id = detail.tr_desc.compagnie_id
                if compagnie_id not in rows_by_company:
                    continue

                montant = detail.montant or Decimal('0')
                debit = montant if montant >= 0 else Decimal('0')
                credit = abs(montant) if montant < 0 else Decimal('0')
                releve_source = detail.tr_desc.releves_sources.all().first()
                description = releve_source.desc_ctb if releve_source and releve_source.desc_ctb else detail.tr_desc.desc_ctb

                soldes_by_company[compagnie_id] += montant

                rows_by_company[compagnie_id].append({
                    'date': detail.tr_desc.date,
                    'source': detail.tr_desc.source,
                    'description': description,
                    'debit': debit,
                    'credit': credit,
                    'solde': soldes_by_company[compagnie_id],
                })

            for compagnie in compagnies:
                company_solde = soldes_by_company.get(compagnie.id, Decimal('0'))
                total_des_soldes += company_solde
                blocks.append({
                    'compagnie': compagnie,
                    'rows': rows_by_company.get(compagnie.id, []),
                    'solde_final': company_solde,
                })

    # Les soldes reportes (repartition CAP/CAR) doivent apparaitre dans les soldes par compagnie.
    for block in blocks:
        compagnie_id = block['compagnie'].id
        solde_reporte = soldes_reportes_map.get(compagnie_id, Decimal('0'))
        rows = block.get('rows', [])

        if solde_reporte != Decimal('0'):
            for row in rows:
                row['solde'] = (row.get('solde') or Decimal('0')) + solde_reporte

            rows.insert(0, {
                'date': None,
                'source': None,
                'description': 'Solde reporté',
                'debit': solde_reporte if solde_reporte >= 0 else Decimal('0'),
                'credit': abs(solde_reporte) if solde_reporte < 0 else Decimal('0'),
                'solde': solde_reporte,
            })

        block['solde_reporte'] = solde_reporte
        block['solde_final'] = (block.get('solde_final') or Decimal('0')) + solde_reporte

    total_des_soldes = sum((block.get('solde_final') or Decimal('0') for block in blocks), Decimal('0'))

    total_des_soldes = _money(total_des_soldes)
    compte_solde_grand_livre = _money(compte_solde_grand_livre)
    ecart_solde = _money(total_des_soldes - compte_solde_grand_livre)
    mode_code = 'CAP' if is_cap else 'CAR'

    return {
        'blocks': blocks,
        'total_des_soldes': total_des_soldes,
        'compte_solde_grand_livre': compte_solde_grand_livre,
        'ecart_solde': ecart_solde,
        'is_solde_coherent': ecart_solde == Decimal('0.00'),
        'mode_compte': settings_instance.cap if (settings_instance and is_cap) else settings_instance.car if settings_instance else None,
        'mode_code': mode_code,
        'report_year_label': report_year_label,
    }


def compte_a_payer(request):
    settings_instance = get_setting()
    context = _build_compte_mode_context(Compagnie.MODE_CAP, settings_instance)
    context['title'] = "Comptes à payer"
    return render(request, "facture/compte_mode.html", context)


def compte_a_recevoir(request):
    settings_instance = get_setting()
    context = _build_compte_mode_context(Compagnie.MODE_CAR, settings_instance)
    context['title'] = "Comptes à recevoir"
    return render(request, "facture/compte_mode.html", context)


def rapport_de_taxes(request):
    def parse_period(raw_value):
        value = (raw_value or '').strip()
        try:
            year_str, month_str = value.split('-')
            year = int(year_str)
            month = int(month_str)
            if 1 <= month <= 12:
                return year, month, f"{year:04d}-{month:02d}"
        except (ValueError, AttributeError):
            pass
        return None, None, None

    settings_instance = get_setting()
    tps_percue_id = None
    tps_payee_id = None
    tvq_percue_id = None
    tvq_payee_id = None
    tax_account_ids = []
    if settings_instance:
        tps_percue_id = settings_instance.compte_tps_percue_id
        tps_payee_id = settings_instance.compte_tps_payee_id
        tvq_percue_id = settings_instance.compte_tvq_percue_id
        tvq_payee_id = settings_instance.compte_tvq_payee_id
        tax_account_ids = [
            account_id for account_id in [
                tps_percue_id,
                tps_payee_id,
                tvq_percue_id,
                tvq_payee_id,
            ] if account_id
        ]

    def build_tax_blocks(details):
        blocks = {
            'TPS': {
                'rows': [],
                'total_percue': Decimal('0'),
                'total_percue_signee': Decimal('0'),
                'total_payee': Decimal('0'),
                'solde_a_reclamer': Decimal('0'),
            },
            'TVQ': {
                'rows': [],
                'total_percue': Decimal('0'),
                'total_percue_signee': Decimal('0'),
                'total_payee': Decimal('0'),
                'solde_a_reclamer': Decimal('0'),
            },
        }

        for detail in details:
            tax_type = None
            percue = None
            percue_signee = None
            payee = None
            amount = _money(detail.montant)

            if detail.compte_id == tps_percue_id:
                tax_type = 'TPS'
                percue_signee = amount
                percue = _money(abs(amount))
            elif detail.compte_id == tps_payee_id:
                tax_type = 'TPS'
                payee = amount
            elif detail.compte_id == tvq_percue_id:
                tax_type = 'TVQ'
                percue_signee = amount
                percue = _money(abs(amount))
            elif detail.compte_id == tvq_payee_id:
                tax_type = 'TVQ'
                payee = amount

            if not tax_type:
                continue

            if percue is not None:
                blocks[tax_type]['total_percue'] = _money(blocks[tax_type]['total_percue'] + percue)
            if percue_signee is not None:
                blocks[tax_type]['total_percue_signee'] = _money(blocks[tax_type]['total_percue_signee'] + percue_signee)
            if payee is not None:
                blocks[tax_type]['total_payee'] = _money(blocks[tax_type]['total_payee'] + payee)

            blocks[tax_type]['rows'].append({
                'id': detail.id,
                'date': detail.tr_desc.date,
                'compagnie_nom': detail.tr_desc.compagnie.nom if detail.tr_desc.compagnie else '-',
                'facture': detail.tr_desc.desc_ctb or '-',
                'percue': percue,
                'payee': payee,
            })

        for tax_type in ('TPS', 'TVQ'):
            blocks[tax_type]['solde_a_reclamer'] = _money(
                blocks[tax_type]['total_percue_signee'] + blocks[tax_type]['total_payee']
            )

        return blocks

    base_tax_details = Tr_detail.objects.select_related(
        'tr_desc__compagnie',
        'compte',
        'rapport_taxes',
    ).filter(compte_id__in=tax_account_ids).order_by('tr_desc__date', 'id')

    feedback = []
    error_messages = []

    default_now = timezone.localdate()
    selected_year = default_now.year
    selected_month = default_now.month
    selected_month_value = f"{selected_year:04d}-{selected_month:02d}"

    incoming_month = request.GET.get('mois') or request.POST.get('selected_month') or request.POST.get('periode_mensuelle')
    parsed_year, parsed_month, parsed_value = parse_period(incoming_month)
    if incoming_month and not parsed_value:
        error_messages.append("Mois invalide. Utilise le format YYYY-MM.")
    if parsed_value:
        selected_year = parsed_year
        selected_month = parsed_month
        selected_month_value = parsed_value

    month_tax_details = base_tax_details.filter(
        tr_desc__date__year=selected_year,
        tr_desc__date__month=selected_month,
    )

    selected_report = RapportTaxes.objects.filter(
        annee=selected_year,
        mois=selected_month,
    ).first()

    if tax_account_ids and month_tax_details.exists():
        if not selected_report:
            selected_report = RapportTaxes.objects.create(annee=selected_year, mois=selected_month)

        if not selected_report.est_transmis:
            month_tax_details.filter(rapport_taxes__isnull=True).update(rapport_taxes=selected_report)

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
                removed_count = base_tax_details.filter(
                    pk=detail_id,
                    rapport_taxes=report,
                ).update(rapport_taxes=None)
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
            else:
                has_lines = base_tax_details.filter(rapport_taxes=report).exists()
                if not has_lines:
                    error_messages.append("Impossible de transmettre un rapport sans ligne de taxes.")
                else:
                    tax_mode = tax_target_mode_from_setting(settings_instance)
                    mode_label = "CAR" if tax_mode == Compagnie.MODE_CAR else "CAP"
                    target_compte = settings_instance.car if tax_mode == Compagnie.MODE_CAR and settings_instance else settings_instance.cap if settings_instance else None

                    if not target_compte:
                        error_messages.append(
                            f"Compte {mode_label} non configure dans Setting. Configure ce compte avant de transmettre le rapport."
                        )
                    else:
                        tax_account_map = {
                            'TPS': {
                                'percue': settings_instance.compte_tps_percue,
                                'payee': settings_instance.compte_tps_payee,
                            },
                            'TVQ': {
                                'percue': settings_instance.compte_tvq_percue,
                                'payee': settings_instance.compte_tvq_payee,
                            },
                        }

                        missing_accounts = [
                            f"{tax_name} {line_name}"
                            for tax_name, accounts in tax_account_map.items()
                            for line_name, account in accounts.items()
                            if account is None
                        ]
                        if missing_accounts:
                            error_messages.append(
                                "Comptes taxes manquants dans Setting pour la transmission: " + ", ".join(missing_accounts) + "."
                            )
                        else:
                            ensure_tax_authority_companies(settings_instance)
                            tax_companies = {
                                'TPS': Compagnie.objects.filter(nom='Revenu Canada TPS').first(),
                                'TVQ': Compagnie.objects.filter(nom='Revenu Quebec TVQ').first(),
                            }
                            if not tax_companies['TPS'] or not tax_companies['TVQ']:
                                error_messages.append("Impossible de preparer les compagnies fiscales TPS/TVQ.")
                            else:
                                report_rows = base_tax_details.filter(rapport_taxes=report)
                                tax_blocks = build_tax_blocks(report_rows)
                                source_rapport, _ = Source.objects.get_or_create(nom='Rapport de taxes')
                                report_date = date(report.annee, report.mois, monthrange(report.annee, report.mois)[1])
                                posted_count = 0

                                with transaction.atomic():
                                    for tax_name in ('TPS', 'TVQ'):
                                        percue_compte = tax_account_map[tax_name]['percue']
                                        payee_compte = tax_account_map[tax_name]['payee']
                                        compagnie_fiscale = tax_companies[tax_name]

                                        # Solde reel a la fin du mois: on poste l'inverse pour ramener
                                        # chaque compte de taxe a zero apres transmission.
                                        percue_balance = Tr_detail.objects.filter(
                                            compte=percue_compte,
                                            tr_desc__date__lte=report_date,
                                        ).aggregate(total=Sum('montant')).get('total') or Decimal('0')
                                        payee_balance = Tr_detail.objects.filter(
                                            compte=payee_compte,
                                            tr_desc__date__lte=report_date,
                                        ).aggregate(total=Sum('montant')).get('total') or Decimal('0')

                                        percue_amount = _money(-percue_balance)
                                        payee_amount = _money(-payee_balance)
                                        target_line_amount = _money(-(percue_amount + payee_amount))

                                        if percue_amount == Decimal('0') and payee_amount == Decimal('0') and target_line_amount == Decimal('0'):
                                            continue

                                        tr_desc = Tr_desc.objects.create(
                                            no_ej=_next_no_ej(),
                                            compagnie=compagnie_fiscale,
                                            date=report_date,
                                            desc_ctb=f"Rapport de taxes {tax_name} {report.annee}-{report.mois:02d}",
                                            source=source_rapport,
                                        )

                                        if percue_amount != Decimal('0'):
                                            Tr_detail.objects.create(
                                                tr_desc=tr_desc,
                                                compte=percue_compte,
                                                montant=percue_amount,
                                            )
                                        if payee_amount != Decimal('0'):
                                            Tr_detail.objects.create(
                                                tr_desc=tr_desc,
                                                compte=payee_compte,
                                                montant=payee_amount,
                                            )
                                        if target_line_amount != Decimal('0'):
                                            Tr_detail.objects.create(
                                                tr_desc=tr_desc,
                                                compte=target_compte,
                                                montant=target_line_amount,
                                            )
                                        posted_count += 1

                                    report.transmis_le = timezone.now()
                                    report.save(update_fields=['transmis_le'])

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
                report_date = date(report.annee, report.mois, monthrange(report.annee, report.mois)[1])
                expected_descriptions = [
                    f"Rapport de taxes TPS {report.annee}-{report.mois:02d}",
                    f"Rapport de taxes TVQ {report.annee}-{report.mois:02d}",
                ]

                with transaction.atomic():
                    source_rapport = Source.objects.filter(nom='Rapport de taxes').first()
                    transmission_entries = Tr_desc.objects.none()
                    if source_rapport:
                        transmission_entries = Tr_desc.objects.filter(
                            source=source_rapport,
                            date=report_date,
                            description__in=expected_descriptions,
                            compagnie__nom__in=TAX_AUTHORITY_COMPANY_NAMES,
                        )

                    deleted_entries = transmission_entries.count()
                    if deleted_entries:
                        transmission_entries.delete()

                    RapportTaxes.objects.filter(pk=report.pk).update(transmis_le=None)

                feedback.append(
                    f"Transmission annulee. Rapport remis en brouillon ({deleted_entries} ecriture(s) supprimee(s))."
                )

        elif action:
            error_messages.append("Action inconnue sur le rapport de taxes.")

    selected_report = RapportTaxes.objects.prefetch_related(
        Prefetch(
            'details_taxes',
            queryset=base_tax_details,
        )
    ).filter(annee=selected_year, mois=selected_month).first()

    if selected_report:
        selected_report.tax_blocks = build_tax_blocks(selected_report.details_taxes.all())

    return render(request, "facture/rapport_de_taxe.html", {
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
        base_qs = base_qs.filter(retrait=montant_cible)
    else:
        # Ligne courante: retrait. Contrepartie attendue: depot du meme montant.
        base_qs = base_qs.filter(depot=montant_cible)

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
                retrait = parse_decimal(row[7] if len(row) > 7 else '', none_if_blank=True)
                depot = parse_decimal(row[8] if len(row) > 8 else '', none_if_blank=True)
                solde = parse_decimal(row[13] if len(row) > 13 else '', none_if_blank=False) or Decimal('0')
            else:
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
                'retrait': retrait,
                'depot': depot,
                'solde': solde,
                'ecriture_creee': False,
            }

            releves.append(releve_data)

        except Exception as exc:
            errors.append(f"Ligne {row_num}: Erreur lors du parsing ({str(exc)})")
            continue

    if releves:
        try:
            with transaction.atomic():
                for data in releves:
                    Releve.objects.create(**data)
            errors.insert(0, f"✓ {len(releves)} ligne(s) ajoutée(s) à la base de données avec succès!")
        except Exception as exc:
            errors.append(f"Erreur lors de l'insertion: {str(exc)}")

    return errors


def releve_bancaire(request):
    releves = []
    errors = []
    open_releve_modal = False
    modal_releve_id = ''
    selected_compagnie_id = ''
    comptes_queryset = Compte.objects.all().order_by('numero')
    compagnies = Compagnie.objects.order_by('nom')

    tr_desc_form = TrDescForm(prefix='trdesc_releve')
    tr_detail_formset = TrDetailFormSet(
        prefix='detail_releve',
        form_kwargs={'comptes_queryset': comptes_queryset}
    )

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'create_ecriture':
            releve_id = (request.POST.get('releve_id') or '').strip()
            selected_compagnie_id = (request.POST.get('compagnie_id') or '').strip()
            modal_releve_id = releve_id
            open_releve_modal = True
            releve = Releve.objects.select_related('ecriture_tr_desc', 'compte_releve', 'compte_releve__compte_comptable').filter(pk=releve_id).first()
            existing_tr_desc = releve.ecriture_tr_desc if releve and releve.ecriture_creee and releve.ecriture_tr_desc_id else None
            selected_compagnie = None
            if selected_compagnie_id:
                selected_compagnie = Compagnie.objects.filter(pk=selected_compagnie_id).first()
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
                        "Aucun compte comptable lié au compte de relevé. Configure `compte_comptable` sur ce compte de relevé."
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
                        compagnie_ecriture = None if is_virement_inter_releves else selected_compagnie

                        montant_releve = abs(releve.depot) if depot_present else abs(releve.retrait)
                        total_contrepartie = sum((montant for _, montant in detail_rows), Decimal('0'))
                        if total_contrepartie != montant_releve:
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
                                    tr_desc.no_ej = _next_no_ej()
                                tr_desc.source = source_releve
                                tr_desc.compagnie = compagnie_ecriture
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
                errors.extend(_import_releve_csv(csv_file))
            except Exception as e:
                errors.append(f"Erreur lors de la lecture du fichier: {str(e)}")

    _relink_releves_compte_type_mismatch()

    settings_instance = get_setting()
    fiscal_period_options = build_fiscal_period_options(settings_instance)
    fiscal_period_map = {item['value']: item for item in fiscal_period_options}

    selected_periode = (request.GET.get('periode') or '').strip()
    cookie_periode = (request.COOKIES.get('releve_periode') or '').strip()

    # Compatibilite avec anciens parametres ?mois=MM&annee=YYYY
    legacy_mois = (request.GET.get('mois') or '').strip()
    legacy_annee = (request.GET.get('annee') or '').strip()
    legacy_periode = f"{legacy_annee}-{legacy_mois}" if legacy_mois and legacy_annee else ''
    if not selected_periode and legacy_periode in fiscal_period_map:
        selected_periode = legacy_periode

    if not selected_periode and cookie_periode in fiscal_period_map:
        selected_periode = cookie_periode

    if selected_periode not in fiscal_period_map and fiscal_period_options:
        selected_periode = fiscal_period_options[0]['value']

    selected_period = fiscal_period_map.get(selected_periode, {})
    mois_selectionne = selected_period.get('mois', '')
    annee_selectionnee = selected_period.get('annee', '')
    periode_label = selected_period.get('label', '')

    comptes_releves = CompteReleve.objects.order_by('type_onglet', 'nom_affichage')

    # Construire les données par compte pour l'affichage dans les onglets
    releves_qs = Releve.objects.select_related(
        'compte_releve',
        'compte_releve__compte_comptable',
        'ecriture_tr_desc',
        'ecriture_tr_desc__compagnie',
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

    releves_par_compte = {}
    for compte in comptes_releves:
        releves_list = list(releves_qs.filter(compte_releve=compte))

        for releve in releves_list:
            suggested_compte = _suggest_compte_from_releve(releve)
            suggested_montant = _suggest_montant_from_releve(releve)
            releve.suggested_compte_id = suggested_compte.pk if suggested_compte else ''
            releve.suggested_compte_label = str(suggested_compte) if suggested_compte else ''
            releve.suggested_montant = suggested_montant
            releve.ecriture_date = ''
            releve.ecriture_description = ''
            releve.ecriture_compagnie_id = ''
            releve.ecriture_details_json = '[]'

            tr_desc = releve.ecriture_tr_desc
            if tr_desc:
                releve.ecriture_date = tr_desc.date.strftime('%Y-%m-%d') if tr_desc.date else ''
                releve.ecriture_description = tr_desc.desc_ctb or ''
                releve.ecriture_compagnie_id = str(tr_desc.compagnie_id or '')

                detail_rows = []
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
                        'montant': str(abs(detail.montant or Decimal('0'))),
                    })

                releve.ecriture_details_json = json.dumps(detail_rows)
        
        # Calculer le solde cumulatif pour les cartes de crédit
        if compte.type_onglet in ['carte_credit', 'marge_credit']:
            solde_cumulatif = Decimal('0')
            for releve in releves_list:
                # Pour les cartes: solde = solde_precedent + depot - retrait
                if releve.depot:
                    solde_cumulatif += releve.depot
                if releve.retrait:
                    solde_cumulatif -= releve.retrait
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

    response = render(request, "facture/releve.html", {
        'title': "Relevé bancaire",
        'errors': errors,
        'unlinked_comptes_with_lines': unlinked_comptes_with_lines,
        'open_releve_modal': open_releve_modal,
        'modal_releve_id': modal_releve_id,
        'selected_compagnie_id': selected_compagnie_id,
        'compagnies': compagnies,
        'selected_periode': selected_periode,
        'mois_selectionne': mois_selectionne,
        'annee_selectionnee': annee_selectionnee,
        'periode_label': periode_label,
        'fiscal_period_options': fiscal_period_options,
        'tr_desc_form': tr_desc_form,
        'tr_detail_formset': tr_detail_formset,
        'groupes': groupes,
        'releves_par_compte': releves_par_compte,
        'fichiers_par_compte': fichiers_par_compte,
    })

    if selected_periode:
        response.set_cookie(
            'releve_periode',
            selected_periode,
            max_age=365 * 24 * 60 * 60,
            samesite='Lax',
        )

    return response



