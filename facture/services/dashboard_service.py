import calendar
from decimal import Decimal
from facture.models import Client, Facture, Fournisseur, Releve, CompteReleve
from facture.working_period import get_working_period


def compute_dashboard_data(request):
    working_period = get_working_period(request)
    mois = working_period['month']
    annee = working_period['year']

    nom_mois = calendar.month_name[mois]

    mois_liste = [(i, calendar.month_name[i]) for i in range(1, 13)]
    annees = list(range(annee - 5, annee + 2))

    noms_compagnies_actives = list(
        Client.objects.filter(active=True).values_list('nom', flat=True)
    ) + list(
        Fournisseur.objects.filter(active=True).values_list('nom', flat=True)
    )
    nb_compagnies = len(noms_compagnies_actives)

    factures_mois = Facture.objects.filter(date__month=mois, date__year=annee)
    nb_factures = factures_mois.values('no_ej').distinct().count()

    compagnies_avec_facture = factures_mois.filter(compagnie__in=noms_compagnies_actives).values("compagnie").distinct().count()
    compagnies_sans_facture = nb_compagnies - compagnies_avec_facture

    if compagnies_sans_facture == 0:
        reminder_factures = "Toutes les compagnies actives ont une facture enregistrée ce mois-ci."
        badge_class = "status-success"
    elif compagnies_sans_facture == 1:
        reminder_factures = "1 compagnie active n'a aucune facture enregistrée ce mois-ci."
        badge_class = "status-warning"
    else:
        reminder_factures = f"{compagnies_sans_facture} compagnies actives n'ont aucune facture enregistrée ce mois-ci."
        badge_class = "status-warning"

    nb_releves_attendus = CompteReleve.objects.filter(actif=True).count()
    nb_releves_importes = (
        Releve.objects.filter(
            date__month=mois,
            date__year=annee,
            compte_releve__isnull=False,
            compte_releve__actif=True,
        ).values("compte_releve").distinct().count()
    )
    releves_manquants = nb_releves_attendus - nb_releves_importes

    if releves_manquants == 0:
        reminder_releves = "Tous les relevés attendus ont été importés."
        badge_releves = "status-success"
    elif releves_manquants == 1:
        reminder_releves = "1 relevé est toujours attendu."
        badge_releves = "status-warning"
    else:
        reminder_releves = f"{releves_manquants} relevés sont toujours attendus."
        badge_releves = "status-warning"

    return {
        "nb_compagnies": nb_compagnies,
        "nb_factures": nb_factures,
        "compagnies_sans_facture": compagnies_sans_facture,
        "reminder_factures": reminder_factures,
        "badge_class": badge_class,
        "mois": mois,
        "annee": annee,
        "nom_mois": nom_mois,
        "mois_liste": mois_liste,
        "mois_selectionne": mois,
        "annee_selectionnee": annee,
        "annees": annees,
        "nb_releves_attendus": nb_releves_attendus,
        "nb_releves_importes": nb_releves_importes,
        "releves_manquants": releves_manquants,
        "reminder_releves": reminder_releves,
        "badge_releves": badge_releves,
        "nb_employes": 0,
        "nb_paies": 0,
        "badge_paie": "badge-neutral",
        "reminder_paie": "Le module Paie sera disponible prochainement.",
    }