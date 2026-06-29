from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command

from compte.models import Compte, Total
from facture.models import Compagnie, Source, Tr_desc, Tr_detail


class Command(BaseCommand):
    help = "Charge un jeu de donnees volumineux pour tester les performances de Tr_detail."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="client_test", help="Alias de base cible.")
        parser.add_argument("--entries", type=int, default=25000, help="Nombre de Tr_desc a creer.")
        parser.add_argument("--details-per-entry", type=int, default=8, help="Nombre de Tr_detail par Tr_desc.")
        parser.add_argument("--batch-size", type=int, default=2000, help="Taille des lots bulk_create.")
        parser.add_argument(
            "--cleanup-only",
            action="store_true",
            help="Supprime uniquement le dataset perf existant, sans en recreer.",
        )

    def _ensure_accounts(self, alias):
        comptes = list(Compte.objects.using(alias).order_by("numero")[:20])
        if len(comptes) >= 10:
            return comptes

        total = Total.objects.using(alias).filter(no_total=0).first()
        if not total:
            total = Total(no_total=0, desc="Total perf")
            total.save(using=alias)

        existing = {c.numero for c in Compte.objects.using(alias).all()}
        to_create = []
        for numero in range(8900, 8920):
            if numero in existing:
                continue
            to_create.append(Compte(numero=numero, libelle=f"Compte perf {numero}", no_total=total))

        if to_create:
            Compte.objects.using(alias).bulk_create(to_create, batch_size=100)

        return list(Compte.objects.using(alias).order_by("numero")[:20])

    def _ensure_companies(self, alias):
        cap, _ = Compagnie.objects.using(alias).get_or_create(
            nom="PERF CAP",
            defaults={"logo": "images.png", "cap_ou_car": Compagnie.MODE_CAP},
        )
        car, _ = Compagnie.objects.using(alias).get_or_create(
            nom="PERF CAR",
            defaults={"logo": "images.png", "cap_ou_car": Compagnie.MODE_CAR},
        )
        autre, _ = Compagnie.objects.using(alias).get_or_create(
            nom="PERF AUTRE",
            defaults={"logo": "images.png", "cap_ou_car": Compagnie.MODE_AUTRE},
        )

        # Garantit les modes memes si les lignes existaient deja avec autre valeur.
        if cap.cap_ou_car != Compagnie.MODE_CAP:
            cap.cap_ou_car = Compagnie.MODE_CAP
            cap.save(using=alias, update_fields=["cap_ou_car"])
        if car.cap_ou_car != Compagnie.MODE_CAR:
            car.cap_ou_car = Compagnie.MODE_CAR
            car.save(using=alias, update_fields=["cap_ou_car"])
        if autre.cap_ou_car != Compagnie.MODE_AUTRE:
            autre.cap_ou_car = Compagnie.MODE_AUTRE
            autre.save(using=alias, update_fields=["cap_ou_car"])

        return [cap, car, autre]

    def handle(self, *args, **options):
        alias = options["database"]
        entries = max(1, options["entries"])
        details_per_entry = max(2, options["details_per_entry"])
        batch_size = max(100, options["batch_size"])
        cleanup_only = options["cleanup_only"]

        if alias not in settings.DATABASES:
            raise CommandError(f"Alias inconnu: {alias}")

        source_name = f"PERF_LOAD_{alias}"
        source, _ = Source.objects.using(alias).get_or_create(nom=source_name[:15])

        existing_qs = Tr_desc.objects.using(alias).filter(source_id=source.id)
        existing_count = existing_qs.count()
        if existing_count:
            existing_qs.delete()
            self.stdout.write(self.style.WARNING(f"Dataset perf precedent supprime: {existing_count} Tr_desc"))

        if cleanup_only:
            self.stdout.write(self.style.SUCCESS("Nettoyage termine (mode cleanup-only)."))
            return

        comptes = self._ensure_accounts(alias)
        if not comptes:
            raise CommandError("Impossible de preparer les comptes pour le dataset perf.")

        compagnies = self._ensure_companies(alias)
        today = date.today()

        tr_desc_buffer = []
        for i in range(entries):
            compagnie = compagnies[i % len(compagnies)]
            tr_desc_buffer.append(
                Tr_desc(
                    no_ej=f"P{i+1:09d}",
                    compagnie=compagnie,
                    date=today - timedelta(days=(i % 365)),
                    description=f"PERF charge {i+1}",
                    source=source,
                )
            )

        Tr_desc.objects.using(alias).bulk_create(tr_desc_buffer, batch_size=batch_size)
        tr_desc_ids = list(
            Tr_desc.objects.using(alias)
            .filter(source_id=source.id)
            .order_by("id")
            .values_list("id", flat=True)
        )

        compte_ids = [c.numero for c in comptes]
        total_details = 0
        detail_buffer = []

        for idx, tr_desc_id in enumerate(tr_desc_ids):
            base = Decimal((idx % 97) + 10)
            for j in range(details_per_entry):
                compte_id = compte_ids[(idx + j) % len(compte_ids)]
                if j == 0:
                    montant = base
                elif j == 1:
                    montant = -base
                else:
                    sign = Decimal("1") if (j % 2 == 0) else Decimal("-1")
                    montant = sign * Decimal((j % 7) + 1)

                detail_buffer.append(
                    Tr_detail(
                        tr_desc_id=tr_desc_id,
                        compte_id=compte_id,
                        montant=montant,
                    )
                )

                if len(detail_buffer) >= batch_size:
                    Tr_detail.objects.using(alias).bulk_create(detail_buffer, batch_size=batch_size)
                    total_details += len(detail_buffer)
                    detail_buffer = []

        if detail_buffer:
            Tr_detail.objects.using(alias).bulk_create(detail_buffer, batch_size=batch_size)
            total_details += len(detail_buffer)

        self.stdout.write(
            self.style.SUCCESS(
                f"Dataset perf cree: {len(tr_desc_ids)} Tr_desc / {total_details} Tr_detail sur {alias}."
            )
        )

        self.stdout.write(self.style.NOTICE("Benchmark post-charge:"))
        call_command("benchmark_ledger_views", database=alias)
