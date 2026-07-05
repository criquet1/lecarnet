from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import SocieteForm, SocieteUserAssignForm, SocieteUserCreateForm
from .models import Societe, UserSocieteAccess
from .services import get_user_client_accesses, mark_user_must_change_password, set_active_client_on_session, sync_user_client_accesses


@login_required
def select_client(request):
    sync_user_client_accesses(request.user)
    accesses = get_user_client_accesses(request.user)
    if accesses.count() == 1:
        only_access = accesses.first()
        set_active_client_on_session(request, only_access)
        return redirect('accueil')

    return render(request, 'tenancy/select_client.html', {
        'title': 'Choisir un client',
        'accesses': accesses.order_by('client__name'),
    })


@login_required
@require_POST
def set_active_client(request):
    sync_user_client_accesses(request.user)
    accesses = get_user_client_accesses(request.user)
    access = get_object_or_404(accesses, client_id=request.POST.get('client_id'))
    set_active_client_on_session(request, access)
    return redirect(request.POST.get('next') or 'accueil')


@login_required
def manage_societes(request):
    if not request.user.is_superuser:
        messages.error(request, 'Acces reserve aux superusers.')
        return redirect('accueil')

    societe_to_edit = None
    edit_id = (request.GET.get('edit') or request.POST.get('societe_id') or '').strip()
    if edit_id:
        societe_to_edit = Societe.objects.filter(pk=edit_id).first()

    societes_qs = Societe.objects.order_by('name', 'id')
    active_societes_qs = societes_qs.filter(is_active=True)
    users_qs = get_user_model().objects.order_by('username', 'id')

    societe_form = SocieteForm(instance=societe_to_edit)
    user_form = SocieteUserCreateForm(societes_qs=active_societes_qs)
    user_assign_form = SocieteUserAssignForm(societes_qs=active_societes_qs, users_qs=users_qs)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'save_societe':
            societe_form = SocieteForm(request.POST, instance=societe_to_edit)
            user_form = SocieteUserCreateForm(societes_qs=active_societes_qs)
            user_assign_form = SocieteUserAssignForm(societes_qs=active_societes_qs, users_qs=users_qs)
            if societe_form.is_valid():
                societe = societe_form.save()
                messages.success(request, f'Societe enregistree: {societe.name}')
                return redirect('manage_societes')

        elif action == 'create_societe_user':
            user_form = SocieteUserCreateForm(request.POST, societes_qs=active_societes_qs)
            societe_form = SocieteForm(instance=societe_to_edit)
            user_assign_form = SocieteUserAssignForm(societes_qs=active_societes_qs, users_qs=users_qs)
            if user_form.is_valid():
                user = user_form.save()
                expert_group, _ = Group.objects.get_or_create(name='expert')
                if user_form.cleaned_data.get('is_expert'):
                    user.groups.add(expert_group)
                else:
                    user.groups.remove(expert_group)
                security_state = mark_user_must_change_password(user, True)
                if security_state is None:
                    user.delete()
                    messages.error(request, 'Utilisateur non cree: impossible d activer le changement obligatoire du mot de passe.')
                else:
                    societe = user_form.cleaned_data['societe']
                    if not UserSocieteAccess.objects.filter(user=user, is_default=True).exists():
                        UserSocieteAccess.objects.filter(user=user, societe=societe).update(is_default=True)
                    messages.success(request, f"Utilisateur cree: {user.username} (societe: {societe.name})")
                    return redirect('manage_societes')

        elif action == 'assign_existing_user':
            user_assign_form = SocieteUserAssignForm(request.POST, societes_qs=active_societes_qs, users_qs=users_qs)
            societe_form = SocieteForm(instance=societe_to_edit)
            user_form = SocieteUserCreateForm(societes_qs=active_societes_qs)
            if user_assign_form.is_valid():
                access = user_assign_form.save()
                expert_group, _ = Group.objects.get_or_create(name='expert')
                if user_assign_form.cleaned_data.get('is_expert'):
                    access.user.groups.add(expert_group)
                else:
                    access.user.groups.remove(expert_group)
                messages.success(request, f"Utilisateur associe: {access.user.username} -> {access.societe.name}")
                return redirect('manage_societes')

        else:
            messages.error(request, 'Action inconnue.')

    societes = societes_qs
    user_societe_accesses = UserSocieteAccess.objects.select_related('user', 'societe').order_by('user__username', 'societe__name', 'id')
    return render(request, 'tenancy/manage_societes.html', {
        'title': 'Gestion des societes',
        'societe_form': societe_form,
        'user_form': user_form,
        'user_assign_form': user_assign_form,
        'societes': societes,
        'societe_to_edit': societe_to_edit,
        'user_societe_accesses': user_societe_accesses,
    })
