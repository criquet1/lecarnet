from decimal import Decimal

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render

from facture.models import Compagnie, CompagnieSoldeDepart, CompteReleve
from facture.utils import expert_required, get_settings, parse_decimal, read_csv_rows

from .forms import CompteCsvImportForm, CompteForm, SettingForm
from .models import Compte, SoldeAuxLivres, Total


def _import_comptes_csv(csv_file):
	rows = read_csv_rows(csv_file.read())
	report = {
		'created': 0,
		'updated': 0,
		'skipped': 0,
		'errors': [],
	}

	if not rows:
		report['errors'].append('Le fichier CSV ne contient aucune ligne de donnees.')
		return report

	parsed_rows = []
	for idx, row in enumerate(rows, start=2):
		numero_raw = (row.get('numero') or row.get('compte_no') or '').strip()
		libelle = (row.get('libelle') or row.get('compte_libelle') or '').strip()
		no_total_raw = (row.get('no_total') or row.get('compte_total') or '').strip()

		if not numero_raw and not libelle and not no_total_raw:
			continue

		try:
			numero = int(numero_raw)
		except ValueError:
			report['skipped'] += 1
			report['errors'].append(f'Ligne {idx}: numero invalide ({numero_raw}).')
			continue

		if not libelle:
			report['skipped'] += 1
			report['errors'].append(f'Ligne {idx}: libelle manquant.')
			continue

		try:
			no_total = int(no_total_raw)
		except ValueError:
			report['skipped'] += 1
			report['errors'].append(f'Ligne {idx}: no_total invalide ({no_total_raw}).')
			continue

		parsed_rows.append((numero, libelle, no_total))

	if not parsed_rows:
		return report

	with transaction.atomic():
		unique_totals = sorted({no_total for _, _, no_total in parsed_rows})
		totals_map = Total.objects.in_bulk(unique_totals, field_name='no_total')

		missing_totals = [
			Total(
				no_total=no_total,
				desc='Sans total' if no_total == 0 else f'Total {no_total}',
			)
			for no_total in unique_totals
			if no_total not in totals_map
		]
		if missing_totals:
			Total.objects.bulk_create(missing_totals)
			totals_map = Total.objects.in_bulk(unique_totals, field_name='no_total')

		unique_numeros = sorted({numero for numero, _, _ in parsed_rows})
		existing_comptes = Compte.objects.in_bulk(unique_numeros, field_name='numero')

		to_create = []
		to_update = []
		for numero, libelle, no_total in parsed_rows:
			total_obj = totals_map.get(no_total)
			if total_obj is None:
				report['skipped'] += 1
				report['errors'].append(f'Ligne compte {numero}: total introuvable ({no_total}).')
				continue

			existing = existing_comptes.get(numero)
			if existing is None:
				to_create.append(
					Compte(
						numero=numero,
						libelle=libelle,
						no_total=total_obj,
					)
				)
			else:
				existing.libelle = libelle
				existing.no_total = total_obj
				to_update.append(existing)

		if to_create:
			Compte.objects.bulk_create(to_create)
		if to_update:
			Compte.objects.bulk_update(to_update, ['libelle', 'no_total'])

		report['created'] += len(to_create)
		report['updated'] += len(to_update)

		all_compte_ids = set(unique_numeros)
		existing_solde_ids = set(
			SoldeAuxLivres.objects.filter(compte_id__in=all_compte_ids).values_list('compte_id', flat=True)
		)
		missing_solde_ids = [
			SoldeAuxLivres(compte_id=compte_id, solde_depart=Decimal('0'))
			for compte_id in sorted(all_compte_ids - existing_solde_ids)
		]
		if missing_solde_ids:
			SoldeAuxLivres.objects.bulk_create(missing_solde_ids)

	return report
def _build_repartition_state():
	settings_instance = get_settings()
	cap_total = Decimal('0')
	car_total = Decimal('0')

	if settings_instance and settings_instance.cap_id:
		cap_solde = SoldeAuxLivres.objects.filter(compte_id=settings_instance.cap_id).first()
		if cap_solde:
			cap_total = cap_solde.solde_depart

	if settings_instance and settings_instance.car_id:
		car_solde = SoldeAuxLivres.objects.filter(compte_id=settings_instance.car_id).first()
		if car_solde:
			car_total = car_solde.solde_depart

	compagnies = Compagnie.objects.filter(
		cap_ou_car__in=[Compagnie.MODE_CAP, Compagnie.MODE_CAR],
	).order_by('cap_ou_car', 'nom')

	soldes_map = {
		item.compagnie_id: item.montant
		for item in CompagnieSoldeDepart.objects.select_related('compagnie')
	}

	repartition_rows = []
	cap_reparti = Decimal('0')
	car_reparti = Decimal('0')

	for compagnie in compagnies:
		montant = soldes_map.get(compagnie.id, Decimal('0'))
		repartition_rows.append({
			'compagnie': compagnie,
			'montant': montant,
			'field_name': f'repartition_{compagnie.id}',
		})
		if (compagnie.cap_ou_car or '').upper() == Compagnie.MODE_CAP:
			cap_reparti += montant
		elif (compagnie.cap_ou_car or '').upper() == Compagnie.MODE_CAR:
			car_reparti += montant

	return {
		'rows': repartition_rows,
		'cap_total': cap_total,
		'car_total': car_total,
		'cap_reparti': cap_reparti,
		'car_reparti': car_reparti,
		'ecart_cap': cap_reparti - cap_total,
		'ecart_car': car_reparti - car_total,
	}


@expert_required
def compte_page(request):
	import_report = None
	repartition_report = None
	edit_numero = (request.GET.get('edit') or request.POST.get('editing_numero') or '').strip()
	editing_compte = Compte.objects.filter(pk=edit_numero).first() if edit_numero else None

	if request.method == 'POST' and request.POST.get('inline_solde_compte'):
		compte_id = (request.POST.get('inline_solde_compte') or '').strip()
		raw_value = (request.POST.get('solde_depart') or '').strip()
		compte = Compte.objects.filter(pk=compte_id).first() if compte_id else None

		if not compte:
			return JsonResponse({'ok': False, 'error': 'Compte introuvable.'}, status=404)

		solde_depart = parse_decimal(raw_value)
		if solde_depart is None:
			return JsonResponse({'ok': False, 'error': 'Valeur invalide.'}, status=400)

		SoldeAuxLivres.objects.update_or_create(
			compte=compte,
			defaults={'solde_depart': solde_depart},
		)

		return JsonResponse({
			'ok': True,
			'compte_id': compte.pk,
			'solde_depart': format(solde_depart, '.2f'),
		})

	if request.method == 'POST' and request.POST.get('link_releve_account'):
		releve_account_id = (request.POST.get('link_releve_account') or '').strip()
		compte_comptable_id = (request.POST.get('compte_comptable_id') or '').strip() or None
		releve_account = CompteReleve.objects.filter(pk=releve_account_id).first()

		if not releve_account:
			return JsonResponse({'ok': False, 'error': 'Compte de relevé introuvable.'}, status=404)

		compte_comptable = None
		if compte_comptable_id:
			compte_comptable = Compte.objects.filter(pk=compte_comptable_id).first()
			if not compte_comptable:
				return JsonResponse({'ok': False, 'error': 'Compte comptable introuvable.'}, status=404)

		releve_account.compte_comptable = compte_comptable
		releve_account.save(update_fields=['compte_comptable'])

		return JsonResponse({
			'ok': True,
			'releve_account_id': releve_account.pk,
			'compte_comptable_id': compte_comptable_id or '',
			'compte_comptable_label': str(compte_comptable) if compte_comptable else '',
		})

	if request.method == 'POST':
		if request.POST.get('import_csv'):
			import_form = CompteCsvImportForm(request.POST, request.FILES)
			if import_form.is_valid():
				try:
					import_report = _import_comptes_csv(import_form.cleaned_data['csv_file'])
				except UnicodeDecodeError as exc:
					import_report = {
						'created': 0,
						'updated': 0,
						'skipped': 0,
						'errors': [f'Import impossible: {exc}'],
					}
			form = CompteForm(instance=editing_compte)
		else:
			if request.POST.get('save_repartition'):
				import_form = CompteCsvImportForm()
				form = CompteForm(instance=editing_compte)
				compagnies = Compagnie.objects.filter(
					cap_ou_car__in=[Compagnie.MODE_CAP, Compagnie.MODE_CAR],
				)
				errors = []
				with transaction.atomic():
					for compagnie in compagnies:
						field_name = f'repartition_{compagnie.id}'
						montant = parse_decimal(request.POST.get(field_name, '0'))
						if montant is None:
							errors.append(f"{compagnie.nom}: montant invalide")
							continue
						CompagnieSoldeDepart.objects.update_or_create(
							compagnie=compagnie,
							defaults={'montant': montant},
						)

				if errors:
					repartition_report = {
						'ok': False,
						'message': 'Certaines lignes sont invalides.',
						'errors': errors,
					}
				else:
					repartition_report = {
						'ok': True,
						'message': 'Repartition enregistree.',
						'errors': [],
					}
			else:
				import_form = CompteCsvImportForm()
				form = CompteForm(request.POST, instance=editing_compte)
				if form.is_valid():
					compte = form.save()
					SoldeAuxLivres.objects.get_or_create(
						compte=compte,
						defaults={'solde_depart': Decimal('0')},
					)
					return redirect('compte')
	else:
		form = CompteForm(instance=editing_compte)
		import_form = CompteCsvImportForm()

	repartition_state = _build_repartition_state()

	comptes = Compte.objects.select_related('no_total').order_by('numero')
	soldes_par_compte = {compte.pk: Decimal('0') for compte in comptes}
	for solde in SoldeAuxLivres.objects.select_related('compte'):
		soldes_par_compte[solde.compte_id] = solde.solde_depart

	comptes_releves = CompteReleve.objects.order_by('type_onglet', 'nom_affichage')

	return render(request, 'compte/compte.html', {
		'title': 'Comptes',
		'form': form,
		'import_form': import_form,
		'import_report': import_report,
		'repartition_report': repartition_report,
		'repartition_state': repartition_state,
		'comptes': comptes,
		'editing_compte': editing_compte,
		'soldes_par_compte': soldes_par_compte,
		'comptes_releves': comptes_releves,
	})


@expert_required
def settings_page(request):
	settings_instance = get_settings()

	if request.method == 'POST':
		form = SettingForm(request.POST, instance=settings_instance)
		if form.is_valid():
			form.save()
			return redirect('settings')
	else:
		form = SettingForm(instance=settings_instance)

	return render(request, 'compte/settings.html', {
		'title': 'Paramètres',
		'form': form,
		'settings_instance': settings_instance,
	})
