"""Inclut les ecritures 'Facture (photo)' dans la vue `factures`.

Cette vue sert a determiner quelles ecritures comptent comme des factures
(affichees dans l'historique fournisseur/client sur la page Facturation).
Elle ne gardait avant que la source exactement 'Facture', ce qui excluait
par erreur les factures creees via la capture par photo (source
'Facture (photo)') alors qu'elles sont comptablement identiques.
"""

from django.db import migrations

SQL_NOUVELLE_VUE = """
CREATE OR REPLACE VIEW public.factures AS
SELECT
    t.transaction_id,
    t.no_ej,
    t.date,
    t.compagnie,
    t.description,
    t.source,
    t.compte_numero,
    t.compte_libelle,
    t.rapport_taxes_id,
    t.debit,
    t.credit
FROM transactions_liste t
WHERE t.source ILIKE 'facture' OR t.source ILIKE 'facture (photo)'
ORDER BY t.date, t.no_ej, t.compte_numero;
"""

SQL_ANCIENNE_VUE = """
CREATE OR REPLACE VIEW public.factures AS
SELECT
    t.transaction_id,
    t.no_ej,
    t.date,
    t.compagnie,
    t.description,
    t.source,
    t.compte_numero,
    t.compte_libelle,
    t.rapport_taxes_id,
    t.debit,
    t.credit
FROM transactions_liste t
WHERE t.source ILIKE 'facture'
ORDER BY t.date, t.no_ej, t.compte_numero;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('facture', '0035_petitecaissephotoenattente'),
    ]

    operations = [
        migrations.RunSQL(
            sql=SQL_NOUVELLE_VUE,
            reverse_sql=SQL_ANCIENNE_VUE,
        ),
    ]
