"""Vues liées à la conciliation bancaire (onglet Banque)."""

from datetime import date
from decimal import Decimal

from django.db import connections
from django.db.models import Sum
from django.shortcuts import render

from facture.models import Cheque, CompteReleve, Releve, Tr_detail
from facture.utils import get_setting
from facture.working_period import get_working_period


def _premier_jour_mois_suivant(annee, mois):
    if mois == 12:
        return date(annee + 1, 1, 1)
    return date(annee, mois + 1, 1)


def _ledger_db_alias():
    return Tr_detail.objects.all().db


def _fetch_bloc_grand_livre(compte_id, date_debut, date_fin_exclusive):
    """Utilise la fonction SQL solde_conciliation_pour_periode (voir migration
    0031_create_solde_conciliation_function.py), meme pattern que
    solde_fin_pour_exercice pour le grand livre."""
    db_alias = _ledger_db_alias()
    with connections[db_alias].cursor() as cursor:
        cursor.execute(
            "SELECT solde_depart, total_debits, total_credits, solde_fin "
            "FROM solde_conciliation_pour_periode(%s, %s, %s)",
            [compte_id, date_debut, date_fin_exclusive],
        )
        row = cursor.fetchone()

    if not row:
        return None

    return {
        'solde_depart': row[0],
        'total_debits': row[1],
        'total_credits': row[2],
        'solde_fin': row[3],
    }


def conciliation(request):
    setting = get_setting()
    working_period = get_working_period(request)
    compte_banque_id = setting.compte_cheques_id if setting else None

    premier_jour_mois = date(working_period['year'], working_period['month'], 1)
    premier_jour_mois_suivant = _premier_jour_mois_suivant(working_period['year'], working_period['month'])

    bloc_grand_livre = None
    bloc_releve = None
    compte_releve_banque = None

    if compte_banque_id:
        bloc_grand_livre = _fetch_bloc_grand_livre(compte_banque_id, premier_jour_mois, premier_jour_mois_suivant)
        compte_releve_banque = CompteReleve.objects.filter(compte_comptable_id=compte_banque_id).first()

    if compte_releve_banque:
        no_cheques_encaisses = (
            Releve.objects
            .filter(compte_releve_id=compte_releve_banque.id)
            .exclude(no_cheque='')
            .values_list('no_cheque', flat=True)
        )
    else:
        no_cheques_encaisses = []

    cheques_en_circulation = (
        Cheque.objects
        .filter(annule=False)
        .exclude(no_cheque__in=no_cheques_encaisses)
        .order_by('date_emission')
    )

    if compte_releve_banque:
        derniere_ligne_releve = (
            Releve.objects
            .filter(
                compte_releve_id=compte_releve_banque.id,
                date__gte=premier_jour_mois,
                date__lt=premier_jour_mois_suivant,
            )
            .order_by('-date', '-no_ligne')
            .first()
        )

        if derniere_ligne_releve:
            cheques_en_circulation_periode = cheques_en_circulation.filter(date_emission__lt=premier_jour_mois_suivant)
            total_cheques_circulation = cheques_en_circulation_periode.aggregate(
                total=Sum('montant')
            )['total'] or Decimal('0')

            solde_releve = derniere_ligne_releve.solde
            solde_ajuste = solde_releve - total_cheques_circulation

            bloc_releve = {
                'solde_releve': solde_releve,
                'date_releve': derniere_ligne_releve.date,
                'total_cheques_circulation': total_cheques_circulation,
                'solde_ajuste': solde_ajuste,
            }

    return render(request, "conciliation/index.html", {
        'title': "Conciliation bancaire",
        'working_period': working_period,
        'bloc_grand_livre': bloc_grand_livre,
        'bloc_releve': bloc_releve,
        'cheques_en_circulation': cheques_en_circulation,
    })
