from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal, ROUND_HALF_UP
from paie.models import FrequencePaie

def validate_4_digits(value):
    if not (1000 <= value <= 9999):
        raise ValidationError("Le numéro doit être compris entre 1000 et 9999.")


def validate_total_number(value):
    if value == 0:
        return
    if not (1000 <= value <= 9999):
        raise ValidationError("Le total doit etre 0 ou compris entre 1000 et 9999.")

class Total(models.Model):
    no_total = models.IntegerField(
        primary_key=True,
        validators=[
            MinValueValidator(0), 
            MaxValueValidator(9999),
            validate_total_number
        ]
    )
    desc = models.CharField(max_length=120, blank=False, null=False)

    class Meta:
        verbose_name = "Total"
        verbose_name_plural = "Totaux"

    def __str__(self):
        # Retourne une chaîne de caractères, nécessaire pour les clés étrangères et l'admin Django
        return f"{self.no_total} - {self.desc}"


class Compte(models.Model):
    numero = models.IntegerField(
        primary_key=True,
        validators=[
            MinValueValidator(1000), 
            MaxValueValidator(9999),
            validate_4_digits
        ]
    )
    libelle = models.CharField(max_length=120, blank=False, null=False)
    no_total = models.ForeignKey(
        Total,
        on_delete=models.CASCADE,    # Supprime ce compte si le Total associé est supprimé
        db_column='no_total',        # Force le nom exact de la colonne dans la base de données
        verbose_name="Total"         # Nom affiché dans l'interface d'administration
    )

    class Meta:
        verbose_name = "Compte"
        verbose_name_plural = "Comptes"

    def __str__(self):
        # Retourne une chaîne de caractères au lieu d'un entier direct
        return f"{self.numero} - {self.libelle}"


class SoldeAuxLivres(models.Model):
    compte = models.OneToOneField(
        Compte,
        on_delete=models.CASCADE,
        related_name='solde_aux_livres',
    )
    solde_depart = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Solde aux livres"
        verbose_name_plural = "Soldes aux livres"
        ordering = ['compte__numero']

    def __str__(self):
        return f"{self.compte.numero} - {self.solde_depart}"


class BulletinPaie(models.Model):
    # Informations de base
    employe_nom = models.CharField(max_length=100)
    date_paie = models.DateField(auto_now=True)
    
    # Entrées pour le calcul du salaire
    heures_travaillees = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('40.00'))
    taux_horaire = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('40.00'))
    periodes_par_annee = models.IntegerField(default=26, help_text="Ex: 26 pour aux deux semaines, 12 pour mensuel")

    # Crédits personnels issus des formulaires TD1 et TP-1015.3
    # S'ils restent à 0, le code appliquera automatiquement les montants de base légaux de l'année
    montant_personnel_federal_td1 = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    montant_personnel_quebec_tp1015 = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    # Historique cumulé de l'employé DEPUIS LE DÉBUT DE L'ANNÉE (excluant cette paie)
    # Requis pour bloquer les cotisations lorsque le plafond annuel est atteint
    cumul_brut_annee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    cumul_rrq_annee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    cumul_rqap_annee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    cumul_ae_annee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    # --- Sorties calculées automatiquement ---
    salaire_brut_periode = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    rqap = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    rrq = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    ae = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    impot_federal = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    impot_provincial = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    salaire_net = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    def _calculer_impot_tranches(self, revenu_annuel, tranches):
        """Calcule l'impôt brut selon une grille de tranches progressives."""
        impot_total = Decimal('0.00')
        limite_precedente = Decimal('0.00')

        for limite_max, taux in tranches:
            if revenu_annuel > limite_precedente:
                if limite_max is None or revenu_annuel <= limite_max:
                    portion_imposable = revenu_annuel - limite_precedente
                else:
                    portion_imposable = limite_max - limite_precedente

                impot_total += portion_imposable * taux
                limite_precedente = limite_max if limite_max is not None else limite_precedente
            else:
                break
        return impot_total

    def _arrondi_monnaie(self, valeur):
        return Decimal(valeur).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def save(self, *args, **kwargs):
        # 1. CALCUL DU BRUT DE LA PÉRIODE
        self.salaire_brut_periode = self._arrondi_monnaie(self.heures_travaillees * self.taux_horaire)
        salaire_annuel_estime = self.salaire_brut_periode * self.periodes_par_annee

        # 2. CONSTANTES FISCALES (Taux et Maximums Annuels)
        # Cotisations sociales
        taux_rqap = Decimal('0.00430')
        max_annuel_rqap = Decimal('424.31')  # Plafond maximum de cotisation annuelle

        taux_rrq = Decimal('0.0630')
        max_annuel_rrq = Decimal('4348.00')   # Valeur type de plafond annuel de base

        taux_ae = Decimal('0.0130')
        max_annuel_ae = Decimal('878.22')

        # 3. CALCUL DU RQAP, RRQ ET ASSURANCE-EMPLOI (Avec blocage au plafond)
        # RQAP
        rqap_theorique = self.salaire_brut_periode * taux_rqap
        self.rqap = self._arrondi_monnaie(min(rqap_theorique, max_annuel_rqap - self.cumul_rqap_annee))
        self.rqap = max(Decimal('0.00'), self.rqap)

        # RRQ (Prend en compte l'exemption de base sur la paie, ex: 3500$ / periodes)
        exemption_periode = Decimal('3500.00') / self.periodes_par_annee
        salaire_admissible_rrq = max(Decimal('0.00'), self.salaire_brut_periode - exemption_periode)
        rrq_theorique = salaire_admissible_rrq * taux_rrq
        self.rrq = self._arrondi_monnaie(min(rrq_theorique, max_annuel_rrq - self.cumul_rrq_annee))
        self.rrq = max(Decimal('0.00'), self.rrq)

        # Assurance-Emploi
        ae_theorique = self.salaire_brut_periode * taux_ae
        self.ae = self._arrondi_monnaie(min(ae_theorique, max_annuel_ae - self.cumul_ae_annee))
        self.ae = max(Decimal('0.00'), self.ae)

        # 4. GRILLES DES TRANCHES PROGRESSIVES D'IMPÔT
        tranches_federales = [
            (Decimal('58523'), Decimal('0.14')),
            (Decimal('117045'), Decimal('0.205')),
            (Decimal('181440'), Decimal('0.26')),
            (Decimal('258482'), Decimal('0.29')),
            (None, Decimal('0.33'))
        ]

        # 5. CALCUL DE L'IMPÔT BRUT ANNUEL
        impot_fed_annuel_brut = self._calculer_impot_tranches(salaire_annuel_estime, tranches_federales)

        # 6. GESTION DYNAMIQUE DES CRÉDITS PERSONNELS
        # Si aucun montant personnalisé n'est entré, on utilise le montant de base par défaut
        credit_base_fed = self.montant_personnel_federal_td1 if self.montant_personnel_federal_td1 > 0 else Decimal('16452')
        credit_base_qc = self.montant_personnel_quebec_tp1015 if self.montant_personnel_quebec_tp1015 > 0 else Decimal('18952')

        # Conversion des crédits en valeur de réduction d'impôt (Taux du premier palier : 14%)
        valeur_credit_fed = credit_base_fed * Decimal('0.14')
        valeur_credit_qc = credit_base_qc * Decimal('0.14')

        # Impôt annuel après crédits
        impot_fed_annuel_net = max(Decimal('0.00'), impot_fed_annuel_brut - valeur_credit_fed)

        # Québec: logique alignée à feuille_de_travail.html
        # I = P * (G - F - H - CSA) - J - J1 (tronqué à 0)
        # T selon tranches, Y = (T * I) - (0.14 * E_prov), A = (Y / P) + L (tronqué à 0)
        p = Decimal(self.periodes_par_annee or 0)
        g = self.salaire_brut_periode

        # Champs non présents dans BulletinPaie: par défaut à 0.00
        f = Decimal('0.00')
        csa = Decimal('0.00')
        j = Decimal('0.00')
        j1 = Decimal('0.00')
        l = Decimal('0.00')

        h_max = (Decimal('1450.00') / p) if p > 0 else Decimal('0.00')
        h = min(Decimal('0.06') * g, h_max)

        i_revenu_imposable = max(Decimal('0.00'), (p * (g - f - h - csa)) - j - j1)

        if i_revenu_imposable <= Decimal('54345.00'):
            t = Decimal('0.14')
        elif i_revenu_imposable <= Decimal('108680.00'):
            t = Decimal('0.19')
        elif i_revenu_imposable <= Decimal('132245.00'):
            t = Decimal('0.24')
        else:
            t = Decimal('0.2575')

        e_prov = credit_base_qc
        y_impot_annuel_qc = max(Decimal('0.00'), (t * i_revenu_imposable) - (Decimal('0.14') * e_prov))

        # 7. AJUSTEMENT À LA PÉRIODE DE PAIE
        self.impot_federal = self._arrondi_monnaie(impot_fed_annuel_net / self.periodes_par_annee)

        a_impot_periode_qc = max(Decimal('0.00'), ((y_impot_annuel_qc / p) if p > 0 else Decimal('0.00')) + l)
        self.impot_provincial = self._arrondi_monnaie(a_impot_periode_qc)

        # 8. CALCUL DU SALAIRE NET FINAL
        total_retenues = self.rqap + self.rrq + self.ae + self.impot_federal + self.impot_provincial
        self.salaire_net = self._arrondi_monnaie(self.salaire_brut_periode - total_retenues)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"Paie de {self.employe_nom} du {self.date_paie} (Net: {self.salaire_net} $)"


class Setting(models.Model):
    TAX_MODE_RECLAMER = 'RECLAMER'
    TAX_MODE_PAYER = 'PAYER'
    TAX_MODE_CHOICES = [
        (TAX_MODE_RECLAMER, 'Les taxes sont generalement a reclamer'),
        (TAX_MODE_PAYER, 'Les taxes sont generalement a payer'),
    ]

    nom = models.CharField(max_length=60, blank=False, null=False)
    logo = models.CharField(
        max_length=100,
        blank=False,
        null=False,
        default='images.png',
        help_text="Nom du fichier logo dans static/images/logos (ex: images.png)."
    )
    adresse = models.CharField(max_length=60, blank=False, null=False)
    ville = models.CharField(max_length=255, blank=False, null=False)
    code_postal = models.CharField(max_length=60, blank=False, null=False)
    pays = models.CharField(max_length=255, blank=False, null=False)
    phone = models.CharField(max_length=100, blank=False, null=False)
    email = models.EmailField(blank=False, null=False)
    fin_annee_jour = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        verbose_name="Jour de fin d'exercice",
    )
    fin_annee_mois = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        verbose_name="Mois de fin d'exercice",
    )

    # Ajout de related_name uniques pour éviter le conflit
    car = models.ForeignKey(Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name='settings_car')
    cap = models.ForeignKey(Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name='settings_cap')
    
    compte_tps_percue = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL,
        null=True, 
        blank=True, 
        related_name='settings_tps_percue'
    )

    compte_tps_payee = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL,
        null=True, 
        blank=True, 
        related_name='settings_tps_payee'
    )
    compte_tvq_percue = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL,
        null=True, 
        blank=True, 
        related_name='settings_tvq_percue'
    )
    compte_tvq_payee = models.ForeignKey(
        Compte, 
        on_delete=models.SET_NULL,
        null=True, 
        blank=True, 
        related_name='settings_tvq_payee'
    )
    compte_fr_retard = models.ForeignKey(Compte, on_delete=models.SET_NULL, null=True, blank=True, related_name='settings_fr_retard')
    taxes_mode = models.CharField(
        max_length=16,
        choices=TAX_MODE_CHOICES,
        default=TAX_MODE_RECLAMER,
    )
    frequence_paie = models.ForeignKey(
        FrequencePaie,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='settings',
        verbose_name="Fréquence de paie"
    )

    def __str__(self):
        return self.nom
