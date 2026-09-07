"""Registre de référence des intérêts (page 'Intérêts', sous Administration).

Page volontairement séparée des transactions réelles du projet : sert
uniquement à calculer, année par année et par prêteur, les intérêts courus
sur des montants prêtés/remboursés saisis à la main (souvent à partir des
relevés bancaires). Voir compte/models.py (Preteur, InteretAnnee,
InteretLigne) pour le schéma, et le docstring de calculer_annee ci-dessous
pour la méthode de calcul.
"""

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from facture.utils import expert_required, parse_decimal

from .models import InteretAnnee, InteretLigne, Preteur

NB_LIGNES_VIDES = 3  # lignes vierges toujours offertes en bas du tableau, pour ajouter des entrées


def calculer_annee(annee_obj):
    """Calcule, ligne par ligne, les intérêts courus et le solde à rembourser pour une année.

    Hypothèse : les lignes sont entrées en ordre chronologique. Pour chaque
    ligne, on prend sa date (celle du montant prêté, sinon celle du
    remboursement) comme repère ; les intérêts courent sur le solde en
    cours depuis le dernier repère (ou depuis le 1er janvier), au taux
    annuel de l'année proraté par jour (taux / 100 / 365). On calcule aussi
    les intérêts courus jusqu'au 31 décembre, pour le résumé de fin d'année.
    """
    taux = (annee_obj.taux or Decimal('0')) / Decimal('100')
    solde = annee_obj.solde_initial or Decimal('0')
    dernier_repere = date(annee_obj.annee, 1, 1)
    interet_cumule = Decimal('0')
    total_montant = Decimal('0')
    total_remboursement = Decimal('0')

    rangees = []
    for ligne in annee_obj.lignes.all():
        date_repere = ligne.date_montant or ligne.date_remboursement
        interet_affiche = None
        solde_affiche = None
        if date_repere:
            jours = (date_repere - dernier_repere).days
            if jours > 0:
                interet_cumule += solde * taux / Decimal('365') * Decimal(jours)
                dernier_repere = date_repere
            montant = ligne.montant or Decimal('0')
            remboursement = ligne.remboursement or Decimal('0')
            total_montant += montant
            total_remboursement += remboursement
            solde += montant - remboursement
            interet_affiche = interet_cumule
            solde_affiche = solde
        rangees.append({'ligne': ligne, 'interet': interet_affiche, 'solde': solde_affiche})

    fin_annee = date(annee_obj.annee, 12, 31)
    jours_jusqu_fin = (fin_annee - dernier_repere).days
    interet_final = interet_cumule
    if jours_jusqu_fin > 0:
        interet_final += solde * taux / Decimal('365') * Decimal(jours_jusqu_fin)

    return {
        'rangees': rangees,
        'solde_final': solde,
        'interet_final': interet_final,
        'total_montant': total_montant,
        'total_remboursement': total_remboursement,
    }


def _redirect_interets(annee):
    return redirect(reverse('interets') + f'?annee={annee}')


@expert_required
def interets_page(request):
    if request.method == 'POST':
        return _traiter_action(request)

    try:
        annee_courante = int(request.GET.get('annee') or date.today().year)
    except (TypeError, ValueError):
        annee_courante = date.today().year

    preteurs = list(Preteur.objects.all())
    annees_disponibles = {annee_courante, date.today().year}
    for a in InteretAnnee.objects.values_list('annee', flat=True):
        annees_disponibles.add(a)

    blocs = []
    total_montant_general = Decimal('0')
    total_remboursement_general = Decimal('0')
    total_interet_general = Decimal('0')
    total_solde_general = Decimal('0')
    for preteur in preteurs:
        annee_obj = preteur.annees.filter(annee=annee_courante).first()
        calc = calculer_annee(annee_obj) if annee_obj else None
        if calc:
            total_montant_general += calc['total_montant']
            total_remboursement_general += calc['total_remboursement']
            total_interet_general += calc['interet_final']
            total_solde_general += calc['solde_final']
        premiere_annee = bool(annee_obj) and not preteur.annees.filter(annee__lt=annee_courante).exists()

        annee_precedente_obj = preteur.annees.filter(annee=annee_courante - 1).first()
        rangees_precedentes = None
        if annee_precedente_obj:
            rangees_precedentes = calculer_annee(annee_precedente_obj)['rangees']

        blocs.append({
            'preteur': preteur,
            'annee_obj': annee_obj,
            'calc': calc,
            'lignes_vides': range(NB_LIGNES_VIDES),
            'premiere_annee': premiere_annee,
            'annee_precedente_num': annee_courante - 1,
            'rangees_precedentes': rangees_precedentes,
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
            'solde_final': total_solde_general,
        },
        'nb_preteurs_avec_donnees': sum(1 for b in blocs if b['calc']),
    })


def _traiter_action(request):
    action = request.POST.get('action')
    annee_courante = request.POST.get('annee_courante') or date.today().year

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
        if precedente:
            calc = calculer_annee(precedente)
            defaults = {'taux': precedente.taux, 'solde_initial': calc['solde_final']}
        else:
            defaults = {'taux': Decimal('0'), 'solde_initial': Decimal('0')}
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
            # Le solde de départ n'est modifiable à la main que pour la toute première année suivie de ce prêteur
            # (les années suivantes reçoivent leur solde de départ par report — voir action 'reporter_annee').
            nouveau_solde = parse_decimal(request.POST.get('solde_initial'), none_if_blank=True)
            if nouveau_solde is not None:
                annee_obj.solde_initial = nouveau_solde
        annee_obj.save()

    elif action == 'enregistrer_lignes':
        annee_obj = get_object_or_404(InteretAnnee, pk=request.POST.get('annee_id'))
        ids = request.POST.getlist('ligne_id[]')
        dates_montant = request.POST.getlist('date_montant[]')
        montants = request.POST.getlist('montant[]')
        dates_remboursement = request.POST.getlist('date_remboursement[]')
        remboursements = request.POST.getlist('remboursement[]')

        prochain_ordre = (annee_obj.lignes.aggregate(Max('ordre'))['ordre__max'] or 0) + 1
        for i in range(len(ids)):
            d1 = (dates_montant[i] or '').strip()
            m = parse_decimal(montants[i], none_if_blank=True)
            d2 = (dates_remboursement[i] or '').strip()
            r = parse_decimal(remboursements[i], none_if_blank=True)
            vide = not d1 and m is None and not d2 and r is None
            ligne_id = ids[i]

            if ligne_id:
                ligne = InteretLigne.objects.filter(pk=ligne_id, annee=annee_obj).first()
                if not ligne:
                    continue
                if vide:
                    ligne.delete()
                else:
                    ligne.date_montant = d1 or None
                    ligne.montant = m
                    ligne.date_remboursement = d2 or None
                    ligne.remboursement = r
                    ligne.save()
            elif not vide:
                InteretLigne.objects.create(
                    annee=annee_obj, ordre=prochain_ordre,
                    date_montant=d1 or None, montant=m,
                    date_remboursement=d2 or None, remboursement=r,
                )
                prochain_ordre += 1

    elif action == 'supprimer_ligne':
        InteretLigne.objects.filter(pk=request.POST.get('ligne_id')).delete()

    elif action == 'supprimer_preteur':
        Preteur.objects.filter(pk=request.POST.get('preteur_id')).delete()

    elif action == 'reporter_annee':
        annee_obj = get_object_or_404(InteretAnnee, pk=request.POST.get('annee_id'))
        calc = calculer_annee(annee_obj)
        InteretAnnee.objects.get_or_create(
            preteur=annee_obj.preteur, annee=annee_obj.annee + 1,
            defaults={'taux': annee_obj.taux, 'solde_initial': calc['solde_final']},
        )
        annee_courante = annee_obj.annee + 1

    return _redirect_interets(annee_courante)
