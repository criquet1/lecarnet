"""Vues et helpers pour le journal des paies : génération et mise à jour
de l'écriture comptable de salaire, calcul des totaux employeur, et la
page journal_paies_page elle-même. Extrait de paie/views.py pour alléger
ce fichier.
"""

import re
from datetime import date as date_type
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin

from facture.models import Source, Tr_desc, Tr_detail
from facture.utils import expert_required, get_setting, is_expert

from .models import Paie, PeriodePaie
from .forms import PaieForm


def _next_no_ej_paie():
	last_tr_desc = Tr_desc.objects.order_by('-id').first()
	if not last_tr_desc:
		return 'EJ1'

	match = re.match(r'^EJ(\d+)$', last_tr_desc.no_ej or '')
	if not match:
		return 'EJ1'

	return f"EJ{int(match.group(1)) + 1}"


def _money(value):
	return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _compute_employer_totals_for_period(paies, settings_instance):
	totals = {
		'rrq_employeur': Decimal('0.00'),
		'rqap_employeur': Decimal('0.00'),
		'ae_employeur': Decimal('0.00'),
		'fss_employeur': Decimal('0.00'),
		'cnesst_employeur': Decimal('0.00'),
	}

	def _d(value):
		return value if value is not None else Decimal('0.00')

	for paie in paies:
		totals['rrq_employeur'] += _d(paie.rrq_employeur)
		totals['rqap_employeur'] += _d(paie.rqap_employeur)
		totals['ae_employeur'] += _d(paie.ae_employeur)
		totals['fss_employeur'] += _d(paie.fss_employeur)
		totals['cnesst_employeur'] += _d(paie.cnesst_employeur)

	return totals


def _serialize_ecriture_salaire(tr_desc, already_existed, matches=True, updated=False, update_url=None):
	details = (
		Tr_detail.objects
		.filter(tr_desc=tr_desc)
		.select_related('compte')
		.order_by('id')
	)
	# Meme ordre que le journal general: debits par compte croissant, puis credits par compte croissant.
	sorted_details = sorted(
		details,
		key=lambda detail: ((detail.montant or Decimal('0.00')) < 0, detail.compte.numero),
	)
	lines = []
	for detail in sorted_details:
		montant = detail.montant or Decimal('0.00')
		lines.append({
			'compte_numero': detail.compte.numero,
			'compte_libelle': detail.compte.libelle,
			'debit': f"{montant:.2f}" if montant > 0 else '',
			'credit': f"{-montant:.2f}" if montant < 0 else '',
		})
	return {
		'ok': True,
		'already_existed': already_existed,
		'matches': matches,
		'updated': updated,
		'update_url': update_url,
		'no_ej': tr_desc.no_ej,
		'date': tr_desc.date.strftime('%Y-%m-%d'),
		'desc_ctb': tr_desc.desc_ctb,
		'lines': lines,
	}


def _calculer_lignes_ecriture_salaire(periode, settings_instance=None, source_salaire=None):
	"""Calcule (source, date, desc_ctb, lignes) pour l'ecriture de salaire d'une periode.

	Leve ValueError avec un message utilisateur si le calcul est impossible.
	"""
	paies = list(
		Paie.objects
		.filter(periode=periode)
		.select_related('periode')
		.only(
			'id',
			'periode__id',
			'periode__date_fin',
			'periode__date_paie',
			'salaire_brut_periode',
			'salaire_net',
			'vacances_payees',
			'vacances',
			'rrq',
			'rrq_employeur',
			'rqap',
			'rqap_employeur',
			'ae',
			'ae_employeur',
			'cnesst_employeur',
			'fss_employeur',
			'impot_federal',
			'impot_provincial',
		)
	)

	if not paies:
		raise ValueError('Aucune paie trouvee pour cette periode.')

	if settings_instance is None:
		settings_instance = get_setting(
			'compte_salaire',
			'compte_vacances',
			'compte_benefices_marginaux',
			'compte_salaires_a_payer',
			'compte_vacances_a_payer',
			'compte_das_federales',
			'compte_das_provinciales',
			'taux_cnesst_employeur',
			'taux_fss_employeur',
		)

	required_accounts = [
		('Salaires (débit)', getattr(settings_instance, 'compte_salaire', None)),
		('Vacances (débit)', getattr(settings_instance, 'compte_vacances', None)),
		('Bénéfices marginaux (débit)', getattr(settings_instance, 'compte_benefices_marginaux', None)),
		('Salaires à payer (crédit)', getattr(settings_instance, 'compte_salaires_a_payer', None)),
		('Vacances à payer', getattr(settings_instance, 'compte_vacances_a_payer', None)),
		('DAS féd à payer (crédit)', getattr(settings_instance, 'compte_das_federales', None)),
		('DAS prov à payer (crédit)', getattr(settings_instance, 'compte_das_provinciales', None)),
	]

	missing_labels = [label for label, account in required_accounts if account is None]
	if missing_labels:
		raise ValueError('Comptes de paie manquants dans les paramètres: ' + ', '.join(missing_labels))

	if source_salaire is None:
		source_salaire, _ = Source.objects.get_or_create(nom='Salaire')
	entry_date = periode.date_paie or periode.date_fin
	desc_ctb = f"Paie P{periode.id} {periode.date_fin:%Y-%m-%d}"[:40]

	total_brut = _money(sum((p.salaire_brut_periode or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_vacances_payees = _money(sum((p.vacances_payees or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_vacances = _money(sum((p.vacances or Decimal('0.00') for p in paies), Decimal('0.00')))

	# Vacances payees reduisent le debit salaires dans l'ecriture.
	# Vacances (accrues) sont au debit, puis Vacances a payer porte le net restant.
	debit_salaire = _money(total_brut - total_vacances_payees)
	debit_vacances = total_vacances
	# Montant signe: positif = debit, negatif = credit.
	montant_vacances_a_payer = _money(total_vacances_payees - total_vacances)
	credit_salaires_a_payer = _money(sum((p.salaire_net or Decimal('0.00') for p in paies), Decimal('0.00')))

	total_rrq = _money(sum((p.rrq or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_rqap = _money(sum((p.rqap or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_ae = _money(sum((p.ae or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_impot_fed = _money(sum((p.impot_federal or Decimal('0.00') for p in paies), Decimal('0.00')))
	total_impot_prov = _money(sum((p.impot_provincial or Decimal('0.00') for p in paies), Decimal('0.00')))

	employer_totals = _compute_employer_totals_for_period(paies, settings_instance)
	# RAMQ employeur est mappe ici au bloc RQAP employeur utilise dans la paie.
	debit_benefices = _money(
		employer_totals['rrq_employeur']
		+ employer_totals['rqap_employeur']
		+ employer_totals['ae_employeur']
		+ employer_totals['fss_employeur']
		+ employer_totals['cnesst_employeur']
	)

	total_qc = _money(
		total_rrq
		+ employer_totals['rrq_employeur']
		+ total_rqap
		+ employer_totals['rqap_employeur']
		+ total_impot_prov
		+ employer_totals['fss_employeur']
		+ employer_totals['cnesst_employeur']
	)
	total_ca = _money(total_ae + employer_totals['ae_employeur'] + total_impot_fed)

	credit_das_prov = total_qc
	credit_das_fed = total_ca

	detail_rows = [
		(getattr(settings_instance, 'compte_salaire'), debit_salaire),
		(getattr(settings_instance, 'compte_vacances'), debit_vacances),
		(getattr(settings_instance, 'compte_vacances_a_payer'), montant_vacances_a_payer),
		(getattr(settings_instance, 'compte_benefices_marginaux'), debit_benefices),
		(getattr(settings_instance, 'compte_salaires_a_payer'), -credit_salaires_a_payer),
		(getattr(settings_instance, 'compte_das_federales'), -credit_das_fed),
		(getattr(settings_instance, 'compte_das_provinciales'), -credit_das_prov),
	]

	return source_salaire, entry_date, desc_ctb, detail_rows


def _ecriture_salaire_correspond(tr_desc, detail_rows):
	"""Compare les montants deja enregistres avec ceux recalcules pour la periode."""
	attendu = {}
	for compte, montant in detail_rows:
		montant = _money(montant)
		if montant == Decimal('0.00'):
			continue
		attendu[compte.pk] = attendu.get(compte.pk, Decimal('0.00')) + montant

	actuel = {}
	for detail in tr_desc.details.all():
		montant = detail.montant or Decimal('0.00')
		actuel[detail.compte_id] = actuel.get(detail.compte_id, Decimal('0.00')) + montant

	return attendu == actuel


def _statut_transmission_ecriture_salaire(periode, settings_instance=None, source_salaire=None):
	"""Retourne 'vert' (transmise et à jour), 'jaune' (transmise mais montants différents) ou 'blanc' (non transmise)."""
	try:
		source_salaire, entry_date, desc_ctb, detail_rows = _calculer_lignes_ecriture_salaire(
			periode,
			settings_instance=settings_instance,
			source_salaire=source_salaire,
		)
	except ValueError:
		return 'blanc'

	existing = Tr_desc.objects.filter(
		source=source_salaire,
		desc_ctb=desc_ctb,
		date=entry_date,
	).first()
	if not existing:
		return 'blanc'

	return 'vert' if _ecriture_salaire_correspond(existing, detail_rows) else 'jaune'


@expert_required
def creer_ecriture_salaire(request, periode_id):
	if request.method != 'POST':
		return redirect('paie:paie_journal')

	periode = get_object_or_404(PeriodePaie, pk=periode_id)

	try:
		source_salaire, entry_date, desc_ctb, detail_rows = _calculer_lignes_ecriture_salaire(periode)
	except ValueError as exc:
		return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

	existing = Tr_desc.objects.filter(
		source=source_salaire,
		desc_ctb=desc_ctb,
		date=entry_date,
	).first()
	if existing:
		matches = _ecriture_salaire_correspond(existing, detail_rows)
		update_url = None if matches else reverse('paie:paie_mettre_a_jour_ecriture_salaire', args=[periode.id])
		return JsonResponse(_serialize_ecriture_salaire(existing, already_existed=True, matches=matches, update_url=update_url))

	with transaction.atomic():
		tr_desc = Tr_desc.objects.create(
			no_ej=_next_no_ej_paie(),
			date=entry_date,
			desc_ctb=desc_ctb,
			source=source_salaire,
		)

		for compte, montant in detail_rows:
			if _money(montant) == Decimal('0.00'):
				continue
			Tr_detail.objects.create(
				tr_desc=tr_desc,
				compte=compte,
				montant=_money(montant),
			)

	return JsonResponse(_serialize_ecriture_salaire(tr_desc, already_existed=False))


@expert_required
def mettre_a_jour_ecriture_salaire(request, periode_id):
	if request.method != 'POST':
		return redirect('paie:paie_journal')

	periode = get_object_or_404(PeriodePaie, pk=periode_id)

	try:
		source_salaire, entry_date, desc_ctb, detail_rows = _calculer_lignes_ecriture_salaire(periode)
	except ValueError as exc:
		return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

	existing = Tr_desc.objects.filter(
		source=source_salaire,
		desc_ctb=desc_ctb,
		date=entry_date,
	).first()
	if not existing:
		return JsonResponse({'ok': False, 'error': "Aucune écriture existante à mettre à jour pour cette période."}, status=400)

	with transaction.atomic():
		existing.details.all().delete()
		for compte, montant in detail_rows:
			if _money(montant) == Decimal('0.00'):
				continue
			Tr_detail.objects.create(
				tr_desc=existing,
				compte=compte,
				montant=_money(montant),
			)

	return JsonResponse(_serialize_ecriture_salaire(existing, already_existed=True, matches=True, updated=True))


@login_required
@xframe_options_sameorigin
def journal_paies_page(request):
	try:
		active_employe_id = int(request.GET.get('employe', ''))
	except (TypeError, ValueError):
		active_employe_id = None

	paies = list(
		Paie.objects
		.select_related('employe', 'periode', 'periode__frequence_paie')
		.only(
			'id',
			'employe__id',
			'employe__nom',
			'employe__prenom',
			'periode__id',
			'periode__date_debut',
			'periode__date_fin',
			'periode__date_paie',
			'periode__frequence_paie__code',
			'heures_travaillees',
			'heures_supp',
			'vacances_payees',
			'salaire_brut_periode',
			'salaire_net',
			'vacances',
			'rrq',
			'rrq_employeur',
			'rqap',
			'rqap_employeur',
			'ae',
			'ae_employeur',
			'cnesst_employeur',
			'fss_employeur',
			'impot_federal',
			'impot_provincial',
		)
		.order_by('periode__date_paie', 'periode__date_fin', 'id')
	)
	def _d(value):
		return value if value is not None else Decimal('0.00')

	def _money(value):
		return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

	def _employer_charges_for_paie(paie, date_paie):
		rrq_employeur = _d(paie.rrq_employeur)
		rqap_employeur = _d(paie.rqap_employeur)
		ae_employeur = _d(paie.ae_employeur)
		fss_employeur = _d(paie.fss_employeur)
		cnesst_employeur = _d(paie.cnesst_employeur)

		charge_employeur = _money(rrq_employeur + rqap_employeur + ae_employeur + fss_employeur + cnesst_employeur)
		return {
			'rrq_employeur': rrq_employeur,
			'rqap_employeur': rqap_employeur,
			'ae_employeur': ae_employeur,
			'fss_employeur': fss_employeur,
			'cnesst_employeur': cnesst_employeur,
			'charge_employeur': charge_employeur,
		}

	paie_entries = []
	for paie in paies:
		date_paie = paie.periode.date_paie or paie.periode.date_fin
		paie_entries.append({
			'paie': paie,
			'date_paie': date_paie,
			'employer': _employer_charges_for_paie(paie, date_paie),
		})

	mois_fr = {
		1: 'Janvier',
		2: 'Fevrier',
		3: 'Mars',
		4: 'Avril',
		5: 'Mai',
		6: 'Juin',
		7: 'Juillet',
		8: 'Aout',
		9: 'Septembre',
		10: 'Octobre',
		11: 'Novembre',
		12: 'Decembre',
	}

	month_options = []
	current_month = date_type.today().replace(day=1)
	for offset in range(-12, 13):
		option_date = PaieForm._add_months(current_month, offset)
		month_options.append({
			'value': option_date.strftime('%Y-%m'),
			'label': f"{mois_fr.get(option_date.month, option_date.month)} {option_date.year}",
		})

	def _build_journal_rows(paie_entries_list):
		journal_rows = []
		current_month_key = None
		month_totals = {
			'heures': Decimal('0.00'),
			'vacances_payees': Decimal('0.00'),
			'brut': Decimal('0.00'),
			'vacances': Decimal('0.00'),
			'rrq': Decimal('0.00'),
			'rrq_employeur': Decimal('0.00'),
			'rqap': Decimal('0.00'),
			'rqap_employeur': Decimal('0.00'),
			'ae': Decimal('0.00'),
			'ae_employeur': Decimal('0.00'),
			'fss_employeur': Decimal('0.00'),
			'cnesst_employeur': Decimal('0.00'),
			'charge_employeur': Decimal('0.00'),
			'impot_federal': Decimal('0.00'),
			'impot_provincial': Decimal('0.00'),
			'net': Decimal('0.00'),
		}
		total_brut_local = Decimal('0.00')
		total_net_local = Decimal('0.00')
		total_charge_employeur_local = Decimal('0.00')

		for entry in paie_entries_list:
			paie = entry['paie']
			date_paie = entry['date_paie']
			employer = entry['employer']
			month_key = (date_paie.year, date_paie.month)

			if current_month_key is None:
				current_month_key = month_key
			elif month_key != current_month_key:
				journal_rows.append({
					'type': 'subtotal',
					'month_name': mois_fr.get(current_month_key[1], str(current_month_key[1])),
					'totals': month_totals.copy(),
				})
				journal_rows.append({'type': 'separator'})
				current_month_key = month_key
				month_totals = {
					'heures': Decimal('0.00'),
					'vacances_payees': Decimal('0.00'),
					'brut': Decimal('0.00'),
					'vacances': Decimal('0.00'),
					'rrq': Decimal('0.00'),
					'rrq_employeur': Decimal('0.00'),
					'rqap': Decimal('0.00'),
					'rqap_employeur': Decimal('0.00'),
					'ae': Decimal('0.00'),
					'ae_employeur': Decimal('0.00'),
					'fss_employeur': Decimal('0.00'),
					'cnesst_employeur': Decimal('0.00'),
					'charge_employeur': Decimal('0.00'),
					'impot_federal': Decimal('0.00'),
					'impot_provincial': Decimal('0.00'),
					'net': Decimal('0.00'),
				}

			journal_rows.append({
				'type': 'paie',
				'paie': paie,
				'date_paie': date_paie,
				'employer': employer,
			})

			month_totals['heures'] += _d(paie.heures_travaillees) + _d(paie.heures_supp)
			month_totals['vacances_payees'] += _d(paie.vacances_payees)
			month_totals['brut'] += _d(paie.salaire_brut_periode)
			month_totals['vacances'] += _d(paie.vacances)
			month_totals['rrq'] += _d(paie.rrq)
			month_totals['rrq_employeur'] += employer['rrq_employeur']
			month_totals['rqap'] += _d(paie.rqap)
			month_totals['rqap_employeur'] += employer['rqap_employeur']
			month_totals['ae'] += _d(paie.ae)
			month_totals['ae_employeur'] += employer['ae_employeur']
			month_totals['fss_employeur'] += employer['fss_employeur']
			month_totals['cnesst_employeur'] += employer['cnesst_employeur']
			month_totals['charge_employeur'] += employer['charge_employeur']
			month_totals['impot_federal'] += _d(paie.impot_federal)
			month_totals['impot_provincial'] += _d(paie.impot_provincial)
			month_totals['net'] += _d(paie.salaire_net)

			total_brut_local += _d(paie.salaire_brut_periode)
			total_net_local += _d(paie.salaire_net)
			total_charge_employeur_local += employer['charge_employeur']

		if current_month_key is not None:
			journal_rows.append({
				'type': 'subtotal',
				'month_name': mois_fr.get(current_month_key[1], str(current_month_key[1])),
				'totals': month_totals.copy(),
			})

		return journal_rows, total_brut_local, total_net_local, total_charge_employeur_local

	def _build_total_period_rows(paie_entries_list, compute_statut=False, statut_settings_instance=None, statut_source_salaire=None):
		period_groups = {}
		for entry in paie_entries_list:
			paie = entry['paie']
			date_paie = entry['date_paie']
			employer = entry['employer']
			key = paie.periode_id
			if key not in period_groups:
				period_groups[key] = {
					'periode': paie.periode,
					'date_paie': date_paie,
					'date_fin': paie.periode.date_fin,
					'heures': Decimal('0.00'),
					'vacances_payees': Decimal('0.00'),
					'brut': Decimal('0.00'),
					'vacances': Decimal('0.00'),
					'net': Decimal('0.00'),
					'rrq_employe': Decimal('0.00'),
					'rrq_employeur': Decimal('0.00'),
					'rqap': Decimal('0.00'),
					'rqap_employeur': Decimal('0.00'),
					'impot_provincial': Decimal('0.00'),
					'ae': Decimal('0.00'),
					'ae_employeur': Decimal('0.00'),
					'fss_employeur': Decimal('0.00'),
					'cnesst_employeur': Decimal('0.00'),
					'charge_employeur': Decimal('0.00'),
					'impot_federal': Decimal('0.00'),
					'total_qc': Decimal('0.00'),
					'total_ca': Decimal('0.00'),
				}
			bucket = period_groups[key]
			bucket['heures'] += _d(paie.heures_travaillees) + _d(paie.heures_supp)
			bucket['vacances_payees'] += _d(paie.vacances_payees)
			bucket['brut'] += _d(paie.salaire_brut_periode)
			bucket['vacances'] += _d(paie.vacances)
			bucket['net'] += _d(paie.salaire_net)
			bucket['rrq_employe'] += _d(paie.rrq)
			bucket['rrq_employeur'] += employer['rrq_employeur']
			bucket['rqap'] += _d(paie.rqap)
			bucket['rqap_employeur'] += employer['rqap_employeur']
			bucket['impot_provincial'] += _d(paie.impot_provincial)
			bucket['ae'] += _d(paie.ae)
			bucket['ae_employeur'] += employer['ae_employeur']
			bucket['fss_employeur'] += employer['fss_employeur']
			bucket['cnesst_employeur'] += employer['cnesst_employeur']
			bucket['charge_employeur'] += employer['charge_employeur']
			bucket['impot_federal'] += _d(paie.impot_federal)
			bucket['total_qc'] += (
				_d(paie.rrq)
				+ employer['rrq_employeur']
				+ _d(paie.rqap)
				+ employer['rqap_employeur']
				+ _d(paie.impot_provincial)
				+ employer['fss_employeur']
				+ employer['cnesst_employeur']
			)
			bucket['total_ca'] += (
				_d(paie.ae)
				+ employer['ae_employeur']
				+ _d(paie.impot_federal)
			)

		ordered_groups = sorted(
			period_groups.values(),
			key=lambda g: (g['date_paie'], g['date_fin']),
		)
		period_number_counters = {}

		grand_totals = {
			'heures': Decimal('0.00'),
			'vacances_payees': Decimal('0.00'),
			'brut': Decimal('0.00'),
			'vacances': Decimal('0.00'),
			'net': Decimal('0.00'),
			'rrq_employe': Decimal('0.00'),
			'rrq_employeur': Decimal('0.00'),
			'rqap': Decimal('0.00'),
			'rqap_employeur': Decimal('0.00'),
			'impot_provincial': Decimal('0.00'),
			'ae': Decimal('0.00'),
			'ae_employeur': Decimal('0.00'),
			'fss_employeur': Decimal('0.00'),
			'cnesst_employeur': Decimal('0.00'),
			'impot_federal': Decimal('0.00'),
			'total_qc': Decimal('0.00'),
			'total_ca': Decimal('0.00'),
		}

		total_rows = []
		current_month_key = None
		month_totals = {
			'heures': Decimal('0.00'),
			'vacances_payees': Decimal('0.00'),
			'brut': Decimal('0.00'),
			'vacances': Decimal('0.00'),
			'net': Decimal('0.00'),
			'rrq_employe': Decimal('0.00'),
			'rrq_employeur': Decimal('0.00'),
			'rqap': Decimal('0.00'),
			'rqap_employeur': Decimal('0.00'),
			'impot_provincial': Decimal('0.00'),
			'ae': Decimal('0.00'),
			'ae_employeur': Decimal('0.00'),
			'fss_employeur': Decimal('0.00'),
			'cnesst_employeur': Decimal('0.00'),
			'charge_employeur': Decimal('0.00'),
			'impot_federal': Decimal('0.00'),
			'total_qc': Decimal('0.00'),
			'total_ca': Decimal('0.00'),
		}

		for idx, group in enumerate(ordered_groups, start=1):
			date_paie = group['date_paie']
			month_key = (date_paie.year, date_paie.month)
			periode = group['periode']
			period_cycle_key = (periode.frequence_paie_id, date_paie.year)
			period_no = period_number_counters.get(period_cycle_key, 0) + 1
			period_number_counters[period_cycle_key] = period_no

			if current_month_key is None:
				current_month_key = month_key
			elif month_key != current_month_key:
				total_rows.append({
					'type': 'subtotal',
					'month_name': mois_fr.get(current_month_key[1], str(current_month_key[1])),
					'totals': month_totals.copy(),
				})
				total_rows.append({'type': 'separator'})
				current_month_key = month_key
				month_totals = {
					'heures': Decimal('0.00'),
					'vacances_payees': Decimal('0.00'),
					'brut': Decimal('0.00'),
					'vacances': Decimal('0.00'),
					'net': Decimal('0.00'),
					'rrq_employe': Decimal('0.00'),
					'rrq_employeur': Decimal('0.00'),
					'rqap': Decimal('0.00'),
					'rqap_employeur': Decimal('0.00'),
					'impot_provincial': Decimal('0.00'),
					'ae': Decimal('0.00'),
					'ae_employeur': Decimal('0.00'),
					'fss_employeur': Decimal('0.00'),
					'cnesst_employeur': Decimal('0.00'),
					'charge_employeur': Decimal('0.00'),
					'impot_federal': Decimal('0.00'),
					'total_qc': Decimal('0.00'),
					'total_ca': Decimal('0.00'),
				}

			total_rows.append({
				'type': 'period',
				'period_index': period_no,
				'date_paie': group['date_paie'],
				'date_fin': group['date_fin'],
				'totals': group,
				'statut_transmission': _statut_transmission_ecriture_salaire(periode, settings_instance=statut_settings_instance, source_salaire=statut_source_salaire) if compute_statut else None,
			})

			month_totals['heures'] += group['heures']
			month_totals['vacances_payees'] += group['vacances_payees']
			month_totals['brut'] += group['brut']
			month_totals['vacances'] += group['vacances']
			month_totals['net'] += group['net']
			month_totals['rrq_employe'] += group['rrq_employe']
			month_totals['rrq_employeur'] += group['rrq_employeur']
			month_totals['rqap'] += group['rqap']
			month_totals['rqap_employeur'] += group['rqap_employeur']
			month_totals['impot_provincial'] += group['impot_provincial']
			month_totals['ae'] += group['ae']
			month_totals['ae_employeur'] += group['ae_employeur']
			month_totals['fss_employeur'] += group['fss_employeur']
			month_totals['cnesst_employeur'] += group['cnesst_employeur']
			month_totals['charge_employeur'] += group['charge_employeur']
			month_totals['impot_federal'] += group['impot_federal']
			month_totals['total_qc'] += group['total_qc']
			month_totals['total_ca'] += group['total_ca']

			grand_totals['heures'] += group['heures']
			grand_totals['vacances_payees'] += group['vacances_payees']
			grand_totals['brut'] += group['brut']
			grand_totals['vacances'] += group['vacances']
			grand_totals['net'] += group['net']
			grand_totals['rrq_employe'] += group['rrq_employe']
			grand_totals['rrq_employeur'] += group['rrq_employeur']
			grand_totals['rqap'] += group['rqap']
			grand_totals['rqap_employeur'] += group['rqap_employeur']
			grand_totals['impot_provincial'] += group['impot_provincial']
			grand_totals['ae'] += group['ae']
			grand_totals['ae_employeur'] += group['ae_employeur']
			grand_totals['fss_employeur'] += group['fss_employeur']
			grand_totals['cnesst_employeur'] += group['cnesst_employeur']
			grand_totals['impot_federal'] += group['impot_federal']
			grand_totals['total_qc'] += group['total_qc']
			grand_totals['total_ca'] += group['total_ca']

		if current_month_key is not None:
			total_rows.append({
				'type': 'subtotal',
				'month_name': mois_fr.get(current_month_key[1], str(current_month_key[1])),
				'totals': month_totals.copy(),
			})

		return total_rows, grand_totals

	journal_rows, total_brut, total_net, total_charge_employeur = _build_journal_rows(paie_entries)
	can_create_salary_entry = is_expert(request.user)
	statut_settings_instance = None
	statut_source_salaire = None
	if can_create_salary_entry:
		statut_settings_instance = get_setting(
			'compte_salaire',
			'compte_vacances',
			'compte_benefices_marginaux',
			'compte_salaires_a_payer',
			'compte_vacances_a_payer',
			'compte_das_federales',
			'compte_das_provinciales',
			'taux_cnesst_employeur',
			'taux_fss_employeur',
		)
		statut_source_salaire, _ = Source.objects.get_or_create(nom='Salaire')
	total_rows, total_rows_grand_totals = _build_total_period_rows(
		paie_entries,
		compute_statut=can_create_salary_entry,
		statut_settings_instance=statut_settings_instance,
		statut_source_salaire=statut_source_salaire,
	)

	by_employe = {}
	for entry in paie_entries:
		paie = entry['paie']
		employe_entry = by_employe.setdefault(
			paie.employe_id,
			{'employe': paie.employe, 'paies': []},
		)
		employe_entry['paies'].append(entry)

	employe_tabs = []
	for _, payload in sorted(by_employe.items(), key=lambda item: (item[1]['employe'].nom, item[1]['employe'].prenom, item[1]['employe'].id)):
		rows, brut, net, charge_employeur = _build_journal_rows(payload['paies'])
		employe_tabs.append({
			'employe': payload['employe'],
			'tab_id': f"emp-{payload['employe'].id}",
			'journal_rows': rows,
			'total_brut': brut,
			'total_net': net,
			'total_charge_employeur': charge_employeur,
		})

	active_employe_tab_id = None
	if active_employe_id is not None and any(tab['employe'].id == active_employe_id for tab in employe_tabs):
		active_employe_tab_id = f'emp-{active_employe_id}'

	return render(request, 'paie/journal_paies.html', {
		'title': 'Journal des paies',
		'journal_rows': journal_rows,
		'total_rows': total_rows,
		'total_rows_grand_totals': total_rows_grand_totals,
		'total_brut': total_brut,
		'total_net': total_net,
		'total_charge_employeur': total_charge_employeur,
		'employe_tabs': employe_tabs,
		'active_employe_tab_id': active_employe_tab_id,
		'can_create_salary_entry': can_create_salary_entry,
	})


