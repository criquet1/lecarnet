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
from facture.views_releve_bancaire import releve_bancaire, releve_ecriture_similaire
from facture.views_cheques import creer_cheque, cheques
from facture.views_rapports import (
    journal_general,
    grand_livre,
    balance_de_verification,
    etat_revenus_depenses,
    bilan,
    compte_a_payer,
    compte_a_recevoir,
    rapport_de_taxes,
)




def _money(value):
    return (value or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)




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
        'compteModifieParNonExpert': bool(tr.compte_modifie_par_non_expert),
    }


def _parse_facture_total(raw_value):
    value = parse_decimal(raw_value, strip_spaces=True)
    if value is None:
        return None
    return _money(value)


def _migrer_compagnie_vers_autre_type(old_company, old_type, new_company):
    """Deplace les factures, cheques et le solde de depart de old_company vers
    new_company. Sert quand un expert corrige le type (Client <-> Fournisseur)
    d'une compagnie qu'un non-expert a creee par erreur dans la mauvaise table.

    Corrige aussi, sur les factures deplacees, le compte de contrepartie
    (CAP/CAR) et les comptes de taxes (percues cote client, payees cote
    fournisseur) pour que le journal general et le grand livre restent
    coherents avec le nouveau type -- sinon les ecritures resteraient
    comptabilisees sous l'ancien compte meme si la carte affiche le bon type.
    """
    old_type_field = old_type
    new_type_field = 'fournisseur' if old_type == 'client' else 'client'

    tr_desc_ids = list(
        Tr_desc.objects.filter(**{old_type_field: old_company}).values_list('pk', flat=True)
    )
    if Tr_detail.objects.filter(
        tr_desc_id__in=tr_desc_ids,
        rapport_taxes__transmis_le__isnull=False,
    ).exists():
        raise ValidationError(
            "Cette compagnie a des factures rattachees a un rapport de taxes deja "
            "transmis. Le type ne peut pas etre corrige tant que ce rapport n'est "
            "pas rouvert -- les ecritures deja transmises ne doivent pas bouger."
        )

    Tr_desc.objects.filter(**{old_type_field: old_company}).update(**{old_type_field: None, new_type_field: new_company})
    Cheque.objects.filter(**{old_type_field: old_company}).update(**{old_type_field: None, new_type_field: new_company})
    CompagnieSoldeDepart.objects.filter(**{old_type_field: old_company}).update(**{old_type_field: None, new_type_field: new_company})

    # Le compte clients (CAR) est debiteur et le compte fournisseurs (CAP) est
    # crediteur : les memes montants s'inscrivent avec un signe oppose selon le
    # mode. Comme toutes les lignes changent de camp, on inverse le signe de
    # chaque ligne de facture deplacee (y compris les lignes de taxes et le
    # compte de contrepartie), pas seulement le numero de compte utilise.
    Tr_detail.objects.filter(tr_desc_id__in=tr_desc_ids).update(montant=-F('montant'))

    settings_instance = Setting.objects.first()
    if settings_instance:
        if old_type == 'fournisseur':
            # fournisseur (CAP, taxes payees) -> client (CAR, taxes percues)
            account_swaps = [
                (settings_instance.cap_id, settings_instance.car_id),
                (settings_instance.compte_tps_payee_id, settings_instance.compte_tps_percue_id),
                (settings_instance.compte_tvq_payee_id, settings_instance.compte_tvq_percue_id),
            ]
        else:
            # client (CAR, taxes percues) -> fournisseur (CAP, taxes payees)
            account_swaps = [
                (settings_instance.car_id, settings_instance.cap_id),
                (settings_instance.compte_tps_percue_id, settings_instance.compte_tps_payee_id),
                (settings_instance.compte_tvq_percue_id, settings_instance.compte_tvq_payee_id),
            ]

        for old_compte_id, new_compte_id in account_swaps:
            if not old_compte_id or not new_compte_id:
                continue
            Tr_detail.objects.filter(**{
                f'tr_desc__{new_type_field}': new_company,
                'compte_id': old_compte_id,
            }).update(compte_id=new_compte_id)

    old_company.delete()


def facture(request):
    title = "Facture"
    company_type = (request.POST.get('company_type') or 'client').strip().lower()
    company_form_class = FournisseurForm if company_type == 'fournisseur' else ClientForm
    company_model_class = Fournisseur if company_type == 'fournisseur' else Client
    company_form = company_form_class(request.POST or None, request.FILES or None, prefix='company', user=request.user)
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
            raw_company_type = (request.POST.get('company_type') or '').strip().lower()
            if raw_company_type not in ('client', 'fournisseur'):
                company_form.add_error(None, "Choisis un type de compagnie (Client ou Fournisseur) avant d'enregistrer.")
            elif company_form.is_valid():
                company = company_form.save(commit=False)
                company.created_by_non_expert = not is_expert(request.user)
                company.save()
                company_form.save_m2m()
                return redirect('facture')
            open_company_modal = True

        elif action == 'edit_company':
            company_id = (request.POST.get('company_id') or '').strip()
            existing_client = Client.objects.filter(pk=company_id).first()
            existing_fournisseur = Fournisseur.objects.filter(pk=company_id).first()
            current_company = existing_client or existing_fournisseur
            current_type = 'client' if existing_client else ('fournisseur' if existing_fournisseur else '')
            company_modal_action = 'edit_company'
            editing_company_id = company_id
            type_is_changing = bool(current_company) and current_type != company_type

            if not current_company:
                company_form = company_form_class(request.POST, request.FILES, prefix='company', user=request.user)
                company_form.add_error(None, "Compagnie introuvable.")
                open_company_modal = True
            elif type_is_changing and not is_expert(request.user):
                company_form = company_form_class(request.POST, request.FILES, prefix='company', user=request.user)
                company_form.add_error(
                    None,
                    "Seul un compte expert peut corriger le type (Client / Fournisseur) d'une compagnie existante."
                )
                open_company_modal = True
            else:
                # Si le type ne change pas, on modifie l'enregistrement existant.
                # S'il change, Client et Fournisseur etant deux tables separees,
                # on cree un nouvel enregistrement dans l'autre table et on y
                # deplace les factures, cheques et solde de depart avant de
                # supprimer l'ancien (voir _migrer_compagnie_vers_autre_type).
                company_form = company_form_class(
                    request.POST,
                    request.FILES,
                    prefix='company',
                    instance=(current_company if not type_is_changing else None),
                    user=request.user,
                )

                if company_form.is_valid():
                    try:
                        with transaction.atomic():
                            new_company = company_form.save(commit=False)
                            # Un expert qui enregistre est considere comme ayant
                            # verifie la compagnie : le jaune "a faire verifier" disparait.
                            if is_expert(request.user):
                                new_company.created_by_non_expert = False
                            new_company.save()
                            company_form.save_m2m()

                            if type_is_changing:
                                _migrer_compagnie_vers_autre_type(current_company, current_type, new_company)
                    except ValidationError as exc:
                        company_form.add_error(None, exc.message if hasattr(exc, 'message') else str(exc))
                        open_company_modal = True
                    else:
                        return redirect('facture')
                else:
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

                    # Signale les factures ou un utilisateur non-expert a choisi un
                    # compte different de celui propose par defaut pour la compagnie,
                    # ou a ajoute une ligne de compte supplementaire (bouton "+ ligne"),
                    # afin que le comptable puisse les reperer facilement (voir le
                    # "check" jaune sur la carte de la compagnie).
                    company_key = f"{selected_company_type}:{selected_company.pk}"
                    comptes_attendus = companies_comptes.get(company_key, [])
                    expected_compte_ids = {c['id'] for c in comptes_attendus}
                    lignes_non_forcees = [
                        (compte, montant) for compte, montant in detail_rows
                        if not forced_compte or compte.pk != forced_compte.pk
                    ]
                    has_compte_inattendu = any(
                        compte.pk not in expected_compte_ids
                        for compte, _montant in lignes_non_forcees
                    )
                    has_ligne_ajoutee = len(lignes_non_forcees) > len(comptes_attendus)
                    tr_desc.compte_modifie_par_non_expert = (
                        (has_compte_inattendu or has_ligne_ajoutee) and not is_expert(request.user)
                    )

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
        'is_expert_user': is_expert(request.user),
    })


