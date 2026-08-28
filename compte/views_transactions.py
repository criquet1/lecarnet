"""Vues pour la saisie, la modification et la suppression des écritures
comptables manuelles (transactions). Extrait de compte/views.py pour
alléger ce fichier.
"""

import logging
import re
from decimal import Decimal
from datetime import date, timedelta

from django.contrib import messages
from django.db import DatabaseError, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.connection import ConnectionDoesNotExist
from django.utils import timezone

from facture.helpers.dates import verifier_exercice_modifiable
from facture.models import Client, Fournisseur, Source, Tr_desc, Tr_detail
from facture.views import _next_no_ej
from facture.utils import expert_required, get_settings, parse_decimal

from .models import Compte


logger = logging.getLogger(__name__)


def _parse_compte_numero(raw_value):
	value = (raw_value or '').strip()
	if not value:
		return None
	if value.isdigit():
		return int(value)
	match = re.match(r'^(\d{4})', value)
	if not match:
		return None
	return int(match.group(1))


def _resolve_transaction_date(raw_value):
	value = (raw_value or '').strip().lower()
	if value == 'today':
		return timezone.localdate()
	if value == 'yesterday':
		return timezone.localdate() - timedelta(days=1)
	try:
		return date.fromisoformat(value)
	except ValueError:
		return None
	return None


@expert_required
def transactions_page(request):
	try:
		try:
			compagnies = sorted(
				[{'type': 'client', 'obj': c, 'key': f'client:{c.pk}'} for c in Client.objects.filter(active=True)] +
				[{'type': 'fournisseur', 'obj': f, 'key': f'fournisseur:{f.pk}'} for f in Fournisseur.objects.filter(active=True)],
				key=lambda item: item['obj'].nom.lower()
			)
			comptes = list(Compte.objects.order_by('numero'))
			settings_instance = get_settings()
		except (OperationalError, ProgrammingError, ConnectionDoesNotExist):
			logger.exception('Transactions indisponible: tables facture/compte manquantes sur la base active.')
			messages.error(
				request,
				'La base client active n\'est pas initialisee (tables facture/compte manquantes). '
				'Lancez les migrations tenant (migrate_tenants) puis rechargez la page.'
			)
			return render(request, 'compte/transactions.html', {
				'title': 'Transactions',
				'compagnies': [],
				'comptes': [],
			})

		def _render_transactions_page():
			return render(request, 'compte/transactions.html', {
				'title': 'Transactions',
				'compagnies': compagnies,
				'comptes': comptes,
				'compte_cap_id': settings_instance.cap_id if settings_instance else None,
				'compte_car_id': settings_instance.car_id if settings_instance else None,
			})

		if request.method == 'POST':
			raw_date = (request.POST.get('date_select') or '').strip()
			raw_compagnie_id = (request.POST.get('compagnie') or '').strip()
			description = (request.POST.get('description') or '').strip()
			source_name = (request.POST.get('source') or '').strip()

			compte_values = request.POST.getlist('comptes_comptables[]')
			debit_values = request.POST.getlist('montant_debit[]')
			credit_values = request.POST.getlist('montant_credit[]')

			date_value = _resolve_transaction_date(raw_date)
			if not date_value:
				messages.error(request, 'Veuillez selectionner une date valide.')
				return _render_transactions_page()

			compagnie = None
			compagnie_type = 'client'
			if raw_compagnie_id:
				if ':' in raw_compagnie_id:
					compagnie_type, raw_compagnie_id = raw_compagnie_id.split(':', 1)
					compagnie_type = compagnie_type.strip().lower()
				compagnie_model = Fournisseur if compagnie_type == 'fournisseur' else Client
				try:
					compagnie = compagnie_model.objects.filter(pk=raw_compagnie_id).first()
				except (ValueError, TypeError):
					messages.error(request, 'Compagnie invalide.')
					return _render_transactions_page()
				except (OperationalError, ProgrammingError, ConnectionDoesNotExist, DatabaseError):
					logger.exception('Echec lecture compagnie: base active non initialisee.')
					messages.error(
						request,
						'Impossible de charger la compagnie: base client active non initialisee. '
						'Executez les migrations tenant puis reessayez.'
					)
					return _render_transactions_page()
				if not compagnie:
					messages.error(request, 'Compagnie invalide.')
					return _render_transactions_page()

			if not description:
				messages.error(request, 'La description est obligatoire.')
				return _render_transactions_page()

			if not source_name:
				source_name = 'Manuel'

			line_count = max(len(compte_values), len(debit_values), len(credit_values))
			lines = []
			total_debit = Decimal('0')
			total_credit = Decimal('0')

			for idx in range(line_count):
				compte_raw = compte_values[idx] if idx < len(compte_values) else ''
				debit_raw = debit_values[idx] if idx < len(debit_values) else ''
				credit_raw = credit_values[idx] if idx < len(credit_values) else ''

				if not (compte_raw or debit_raw or credit_raw):
					continue

				compte_numero = _parse_compte_numero(compte_raw)
				if not compte_numero:
					messages.error(request, f'Ligne {idx + 1}: compte de grand livre invalide (format attendu: 1234).')
					return _render_transactions_page()

				try:
					compte = Compte.objects.filter(pk=compte_numero).first()
				except (OperationalError, ProgrammingError, ConnectionDoesNotExist, DatabaseError):
					logger.exception('Echec lecture compte: base active non initialisee.')
					messages.error(
						request,
						'Impossible de charger les comptes: base client active non initialisee. '
						'Executez les migrations tenant puis reessayez.'
					)
					return _render_transactions_page()
				if not compte:
					messages.error(request, f'Ligne {idx + 1}: le compte {compte_numero} est introuvable.')
					return _render_transactions_page()

				debit_amount = parse_decimal(debit_raw) if (debit_raw or '').strip() else Decimal('0')
				credit_amount = parse_decimal(credit_raw) if (credit_raw or '').strip() else Decimal('0')
				if debit_amount is None or credit_amount is None:
					messages.error(request, f'Ligne {idx + 1}: montant debit/credit invalide.')
					return _render_transactions_page()

				if debit_amount > 0 and credit_amount > 0:
					messages.error(request, f'Ligne {idx + 1}: choisissez debit OU credit, pas les deux.')
					return _render_transactions_page()

				if debit_amount <= 0 and credit_amount <= 0:
					messages.error(request, f'Ligne {idx + 1}: entrez un montant debit ou credit.')
					return _render_transactions_page()

				if debit_amount > 0:
					total_debit += debit_amount
					montant = debit_amount
				else:
					total_credit += credit_amount
					montant = -credit_amount

				lines.append({
					'compte': compte,
					'montant': montant,
				})

			if not lines:
				messages.error(request, 'Ajoutez au moins une ligne comptable.')
				return _render_transactions_page()

			company_required_account_ids = {
				account_id
				for account_id in (
					settings_instance.cap_id if settings_instance else None,
					settings_instance.car_id if settings_instance else None,
				)
				if account_id is not None
			}
			if compagnie is None and any(
				line['compte'].pk in company_required_account_ids
				for line in lines
			):
				messages.error(
					request,
					'Une compagnie est obligatoire lorsqu\'une ligne utilise le compte CAP ou CAR.'
				)
				return _render_transactions_page()

			if total_debit.quantize(Decimal('0.01')) != total_credit.quantize(Decimal('0.01')):
				messages.error(request, 'La transaction doit etre equilibree (debit = credit).')
				return _render_transactions_page()

			try:
				verifier_exercice_modifiable(date_value)
				with transaction.atomic():
					source, _ = Source.objects.get_or_create(nom=source_name[:15])

					tr_desc_kwargs = {
						'no_ej': _next_no_ej(date_value),
						'date': date_value,
						'desc_releve': description,
						'desc_ctb': description[:40],
						'source': source,
					}
					if compagnie is not None:
						if compagnie_type == 'fournisseur':
							tr_desc_kwargs['fournisseur'] = compagnie
						else:
							tr_desc_kwargs['client'] = compagnie
					tr_desc = Tr_desc.objects.create(**tr_desc_kwargs)

					for line in lines:
						Tr_detail.objects.create(
							tr_desc=tr_desc,
							compte=line['compte'],
							montant=line['montant'],
						)
			except (OperationalError, ProgrammingError, ConnectionDoesNotExist, DatabaseError):
				logger.exception('Echec sauvegarde transaction: base active non initialisee.')
				messages.error(
					request,
					'Impossible de sauvegarder: la base client active n\'est pas initialisee. '
					'Executez les migrations tenant puis reessayez.'
				)
				return _render_transactions_page()
			except ValueError as exc:
				messages.error(request, str(exc))
				return _render_transactions_page()
			except Exception:
				logger.exception('Echec sauvegarde transaction: erreur non prevue.')
				messages.error(
					request,
					'Impossible de sauvegarder la transaction pour le moment. '
					'Consultez les logs serveur pour le detail technique.'
				)
				return _render_transactions_page()

			messages.success(request, f"Transaction sauvegardee ({tr_desc.no_ej}).")
			return redirect('journal_general')

		return _render_transactions_page()
	except Exception:
		logger.exception('Erreur inattendue dans transactions_page.')
		messages.error(
			request,
			'Une erreur inattendue est survenue dans Transactions. '
			'Reessayez apres avoir redemarre le serveur.'
		)
		return render(request, 'compte/transactions.html', {
			'title': 'Transactions',
			'compagnies': [],
			'comptes': [],
		})


@expert_required
def transaction_edit_page(request, pk):
	tr_desc = get_object_or_404(Tr_desc, pk=pk)

	try:
		compagnies = sorted(
			[{'type': 'client', 'obj': c, 'key': f'client:{c.pk}'} for c in Client.objects.filter(active=True)] +
			[{'type': 'fournisseur', 'obj': f, 'key': f'fournisseur:{f.pk}'} for f in Fournisseur.objects.filter(active=True)],
			key=lambda item: item['obj'].nom.lower()
		)
		comptes = list(Compte.objects.order_by('numero'))
		settings_instance = get_settings()
	except (OperationalError, ProgrammingError, ConnectionDoesNotExist):
		logger.exception('Modification de transaction indisponible: tables facture/compte manquantes sur la base active.')
		messages.error(
			request,
			'La base client active n\'est pas initialisee (tables facture/compte manquantes). '
			'Lancez les migrations tenant (migrate_tenants) puis rechargez la page.'
		)
		return redirect('journal_general')

	existing_compagnie_key = ''
	if tr_desc.fournisseur_id:
		existing_compagnie_key = f'fournisseur:{tr_desc.fournisseur_id}'
	elif tr_desc.client_id:
		existing_compagnie_key = f'client:{tr_desc.client_id}'

	def _existing_lines():
		lines = []
		for detail in tr_desc.details.select_related('compte').order_by('id'):
			montant = detail.montant
			lines.append({
				'compte_numero': detail.compte_id,
				'debit': montant if montant > 0 else '',
				'credit': -montant if montant < 0 else '',
			})
		return lines

	def _render_edit_page(line_items=None):
		return render(request, 'compte/transaction_edit.html', {
			'title': f"Modifier l'écriture {tr_desc.no_ej}",
			'tr_desc': tr_desc,
			'compagnies': compagnies,
			'comptes': comptes,
			'compte_cap_id': settings_instance.cap_id if settings_instance else None,
			'compte_car_id': settings_instance.car_id if settings_instance else None,
			'existing_compagnie_key': existing_compagnie_key,
			'line_items': line_items if line_items is not None else _existing_lines(),
		})

	if request.method == 'POST':
		raw_date = (request.POST.get('date_select') or '').strip()
		raw_compagnie_id = (request.POST.get('compagnie') or '').strip()
		description = (request.POST.get('description') or '').strip()
		source_name = (request.POST.get('source') or '').strip()

		compte_values = request.POST.getlist('comptes_comptables[]')
		debit_values = request.POST.getlist('montant_debit[]')
		credit_values = request.POST.getlist('montant_credit[]')

		date_value = _resolve_transaction_date(raw_date)
		if not date_value:
			messages.error(request, 'Veuillez selectionner une date valide.')
			return _render_edit_page()

		compagnie = None
		compagnie_type = 'client'
		if raw_compagnie_id:
			if ':' in raw_compagnie_id:
				compagnie_type, raw_compagnie_id = raw_compagnie_id.split(':', 1)
				compagnie_type = compagnie_type.strip().lower()
			compagnie_model = Fournisseur if compagnie_type == 'fournisseur' else Client
			try:
				compagnie = compagnie_model.objects.filter(pk=raw_compagnie_id).first()
			except (ValueError, TypeError):
				messages.error(request, 'Compagnie invalide.')
				return _render_edit_page()
			if not compagnie:
				messages.error(request, 'Compagnie invalide.')
				return _render_edit_page()

		if not description:
			messages.error(request, 'La description est obligatoire.')
			return _render_edit_page()

		if not source_name:
			source_name = 'Manuel'

		line_count = max(len(compte_values), len(debit_values), len(credit_values))
		lines = []
		total_debit = Decimal('0')
		total_credit = Decimal('0')

		for idx in range(line_count):
			compte_raw = compte_values[idx] if idx < len(compte_values) else ''
			debit_raw = debit_values[idx] if idx < len(debit_values) else ''
			credit_raw = credit_values[idx] if idx < len(credit_values) else ''

			if not (compte_raw or debit_raw or credit_raw):
				continue

			compte_numero = _parse_compte_numero(compte_raw)
			if not compte_numero:
				messages.error(request, f'Ligne {idx + 1}: compte de grand livre invalide (format attendu: 1234).')
				return _render_edit_page()

			compte = Compte.objects.filter(pk=compte_numero).first()
			if not compte:
				messages.error(request, f'Ligne {idx + 1}: le compte {compte_numero} est introuvable.')
				return _render_edit_page()

			debit_amount = parse_decimal(debit_raw) if (debit_raw or '').strip() else Decimal('0')
			credit_amount = parse_decimal(credit_raw) if (credit_raw or '').strip() else Decimal('0')
			if debit_amount is None or credit_amount is None:
				messages.error(request, f'Ligne {idx + 1}: montant debit/credit invalide.')
				return _render_edit_page()

			if debit_amount > 0 and credit_amount > 0:
				messages.error(request, f'Ligne {idx + 1}: choisissez debit OU credit, pas les deux.')
				return _render_edit_page()

			if debit_amount <= 0 and credit_amount <= 0:
				messages.error(request, f'Ligne {idx + 1}: entrez un montant debit ou credit.')
				return _render_edit_page()

			if debit_amount > 0:
				total_debit += debit_amount
				montant = debit_amount
			else:
				total_credit += credit_amount
				montant = -credit_amount

			lines.append({'compte': compte, 'montant': montant})

		if not lines:
			messages.error(request, 'Ajoutez au moins une ligne comptable.')
			return _render_edit_page()

		company_required_account_ids = {
			account_id
			for account_id in (
				settings_instance.cap_id if settings_instance else None,
				settings_instance.car_id if settings_instance else None,
			)
			if account_id is not None
		}
		if compagnie is None and any(
			line['compte'].pk in company_required_account_ids
			for line in lines
		):
			messages.error(
				request,
				'Une compagnie est obligatoire lorsqu\'une ligne utilise le compte CAP ou CAR.'
			)
			return _render_edit_page()

		if total_debit.quantize(Decimal('0.01')) != total_credit.quantize(Decimal('0.01')):
			messages.error(request, 'La transaction doit etre equilibree (debit = credit).')
			return _render_edit_page()

		try:
			verifier_exercice_modifiable(tr_desc.date)
			verifier_exercice_modifiable(date_value)
			with transaction.atomic():
				source, _ = Source.objects.get_or_create(nom=source_name[:15])

				tr_desc.date = date_value
				tr_desc.desc_releve = description
				tr_desc.desc_ctb = description[:40]
				tr_desc.source = source
				tr_desc.client = compagnie if compagnie_type == 'client' else None
				tr_desc.fournisseur = compagnie if compagnie_type == 'fournisseur' else None
				tr_desc.save()

				tr_desc.details.all().delete()
				for line in lines:
					Tr_detail.objects.create(
						tr_desc=tr_desc,
						compte=line['compte'],
						montant=line['montant'],
					)
		except (OperationalError, ProgrammingError, ConnectionDoesNotExist, DatabaseError):
			logger.exception('Echec modification transaction: base active non initialisee.')
			messages.error(
				request,
				'Impossible de sauvegarder: la base client active n\'est pas initialisee. '
				'Executez les migrations tenant puis reessayez.'
			)
			return _render_edit_page()
		except ValueError as exc:
			messages.error(request, str(exc))
			return _render_edit_page()
		except Exception:
			logger.exception('Echec modification transaction: erreur non prevue.')
			messages.error(
				request,
				'Impossible de sauvegarder les modifications pour le moment. '
				'Consultez les logs serveur pour le detail technique.'
			)
			return _render_edit_page()

		messages.success(request, f"Écriture {tr_desc.no_ej} mise à jour.")
		return redirect('journal_general')

	return _render_edit_page()


@expert_required
def transaction_delete(request, pk):
	if request.method != 'POST':
		return redirect('journal_general')

	tr_desc = get_object_or_404(Tr_desc, pk=pk)
	no_ej = tr_desc.no_ej
	try:
		verifier_exercice_modifiable(tr_desc.date)
		with transaction.atomic():
			tr_desc.delete()
	except ValueError as exc:
		messages.error(request, str(exc))
	except (OperationalError, ProgrammingError, ConnectionDoesNotExist, DatabaseError):
		logger.exception('Echec suppression transaction: base active non initialisee.')
		messages.error(request, 'Impossible de supprimer: la base client active n\'est pas initialisee.')
	except Exception:
		logger.exception('Echec suppression transaction: erreur non prevue.')
		messages.error(request, 'Impossible de supprimer cette écriture pour le moment.')
	else:
		messages.success(request, f"Écriture {no_ej} supprimée.")

	return redirect('journal_general')
