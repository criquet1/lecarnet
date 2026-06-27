from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, Sum, Value, DecimalField, Case, When, IntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone
from decimal import Decimal, InvalidOperation
import re
import csv
import json
from io import TextIOWrapper
import chardet
from datetime import datetime
from facture.models import Compagnie, Tr_desc, Tr_detail, Source, Setting, Releve, RapportTaxes, CompteReleve
from facture.forms import CompagnieForm, TrDescForm, TrDetailFormSet
from compte.models import Compte


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

    journal_entries = Tr_desc.objects.select_related(
        'compagnie',
        'source'
    ).prefetch_related(
        Prefetch('details', queryset=details_queryset)
    ).order_by('-date', '-id')

    return render(request, "facture/journal_general.html", {
        'title': "Journal général",
        'journal_entries': journal_entries,
    })


def grand_livre(request):
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
            solde = Decimal('0')

        montant = detail.montant or Decimal('0')
        debit = montant if montant >= 0 else Decimal('0')
        credit = abs(montant) if montant < 0 else Decimal('0')

        total_debit += debit
        total_credit += credit
        solde += montant
        grand_total_debit += debit
        grand_total_credit += credit

        current_entries.append({
            'date': detail.tr_desc.date,
            'no_ej': detail.tr_desc.no_ej,
            'compagnie': detail.tr_desc.compagnie,
            'description': detail.tr_desc.description,
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
    settings_instance = Setting.objects.first()
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

    return {
        'id': str(tr.id),
        'date': tr.date.isoformat() if tr.date else '',
        'numero': tr.description or '',
        'total': f"{display_total:.2f}",
        'details': details,
    }


def _parse_facture_total(raw_value):
    normalized = str(raw_value or '').strip().replace(' ', '').replace(',', '.')
    if not normalized:
        return Decimal('0')
    return Decimal(normalized)


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
    ).all()
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
                'numero': tr.description or '',
                'date': tr.date.isoformat() if tr.date else '',
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
                            (compte, montant)
                            for (compte, montant) in detail_rows
                            if compte.pk != forced_compte.pk
                        ]

                        if company_mode == 'CAR':
                            filtered_rows = [
                                (compte, -abs(montant))
                                for (compte, montant) in filtered_rows
                            ]

                        for compte, montant in filtered_rows:
                            Tr_detail.objects.create(
                                tr_desc=tr_desc,
                                compte=compte,
                                montant=montant,
                            )

                        if company_mode == 'CAP':
                            forced_amount = -abs(facture_total_value)
                        else:
                            forced_amount = facture_total_value

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
                                montant=montant,
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
    })


def balance_de_verification(request):
    details = Tr_detail.objects.select_related('compte').order_by('compte_id', 'id')

    rows = []
    current_compte_id = None
    current_compte = None
    debit = Decimal('0')
    credit = Decimal('0')

    total_debit = Decimal('0')
    total_credit = Decimal('0')

    for detail in details:
        if current_compte_id is None:
            current_compte_id = detail.compte_id
            current_compte = detail.compte

        if detail.compte_id != current_compte_id:
            rows.append({
                'compte': current_compte,
                'debit': debit,
                'credit': credit,
            })
            total_debit += debit
            total_credit += credit

            current_compte_id = detail.compte_id
            current_compte = detail.compte
            debit = Decimal('0')
            credit = Decimal('0')

        montant = detail.montant or Decimal('0')
        if montant >= 0:
            debit += montant
        else:
            credit += abs(montant)

    if current_compte_id is not None:
        rows.append({
            'compte': current_compte,
            'debit': debit,
            'credit': credit,
        })
        total_debit += debit
        total_credit += credit

    is_balanced = total_debit == total_credit

    return render(request, "facture/balance_de_verification.html", {
        'title': "Balance de vérification",
        'rows': rows,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'is_balanced': is_balanced,
    })


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

    settings_instance = Setting.objects.first()
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

            if detail.compte_id == tps_percue_id:
                tax_type = 'TPS'
                percue_signee = detail.montant
                percue = abs(detail.montant)
            elif detail.compte_id == tps_payee_id:
                tax_type = 'TPS'
                payee = detail.montant
            elif detail.compte_id == tvq_percue_id:
                tax_type = 'TVQ'
                percue_signee = detail.montant
                percue = abs(detail.montant)
            elif detail.compte_id == tvq_payee_id:
                tax_type = 'TVQ'
                payee = detail.montant

            if not tax_type:
                continue

            if percue is not None:
                blocks[tax_type]['total_percue'] += percue
            if percue_signee is not None:
                blocks[tax_type]['total_percue_signee'] += percue_signee
            if payee is not None:
                blocks[tax_type]['total_payee'] += payee

            blocks[tax_type]['rows'].append({
                'id': detail.id,
                'date': detail.tr_desc.date,
                'compagnie_nom': detail.tr_desc.compagnie.nom if detail.tr_desc.compagnie else '-',
                'facture': detail.tr_desc.description or '-',
                'percue': percue,
                'payee': payee,
            })

        for tax_type in ('TPS', 'TVQ'):
            blocks[tax_type]['solde_a_reclamer'] = (
                blocks[tax_type]['total_percue_signee'] - blocks[tax_type]['total_payee']
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
                    report.transmis_le = timezone.now()
                    report.save(update_fields=['transmis_le'])
                    feedback.append("Rapport transmis. Il est maintenant verrouille.")

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

    return render(request, "facture/rapport_de_taxes.html", {
        'title': "Rapport de taxes",
        'selected_report': selected_report,
        'tax_accounts_configured': bool(tax_account_ids),
        'feedback': feedback,
        'error_messages': error_messages,
        'selected_month_value': selected_month_value,
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

    compte, _ = CompteReleve.objects.get_or_create(
        no_compte=no_compte,
        type_compte=type_compte_csv,
        defaults={
            'nom_affichage': nom_affichage,
            'nom_institut': nom_institut,
            'type_onglet': type_onglet,
        },
    )
    return compte


def releve_bancaire(request):
    releves = []
    errors = []

    if request.method == 'POST' and request.FILES.get('csv_file'):
        csv_file = request.FILES['csv_file']

        try:
            file_name = csv_file.name

            # Vérifier si ce fichier a déjà été importé
            if Releve.objects.filter(fichier_source=file_name).exists():
                errors.append(f"⚠ Le fichier « {file_name} » a déjà été importé. Aucune ligne n'a été ajoutée.")
            else:
                # Détecter l'encodage du fichier
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
                        description = row[5].strip() if len(row) > 5 else ''

                        if not all([no_compte, date_str, no_ligne, description]):
                            errors.append(f"Ligne {row_num}: Données manquantes")
                            continue

                        try:
                            date_obj = datetime.strptime(date_str, '%Y/%m/%d').date()
                        except ValueError:
                            errors.append(f"Ligne {row_num}: Format de date invalide ({date_str})")
                            continue

                        def _decimal(val):
                            try:
                                return Decimal(val.replace(',', '.')) if val else None
                            except (InvalidOperation, ValueError):
                                return None

                        # Format banque : col[7]=retrait, col[8]=dépôt, col[13]=solde
                        # Format VISA   : col[11]=charge (retrait), col[12]=paiement (dépôt négatif)
                        if type_compte:  # banque
                            retrait = _decimal(row[7].strip() if len(row) > 7 else '')
                            depot   = _decimal(row[8].strip() if len(row) > 8 else '')
                            solde   = _decimal(row[13].strip() if len(row) > 13 else '') or Decimal('0')
                        else:  # VISA / autre
                            charge   = _decimal(row[11].strip() if len(row) > 11 else '')
                            paiement = _decimal(row[12].strip() if len(row) > 12 else '')
                            retrait  = charge if charge and charge > 0 else None
                            depot    = abs(paiement) if paiement and paiement < 0 else None
                            solde    = Decimal('0')

                        if no_compte not in compte_releve_cache:
                            compte_releve_cache[no_compte] = _obtenir_ou_creer_compte_releve(
                                no_compte, nom_institut, type_compte
                            )

                        releve_data = {
                            'compte_releve': compte_releve_cache[no_compte],
                            'fichier_source': file_name,
                            'nom_institut': nom_institut,
                            'no_compte': no_compte,
                            'type_compte': type_compte,
                            'date': date_obj,
                            'no_ligne': no_ligne,
                            'description': description,
                            'retrait': retrait,
                            'depot': depot,
                            'solde': solde,
                        }

                        releves.append(releve_data)

                    except Exception as e:
                        errors.append(f"Ligne {row_num}: Erreur lors du parsing ({str(e)})")
                        continue

                if releves:
                    try:
                        for data in releves:
                            Releve.objects.create(**data)
                        errors.insert(0, f"✓ {len(releves)} ligne(s) ajoutée(s) à la base de données avec succès!")
                        releves = []
                    except Exception as e:
                        errors.append(f"Erreur lors de l'insertion: {str(e)}")

        except Exception as e:
            errors.append(f"Erreur lors de la lecture du fichier: {str(e)}")

    mois_selectionne = request.GET.get('mois', '')
    comptes_releves = CompteReleve.objects.order_by('type_onglet', 'nom_affichage')

    # Construire les données par compte pour l'affichage dans les onglets
    releves_qs = Releve.objects.select_related('compte_releve').order_by('date', 'no_ligne')
    if mois_selectionne:
        releves_qs = releves_qs.filter(date__month=int(mois_selectionne))

    releves_par_compte = {}
    for compte in comptes_releves:
        releves_list = list(releves_qs.filter(compte_releve=compte))
        
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

    return render(request, "facture/releve_bancaire.html", {
        'title': "Relevé bancaire",
        'errors': errors,
        'mois_selectionne': mois_selectionne,
        'groupes': groupes,
        'releves_par_compte': releves_par_compte,
        'fichiers_par_compte': fichiers_par_compte,
    })



