"""Page mobile minimaliste : Banque > Petite caisse > photo.

Meme esprit que facture/views_facture_photo.py : la page mobile ne fait que
prendre/envoyer la photo, l'analyse par IA est faite tout de suite et le
resultat est mis de cote dans PetiteCaissePhotoEnAttente. Le traitement
(choix du compte, ajout au tableau de la petite caisse) se fait plus tard,
sur l'onglet Banque > Petite caisse -- voir facture/views_petite_caisse.py.

Les fonctions utilitaires (compression de l'image, appel a l'IA) sont
partagees avec facture/views_facture_photo.py plutot que dupliquees.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from facture.models import PetiteCaissePhotoEnAttente
from facture.views_facture_photo import _analyser_photo, _compresser_image, _valeur_texte


@login_required
def petite_caisse_photo_rapide(request):
    """Page minimaliste pour telephone : prend la photo d'un petit reçu,
    l'analyse tout de suite, et met le resultat de cote dans la file
    d'attente."""
    if request.method == 'POST':
        photo = request.FILES.get('photo')
        if not photo:
            return render(request, "petite_caisse/rapide.html", {'erreur': "Choisis une photo avant d'envoyer."})

        photo_bytes = photo.read()
        mime_type = photo.content_type or 'image/jpeg'

        photo_bytes_compressee, mime_type_compresse = _compresser_image(photo_bytes)
        if mime_type_compresse:
            photo_bytes = photo_bytes_compressee
            mime_type = mime_type_compresse

        ligne = PetiteCaissePhotoEnAttente(photo=photo_bytes, photo_type=mime_type)

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

        return render(request, "petite_caisse/rapide.html", {'envoye': True})

    return render(request, "petite_caisse/rapide.html", {})


@login_required
def petite_caisse_photo_image(request, pk):
    """Sert l'image d'un reçu en attente (pour l'aperçu sur l'écran Petite caisse)."""
    ligne = get_object_or_404(PetiteCaissePhotoEnAttente, pk=pk)
    if not ligne.photo:
        raise Http404
    return HttpResponse(bytes(ligne.photo), content_type=ligne.photo_type or 'image/jpeg')
