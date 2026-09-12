"""Vue de l'onglet Banque > Facture par photo.

Prend une photo de facture, l'envoie a l'API Gemini pour extraire les
montants, puis affiche un court ecran de verification avant d'enregistrer
l'ecriture comptable (meme esprit que la petite caisse : rien n'est
comptabilise avant confirmation explicite).
"""

import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from dotenv import load_dotenv
from google import genai
from google.genai import types

from compte.models import Compte
from facture.helpers.dates import verifier_exercice_modifiable
from facture.models import Source, Tr_desc, Tr_detail
from facture.utils import get_setting

load_dotenv()

PROMPT_EXTRACTION = """Analyse cette facture et réponds uniquement en JSON avec les champs suivants :
{
  "fournisseur": "",
  "date": "",
  "montant_total": "",
  "tps": "",
  "tvq": "",
  "montant_avant_taxes": "",
  "description": ""
}
La date doit être au format AAAA-MM-JJ. Les montants doivent être des nombres avec un point comme séparateur décimal (ex: 42.50), sans symbole $. Si un champ est introuvable, mets null."""


def _parse_montant(raw_value):
    if raw_value is None:
        return None
    normalized = str(raw_value).replace(' ', '').replace(',', '.').strip()
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _analyser_photo(image_bytes, mime_type):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            PROMPT_EXTRACTION,
        ],
    )
    texte = response.text.strip()
    if texte.startswith("```"):
        texte = texte.split("```")[1]
        if texte.startswith("json"):
            texte = texte[4:]
    return json.loads(texte)

def _handle_confirmer(request, comptes_queryset):
    compte_id = (request.POST.get('compte') or '').strip()
    compte = comptes_queryset.filter(pk=compte_id).first()
    if not compte:
        messages.error(request, "Compte de dépense invalide.")
        return False

    montant_avant_taxes = _parse_montant(request.POST.get('montant_avant_taxes'))
    tps = _parse_montant(request.POST.get('tps')) or Decimal('0')
    tvq = _parse_montant(request.POST.get('tvq')) or Decimal('0')
    description = (request.POST.get('description') or '').strip() or 'Facture (photo)'
    date_brute = (request.POST.get('date') or '').strip()

    if montant_avant_taxes is None or montant_avant_taxes <= 0:
        messages.error(request, "Montant avant taxes invalide.")
        return False

    try:
        date_facture = datetime.strptime(date_brute, '%Y-%m-%d').date()
    except ValueError:
        date_facture = timezone.now().date()

    settings_instance = get_setting()
    if not settings_instance or not settings_instance.compte_cheques:
        messages.error(request, "Compte courant (compte_cheques) non configuré dans Setting.")
        return False

    total = montant_avant_taxes + tps + tvq

    try:
        with transaction.atomic():
            from facture.views import _next_no_ej  # import tardif : evite l'import circulaire

            verifier_exercice_modifiable(date_facture)

            source_photo, _ = Source.objects.get_or_create(nom='Facture (photo)')

            tr_desc = Tr_desc.objects.create(
                no_ej=_next_no_ej(date_facture),
                date=date_facture,
                desc_ctb=description,
                source=source_photo,
            )

            Tr_detail.objects.create(tr_desc=tr_desc, compte=compte, montant=montant_avant_taxes)

            settings_instance = get_setting()
            if tps and settings_instance.compte_tps_payee:
                Tr_detail.objects.create(tr_desc=tr_desc, compte=settings_instance.compte_tps_payee, montant=tps)
            if tvq and settings_instance.compte_tvq_payee:
                Tr_detail.objects.create(tr_desc=tr_desc, compte=settings_instance.compte_tvq_payee, montant=tvq)

            Tr_detail.objects.create(tr_desc=tr_desc, compte=settings_instance.compte_cheques, montant=-total)
    except ValueError as exc:
        messages.error(request, str(exc))
        return False

    messages.success(request, f"Facture enregistrée (no EJ {tr_desc.no_ej}).")
    return True


@login_required
def facture_photo(request):
    comptes_queryset = Compte.objects.filter(numero__gte=5000).order_by('numero')
    all_comptes = [
        {'id': compte.numero, 'label': f"{compte.numero} - {compte.libelle}"}
        for compte in comptes_queryset
    ]

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'confirmer':
            if _handle_confirmer(request, comptes_queryset):
                return redirect('facture_photo')
            # en cas d'erreur, on revient au formulaire de verification tel quel
            return render(request, "facture_photo/index.html", {
                'title': "Facture par photo",
                'mode': 'verification',
                'extraction': request.POST,
                'all_comptes': all_comptes,
            })

        # sinon, on est dans le cas "analyser" : une photo vient d'etre soumise
        photo = request.FILES.get('photo')
        if not photo:
            messages.error(request, "Choisis une photo avant d'envoyer.")
            return redirect('facture_photo')

        try:
            extraction = _analyser_photo(photo.read(), photo.content_type or 'image/jpeg')
        except Exception:
            messages.error(request, "L'analyse de la photo a échoué. Réessaie, ou entre la facture manuellement.")
            return redirect('facture_photo')

        return render(request, "facture_photo/index.html", {
            'title': "Facture par photo",
            'mode': 'verification',
            'extraction': extraction,
            'all_comptes': all_comptes,
        })

    return render(request, "facture_photo/index.html", {
        'title': "Facture par photo",
        'mode': 'upload',
    })