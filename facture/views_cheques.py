"""Vues liées aux chèques : création (avec écriture comptable associée) et
listing. Extrait de facture/views.py pour alléger ce fichier.
"""

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone

from facture.models import Cheque, Tr_desc, Tr_detail, Source
from facture.forms import ChequeForm
from facture.helpers.dates import verifier_exercice_modifiable
from facture.utils import get_setting


@login_required
@require_POST
def creer_cheque(request):
    from facture.views import _next_no_ej  # import tardif : evite l'import circulaire avec facture.views
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