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

    compagnies = Compagnie.objects.all()

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

    # Prépare un mapping JSON { compagnie_id: [{ id, numero, date, total }, ...], ... }
    companies_factures = {}
    for company in compagnies:
        factures = Facture.objects.filter(compagnie=company).order_by('-date')
        factures_data = [
            {
                'id': f.id,
                'numero': f.numero,
                'date': f.date.strftime('%Y-%m-%d'),
                'total': float(f.total),
            }
            for f in factures
        ]
        companies_factures[str(company.id)] = factures_data

    return render(request, "facture/facture.html", {
        'title': title,
        'compagnies': compagnies,
        'company_form': company_form,
        'facture_form': facture_form,
        'detail_formset': detail_formset,
        'companies_comptes_json': json.dumps(companies_comptes),
        'all_comptes_json': json.dumps(all_comptes),
        'companies_factures_json': json.dumps(companies_factures),
    })
