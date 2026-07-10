from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
MONEY_QUANTUM = Decimal("0.01")


def arrondir_monnaie(valeur: Decimal) -> Decimal:
    return Decimal(valeur).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _positive_or_zero(valeur: Decimal) -> Decimal:
    return max(Decimal("0.00"), Decimal(valeur))


@dataclass(frozen=True)
class DASInputs:
    salaire_brut_periode: Decimal
    periodes_par_annee: int
    taux_rrq_employe: Decimal
    taux_rrq_supplementaire_2_employe: Decimal
    exemption_base_rrq: Decimal
    max_assurable_rrq: Decimal
    max_supplementaire_rrq: Decimal
    taux_rqap_employe: Decimal
    max_assurable_rqap: Decimal
    taux_ae_employe: Decimal
    max_assurable_ae: Decimal
    montant_personnel_federal_td1: Decimal = Decimal("0.00")
    montant_personnel_quebec_tp1015: Decimal = Decimal("0.00")
    cumul_salaire_brut_annee: Decimal = Decimal("0.00")
    cumul_rrq_annee: Decimal = Decimal("0.00")
    cumul_rqap_annee: Decimal = Decimal("0.00")
    cumul_ae_annee: Decimal = Decimal("0.00")
    deduction_code_f: Decimal = Decimal("0.00")
    deduction_tp1015_j: Decimal = Decimal("0.00")
    deduction_tp1016_j1: Decimal = Decimal("0.00")
    retenue_supplementaire_qc: Decimal = Decimal("0.00")
    cotisation_supplementaire_rrq_csa: Decimal = Decimal("0.00")
    credit_personnel_federal_min: Decimal = Decimal("0.00")
    credit_personnel_quebec_min: Decimal = Decimal("0.00")
    taux_credit_federal: Decimal = Decimal("0.00")
    montant_canadien_pour_emploi: Decimal = Decimal("0.00")
    appliquer_abattement_federal_quebec: bool = True
    abattement_federal_quebec: Decimal = Decimal("0.00")
    seuil_federal_1: Decimal = Decimal("0.00")
    seuil_federal_2: Decimal = Decimal("0.00")
    seuil_federal_3: Decimal = Decimal("0.00")
    seuil_federal_4: Decimal = Decimal("0.00")
    taux_federal_1: Decimal = Decimal("0.00")
    taux_federal_2: Decimal = Decimal("0.00")
    taux_federal_3: Decimal = Decimal("0.00")
    taux_federal_4: Decimal = Decimal("0.00")
    taux_federal_5: Decimal = Decimal("0.00")
    deduction_travailleur_qc_max_annuelle: Decimal = Decimal("0.00")
    seuil_qc_1: Decimal = Decimal("0.00")
    seuil_qc_2: Decimal = Decimal("0.00")
    seuil_qc_3: Decimal = Decimal("0.00")
    taux_qc_1: Decimal = Decimal("0.00")
    taux_qc_2: Decimal = Decimal("0.00")
    taux_qc_3: Decimal = Decimal("0.00")
    taux_qc_4: Decimal = Decimal("0.00")
    taux_credit_quebec: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class DASResult:
    salaire_brut_periode: Decimal
    revenu_annuel_estime: Decimal
    rqap: Decimal
    rrq: Decimal
    ae: Decimal
    impot_federal: Decimal
    impot_provincial: Decimal
    total_retenues: Decimal
    salaire_net: Decimal


def calculer_rqap(
    salaire_brut_periode: Decimal,
    cumul_rqap_annee: Decimal,
    taux_rqap: Decimal,
    max_assurable_rqap: Decimal,
) -> Decimal:
    taux_rqap = Decimal(taux_rqap)
    max_assurable_rqap = Decimal(max_assurable_rqap)
    max_annuel_rqap = max_assurable_rqap * taux_rqap
    rqap_theorique = Decimal(salaire_brut_periode) * taux_rqap
    rqap = min(rqap_theorique, max_annuel_rqap - Decimal(cumul_rqap_annee))
    return arrondir_monnaie(_positive_or_zero(rqap))


def calculer_rrq(
    salaire_brut_periode: Decimal,
    periodes_par_annee: int,
    cumul_salaire_brut_annee: Decimal,
    cumul_rrq_annee: Decimal,
    taux_rrq: Decimal,
    taux_rrq_supplementaire_2: Decimal,
    exemption_base_rrq: Decimal,
    max_assurable_rrq: Decimal,
    max_supplementaire_rrq: Decimal,
) -> Decimal:
    if periodes_par_annee <= 0:
        raise ValueError("periodes_par_annee doit etre superieur a zero")

    taux_rrq = Decimal(taux_rrq)
    taux_rrq_supplementaire_2 = Decimal(taux_rrq_supplementaire_2)
    exemption_base_rrq = Decimal(exemption_base_rrq)
    cumul_salaire_brut_annee = Decimal(cumul_salaire_brut_annee)
    salaire_brut_periode = Decimal(salaire_brut_periode)
    max_assurable_rrq = Decimal(max_assurable_rrq)

    max_supplementaire_rrq = Decimal(max_supplementaire_rrq)
    if max_supplementaire_rrq < max_assurable_rrq:
        max_supplementaire_rrq = max_assurable_rrq

    cumul_apres = cumul_salaire_brut_annee + salaire_brut_periode
    exemption_periode = exemption_base_rrq / Decimal(periodes_par_annee)

    # Part des revenus de la periode qui tombe dans la tranche [0, MGA].
    base_avant = min(cumul_salaire_brut_annee, max_assurable_rrq)
    base_apres = min(cumul_apres, max_assurable_rrq)
    revenus_tranche_base = _positive_or_zero(base_apres - base_avant)
    assiette_base = _positive_or_zero(revenus_tranche_base - exemption_periode)
    cot_base = assiette_base * taux_rrq

    # Part des revenus de la periode qui tombe dans la tranche [MGA, max_supplementaire].
    supp2_avant = _positive_or_zero(min(cumul_salaire_brut_annee, max_supplementaire_rrq) - max_assurable_rrq)
    supp2_apres = _positive_or_zero(min(cumul_apres, max_supplementaire_rrq) - max_assurable_rrq)
    revenus_tranche_supp2 = _positive_or_zero(supp2_apres - supp2_avant)
    cot_supp2 = revenus_tranche_supp2 * taux_rrq_supplementaire_2

    rrq = cot_base + cot_supp2
    return arrondir_monnaie(_positive_or_zero(rrq))


def calculer_ae(
    salaire_brut_periode: Decimal,
    cumul_ae_annee: Decimal,
    taux_ae: Decimal,
    max_assurable_ae: Decimal,
) -> Decimal:
    taux_ae = Decimal(taux_ae)
    max_assurable_ae = Decimal(max_assurable_ae)
    max_annuel_ae = max_assurable_ae * taux_ae
    ae_theorique = Decimal(salaire_brut_periode) * taux_ae
    ae = min(ae_theorique, max_annuel_ae - Decimal(cumul_ae_annee))
    return arrondir_monnaie(_positive_or_zero(ae))


def calculer_impot_tranches(revenu_annuel: Decimal, tranches: list[tuple[Decimal | None, Decimal]]) -> Decimal:
    impot_total = Decimal("0.00")
    limite_precedente = Decimal("0.00")

    for limite_max, taux in tranches:
        if revenu_annuel <= limite_precedente:
            break

        if limite_max is None or revenu_annuel <= limite_max:
            portion_imposable = revenu_annuel - limite_precedente
        else:
            portion_imposable = limite_max - limite_precedente

        impot_total += portion_imposable * taux
        if limite_max is not None:
            limite_precedente = limite_max

    return impot_total


def calculer_impot_federal(
    revenu_annuel_estime: Decimal,
    periodes_par_annee: int,
    montant_personnel_federal_td1: Decimal,
    rrq: Decimal = Decimal("0.00"),
    ae: Decimal = Decimal("0.00"),
    rqap: Decimal = Decimal("0.00"),
    appliquer_abattement_federal_quebec: bool = True,
    abattement_federal_quebec: Decimal = Decimal("0.00"),
    taux_credit_federal: Decimal = Decimal("0.00"),
    montant_canadien_pour_emploi: Decimal = Decimal("0.00"),
    credit_personnel_federal_min: Decimal = Decimal("0.00"),
    seuil_federal_1: Decimal = Decimal("0.00"),
    seuil_federal_2: Decimal = Decimal("0.00"),
    seuil_federal_3: Decimal = Decimal("0.00"),
    seuil_federal_4: Decimal = Decimal("0.00"),
    taux_federal_1: Decimal = Decimal("0.00"),
    taux_federal_2: Decimal = Decimal("0.00"),
    taux_federal_3: Decimal = Decimal("0.00"),
    taux_federal_4: Decimal = Decimal("0.00"),
    taux_federal_5: Decimal = Decimal("0.00"),
) -> Decimal:
    if periodes_par_annee <= 0:
        raise ValueError("periodes_par_annee doit etre superieur a zero")

    tranches_federales = [
        (Decimal(seuil_federal_1), Decimal(taux_federal_1)),
        (Decimal(seuil_federal_2), Decimal(taux_federal_2)),
        (Decimal(seuil_federal_3), Decimal(taux_federal_3)),
        (Decimal(seuil_federal_4), Decimal(taux_federal_4)),
        (None, Decimal(taux_federal_5)),
    ]
    credit_base_fed = (
        Decimal(montant_personnel_federal_td1)
        if Decimal(montant_personnel_federal_td1) > 0
        else Decimal(credit_personnel_federal_min)
    )

    # T4127 Etape 3: T3 = impot de base - K1 - K2 - K3 - K4.
    # Cette implementation couvre K1 (credit personnel), K2 (cotisations) et K4 (emploi).
    taux_credit_federal = Decimal(taux_credit_federal)
    valeur_credit_k1 = credit_base_fed * taux_credit_federal
    cotisations_annuelles = _positive_or_zero((Decimal(rrq) + Decimal(ae) + Decimal(rqap)) * Decimal(periodes_par_annee))
    valeur_credit_k2 = cotisations_annuelles * taux_credit_federal
    valeur_credit_k4 = min(
        Decimal(revenu_annuel_estime),
        Decimal(montant_canadien_pour_emploi),
    ) * taux_credit_federal

    impot_fed_annuel_brut = calculer_impot_tranches(Decimal(revenu_annuel_estime), tranches_federales)
    impot_fed_annuel_net = _positive_or_zero(
        impot_fed_annuel_brut - valeur_credit_k1 - valeur_credit_k2 - valeur_credit_k4
    )
    impot_fed_periode = arrondir_monnaie(impot_fed_annuel_net / Decimal(periodes_par_annee))
    if appliquer_abattement_federal_quebec:
        impot_fed_periode = _positive_or_zero(
            impot_fed_periode * (Decimal("1.00") - Decimal(abattement_federal_quebec))
        )
    return arrondir_monnaie(impot_fed_periode)


def calculer_impot_provincial(
    salaire_brut_periode: Decimal,
    periodes_par_annee: int,
    montant_personnel_quebec_tp1015: Decimal,
    deduction_code_f: Decimal = Decimal("0.00"),
    deduction_tp1015_j: Decimal = Decimal("0.00"),
    deduction_tp1016_j1: Decimal = Decimal("0.00"),
    retenue_supplementaire_qc: Decimal = Decimal("0.00"),
    cotisation_supplementaire_rrq_csa: Decimal = Decimal("0.00"),
    credit_personnel_quebec_min: Decimal = Decimal("0.00"),
    deduction_travailleur_qc_max_annuelle: Decimal = Decimal("0.00"),
    seuil_qc_1: Decimal = Decimal("0.00"),
    seuil_qc_2: Decimal = Decimal("0.00"),
    seuil_qc_3: Decimal = Decimal("0.00"),
    taux_qc_1: Decimal = Decimal("0.00"),
    taux_qc_2: Decimal = Decimal("0.00"),
    taux_qc_3: Decimal = Decimal("0.00"),
    taux_qc_4: Decimal = Decimal("0.00"),
    taux_credit_quebec: Decimal = Decimal("0.00"),
) -> Decimal:
    if periodes_par_annee <= 0:
        raise ValueError("periodes_par_annee doit etre superieur a zero")

    p = Decimal(periodes_par_annee)
    g = Decimal(salaire_brut_periode)
    f = Decimal(deduction_code_f)
    j = Decimal(deduction_tp1015_j)
    j1 = Decimal(deduction_tp1016_j1)
    l = Decimal(retenue_supplementaire_qc)
    csa = Decimal(cotisation_supplementaire_rrq_csa)

    h_max = Decimal(deduction_travailleur_qc_max_annuelle) / p
    h = min(Decimal("0.06") * g, h_max)
    i_revenu_imposable = _positive_or_zero((p * (g - f - h - csa)) - j - j1)

    if i_revenu_imposable <= Decimal(seuil_qc_1):
        t = Decimal(taux_qc_1)
    elif i_revenu_imposable <= Decimal(seuil_qc_2):
        t = Decimal(taux_qc_2)
    elif i_revenu_imposable <= Decimal(seuil_qc_3):
        t = Decimal(taux_qc_3)
    else:
        t = Decimal(taux_qc_4)

    credit_base_qc = (
        Decimal(montant_personnel_quebec_tp1015)
        if Decimal(montant_personnel_quebec_tp1015) > 0
        else Decimal(credit_personnel_quebec_min)
    )
    y_impot_annuel_qc = _positive_or_zero((t * i_revenu_imposable) - (Decimal(taux_credit_quebec) * credit_base_qc))
    return arrondir_monnaie(_positive_or_zero((y_impot_annuel_qc / p) + l))


def calculer_das(inputs: DASInputs) -> DASResult:
    if inputs.periodes_par_annee <= 0:
        raise ValueError("periodes_par_annee doit etre superieur a zero")

    salaire_brut_periode = arrondir_monnaie(inputs.salaire_brut_periode)
    revenu_annuel_estime = arrondir_monnaie(salaire_brut_periode * Decimal(inputs.periodes_par_annee))

    rqap = calculer_rqap(
        salaire_brut_periode,
        inputs.cumul_rqap_annee,
        taux_rqap=inputs.taux_rqap_employe,
        max_assurable_rqap=inputs.max_assurable_rqap,
    )
    rrq = calculer_rrq(
        salaire_brut_periode,
        inputs.periodes_par_annee,
        inputs.cumul_salaire_brut_annee,
        inputs.cumul_rrq_annee,
        taux_rrq=inputs.taux_rrq_employe,
        taux_rrq_supplementaire_2=inputs.taux_rrq_supplementaire_2_employe,
        exemption_base_rrq=inputs.exemption_base_rrq,
        max_assurable_rrq=inputs.max_assurable_rrq,
        max_supplementaire_rrq=inputs.max_supplementaire_rrq,
    )
    ae = calculer_ae(
        salaire_brut_periode,
        inputs.cumul_ae_annee,
        taux_ae=inputs.taux_ae_employe,
        max_assurable_ae=inputs.max_assurable_ae,
    )
    impot_federal = calculer_impot_federal(
        revenu_annuel_estime,
        inputs.periodes_par_annee,
        inputs.montant_personnel_federal_td1,
        rrq=rrq,
        ae=ae,
        rqap=rqap,
        appliquer_abattement_federal_quebec=inputs.appliquer_abattement_federal_quebec,
        abattement_federal_quebec=inputs.abattement_federal_quebec,
        taux_credit_federal=inputs.taux_credit_federal,
        montant_canadien_pour_emploi=inputs.montant_canadien_pour_emploi,
        credit_personnel_federal_min=inputs.credit_personnel_federal_min,
        seuil_federal_1=inputs.seuil_federal_1,
        seuil_federal_2=inputs.seuil_federal_2,
        seuil_federal_3=inputs.seuil_federal_3,
        seuil_federal_4=inputs.seuil_federal_4,
        taux_federal_1=inputs.taux_federal_1,
        taux_federal_2=inputs.taux_federal_2,
        taux_federal_3=inputs.taux_federal_3,
        taux_federal_4=inputs.taux_federal_4,
        taux_federal_5=inputs.taux_federal_5,
    )
    impot_provincial = calculer_impot_provincial(
        salaire_brut_periode=salaire_brut_periode,
        periodes_par_annee=inputs.periodes_par_annee,
        montant_personnel_quebec_tp1015=inputs.montant_personnel_quebec_tp1015,
        deduction_code_f=inputs.deduction_code_f,
        deduction_tp1015_j=inputs.deduction_tp1015_j,
        deduction_tp1016_j1=inputs.deduction_tp1016_j1,
        retenue_supplementaire_qc=inputs.retenue_supplementaire_qc,
        cotisation_supplementaire_rrq_csa=inputs.cotisation_supplementaire_rrq_csa,
        credit_personnel_quebec_min=inputs.credit_personnel_quebec_min,
        deduction_travailleur_qc_max_annuelle=inputs.deduction_travailleur_qc_max_annuelle,
        seuil_qc_1=inputs.seuil_qc_1,
        seuil_qc_2=inputs.seuil_qc_2,
        seuil_qc_3=inputs.seuil_qc_3,
        taux_qc_1=inputs.taux_qc_1,
        taux_qc_2=inputs.taux_qc_2,
        taux_qc_3=inputs.taux_qc_3,
        taux_qc_4=inputs.taux_qc_4,
        taux_credit_quebec=inputs.taux_credit_quebec,
    )

    total_retenues = arrondir_monnaie(rqap + rrq + ae + impot_federal + impot_provincial)
    salaire_net = arrondir_monnaie(salaire_brut_periode - total_retenues)

    return DASResult(
        salaire_brut_periode=salaire_brut_periode,
        revenu_annuel_estime=revenu_annuel_estime,
        rqap=rqap,
        rrq=rrq,
        ae=ae,
        impot_federal=impot_federal,
        impot_provincial=impot_provincial,
        total_retenues=total_retenues,
        salaire_net=salaire_net,
    )