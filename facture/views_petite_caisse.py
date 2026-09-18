"""Vue de l'onglet Banque > Petite caisse.

Compile plusieurs petites factures payees comptant dans une table d'attente
(PetiteCaisseLigne). Rien n'est comptabilise tant qu'on n'a pas choisi de
"passer la transaction" : ce moment-la vide la table, cree l'ecriture
(depenses + taxes au debit, compte courant au credit) et ajoute un "cheque en
circulation" (meme mecanique que les cheques, sans numero reel) pour que la
sortie de banque se rapproche plus tard comme n'importe quel autre cheque.
"""

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from compte.models import Compte
from facture.helpers.dates import verifier_exercice_modifiable
from facture.models import Cheque, PetiteCaissePhotoEnAttente, PetiteCaisseLigne, Source, Tr_desc, Tr_detail
from facture.utils import get_setting, no_cheques_encaisses
from facture.views_facture_photo import _trouver_fournisseur_et_compte


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


def _next_groupe():
    dernier = PetiteCaisseLigne.objects.order_by('-groupe').values_list('groupe', flat=True).first()
    return (dernier or 0) + 1


def _handle_add_lignes(request, comptes_queryset):
    settings_instance = get_setting()
    tax_account_ids = {
        settings_instance.compte_tps_payee_id if settings_instance else None,
        settings_instance.compte_tvq_payee_id if settings_instance else None,
    } - {None}

    groupe_locale_vers_groupe = {}
    prochain_groupe = _next_groupe()
    lignes_a_creer = []
    erreurs = []

    index = 0
    while f'ligne-{index}-compte' in request.POST:
        compte_id = (request.POST.get(f'ligne-{index}-compte') or '').strip()
        montant_brut = request.POST.get(f'ligne-{index}-montant')
        date_brute = (request.POST.get(f'ligne-{index}-date') or '').strip()
        description = (request.POST.get(f'ligne-{index}-description') or '').strip()
        groupe_local = (request.POST.get(f'ligne-{index}-groupe-local') or '').strip() or str(index)
        index += 1

        montant = _parse_montant(montant_brut)
        if not compte_id or montant is None or montant == 0:
            continue

        # Les lignes de depense doivent rester dans les comptes 5000+, mais les
        # lignes TPS/TVQ generees automatiquement par le calcul de taxes (cote
        # JS) pointent vers les comptes configures dans Setting, qui ne sont pas
        # necessairement dans cette plage -- on les autorise donc separement.
        if str(compte_id).isdigit() and int(compte_id) in tax_account_ids:
            compte = Compte.objects.filter(pk=compte_id).first()
        else:
            compte = comptes_queryset.filter(pk=compte_id).first()
        if not compte:
            erreurs.append(f"Ligne {index} : compte invalide.")
            continue

        try:
            date_ligne = datetime.strptime(date_brute, '%Y-%m-%d').date()
        except ValueError:
            erreurs.append(f"Ligne {index} : date invalide.")
            continue

        if groupe_local not in groupe_locale_vers_groupe:
            groupe_locale_vers_groupe[groupe_local] = prochain_groupe
            prochain_groupe += 1

        lignes_a_creer.append(PetiteCaisseLigne(
            groupe=groupe_locale_vers_groupe[groupe_local],
            date=date_ligne,
            description=description,
            compte=compte,
            montant=abs(montant),
        ))

    if erreurs:
        for erreur in erreurs:
            messages.error(request, erreur)
        return

    if not lignes_a_creer:
        messages.error(request, "Ajoute au moins un reçu (montant + compte) avant d'enregistrer.")
        return

    PetiteCaisseLigne.objects.bulk_create(lignes_a_creer)
    messages.success(request, f"{len(lignes_a_creer)} ligne(s) ajoutée(s) à la petite caisse en attente.")


def _handle_delete_groupe(request):
    groupe_brut = (request.POST.get('groupe') or '').strip()
    if not groupe_brut.isdigit():
        messages.error(request, "Reçu invalide.")
        return

    nb_supprimees, _ = PetiteCaisseLigne.objects.filter(groupe=int(groupe_brut)).delete()
    if nb_supprimees:
        messages.success(request, "Reçu retiré de la petite caisse en attente.")
    else:
        messages.error(request, "Ce reçu n'existe plus (déjà retiré ?).")


def _handle_ajouter_photo(request, comptes_queryset):
    """Transforme un reçu pris en photo (PetiteCaissePhotoEnAttente) en
    ligne(s) PetiteCaisseLigne, a partir des valeurs telles que confirmees
    (et au besoin corrigees) dans le formulaire -- memes colonnes editables
    que dans "Ajouter des reçus" (date, description, une ou plusieurs lignes
    compte + montant avant taxes, TPS, TVQ), plutot que les valeurs brutes
    detectees par l'IA."""
    photo_id = (request.POST.get('photo_id') or '').strip()
    photo = PetiteCaissePhotoEnAttente.objects.filter(pk=photo_id, traite=False).first()
    if not photo:
        messages.error(request, "Ce reçu photo n'existe plus (déjà ajouté ou supprimé ?).")
        return

    settings_instance = get_setting()

    date_brute = (request.POST.get('date') or '').strip()
    try:
        date_recu = datetime.strptime(date_brute, '%Y-%m-%d').date()
    except ValueError:
        date_recu = timezone.now().date()

    description = (request.POST.get('description') or '').strip() or photo.fournisseur_detecte or photo.description_detectee or ''

    tps = _parse_montant(request.POST.get('tps')) or Decimal('0')
    tvq = _parse_montant(request.POST.get('tvq')) or Decimal('0')

    groupe = _next_groupe()
    lignes_a_creer = []
    index = 0
    while f'ligne-{index}-compte' in request.POST:
        compte_id = (request.POST.get(f'ligne-{index}-compte') or '').strip()
        montant = _parse_montant(request.POST.get(f'ligne-{index}-montant'))
        index += 1
        if not compte_id or montant is None or montant <= 0:
            continue
        compte = comptes_queryset.filter(pk=compte_id).first()
        if not compte:
            continue
        lignes_a_creer.append(PetiteCaisseLigne(
            groupe=groupe, date=date_recu, description=description,
            compte=compte, montant=abs(montant),
        ))

    if not lignes_a_creer:
        messages.error(request, "Choisis un compte et un montant avant taxes avant d'ajouter ce reçu.")
        return

    if tps and settings_instance and settings_instance.compte_tps_payee:
        lignes_a_creer.append(PetiteCaisseLigne(
            groupe=groupe, date=date_recu, description=description,
            compte=settings_instance.compte_tps_payee, montant=abs(tps),
        ))
    if tvq and settings_instance and settings_instance.compte_tvq_payee:
        lignes_a_creer.append(PetiteCaisseLigne(
            groupe=groupe, date=date_recu, description=description,
            compte=settings_instance.compte_tvq_payee, montant=abs(tvq),
        ))

    PetiteCaisseLigne.objects.bulk_create(lignes_a_creer)
    photo.traite = True
    photo.save(update_fields=['traite'])
    messages.success(request, "Reçu ajouté à la petite caisse en attente.")


def _handle_supprimer_photo(request):
    photo_id = (request.POST.get('photo_id') or '').strip()
    nb_supprimees, _ = PetiteCaissePhotoEnAttente.objects.filter(pk=photo_id, traite=False).delete()
    if nb_supprimees:
        messages.success(request, "Photo supprimée.")
    else:
        messages.error(request, "Cette photo n'existe plus.")


def _handle_modifier_transaction(request, comptes_queryset):
    """Modifie une transaction de petite caisse deja passee : reconstruit
    entierement les lignes (Tr_detail) a partir du formulaire de la modale
    d'edition, met a jour la date/description de l'ecriture, et ajuste le
    cheque (ou virement) lie en consequence -- meme principe que le mode
    edition de facture_photo, applique ici a une transaction petite caisse."""
    tr_desc_id = (request.POST.get('tr_desc_id') or '').strip()
    tr_desc = Tr_desc.objects.filter(pk=tr_desc_id, source__nom='Petite caisse').first()
    if not tr_desc:
        messages.error(request, "Cette transaction de petite caisse n'existe plus.")
        return

    cheque = Cheque.objects.filter(tr_desc=tr_desc).first()

    settings_instance = get_setting()
    if not settings_instance or not settings_instance.compte_cheques:
        messages.error(
            request,
            "Compte courant (compte_cheques) non configuré dans Setting. Configure-le avant de modifier cette transaction."
        )
        return

    tax_account_ids = {
        settings_instance.compte_tps_payee_id,
        settings_instance.compte_tvq_payee_id,
    } - {None}

    date_brute = (request.POST.get('date') or '').strip()
    try:
        date_transaction = datetime.strptime(date_brute, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, "Date invalide.")
        return

    description = (request.POST.get('description') or '').strip()

    totaux_par_compte = {}
    index = 0
    while f'ligne-{index}-compte' in request.POST:
        compte_id = (request.POST.get(f'ligne-{index}-compte') or '').strip()
        montant = _parse_montant(request.POST.get(f'ligne-{index}-montant'))
        index += 1
        if not compte_id or montant is None or montant == 0:
            continue
        if str(compte_id).isdigit() and int(compte_id) in tax_account_ids:
            compte = Compte.objects.filter(pk=compte_id).first()
        else:
            compte = comptes_queryset.filter(pk=compte_id).first()
        if not compte:
            continue
        totaux_par_compte[compte.pk] = totaux_par_compte.get(compte.pk, Decimal('0')) + abs(montant)

    total_general = sum(totaux_par_compte.values(), Decimal('0'))
    if total_general <= 0:
        messages.error(request, "Ajoute au moins une ligne (compte + montant) avant d'enregistrer.")
        return

    try:
        verifier_exercice_modifiable(date_transaction)
    except ValueError as exc:
        messages.error(request, str(exc))
        return

    with transaction.atomic():
        tr_desc.date = date_transaction
        if description:
            tr_desc.desc_ctb = description
        tr_desc.save(update_fields=['date', 'desc_ctb'])

        tr_desc.details.all().delete()
        for compte_id, montant in totaux_par_compte.items():
            Tr_detail.objects.create(tr_desc=tr_desc, compte_id=compte_id, montant=montant)
        Tr_detail.objects.create(tr_desc=tr_desc, compte=settings_instance.compte_cheques, montant=-total_general)

        if cheque:
            cheque.montant = total_general
            cheque.date_emission = date_transaction
            if description:
                cheque.description = description
            cheque.save(update_fields=['montant', 'date_emission', 'description'])

    messages.success(request, f"Transaction de petite caisse modifiée (no EJ {tr_desc.no_ej}).")


def _handle_passer_transaction(request):
    from facture.views import _next_no_ej  # import tardif : evite l'import circulaire avec facture.views

    lignes = list(PetiteCaisseLigne.objects.select_related('compte').all())
    if not lignes:
        messages.error(request, "Aucune ligne de petite caisse en attente à transmettre.")
        return

    settings_instance = get_setting()
    if not settings_instance or not settings_instance.compte_cheques:
        messages.error(
            request,
            "Compte courant (compte_cheques) non configuré dans Setting. Configure-le avant de passer la transaction."
        )
        return

    totaux_par_compte = {}
    total_general = Decimal('0')
    for ligne in lignes:
        totaux_par_compte[ligne.compte_id] = totaux_par_compte.get(ligne.compte_id, Decimal('0')) + ligne.montant
        total_general += ligne.montant

    if total_general <= 0:
        messages.error(request, "Le total de la petite caisse en attente est nul.")
        return

    emettre_cheque = request.POST.get('emettre_cheque') == 'on'
    no_cheque_reel = (request.POST.get('no_cheque') or '').strip()
    if emettre_cheque:
        if not no_cheque_reel:
            messages.error(request, "Indique un numéro de chèque, ou décoche la case pour un virement.")
            return
        if Cheque.objects.filter(no_cheque=no_cheque_reel).exists():
            messages.error(request, f"Le numéro de chèque {no_cheque_reel} est déjà utilisé.")
            return

    date_transaction = timezone.now().date()

    try:
        verifier_exercice_modifiable(date_transaction)
    except ValueError as exc:
        messages.error(request, str(exc))
        return

    with transaction.atomic():
        source_petite_caisse, _ = Source.objects.get_or_create(nom='Petite caisse')

        tr_desc = Tr_desc.objects.create(
            no_ej=_next_no_ej(date_transaction),
            date=date_transaction,
            desc_ctb='Petite caisse',
            source=source_petite_caisse,
        )

        for compte_id, montant in totaux_par_compte.items():
            Tr_detail.objects.create(tr_desc=tr_desc, compte_id=compte_id, montant=montant)

        Tr_detail.objects.create(
            tr_desc=tr_desc,
            compte=settings_instance.compte_cheques,
            montant=-total_general,
        )

        # "Cheque en circulation" : meme mecanique que les vrais cheques (voir
        # facture/views_cheques.py). Par defaut (virement), on utilise un
        # identifiant textuel plutot qu'un numero reel -- prochain_no_cheque
        # (facture/context_processors.py) ne compte que les valeurs purement
        # numeriques, donc ce prefixe ne perturbe pas la numerotation
        # sequentielle des vrais cheques. Si l'utilisatrice a coche "emettre
        # un cheque reel", on utilise plutot le numero qu'elle a fourni, deja
        # valide plus haut (non vide, pas deja utilise).
        Cheque.objects.create(
            no_cheque=no_cheque_reel if emettre_cheque else f"PC-{tr_desc.no_ej}",
            date_emission=date_transaction,
            montant=total_general,
            description='Petite caisse',
            tr_desc=tr_desc,
        )

        PetiteCaisseLigne.objects.all().delete()

    messages.success(request, f"Transaction de petite caisse enregistrée (no EJ {tr_desc.no_ej}).")


def _historique_petite_caisse():
    """Transactions de petite caisse deja passees (voir _handle_passer_transaction),
    avec leur statut -- meme logique de rapprochement que la page Cheques.
    Ajoute aussi, pour chaque transaction liee a une ecriture, ses lignes
    (compte + montant, sans la ligne de contrepartie du compte courant) pretes
    a etre editees dans la modale -- meme principe que _lignes_pour_edition
    pour facture_photo."""
    deja_encaisses = no_cheques_encaisses()
    settings_instance = get_setting()
    compte_cheques_id = settings_instance.compte_cheques_id if settings_instance else None

    historique = list(
        Cheque.objects
        .filter(tr_desc__source__nom='Petite caisse')
        .select_related('tr_desc')
        .prefetch_related('tr_desc__details')
        .order_by('-date_emission', '-id')
    )
    for cheque in historique:
        if cheque.annule:
            cheque.statut = 'annule'
        elif cheque.no_cheque in deja_encaisses:
            cheque.statut = 'encaisse'
        else:
            cheque.statut = 'en_circulation'

        lignes_edition = []
        if cheque.tr_desc_id:
            for detail in cheque.tr_desc.details.all():
                if compte_cheques_id and detail.compte_id == compte_cheques_id:
                    continue
                lignes_edition.append({'compte_id': detail.compte_id, 'montant': str(detail.montant)})
        cheque.lignes_edition_json = json.dumps(lignes_edition)
    return historique


@login_required
def petite_caisse(request):
    comptes_queryset = Compte.objects.filter(numero__gte=5000).order_by('numero')

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'add_lignes':
            _handle_add_lignes(request, comptes_queryset)
        elif action == 'delete_groupe':
            _handle_delete_groupe(request)
        elif action == 'ajouter_photo':
            _handle_ajouter_photo(request, comptes_queryset)
        elif action == 'supprimer_photo':
            _handle_supprimer_photo(request)
        elif action == 'passer_transaction':
            _handle_passer_transaction(request)
        elif action == 'modifier_transaction':
            _handle_modifier_transaction(request, comptes_queryset)

        return redirect('petite_caisse')

    lignes = list(PetiteCaisseLigne.objects.select_related('compte').all())
    settings_instance = get_setting()
    tps_id = settings_instance.compte_tps_payee_id if settings_instance else None
    tvq_id = settings_instance.compte_tvq_payee_id if settings_instance else None

    groupes = {}
    ordre_groupes = []
    for ligne in lignes:
        if ligne.groupe not in groupes:
            groupes[ligne.groupe] = {
                'groupe': ligne.groupe,
                'date': ligne.date,
                'description': ligne.description,
                'tps': Decimal('0'),
                'tvq': Decimal('0'),
                'lignes_depense': [],
                'total': Decimal('0'),
            }
            ordre_groupes.append(ligne.groupe)

        groupe_data = groupes[ligne.groupe]
        groupe_data['total'] += ligne.montant
        if tps_id and ligne.compte_id == tps_id:
            groupe_data['tps'] += ligne.montant
        elif tvq_id and ligne.compte_id == tvq_id:
            groupe_data['tvq'] += ligne.montant
        else:
            groupe_data['lignes_depense'].append(ligne)

    recus_en_attente = [groupes[g] for g in ordre_groupes]
    total_en_attente = sum((g['total'] for g in recus_en_attente), Decimal('0'))

    all_comptes = [
        {'id': compte.numero, 'label': f"{compte.numero} - {compte.libelle}"}
        for compte in comptes_queryset
    ]

    # Pour la modale d'edition d'une transaction deja passee : memes comptes
    # que ci-dessus (5000 et plus), plus les comptes de taxes (TPS/TVQ payees)
    # au cas ou une ligne existante pointe vers l'un d'eux.
    all_comptes_edition = list(all_comptes)
    ids_presents = {c['id'] for c in all_comptes_edition}
    for compte_taxe in Compte.objects.filter(pk__in=[i for i in (tps_id, tvq_id) if i]):
        if compte_taxe.numero not in ids_presents:
            all_comptes_edition.append({'id': compte_taxe.numero, 'label': f"{compte_taxe.numero} - {compte_taxe.libelle}"})
            ids_presents.add(compte_taxe.numero)

    photos_en_attente = []
    for photo in PetiteCaissePhotoEnAttente.objects.filter(traite=False):
        _, compte_suggere = _trouver_fournisseur_et_compte(photo.fournisseur_detecte)
        photos_en_attente.append({
            'photo': photo,
            'compte_suggere': compte_suggere,
        })

    return render(request, "petite_caisse/index.html", {
        'title': "Petite caisse",
        'recus_en_attente': recus_en_attente,
        'total_en_attente': total_en_attente,
        'historique_petite_caisse': _historique_petite_caisse(),
        'all_comptes_json': json.dumps(all_comptes),
        'all_comptes': all_comptes,
        'all_comptes_edition_json': json.dumps(all_comptes_edition),
        'photos_en_attente': photos_en_attente,
        'compte_tps_payee_id': tps_id or 0,
        'compte_tvq_payee_id': tvq_id or 0,
    })
