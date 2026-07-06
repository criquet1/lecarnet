from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")
# Minimums de credits personnels valides pour l'annee fiscale 2026.
MIN_CREDIT_PERSONNEL_FEDERAL_2026 = Decimal("16452")
MIN_CREDIT_PERSONNEL_QUEBEC_2026 = Decimal("18952")

# Alias conserves pour compatibilite locale du module.
MIN_CREDIT_PERSONNEL_FEDERAL = MIN_CREDIT_PERSONNEL_FEDERAL_2026
MIN_CREDIT_PERSONNEL_QUEBEC = MIN_CREDIT_PERSONNEL_QUEBEC_2026


def arrondir_monnaie(valeur: Decimal) -> Decimal:
    return Decimal(valeur).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _positive_or_zero(valeur: Decimal) -> Decimal:
    return max(Decimal("0.00"), Decimal(valeur))


@dataclass(frozen=True)
class DASInputs:
    salaire_brut_periode: Decimal
    periodes_par_annee: int
    montant_personnel_federal_td1: Decimal = Decimal("0.00")
    montant_personnel_quebec_tp1015: Decimal = Decimal("0.00")
    cumul_rrq_annee: Decimal = Decimal("0.00")
    cumul_rqap_annee: Decimal = Decimal("0.00")
    cumul_ae_annee: Decimal = Decimal("0.00")
    deduction_code_f: Decimal = Decimal("0.00")
    deduction_tp1015_j: Decimal = Decimal("0.00")
    deduction_tp1016_j1: Decimal = Decimal("0.00")
    retenue_supplementaire_qc: Decimal = Decimal("0.00")
    cotisation_supplementaire_rrq_csa: Decimal = Decimal("0.00")


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


def calculer_rqap(salaire_brut_periode: Decimal, cumul_rqap_annee: Decimal) -> Decimal:
    taux_rqap = Decimal("0.00430")
    max_annuel_rqap = Decimal("424.31")
    rqap_theorique = Decimal(salaire_brut_periode) * taux_rqap
    rqap = min(rqap_theorique, max_annuel_rqap - Decimal(cumul_rqap_annee))
    return arrondir_monnaie(_positive_or_zero(rqap))


def calculer_rrq(
    salaire_brut_periode: Decimal,
    periodes_par_annee: int,
    cumul_rrq_annee: Decimal,
) -> Decimal:
    if periodes_par_annee <= 0:
        raise ValueError("periodes_par_annee doit etre superieur a zero")

    taux_rrq = Decimal("0.0630")
    max_annuel_rrq = Decimal("4348.00")
    exemption_periode = Decimal("3500.00") / Decimal(periodes_par_annee)
    salaire_admissible_rrq = _positive_or_zero(Decimal(salaire_brut_periode) - exemption_periode)
    rrq_theorique = salaire_admissible_rrq * taux_rrq
    rrq = min(rrq_theorique, max_annuel_rrq - Decimal(cumul_rrq_annee))
    return arrondir_monnaie(_positive_or_zero(rrq))


def calculer_ae(salaire_brut_periode: Decimal, cumul_ae_annee: Decimal) -> Decimal:
    taux_ae = Decimal("0.0130")
    max_annuel_ae = Decimal("878.22")
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


def calculer_impot_federal(revenu_annuel_estime: Decimal, periodes_par_annee: int, montant_personnel_federal_td1: Decimal) -> Decimal:
    if periodes_par_annee <= 0:
        raise ValueError("periodes_par_annee doit etre superieur a zero")

    tranches_federales = [
        (Decimal("58523"), Decimal("0.14")),
        (Decimal("117045"), Decimal("0.205")),
        (Decimal("181440"), Decimal("0.26")),
        (Decimal("258482"), Decimal("0.29")),
        (None, Decimal("0.33")),
    ]
    credit_base_fed = (
        Decimal(montant_personnel_federal_td1)
        if Decimal(montant_personnel_federal_td1) > 0
        else MIN_CREDIT_PERSONNEL_FEDERAL
    )
    valeur_credit_fed = credit_base_fed * Decimal("0.14")
    impot_fed_annuel_brut = calculer_impot_tranches(Decimal(revenu_annuel_estime), tranches_federales)
    impot_fed_annuel_net = _positive_or_zero(impot_fed_annuel_brut - valeur_credit_fed)
    return arrondir_monnaie(impot_fed_annuel_net / Decimal(periodes_par_annee))


def calculer_impot_provincial(
    salaire_brut_periode: Decimal,
    periodes_par_annee: int,
    montant_personnel_quebec_tp1015: Decimal,
    deduction_code_f: Decimal = Decimal("0.00"),
    deduction_tp1015_j: Decimal = Decimal("0.00"),
    deduction_tp1016_j1: Decimal = Decimal("0.00"),
    retenue_supplementaire_qc: Decimal = Decimal("0.00"),
    cotisation_supplementaire_rrq_csa: Decimal = Decimal("0.00"),
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

    h_max = Decimal("1450.00") / p
    h = min(Decimal("0.06") * g, h_max)
    i_revenu_imposable = _positive_or_zero((p * (g - f - h - csa)) - j - j1)

    if i_revenu_imposable <= Decimal("54345.00"):
        t = Decimal("0.14")
    elif i_revenu_imposable <= Decimal("108680.00"):
        t = Decimal("0.19")
    elif i_revenu_imposable <= Decimal("132245.00"):
        t = Decimal("0.24")
    else:
        t = Decimal("0.2575")

    credit_base_qc = (
        Decimal(montant_personnel_quebec_tp1015)
        if Decimal(montant_personnel_quebec_tp1015) > 0
        else MIN_CREDIT_PERSONNEL_QUEBEC
    )
    y_impot_annuel_qc = _positive_or_zero((t * i_revenu_imposable) - (Decimal("0.14") * credit_base_qc))
    return arrondir_monnaie(_positive_or_zero((y_impot_annuel_qc / p) + l))


def calculer_das(inputs: DASInputs) -> DASResult:
    if inputs.periodes_par_annee <= 0:
        raise ValueError("periodes_par_annee doit etre superieur a zero")

    salaire_brut_periode = arrondir_monnaie(inputs.salaire_brut_periode)
    revenu_annuel_estime = arrondir_monnaie(salaire_brut_periode * Decimal(inputs.periodes_par_annee))

    rqap = calculer_rqap(salaire_brut_periode, inputs.cumul_rqap_annee)
    rrq = calculer_rrq(salaire_brut_periode, inputs.periodes_par_annee, inputs.cumul_rrq_annee)
    ae = calculer_ae(salaire_brut_periode, inputs.cumul_ae_annee)
    impot_federal = calculer_impot_federal(
        revenu_annuel_estime,
        inputs.periodes_par_annee,
        inputs.montant_personnel_federal_td1,
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