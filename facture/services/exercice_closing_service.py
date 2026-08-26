from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from compte.models import Compte
from facture.models import Tr_desc, Tr_detail, Source


def calculer_soldes_comptes_resultat(date_debut, date_fin):
    """Retourne {compte: solde_net} pour chaque compte de resultat (numero >= 4000)
    ayant un solde non nul sur la periode donnee."""
    comptes = Compte.objects.filter(numero__gte=4000)
    soldes = {}

    for compte in comptes:
        total = Tr_detail.objects.filter(
            compte=compte,
            tr_desc__date__gte=date_debut,
            tr_desc__date__lte=date_fin,
        ).aggregate(total=Sum('montant')).get('total') or Decimal('0')

        if total != Decimal('0'):
            soldes[compte] = total

    return soldes


def clore_exercice(exercice, settings_instance, next_no_ej):
    """Cree l'ecriture de cloture qui remet a zero les comptes de resultat et
    transfere le solde net au compte_bnr. Marque l'exercice comme audite."""
    if exercice.est_audite:
        raise ValueError("Cet exercice est deja audite.")

    if not settings_instance.compte_bnr:
        raise ValueError(
            "Configure le compte des benefices non repartis (compte_bnr) dans Setting avant de clore l'exercice."
        )

    soldes = calculer_soldes_comptes_resultat(exercice.date_debut, exercice.date_fin)

    if not soldes:
        exercice.est_audite = True
        exercice.cloture_le = timezone.now()
        exercice.save(update_fields=['est_audite', 'cloture_le'])
        return 0

    with transaction.atomic():
        source_cloture, _ = Source.objects.get_or_create(nom='Cloture exercice')

        tr_desc = Tr_desc.objects.create(
            no_ej=next_no_ej(exercice.date_fin),
            date=exercice.date_fin,
            desc_ctb=f"Cloture exercice {exercice.date_fin}",
            source=source_cloture,
        )

        total_net = Decimal('0')
        for compte, solde in soldes.items():
            Tr_detail.objects.create(tr_desc=tr_desc, compte=compte, montant=-solde)
            total_net += solde

        if total_net != Decimal('0'):
            Tr_detail.objects.create(tr_desc=tr_desc, compte=settings_instance.compte_bnr, montant=total_net)

        exercice.est_audite = True
        exercice.cloture_le = timezone.now()
        exercice.save(update_fields=['est_audite', 'cloture_le'])

    return len(soldes)