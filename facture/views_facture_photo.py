"""Vue de l'onglet Banque > Facture par photo.

Deux parcours :
- facture_photo_rapide : page mobile minimaliste, sans menu. Prend la photo,
  l'analyse tout de suite, et met le resultat de cote dans la file d'attente
  (FacturePhotoEnAttente). Rien n'est comptabilise ici.
- facture_photo (liste) + facture_photo_traiter (une facture a la fois) :
  page normale du site, pour traiter les photos en attente -- verification,
  choix du compte (suggere automatiquement si le fournisseur detecte
  correspond a un fournisseur existant), et creation de l'ecriture.
"""

import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from dotenv import load_dotenv
from google import genai
from google.genai import types

from compte.models import Compte
from facture.helpers.dates import verifier_exercice_modifiable
from facture.models import FacturePhotoEnAttente, Fournisseur, Source, Tr_desc, Tr_detail
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


def _valeur_texte(extraction, cle):
    valeur = extraction.get(cle) if isinstance(extraction, dict) else None
    if valeur is None:
        return ''
    return str(valeur)


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


def _trouver_fournisseur_et_compte(nom_detecte):
    """Tente de faire correspondre le fournisseur detecte par l'IA a un
    fournisseur deja connu, pour suggerer automatiquement son compte
    habituel (ex : Bell Canada, Hydro-Quebec)."""
    nom_detecte = (nom_detecte or '').strip()
    if not nom_detecte:
        return None, None

    fournisseur = Fournisseur.objects.filter(nom__iexact=nom_detecte).first()
    if not fournisseur:
        fournisseur = Fournisseur.objects.filter(nom__icontains=nom_detecte).first()
    if not fournisseur:
        for candidat in Fournisseur.objects.all():
            if candidat.nom.lower() in nom_detecte.lower():
                fournisseur = candidat
                break
    if not fournisseur:
        return None, None

    compte = fournisseur.comptes.first()
    return fournisseur, compte


@login_required
def facture_photo_rapide(request):
    """Page minimaliste pour telephone : prend la photo, l'analyse tout de
    suite, et met le resultat de cote dans la file d'attente."""
    if request.method == 'POST':
        photo = request.FILES.get('photo')
        if not photo:
            return render(request, "facture_photo/rapide.html", {'erreur': "Choisis une photo avant d'envoyer."})

        photo_bytes = photo.read()
        mime_type = photo.content_type or 'image/jpeg'

        ligne = FacturePhotoEnAttente(photo=photo_bytes, photo_type=mime_type)

        try:
            extraction = _analyser_photo(photo_bytes, mime_type)
            ligne.fournisseur_detecte = _valeur_texte(extraction, 'fournisseur')
            ligne.date_detectee = _valeur_texte(extraction, 'date')
            ligne.montant_total_detecte = _valeur_texte(extraction, 'montant_total')
            ligne.tps_detectee = _valeur_texte(extraction, 'tps')
            ligne.tvq_detectee = _valeur_texte(extraction, 'tvq')
            ligne.montant_avant_taxes_detecte = _valeur_texte(extraction, 'montant_avant_taxes')
            ligne.description_detectee = _valeur_texte(extraction, 'description')
        except Exception as exc:
            ligne.erreur_analyse = str(exc)

        ligne.save()

        return render(request, "facture_photo/rapide.html", {'envoye': True})

    return render(request, "facture_photo/rapide.html", {})


@login_required
def facture_photo_image(request, pk):
    """Sert l'image d'une photo en attente (pour l'aperçu sur la page de traitement)."""
    ligne = get_object_or_404(FacturePhotoEnAttente, pk=pk)
    if not ligne.photo:
        raise Http404
    return HttpResponse(bytes(ligne.photo), content_type=ligne.photo_type or 'image/jpeg')


@login_required
def facture_photo(request):
    """Liste des photos en attente de traitement."""
    en_attente = FacturePhotoEnAttente.objects.all()
    return render(request, "facture_photo/index.html", {
        'title': "Facture par photo",
        'en_attente': en_attente,
    })


def _extraire_lignes_post(request):
    """Lit les lignes compte/montant soumises (ligne-0-compte, ligne-0-montant,
    ligne-1-compte, ...), sans validation -- juste pour re-afficher le
    formulaire tel quel en cas d'erreur, ou pour les traiter dans
    _handle_confirmer."""
    lignes = []
    index = 0
    while f'ligne-{index}-compte' in request.POST:
        lignes.append({
            'compte_id': (request.POST.get(f'ligne-{index}-compte') or '').strip(),
            'montant': (request.POST.get(f'ligne-{index}-montant') or '').strip(),
        })
        index += 1
    return lignes


def _handle_confirmer(request, ligne, comptes_queryset):
    lignes_brutes = _extraire_lignes_post(request)
    lignes_comptes = []
    montant_avant_taxes = Decimal('0')
    for entree in lignes_brutes:
        compte = comptes_queryset.filter(pk=entree['compte_id']).first() if entree['compte_id'] else None
        montant = _parse_montant(entree['montant'])
        if not compte or montant is None or montant <= 0:
            continue
        lignes_comptes.append((compte, montant))
        montant_avant_taxes += montant

    if not lignes_comptes:
        messages.error(request, "Ajoute au moins une ligne (compte + montant) avant d'enregistrer.")
        return False

    tps = _parse_montant(request.POST.get('tps')) or Decimal('0')
    tvq = _parse_montant(request.POST.get('tvq')) or Decimal('0')
    description = (request.POST.get('description') or '').strip() or 'Facture (photo)'
    date_brute = (request.POST.get('date') or '').strip()

    try:
        date_facture = datetime.strptime(date_brute, '%Y-%m-%d').date()
    except ValueError:
        date_facture = timezone.now().date()

    settings_instance = get_setting()
    if not settings_instance or not settings_instance.compte_cheques:
        messages.error(request, "Compte courant (compte_cheques) non configuré dans Setting.")
        return False

    fournisseur_id = (request.POST.get('fournisseur') or '').strip()
    fournisseur = Fournisseur.objects.filter(pk=fournisseur_id).first() if fournisseur_id else None

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
                fournisseur=fournisseur,
            )

            for compte_ligne, montant_ligne in lignes_comptes:
                Tr_detail.objects.create(tr_desc=tr_desc, compte=compte_ligne, montant=montant_ligne)

            settings_instance = get_setting()
            if tps and settings_instance.compte_tps_payee:
                Tr_detail.objects.create(tr_desc=tr_desc, compte=settings_instance.compte_tps_payee, montant=tps)
            if tvq and settings_instance.compte_tvq_payee:
                Tr_detail.objects.create(tr_desc=tr_desc, compte=settings_instance.compte_tvq_payee, montant=tvq)

            Tr_detail.objects.create(tr_desc=tr_desc, compte=settings_instance.compte_cheques, montant=-total)
    except ValueError as exc:
        messages.error(request, str(exc))
        return False

    ligne.delete()
    messages.success(request, f"Facture enregistrée (no EJ {tr_desc.no_ej}).")
    return True


@login_required
def facture_photo_traiter(request, pk):
    ligne = get_object_or_404(FacturePhotoEnAttente, pk=pk)
    comptes_queryset = Compte.objects.filter(numero__gte=5000).order_by('numero')
    all_comptes = [
        {'id': compte.numero, 'label': f"{compte.numero} - {compte.libelle}"}
        for compte in comptes_queryset
    ]

    fournisseur_trouve, compte_suggere = _trouver_fournisseur_et_compte(ligne.fournisseur_detecte)

    if request.method == 'POST':
        if _handle_confirmer(request, ligne, comptes_queryset):
            return redirect('facture_photo')
        return render(request, "facture_photo/traiter.html", {
            'title': "Facture par photo",
            'ligne': ligne,
            'all_comptes': all_comptes,
            'fournisseur_trouve': fournisseur_trouve,
            'compte_suggere': compte_suggere,
            'extraction': request.POST,
            'lignes_initiales': _extraire_lignes_post(request) or [{'compte_id': '', 'montant': ''}],
        })

    return render(request, "facture_photo/traiter.html", {
        'title': "Facture par photo",
        'ligne': ligne,
        'all_comptes': all_comptes,
        'fournisseur_trouve': fournisseur_trouve,
        'compte_suggere': compte_suggere,
        'extraction': {
            'date': ligne.date_detectee,
            'description': ligne.description_detectee,
            'montant_total': ligne.montant_total_detecte,
            'tps': ligne.tps_detectee,
            'tvq': ligne.tvq_detectee,
            'montant_avant_taxes': ligne.montant_avant_taxes_detecte,
        },
        'lignes_initiales': [{
            'compte_id': str(compte_suggere.numero) if compte_suggere else '',
            'montant': ligne.montant_avant_taxes_detecte,
        }],
    })