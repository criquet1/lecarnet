"""Registre de référence des intérêts (page 'Intérêts', sous Administration).

Page volontairement séparée des transactions réelles du projet : sert
uniquement à calculer les intérêts courus sur des montants prêtés/remboursés
saisis à la main (souvent à partir des relevés bancaires), par prêteur. Voir
compte/models.py (Preteur, InteretAnnee, InteretLigne) pour le schéma.

Principe de calcul (voir construire_tranches / interet_gagne_durant
ci-dessous) : chaque montant prêté est une tranche indépendante, qui ne
fusionne jamais avec les autres. Son intérêt se calcule au prorata (base 365
jours) sur ce qu'il en reste — les remboursements réduisent d'abord la
tranche la plus ancienne (FIFO), toutes années confondues — depuis sa propre
date jusqu'à aujourd'hui. À chaque 31 décembre, l'intérêt couru et non versé
d'une tranche se capitalise (s'ajoute à son propre capital), qui continue
ensuite de porter intérêt à son tour : c'est une capitalisation annuelle,
par tranche, indépendante des autres tranches.
"""

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse

from facture.utils import interets_access_required, parse_decimal

from .models import InteretAnnee, InteretLigne, Preteur

NB_LIGNES_VIDES = 3  # lignes vierges toujours offertes en bas du tableau, pour ajouter des entrées


def taux_lookup_pour_preteur(preteur):
    """Retourne une fonction annee -> taux (décimal, ex. 0.12) pour ce prêteur.

    Si une année donnée n'a pas encore sa propre fiche (ex. on calcule
    l'intérêt couru dans une année future pas encore créée), on utilise le
    taux de l'année connue la plus récente qui la précède.
    """
    taux_par_annee = {a.annee: (a.taux or Decimal('0')) / Decimal('100') for a in preteur.annees.all()}
    if not taux_par_annee:
        return lambda y: Decimal('0')

    def lookup(y):
        anterieures = [a for a in taux_par_annee if a <= y]
        if anterieures:
            return taux_par_annee[max(anterieures)]
        return taux_par_annee[min(taux_par_annee)]

    return lookup


def valeur_composee(montant, date_debut, taux_lookup, jusqua, interet_verse=None):
    """Valeur (capital + intérêts capitalisés) d'un montant prêté à date_debut,
    évaluée à la date jusqua, avec capitalisation annuelle (au 31 décembre de
    chaque année traversée) et intérêt simple, au prorata sur 365 jours, à
    l'intérieur de chaque année.

    interet_verse, s'il est fourni, est le montant d'intérêts déjà réglé
    pour la toute première année de cette tranche : il réduit d'autant
    l'intérêt qui se capitalise à la bascule vers l'année suivante (un
    intérêt entièrement versé ne capitalise rien, le capital reste
    inchangé)."""
    if montant <= 0 or jusqua <= date_debut:
        return montant
    valeur = montant
    point = date_debut
    annee = point.year
    annee_origine = point.year
    while point < jusqua:
        borne_annee = date(annee, 12, 31)
        borne = min(jusqua, borne_annee)
        jours = (borne - point).days
        interet_periode = Decimal('0')
        if jours > 0:
            interet_periode = valeur * taux_lookup(annee) / Decimal('365') * Decimal(jours)
        if borne >= borne_annee and jusqua > borne:
            a_capitaliser = interet_periode
            if annee == annee_origine and interet_verse:
                a_capitaliser -= interet_verse
                if a_capitaliser < 0:
                    a_capitaliser = Decimal('0')
            valeur += a_capitaliser
            annee += 1
            point = date(annee, 1, 1)
        else:
            valeur += interet_periode
            point = borne
    return valeur


def valeur_tranche_au(tranche, taux_lookup, jusqua):
    """Valeur (capital restant + intérêts capitalisés) d'une tranche, à la
    date jusqua."""
    montant = tranche['montant_restant']
    origine = tranche['date']
    if montant <= 0 or jusqua <= origine:
        return montant
    return valeur_composee(montant, origine, taux_lookup, jusqua, interet_verse=tranche.get('interet_verse'))


def interet_gagne_durant(tranche, annee_num, taux_lookup):
    """Intérêt gagné par une tranche (sur ce qu'il en reste après FIFO)
    spécifiquement durant l'année civile annee_num (tient compte de la
    capitalisation des années antérieures, mais isole la portion propre à
    cette année-là)."""
    montant = tranche['montant_restant']
    origine = tranche['date']
    fin_annee = date(annee_num, 12, 31)
    fin_periode = min(timezone.localdate(), fin_annee)
    if montant <= 0 or origine > fin_periode:
        return Decimal('0')
    debut_ref = max(origine, date(annee_num, 1, 1))
    valeur_debut = valeur_tranche_au(tranche, taux_lookup, debut_ref)
    valeur_fin = valeur_tranche_au(tranche, taux_lookup, fin_periode)
    return valeur_fin - valeur_debut


def construire_tranches(preteur):
    """Construit, en ordre chronologique, toutes les tranches (montants
    prêtés) de ce prêteur, toutes années confondues, avec ce qu'il en reste
    après application FIFO de tous les remboursements (les plus anciennes
    tranches remboursées en premier). Le solde reporté au 1er janvier de la
    toute première année suivie (avant tout suivi ligne par ligne, s'il y a
    lieu) compte comme une tranche à part, datée du 1er janvier de cette
    année-là — les années suivantes n'ont plus besoin de ce report, chaque
    ligne restant indépendante indéfiniment.

    Retourne (tranches, lignes) où lignes est la liste ordonnée de toutes
    les InteretLigne de ce prêteur.
    """
    tranches = []

    premiere = preteur.annees.order_by('annee').first()
    if premiere:
        ouverture = (premiere.solde_initial or Decimal('0')) + (premiere.interet_reporte or Decimal('0'))
        if ouverture:
            tranches.append({
                'ligne': None, 'date': date(premiere.annee, 1, 1),
                'montant_restant': ouverture, 'interet_verse': None,
            })

    lignes = list(
        InteretLigne.objects.filter(annee__preteur=preteur)
        .select_related('annee').order_by('annee__annee', 'date_montant', 'ordre', 'id')
    )
    for ligne in lignes:
        montant = ligne.montant or Decimal('0')
        if ligne.date_montant and montant:
            tranches.append({
                'ligne': ligne, 'date': ligne.date_montant, 'montant_restant': montant,
                'interet_verse': ligne.interet_verse,
            })

    for ligne in lignes:
        remboursement = ligne.remboursement or Decimal('0')
        if ligne.date_remboursement and remboursement:
            a_repartir = remboursement
            for tranche in tranches:
                if a_repartir <= 0:
                    break
                if tranche['date'] > ligne.date_remboursement:
                    continue  # une tranche pas encore avancée à cette date ne peut pas être remboursée
                prise = min(tranche['montant_restant'], a_repartir)
                tranche['montant_restant'] -= prise
                a_repartir -= prise

    return tranches, lignes


def calculer_preteur_pour_annee(preteur, annee_num):
    """Construit les données d'affichage (lignes de l'année, lignes des
    années antérieures encore visibles, totaux) pour l'onglet d'une année
    donnée, à partir de l'historique complet du prêteur (toutes années
    confondues)."""
    taux_lookup = taux_lookup_pour_preteur(preteur)
    tranches, lignes = construire_tranches(preteur)
    tranche_par_ligne_id = {t['ligne'].id: t for t in tranches if t['ligne'] is not None}
    tranche_ouverture = next((t for t in tranches if t['ligne'] is None), None)

    rangees = []
    rangees_precedentes = []
    total_montant = Decimal('0')
    total_remboursement = Decimal('0')
    total_interet_verse = Decimal('0')

    # "Solde à rembourser" est un cumul qui repart de 0 en haut de CETTE
    # page (celle de annee_num) et additionne chaque ligne affichée, dans
    # l'ordre — le report d'ouverture, s'il existe et qu'aucune ligne
    # antérieure détaillée n'est visible, sert de point de départ.
    solde_courant = Decimal('0')
    if tranche_ouverture and not any(l.annee.annee < annee_num for l in lignes):
        solde_courant = tranche_ouverture['montant_restant']

    debut_annee_courante = date(annee_num, 1, 1)
    for ligne in lignes:
        if ligne.annee.annee > annee_num:
            continue
        tranche = tranche_par_ligne_id.get(ligne.id)
        interet = interet_gagne_durant(tranche, annee_num, taux_lookup) if tranche else None
        if ligne.annee.annee == annee_num:
            # Ligne de l'année courante : "Montant prêté" reste la valeur
            # saisie telle quelle (éditable), pas de capitalisation à afficher.
            solde_courant += ligne.montant or Decimal('0')
            solde_courant -= ligne.remboursement or Decimal('0')
            rangee = {'ligne': ligne, 'interet': interet, 'solde': solde_courant}
            rangees.append(rangee)
            total_montant += ligne.montant or Decimal('0')
            total_remboursement += ligne.remboursement or Decimal('0')
            total_interet_verse += ligne.interet_verse or Decimal('0')
        else:
            # Ligne d'une année antérieure : "Montant prêté" affiche la
            # valeur capitalisée (capital restant + intérêts non versés) au
            # 1er janvier de l'année consultée ; "Solde à rembourser" cumule
            # cette valeur-là (moins un remboursement fait sur cette même
            # ligne) à la suite des lignes précédentes.
            montant_affiche = valeur_tranche_au(tranche, taux_lookup, debut_annee_courante) if tranche else ligne.montant
            solde_courant += montant_affiche or Decimal('0')
            solde_courant -= ligne.remboursement or Decimal('0')
            rangee = {'ligne': ligne, 'interet': interet, 'solde': solde_courant, 'montant_affiche': montant_affiche}
            rangees_precedentes.append(rangee)

    # Total de l'année = l'intérêt gagné cette année-là par TOUTES les
    # tranches encore actives, qu'elles soient nées cette année ou avant
    # (le report d'ouverture, s'il existe, y compris — il n'est pas affiché
    # ligne par ligne au-delà de sa propre année, mais continue de courir).
    interet_final = sum(
        (interet_gagne_durant(t, annee_num, taux_lookup) for t in tranches),
        Decimal('0'),
    )

    return {
        'rangees': rangees,
        'rangees_precedentes': rangees_precedentes,
        'total_montant': total_montant,
        'total_remboursement': total_remboursement,
        'total_interet_verse': total_interet_verse,
        'interet_final': interet_final,
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
    total_interet_verse_general = Decimal('0')
    total_solde_general = Decimal('0')
    for preteur in preteurs:
        annee_obj = preteur.annees.filter(annee=annee_courante).first()
        calc = calculer_preteur_pour_annee(preteur, annee_courante) if annee_obj else None
        if calc:
            total_montant_general += calc['total_montant']
            total_remboursement_general += calc['total_remboursement']
            total_interet_general += calc['interet_final']
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
            'rangees_precedentes': calc['rangees_precedentes'] if calc else None,
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
            'total_interet_verse': total_interet_verse_general,
            'solde_final': total_solde_general,
        },
        'nb_preteurs_avec_donnees': sum(1 for b in blocs if b['calc']),
    })


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
        # Le solde reporté et les intérêts reportés ne servent plus qu'à la
        # toute première année suivie d'un prêteur (avant tout suivi ligne
        # par ligne) — chaque ligne restant ensuite indépendante et visible
        # indéfiniment, il n'y a plus rien à reporter d'une année à l'autre.
        defaults = {
            'taux': precedente.taux if precedente else Decimal('0'),
            'solde_initial': Decimal('0'),
            'interet_reporte': Decimal('0'),
        }
        InteretAnnee.objects.get_or_create(preteur=preteur, annee=annee_num, defaults=defaults)
        annee_courante = annee_num

    elif action == 'maj_parametres':
        annee_obj = get_object_or_404(InteretAnnee, pk=request.POST.get('annee_id'))
        # Une case laissée vide ne modifie pas la valeur existante (évite de remettre
        # accidentellement le taux ou le solde à 0 si le champ est soumis vide).
        nouveau_taux = parse_decimal(request.POST.get('taux'), none_if_blank=True)
        if nouveau_taux is not None:
            annee_obj.taux = nouveau_taux
        premiere_annee = not annee_obj.preteur.annees.filter(annee__lt=annee_obj.annee).exists()
        if premiere_annee:
            # Le solde de départ et les intérêts reportés ne sont modifiables à la main que pour la
            # toute première année suivie de ce prêteur (report d'ouverture, avant tout suivi ligne
            # par ligne). Les années suivantes n'en ont plus besoin.
            nouveau_solde = parse_decimal(request.POST.get('solde_initial'), none_if_blank=True)
            if nouveau_solde is not None:
                annee_obj.solde_initial = nouveau_solde
            nouvel_interet_reporte = parse_decimal(request.POST.get('interet_reporte'), none_if_blank=True)
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
            m = parse_decimal(montants[i], none_if_blank=True)
            d2 = (dates_remboursement[i] or '').strip()
            r = parse_decimal(remboursements[i], none_if_blank=True)
            iv = parse_decimal(interets_verses[i], none_if_blank=True) if i < len(interets_verses) else None
            vide = not d1 and m is None and not d2 and r is None and iv is None
            ligne_id = ids[i]

            if ligne_id:
                # Une ligne existante peut appartenir à N'IMPORTE QUELLE année de ce
                # prêteur (les lignes des années antérieures restent modifiables —
                # remboursement/intérêts versés — depuis l'onglet de l'année en
                # cours), pas seulement à annee_obj : on la retrouve par son id,
                # avec une vérification d'appartenance au bon prêteur.
                ligne = InteretLigne.objects.filter(
                    pk=ligne_id, annee__preteur=annee_obj.preteur,
                ).select_related('annee').first()
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
            elif not vide:
                InteretLigne.objects.create(
                    annee=annee_obj, ordre=prochain_ordre,
                    date_montant=d1 or None, montant=m,
                    date_remboursement=d2 or None, remboursement=r,
                    interet_verse=iv,
                )
                prochain_ordre += 1

    elif action == 'supprimer_ligne':
        InteretLigne.objects.filter(pk=request.POST.get('ligne_id')).delete()

    elif action == 'supprimer_preteur':
        Preteur.objects.filter(pk=request.POST.get('preteur_id')).delete()

    return _redirect_interets(annee_courante)
