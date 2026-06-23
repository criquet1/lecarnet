from django.shortcuts import render, redirect
from django.forms import inlineformset_factory
from decimal import Decimal

from facture.models import Compagnie, Facture, DetailFacture
from facture.forms import CompagnieForm, FactureForm, DetailFactureForm
from compte.models import Compte
import json


def index(request):
    title = "Le carnet à Bibi"
    return render(request, "facture/index.html", {'title': title})


def journal_general(request):
    title = "Journal général"
    journal_rows = []

    facture_ej_map = {
        facture_id: f"EJ{index}"
        for index, facture_id in enumerate(
            Facture.objects.order_by('id').values_list('id', flat=True),
            start=1,
        )
    }

    factures = (
        Facture.objects
        .select_related('compagnie', 'compagnie__cap_ou_car')
        .prefetch_related('details__compte')
        .order_by('-date', '-id')
    )

    for facture in factures:
        cap_ou_car = facture.compagnie.cap_ou_car
        cap_numero = cap_ou_car.numero if cap_ou_car else None
        no_ej = facture_ej_map.get(facture.id, '')

        if cap_numero == 2000:
            cap_side = 'credit'
            details_side = 'debit'
        else:
            # Par défaut (incluant 1200), CAP/CAR est au débit et les détails au crédit.
            cap_side = 'debit'
            details_side = 'credit'

        total_facture = facture.total or Decimal('0.00')

        facture_rows = []

        facture_rows.append({
            'no_ej': no_ej,
            'date': facture.date,
            'desc': f"{facture.compagnie.nom} - Facture {facture.numero}",
            'compte': str(cap_ou_car) if cap_ou_car else 'CAP/CAR non configuré',
            'debit': total_facture if cap_side == 'debit' else '',
            'credit': total_facture if cap_side == 'credit' else '',
        })

        details = facture.details.all().order_by('id')
        for detail in details:
            montant = detail.montant or Decimal('0.00')
            facture_rows.append({
                'no_ej': no_ej,
                'date': facture.date,
                'desc': f"{facture.compagnie.nom} - Facture {facture.numero}",
                'compte': str(detail.compte),
                'debit': montant if details_side == 'debit' else '',
                'credit': montant if details_side == 'credit' else '',
            })

        for index, row in enumerate(facture_rows):
            row['group_start'] = index == 0
            row['group_end'] = index == len(facture_rows) - 1
            journal_rows.append(row)

    return render(request, "facture/journal_general.html", {
        'title': title,
        'journal_rows': journal_rows,
    })


def facture(request):
    title = "Facture"

    DetailFactureFormSet = inlineformset_factory(
        Facture,
        DetailFacture,
        form=DetailFactureForm,
        extra=100,
        can_delete=False,
        fields=['compte', 'montant']
    )

    company_form = CompagnieForm(request.POST or None, request.FILES or None, prefix='company')
    facture_form = FactureForm(request.POST or None, prefix='facture')
    facture_instance = Facture()
    detail_formset = DetailFactureFormSet(
        request.POST or None,
        instance=facture_instance,
        prefix='detail'
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_company' and company_form.is_valid():
            company_form.save()
            return redirect('facture')
        elif action == 'add_facture':
            if facture_form.is_valid() and detail_formset.is_valid():
                total_facture = facture_form.cleaned_data.get('total') or Decimal('0.00')
                total_details = Decimal('0.00')

                for detail_form in detail_formset.forms:
                    cleaned = detail_form.cleaned_data
                    if not cleaned:
                        continue

                    compte = cleaned.get('compte')
                    montant = cleaned.get('montant')
                    if compte and montant is not None:
                        total_details += montant

                if total_details.quantize(Decimal('0.01')) != total_facture.quantize(Decimal('0.01')):
                    facture_form.add_error(
                        'total',
                        f"Le total de la facture ({total_facture:.2f}) doit être égal à la somme des montants ({total_details:.2f})."
                    )
                else:
                    facture = facture_form.save()
                    detail_formset.instance = facture
                    detail_formset.save()
                    return redirect('facture')

    compagnies = Compagnie.objects.select_related('cap_ou_car').all()

    comptes_setting = []
    for id_key, label_key in [
        ('compte_tps', 'compte_tps_label'),
        ('compte_tvq', 'compte_tvq_label'),
        ('compte_fr_retard', 'compte_fr_retard_label'),
    ]:
        compte_id = request.session.get(id_key)
        if compte_id:
            comptes_setting.append({
                'id': int(compte_id),
                'label': request.session.get(label_key, str(compte_id)),
            })

    # Prépare un mapping JSON { compagnie_id: [{id, label}, ...], ... } pour le JS
    companies_comptes = {}
    for company in compagnies:
        comptes = [{'id': compte.pk, 'label': str(compte)} for compte in company.comptes.all()]
        comptes_existants = {c['id'] for c in comptes}

        for compte_setting in comptes_setting:
            if compte_setting['id'] not in comptes_existants:
                comptes.append(compte_setting)

        companies_comptes[str(company.id)] = comptes

    all_comptes = [{'id': compte.pk, 'label': str(compte)} for compte in Compte.objects.all()]

    facture_ej_map = {
        facture_id: f"EJ{index}"
        for index, facture_id in enumerate(
            Facture.objects.order_by('id').values_list('id', flat=True),
            start=1,
        )
    }

    # Prépare un mapping JSON { compagnie_id: [{ id, numero, date, total }, ...], ... }
    companies_factures = {}
    for company in compagnies:
        factures = Facture.objects.filter(compagnie=company).order_by('-date', '-id')
        factures_data = [
            {
                'id': f.id,
                'no_ej': facture_ej_map.get(f.id, ''),
                'numero': f.numero,
                'date': f.date.strftime('%Y-%m-%d'),
                'total': float(f.total),
            }
            for f in factures
        ]
        companies_factures[str(company.id)] = factures_data

    companies_cap_ou_car = {}
    for company in compagnies:
        if company.cap_ou_car:
            companies_cap_ou_car[str(company.id)] = {
                'id': company.cap_ou_car.pk,
                'label': str(company.cap_ou_car),
            }
        else:
            companies_cap_ou_car[str(company.id)] = None

    return render(request, "facture/facture.html", {
        'title': title,
        'compagnies': compagnies,
        'company_form': company_form,
        'facture_form': facture_form,
        'detail_formset': detail_formset,
        'companies_comptes_json': json.dumps(companies_comptes),
        'all_comptes_json': json.dumps(all_comptes),
        'companies_factures_json': json.dumps(companies_factures),
        'companies_cap_ou_car_json': json.dumps(companies_cap_ou_car),
    })
