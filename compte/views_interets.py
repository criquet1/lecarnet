"""Registre de référence des intérêts (page 'Intérêts', sous Administration).

Page volontairement séparée des transactions réelles du projet : sert
uniquement à calculer les intérêts courus sur des montants prêtés/remboursés
saisis à la main (souvent à partir des relevés bancaires), par prêteur. Voir
compte/models.py (Preteur, InteretAnnee, InteretLigne) pour le schéma.

Principe de calcul : chaque ligne vit UNIQUEMENT dans sa propre année civile
(du montant prêté — ou du 1er janvier si c'est une ligne reportée — jusqu'au
31 décembre, ou jusqu'à son remboursement s'il y en a un). Il n'y a plus de
capitalisation automatique d'une année à l'autre calculée à la volée : à la
place, chaque prêt garde un "numero_pret" qui le suit d'année en année, et
quand on clique sur "Enregistrer les lignes", le solde restant au 31
décembre (moins les intérêts déjà versés cette année-là) est reporté comme
point de départ d'une VRAIE nouvelle ligne de l'année suivante, avec le même
numero_pret — cette nouvelle ligne est ensuite tout à fait indépendante
(son propre remboursement, ses propres intérêts versés), exactement comme
n'importe quelle autre ligne. Si une ligne est corrigée plus tard, la ligne
reportée de l'année suivante (retrouvée par son numero_pret) est mise à
jour à son tour — sans jamais toucher à ce que cette ligne suivante a déjà
de son côté (remboursement/intérêts versés propres à elle).
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse

from facture.utils import interets_access_required, parse_decimal

from .models import InteretAnnee, InteretLigne, Preteur

NB_LIGNES_VIDES = 1  # ligne vierge toujours offerte en bas du tableau ; le bouton "+ Ajouter une ligne" (JS) en ajoute d'autres au besoin


def _arrondi_cent(valeur):
    """Arrondit un montant a la cenne pres (meme convention que l'affichage,
    ROUND_HALF_UP) - utilise pour cumuler les totaux a partir des memes
    valeurs arrondies que celles vues a l'ecran, et eviter un ecart d'une
    cenne entre le total et la somme des lignes affichees."""
    return valeur.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def valeur_a(montant, origine, taux, date_remb, remb, jusqua):
    """Valeur (capital + intérêt simple) d'un montant prêté à `origine`,
    évaluée à `jusqua`, TOUJOURS à l'intérieur d'une seule année civile.

    Le compte de jours inclut à la fois le jour de départ et le jour de fin
    (date fin − date début + 1), comme le calcul de l'expert-comptable : un
    prêt du 15 janvier au 31 décembre compte 351 jours, une année complète
    (1er janvier au 31 décembre) compte 365 jours (366 en bissextile).

    Si un remboursement (date_remb/remb) tombe avant `jusqua`, le calcul se
    fait en deux segments : plein montant jusqu'au remboursement inclus,
    puis solde réduit (moins le remboursement) à partir du lendemain."""
    if not origine or montant <= 0 or jusqua <= origine:
        return montant
    if not date_remb or not remb or jusqua < date_remb:
        jours = (jusqua - origine).days + 1
        return montant + montant * taux / Decimal('365') * Decimal(jours)

    jours1 = (date_remb - origine).days + 1
    valeur_au_remb = montant + montant * taux / Decimal('365') * Decimal(jours1)
    valeur_apres = valeur_au_remb - remb
    if valeur_apres < 0:
        valeur_apres = Decimal('0')
    jours2 = (jusqua - date_remb).days
    if jours2 <= 0 or valeur_apres <= 0:
        return valeur_apres
    return valeur_apres + valeur_apres * taux / Decimal('365') * Decimal(jours2)


def interet_courus(montant, origine, taux, date_remb, remb, annee_num, aujourdhui):
    """Intérêt gagné depuis `origine` jusqu'à aujourd'hui (ou jusqu'au 31
    décembre si cette année est déjà terminée)."""
    if not origine or montant <= 0:
        return Decimal('0')
    fin_annee = date(annee_num, 12, 31)
    fin_periode = min(aujourdhui, fin_annee)
    if origine > fin_periode:
        return Decimal('0')
    valeur = valeur_a(montant, origine, taux, date_remb, remb, fin_periode)
    interet = valeur - montant
    if date_remb and remb and origine <= date_remb <= fin_periode:
        interet += remb
    return interet


def interet_31dec(montant, origine, taux, date_remb, remb, annee_num):
    """Intérêt accumulé au 31 décembre de annee_num, peu importe la date
    d'aujourd'hui (utile pour voir d'avance l'intérêt de fin d'année)."""
    if not origine or montant <= 0:
        return Decimal('0')
    fin_annee = date(annee_num, 12, 31)
    valeur = valeur_a(montant, origine, taux, date_remb, remb, fin_annee)
    interet = valeur - montant
    if date_remb and remb and date_remb <= fin_annee:
        interet += remb
    return interet


def montant_a_reporter(montant, origine, taux, date_remb, remb, interet_verse, annee_num):
    """Montant à reporter comme point de départ de la ligne de l'année
    suivante (0 si le prêt est entièrement soldé cette année-là) : le solde
    au 31 décembre (capital + intérêt, moins un remboursement le cas
    échéant), moins les intérêts déjà versés cette année-là — qui ne
    doivent donc pas se capitaliser au prochain point de départ."""
    if not origine or montant <= 0:
        return Decimal('0')
    fin_annee = date(annee_num, 12, 31)
    valeur = valeur_a(montant, origine, taux, date_remb, remb, fin_annee)
    valeur -= interet_verse or Decimal('0')
    if valeur < 0:
        valeur = Decimal('0')
    return _arrondi_cent(valeur)


def calculer_preteur_pour_annee(preteur, annee_num):
    """Construit les données d'affichage (lignes de l'année, totaux) pour
    l'onglet d'une année donnée. Chaque ligne appartient à SA PROPRE année
    uniquement — voir le mécanisme de report automatique dans
    _traiter_action / _reporter_lignes_annee_suivante."""
    annee_obj = preteur.annees.filter(annee=annee_num).first()
    if not annee_obj:
        return None

    taux = (annee_obj.taux or Decimal('0')) / Decimal('100')
    aujourdhui = timezone.localdate()
    lignes = list(annee_obj.lignes.order_by('date_montant', 'ordre', 'id'))
    premiere_annee = not preteur.annees.filter(annee__lt=annee_num).exists()

    rangees = []
    total_montant = Decimal('0')
    total_remboursement = Decimal('0')
    total_interet_verse = Decimal('0')
    interet_final = Decimal('0')
    interet_31dec_final = Decimal('0')

    # Le solde d'ouverture (report d'avant tout suivi ligne par ligne) ne
    # sert qu'à la toute première année suivie de ce prêteur.
    ouverture_montant = Decimal('0')
    if premiere_annee:
        ouverture_montant = (annee_obj.solde_initial or Decimal('0')) + (annee_obj.interet_reporte or Decimal('0'))
    solde_courant = ouverture_montant

    if ouverture_montant:
        origine_ouverture = date(annee_num, 1, 1)
        interet_final += _arrondi_cent(interet_courus(ouverture_montant, origine_ouverture, taux, None, None, annee_num, aujourdhui))
        interet_31dec_final += _arrondi_cent(interet_31dec(ouverture_montant, origine_ouverture, taux, None, None, annee_num))

    for ligne in lignes:
        interet = _arrondi_cent(interet_courus(ligne.montant or Decimal('0'), ligne.date_montant, taux, ligne.date_remboursement, ligne.remboursement, annee_num, aujourdhui))
        interet_fin = _arrondi_cent(interet_31dec(ligne.montant or Decimal('0'), ligne.date_montant, taux, ligne.date_remboursement, ligne.remboursement, annee_num))
        solde_courant += ligne.montant or Decimal('0')
        solde_courant -= ligne.remboursement or Decimal('0')
        rangees.append({'ligne': ligne, 'interet': interet, 'interet_31dec': interet_fin, 'solde': solde_courant})
        total_montant += ligne.montant or Decimal('0')
        total_remboursement += ligne.remboursement or Decimal('0')
        total_interet_verse += ligne.interet_verse or Decimal('0')
        interet_final += interet
        interet_31dec_final += interet_fin

    return {
        'rangees': rangees,
        'total_montant': total_montant,
        'total_remboursement': total_remboursement,
        'total_interet_verse': total_interet_verse,
        'interet_final': interet_final,
        'interet_31dec_final': interet_31dec_final,
        'solde_final': solde_courant,
    }


def _redirect_interets(annee):
    return redirect(reverse('interets') + f'?annee={annee}')


@interets_access_required
def interets_page(request):
    if request.method == 'POST':
        return _traiter_action(request)

    try:
        annee_courante = int(request.GET.get('annee') or timezone.localdate().year)
    except (TypeError, ValueError):
        annee_courante = timezone.localdate().year

    preteurs = list(Preteur.objects.all())
    annees_disponibles = {annee_courante, timezone.localdate().year}
    for a in InteretAnnee.objects.values_list('annee', flat=True):
        annees_disponibles.add(a)

    blocs = []
    total_montant_general = Decimal('0')
    total_remboursement_general = Decimal('0')
    total_interet_general = Decimal('0')
    total_interet_31dec_general = Decimal('0')
    total_interet_verse_general = Decimal('0')
    total_solde_general = Decimal('0')
    for preteur in preteurs:
        annee_obj = preteur.annees.filter(annee=annee_courante).first()
        calc = calculer_preteur_pour_annee(preteur, annee_courante) if annee_obj else None
        if calc:
            total_montant_general += calc['total_montant']
            total_remboursement_general += calc['total_remboursement']
            total_interet_general += calc['interet_final']
            total_interet_31dec_general += calc['interet_31dec_final']
            total_interet_verse_general += calc['total_interet_verse']
            total_solde_general += calc['solde_final']
        premiere_annee = bool(annee_obj) and not preteur.annees.filter(annee__lt=annee_courante).exists()
        annee_terminee = annee_courante < timezone.localdate().year

        blocs.append({
            'preteur': preteur,
            'annee_obj': annee_obj,
            'calc': calc,
            'lignes_vides': range(NB_LIGNES_VIDES),
            'premiere_annee': premiere_annee,
            'annee_terminee': annee_terminee,
        })

    return render(request, 'compte/interets.html', {
        'title': 'Registre des intérêts',
        'annee_courante': annee_courante,
        'annees_disponibles': sorted(annees_disponibles),
        'blocs': blocs,
        'grand_total': {
            'total_montant': total_montant_general,
            'total_remboursement': total_remboursement_general,
            'interet_final': total_interet_general,
            'interet_31dec_final': total_interet_31dec_general,
            'total_interet_verse': total_interet_verse_general,
            'solde_final': total_solde_general,
        },
        'nb_preteurs_avec_donnees': sum(1 for b in blocs if b['calc']),
    })


def _reporter_lignes_annee_suivante(annee_obj):
    """Pour chaque ligne de annee_obj qui garde un solde à la fin de
    l'année, crée ou met à jour la ligne correspondante de l'année suivante
    (même numero_pret), datée du 1er janvier, avec ce solde comme point de
    départ — sans jamais toucher au remboursement/intérêts versés déjà
    inscrits sur cette ligne suivante (qui lui appartiennent en propre)."""
    preteur = annee_obj.preteur
    taux = (annee_obj.taux or Decimal('0')) / Decimal('100')
    annee_suivante_num = annee_obj.annee + 1

    for ligne in annee_obj.lignes.all():
        if not ligne.numero_pret or not ligne.date_montant or not ligne.montant:
            continue
        a_reporter = montant_a_reporter(
            ligne.montant, ligne.date_montant, taux,
            ligne.date_remboursement, ligne.remboursement,
            ligne.interet_verse, annee_obj.annee,
        )
        existante = InteretLigne.objects.filter(
            numero_pret=ligne.numero_pret,
            annee__preteur=preteur, annee__annee=annee_suivante_num,
        ).first()

        if a_reporter <= 0:
            # Prêt soldé : si une ligne suivante existe mais n'a encore
            # aucune activité propre, elle est maintenant sans objet.
            if existante and not existante.remboursement and not existante.date_remboursement and not existante.interet_verse:
                existante.delete()
            continue

        annee_suivante_obj, _ = InteretAnnee.objects.get_or_create(
            preteur=preteur, annee=annee_suivante_num,
            defaults={'taux': annee_obj.taux, 'solde_initial': Decimal('0'), 'interet_reporte': Decimal('0')},
        )
        if existante:
            existante.date_montant = date(annee_suivante_num, 1, 1)
            existante.montant = a_reporter
            existante.save(update_fields=['date_montant', 'montant'])
        else:
            InteretLigne.objects.create(
                annee=annee_suivante_obj,
                ordre=(annee_suivante_obj.lignes.aggregate(Max('ordre'))['ordre__max'] or 0) + 1,
                numero_pret=ligne.numero_pret,
                date_montant=date(annee_suivante_num, 1, 1),
                montant=a_reporter,
            )


def _traiter_action(request):
    action = request.POST.get('action')
    annee_courante = request.POST.get('annee_courante') or timezone.localdate().year

    if action == 'ajouter_preteur':
        nom = (request.POST.get('nom') or '').strip()
        if nom:
            Preteur.objects.get_or_create(nom=nom)
        else:
            messages.error(request, "Le nom du prêteur ne peut pas être vide.")

    elif action == 'creer_annee':
        preteur = get_object_or_404(Preteur, pk=request.POST.get('preteur_id'))
        try:
            annee_num = int(request.POST.get('annee'))
        except (TypeError, ValueError):
            messages.error(request, "Année invalide.")
            return _redirect_interets(annee_courante)

        precedente = preteur.annees.filter(annee__lt=annee_num).order_by('-annee').first()
        defaults = {
            'taux': precedente.taux if precedente else Decimal('0'),
            'solde_initial': Decimal('0'),
            'interet_reporte': Decimal('0'),
        }
        InteretAnnee.objects.get_or_create(preteur=preteur, annee=annee_num, defaults=defaults)
        annee_courante = annee_num

    elif action == 'maj_parametres':
        annee_obj = get_object_or_404(InteretAnnee, pk=request.POST.get('annee_id'))
        nouveau_taux = parse_decimal(request.POST.get('taux'), none_if_blank=True, strip_spaces=True)
        if nouveau_taux is not None:
            annee_obj.taux = nouveau_taux
        premiere_annee = not annee_obj.preteur.annees.filter(annee__lt=annee_obj.annee).exists()
        if premiere_annee:
            nouveau_solde = parse_decimal(request.POST.get('solde_initial'), none_if_blank=True, strip_spaces=True)
            if nouveau_solde is not None:
                annee_obj.solde_initial = nouveau_solde
            nouvel_interet_reporte = parse_decimal(request.POST.get('interet_reporte'), none_if_blank=True, strip_spaces=True)
            if nouvel_interet_reporte is not None:
                annee_obj.interet_reporte = nouvel_interet_reporte
        annee_obj.save()

    elif action == 'enregistrer_lignes':
        annee_obj = get_object_or_404(InteretAnnee, pk=request.POST.get('annee_id'))
        ids = request.POST.getlist('ligne_id[]')
        dates_montant = request.POST.getlist('date_montant[]')
        montants = request.POST.getlist('montant[]')
        dates_remboursement = request.POST.getlist('date_remboursement[]')
        remboursements = request.POST.getlist('remboursement[]')
        interets_verses = request.POST.getlist('interet_verse[]')

        prochain_ordre = (annee_obj.lignes.aggregate(Max('ordre'))['ordre__max'] or 0) + 1
        for i in range(len(ids)):
            d1 = (dates_montant[i] or '').strip()
            m = parse_decimal(montants[i], none_if_blank=True, strip_spaces=True)
            d2 = (dates_remboursement[i] or '').strip()
            r = parse_decimal(remboursements[i], none_if_blank=True, strip_spaces=True)
            iv = parse_decimal(interets_verses[i], none_if_blank=True, strip_spaces=True) if i < len(interets_verses) else None
            vide = not d1 and m is None and not d2 and r is None and iv is None
            ligne_id = ids[i]

            if ligne_id:
                ligne = InteretLigne.objects.filter(
                    pk=ligne_id, annee=annee_obj,
                ).first()
                if not ligne:
                    continue
                if vide:
                    ligne.delete()
                else:
                    ligne.date_montant = d1 or None
                    ligne.montant = m
                    ligne.date_remboursement = d2 or None
                    ligne.remboursement = r
                    ligne.interet_verse = iv
                    ligne.save()
                    if not ligne.numero_pret:
                        ligne.numero_pret = ligne.id
                        ligne.save(update_fields=['numero_pret'])
            elif not vide:
                nouvelle = InteretLigne.objects.create(
                    annee=annee_obj, ordre=prochain_ordre,
                    date_montant=d1 or None, montant=m,
                    date_remboursement=d2 or None, remboursement=r,
                    interet_verse=iv,
                )
                nouvelle.numero_pret = nouvelle.id
                nouvelle.save(update_fields=['numero_pret'])
                prochain_ordre += 1

        _reporter_lignes_annee_suivante(annee_obj)

    elif action == 'supprimer_ligne':
        InteretLigne.objects.filter(pk=request.POST.get('ligne_id')).delete()

    elif action == 'supprimer_annee':
        InteretAnnee.objects.filter(pk=request.POST.get('annee_id')).delete()

    elif action == 'supprimer_preteur':
        Preteur.objects.filter(pk=request.POST.get('preteur_id')).delete()

    return _redirect_interets(annee_courante)
