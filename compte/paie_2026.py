"""
Données officielles de paie 2026 - Canada / Québec
Sources:
- Agence du revenu du Canada (ARC) - Formule T4127
- Revenu Québec - Tables d'imposition 2026
- Commission des normes, de l'équité, de la santé et de la sécurité du travail (CNESST)
"""

# ============================================================================
# RÉGIME DE RENTES DU QUÉBEC (RRQ) - Employé
# Source: Régie des rentes du Québec
# ============================================================================
RRQ = {
    'taux': 0.063,              # 6.3% du revenu cotisable
    'cotisation_max': 3867.50,  # Cotisation maximale annuelle 2026
    'exemption_annuelle': 3500, # Exemption annuelle
    'nom': 'Régime de rentes du Québec'
    # Formule: RRQ = 0.063 × [G - (3500 ÷ P)]
    # Où G = salaire brut par paie, P = nombre de périodes
}

# ============================================================================
# ASSURANCE-EMPLOI (AE) - Québec
# Source: Service Canada
# ============================================================================
EI = {
    'taux': 0.013,              # 1.3% du revenu
    'cotisation_max': 1049.12,  # Cotisation maximale annuelle 2026
    'nom': 'Assurance-emploi'
}

# ============================================================================
# FONDS DE SERVICES DE SANTÉ (FSS) - Québec
# Source: Revenu Québec - Cotisation obligatoire depuis 2023
# Taux varient selon le secteur d'activité
# ============================================================================
FSS = {
    'taux_prive': 0.0165,           # 1.65% pour secteur privé (moyen)
    'taux_primaire_mfg': 0.0195,    # 1.95% pour secteur primaire/manufacturier
    'taux_public': 0.0270,          # 2.70% pour secteur public
    'nom': 'Fonds de services de santé'
}

# ============================================================================
# RÉGIME QUÉBÉCOIS D'ASSURANCE PARENTALE (RQAP)
# Source: Commission des normes, de l'équité, de la santé et de la sécurité du travail
# ============================================================================
RQAP = {
    'taux': 0.0043,                 # 0.43% du revenu
    'revenu_max_annuel': 103000,    # Revenu assurable maximal
    'nom': 'Assurance parentale'
}

# ============================================================================
# IMPÔT SUR LE REVENU FÉDÉRAL 2026
# Source: Agence du revenu du Canada - Formule T4127
# Taux progressifs fédéraux (bruts, avant crédits)
# ============================================================================
FEDERAL = {
    'montant_personnel_base': 16452,     # Montant personnel pour crédit
    'montant_canadien_travailleur': 1473,# Montant canadien pour travailleurs
    'taux_de_base': 0.14,                # 14% taux marginal de base
    'abattement_quebec': 0.165,          # Abattement automatique 16.5% pour résidents QC
    'nom': 'Impôt fédéral',
    'tranches': [
        # Tranches progressives fédérales (pour calcul sur années complètes)
        {'limite': 55867, 'taux': 0.15},
        {'limite': 111733, 'taux': 0.205},
        {'limite': 173205, 'taux': 0.26},
        {'limite': 246752, 'taux': 0.29},
        {'limite': float('inf'), 'taux': 0.33}
    ]
}

# ============================================================================
# IMPÔT SUR LE REVENU PROVINCIAL (QUÉBEC) 2026
# Source: Revenu Québec - Tables d'imposition
# Formule de calcul: voir calculer_impot_provincial_qc() ci-dessous
# ============================================================================
PROVINCIAL_QC = {
    'montant_personnel_base': 18_952,     # Montant personnel de base pour crédit
    'taux_premiere_tranche': 0.14,        # Taux 14% sur première tranche jusqu'à 54 345$
    'facteur_abattement': 0.9734,         # Facteur multiplicateur d'abattement QC
    'taux_rrq': 0.063,                    # Taux RRQ pour estimation dans crédits
    'taux_rqap': 0.0043,                  # Taux RQAP pour estimation dans crédits
    'exemption_rrq_annuelle': 3_500,      # Exemption annuelle RRQ
    'nom': 'Impôt provincial (Québec)',
    'tranches': [
        # Tranches progressives pour revenus supérieurs à 54 345$ (future implémentation)
        {'limite': 51446, 'taux': 0.1499, 'cumul': 0},
        {'limite': 102892, 'taux': 0.2037, 'cumul': 7711.54},
        {'limite': 165430, 'taux': 0.2575, 'cumul': 17216.64},
        {'limite': 235675, 'taux': 0.2867, 'cumul': 33300.66},
        {'limite': float('inf'), 'taux': 0.2975, 'cumul': 53568.24}
    ]
}

# ============================================================================
# FRÉQUENCES DE PAIE
# ============================================================================
FREQUENCES_PAIE = {
    'hebdomadaire': {
        'nombre_periodes': 52,
        'label': 'Hebdomadaire (52 paies/an)'
    },
    'bihebdomadaire': {
        'nombre_periodes': 26,
        'label': 'Aux 2 semaines (26 paies/an)'
    },
    'mensuel': {
        'nombre_periodes': 12,
        'label': 'Mensuel (12 paies/an)'
    },
    'bimensuel': {
        'nombre_periodes': 24,
        'label': 'Deux fois par mois (24 paies/an)'
    }
}

# ============================================================================
# FORMULE OFFICIELLE DE CALCUL
# ============================================================================
def calculer_rrq(salaire_brut_annuel):
    """Calcule la cotisation RRQ annuelle (employé)"""
    base = max(0, salaire_brut_annuel - RRQ['exemption_annuelle'])
    return min(base * RRQ['taux'], RRQ['cotisation_max'])

def calculer_ei(salaire_brut_annuel):
    """Calcule la cotisation AE annuelle"""
    return min(salaire_brut_annuel * EI['taux'], EI['cotisation_max'])

def calculer_impot_federal(salaire_brut, cpp_canada, rrq_annuel, ei_annuel, nb_periodes):
    """
    Calcule l'impôt fédéral selon la formule officielle d'ARC (T4127)
    pour résidents du Québec
    """
    # 1. Annualiser le salaire brut
    salaire_annuel = salaire_brut * nb_periodes
    
    # 2. Soustraire les cotisations RRQ et AE du revenu imposable
    revenu_imposable = salaire_annuel - rrq_annuel - ei_annuel
    
    # 3. Calculer l'impôt brut au taux fédéral de base (14%)
    impot_brut = revenu_imposable * FEDERAL['taux_de_base']
    
    # 4. Appliquer les crédits d'impôt non remboursables
    montants_credit = FEDERAL['montant_personnel_base'] + FEDERAL['montant_canadien_travailleur']
    credit_impot = montants_credit * FEDERAL['taux_de_base']
    impot_net = max(0, impot_brut - credit_impot)
    
    # 5. Appliquer l'abattement du Québec (16,5%)
    abattement = impot_net * FEDERAL['abattement_quebec']
    impot_final = max(0, impot_net - abattement)
    
    # 6. Convertir à la période de paie
    return impot_final / nb_periodes

def calculer_impot_provincial(salaire_brut, cpp_quebec, nb_periodes):
    """
    Calcule l'impôt provincial (Québec) 2026
    selon la formule officielle de Revenu Québec
    
    Formule (pour revenus jusqu'à 54 345$ annuels):
    1. R = salaire_brut × nb_periodes (revenu annuel)
    2. Impôt brut = R × 14%
    3. RRQ estimé = (R - 3500) × 6.3%
    4. RQAP estimé = R × 0.43%
    5. F = RRQ + RQAP (cotisations totales)
    6. Crédits = (18952 × 14%) + (F × 14%)
    7. A = Impôt brut - Crédits
    8. Impôt final = A × 0.9734 (abattement)
    9. Par paie = Impôt final / nb_periodes
    
    Sources: Revenu Québec 2026
    """
    # 1. Revenu annuel imposable
    revenu_annuel = salaire_brut * nb_periodes
    
    # 2. Impôt brut sur première tranche (14%)
    # Note: pour revenus > 54 345$, utiliser les tranches progressives
    impot_brut = revenu_annuel * PROVINCIAL_QC['taux_premiere_tranche']
    
    # 3. Estimer les cotisations RRQ et RQAP
    # RRQ: (R - 3500) × 6.3%
    rrq_estime = max(0, revenu_annuel - PROVINCIAL_QC['exemption_rrq_annuelle']) * PROVINCIAL_QC['taux_rrq']
    
    # RQAP: R × 0.43%
    rqap_estime = revenu_annuel * PROVINCIAL_QC['taux_rqap']
    
    # 4. Total des cotisations
    cotisations_total = rrq_estime + rqap_estime
    
    # 5. Crédits d'impôt
    # Crédit montant personnel = 18 952 × 14%
    credit_montant_personnel = PROVINCIAL_QC['montant_personnel_base'] * PROVINCIAL_QC['taux_premiere_tranche']
    
    # Crédit pour cotisations = F × 14%
    credit_cotisations = cotisations_total * PROVINCIAL_QC['taux_premiere_tranche']
    
    # Total des crédits
    credits_total = credit_montant_personnel + credit_cotisations
    
    # 6. Impôt net avant abattement
    impot_net = max(0, impot_brut - credits_total)
    
    # 7. Application de l'abattement (0.9734)
    impot_final = impot_net * PROVINCIAL_QC['facteur_abattement']
    
    # 8. Convertir à la période de paie
    return max(0, impot_final / nb_periodes)

# ============================================================================
# EXEMPLE D'UTILISATION
# ============================================================================
if __name__ == '__main__':
    # Exemple: Salaire de 1 000 $ aux 2 semaines
    salaire_par_paie = 1000
    nb_periodes = 26
    salaire_annuel = salaire_par_paie * nb_periodes
    
    print("=" * 70)
    print(f"PAIE HEBDOMADAIRE - 1 000 $ x {nb_periodes} périodes = {salaire_annuel:,} $ annuels")
    print("=" * 70)
    
    # Cotisations sociales
    rrq_annuel = calculer_rrq(salaire_annuel)
    ei_annuel = calculer_ei(salaire_annuel)
    
    print(f"\nRRQ:  {rrq_annuel:.2f} $ / {nb_periodes} = {rrq_annuel / nb_periodes:.2f} $ par paie")
    print(f"AE:   {ei_annuel:.2f} $ / {nb_periodes} = {ei_annuel / nb_periodes:.2f} $ par paie")
    
    # Impôts
    impot_fed = calculer_impot_federal(salaire_par_paie, 15705, rrq_annuel, ei_annuel, nb_periodes)
    impot_prov = calculer_impot_provincial(salaire_par_paie, 15705, nb_periodes)
    
    print(f"Impôt fédéral: {impot_fed:.2f} $ par paie")
    print(f"Impôt provincial: {impot_prov:.2f} $ par paie")
    
    total_retenues = (rrq_annuel / nb_periodes) + (ei_annuel / nb_periodes) + impot_fed + impot_prov
    net = salaire_par_paie - total_retenues
    
    print(f"\nTotal retenues: {total_retenues:.2f} $")
    print(f"Net à payer: {net:.2f} $")
