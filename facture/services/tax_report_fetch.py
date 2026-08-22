from django.db.models import Prefetch
from facture.models import RapportTaxes, Tr_detail


def fetch_report_with_details(year, month, tax_account_ids):
    return RapportTaxes.objects.prefetch_related(
        Prefetch(
            'details_taxes',
            queryset=Tr_detail.objects.select_related(
                'tr_desc__compagnie',
                'compte',
                'rapport_taxes',
            ).filter(compte_id__in=tax_account_ids)
        )
    ).filter(annee=year, mois=month).first()
