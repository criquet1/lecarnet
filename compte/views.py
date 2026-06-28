from functools import wraps
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from facture.models import Setting

from .forms import CompteForm, SettingForm
from .models import Compte, SoldeAuxLivres


def _is_expert(user):
	return user.is_superuser or user.groups.filter(name__iexact='expert').exists()


def expert_required(view_func):
	@wraps(view_func)
	@login_required
	def _wrapped(request, *args, **kwargs):
		if not _is_expert(request.user):
			raise PermissionDenied("Accès réservé aux experts.")
		return view_func(request, *args, **kwargs)

	return _wrapped


@expert_required
def compte_page(request):
	edit_numero = (request.GET.get('edit') or request.POST.get('editing_numero') or '').strip()
	editing_compte = Compte.objects.filter(pk=edit_numero).first() if edit_numero else None

	if request.method == 'POST' and request.POST.get('inline_solde_compte'):
		compte_id = (request.POST.get('inline_solde_compte') or '').strip()
		raw_value = (request.POST.get('solde_depart') or '').strip()
		compte = Compte.objects.filter(pk=compte_id).first() if compte_id else None

		if not compte:
			return JsonResponse({'ok': False, 'error': 'Compte introuvable.'}, status=404)

		try:
			solde_depart = Decimal((raw_value or '0').replace(',', '.'))
		except (InvalidOperation, ValueError):
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

	if request.method == 'POST':
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

	comptes = Compte.objects.select_related('no_total').order_by('numero')
	soldes_par_compte = {compte.pk: Decimal('0') for compte in comptes}
	for solde in SoldeAuxLivres.objects.select_related('compte'):
		soldes_par_compte[solde.compte_id] = solde.solde_depart

	return render(request, 'compte/compte.html', {
		'title': 'Comptes',
		'form': form,
		'comptes': comptes,
		'editing_compte': editing_compte,
		'soldes_par_compte': soldes_par_compte,
	})


@expert_required
def settings_page(request):
	settings_instance = Setting.objects.first()

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
