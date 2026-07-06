from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    ExpertSocieteUserCreateForm,
    ExpertUserTenantAssignForm,
    SocieteForm,
    SocieteUserAssignForm,
    SocieteUserCreateForm,
)
from .models import ClientDatabase, Societe, UserClientAccess, UserSocieteAccess
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


@login_required
def manage_societe_users(request):
    is_expert = request.user.groups.filter(name='expert').exists()
    if not (request.user.is_superuser or is_expert):
        messages.error(request, 'Acces reserve aux experts.')
        return redirect('accueil')

    societe_access = UserSocieteAccess.objects.select_related('societe').filter(
        user=request.user,
        societe__is_active=True,
    ).order_by('-is_default', 'societe__name', 'id').first()

    if not societe_access:
        messages.error(request, 'Aucune societe active n est assignee a votre utilisateur.')
        return redirect('accueil')

    managed_societe = societe_access.societe
    user_model = get_user_model()

    societe_user_accesses_qs = UserSocieteAccess.objects.select_related('user').filter(
        societe=managed_societe,
    ).order_by('user__username', 'id')
    societe_user_ids = list(societe_user_accesses_qs.values_list('user_id', flat=True))

    societe_users_qs = user_model.objects.filter(id__in=societe_user_ids).order_by('username', 'id')
    tenants_qs = ClientDatabase.objects.filter(societe=managed_societe).order_by('name', 'id')
    tenant_ids = list(tenants_qs.values_list('id', flat=True))

    create_user_form = ExpertSocieteUserCreateForm()
    assign_tenant_form = ExpertUserTenantAssignForm(
        users_qs=societe_users_qs.filter(is_active=True),
        tenants_qs=tenants_qs.filter(is_active=True),
    )

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'create_societe_user':
            create_user_form = ExpertSocieteUserCreateForm(request.POST)
            if create_user_form.is_valid():
                user = create_user_form.save(managed_societe)
                expert_group, _ = Group.objects.get_or_create(name='expert')
                if create_user_form.cleaned_data.get('is_expert'):
                    user.groups.add(expert_group)
                else:
                    user.groups.remove(expert_group)

                security_state = mark_user_must_change_password(user, True)
                if security_state is None:
                    user.delete()
                    messages.error(request, 'Utilisateur non cree: impossible d activer le changement obligatoire du mot de passe.')
                else:
                    messages.success(request, f"Utilisateur cree: {user.username} (societe: {managed_societe.name})")
                    return redirect('manage_societe_users')

        elif action == 'assign_tenant_access':
            assign_tenant_form = ExpertUserTenantAssignForm(
                request.POST,
                users_qs=societe_users_qs.filter(is_active=True),
                tenants_qs=tenants_qs.filter(is_active=True),
            )
            if assign_tenant_form.is_valid():
                access = assign_tenant_form.save()
                sync_user_client_accesses(access.user)
                messages.success(request, f"Acces tenant ajoute: {access.user.username} -> {access.client.name}")
                return redirect('manage_societe_users')

        elif action == 'toggle_user_expert':
            user_id = request.POST.get('user_id')
            target_user = get_object_or_404(societe_users_qs, pk=user_id)
            should_be_expert = request.POST.get('is_expert') == '1'
            expert_group, _ = Group.objects.get_or_create(name='expert')

            if should_be_expert:
                target_user.groups.add(expert_group)
                messages.success(request, f'Role Expert active pour {target_user.username}.')
            else:
                if target_user.pk == request.user.pk and not request.user.is_superuser:
                    messages.error(request, 'Vous ne pouvez pas retirer votre propre role Expert.')
                else:
                    target_user.groups.remove(expert_group)
                    messages.success(request, f'Role Expert retire pour {target_user.username}.')

            return redirect('manage_societe_users')

        elif action == 'toggle_user_active':
            user_id = request.POST.get('user_id')
            target_user = get_object_or_404(societe_users_qs, pk=user_id)
            should_be_active = request.POST.get('is_active') == '1'

            if target_user.pk == request.user.pk and not should_be_active:
                messages.error(request, 'Vous ne pouvez pas vous desactiver vous-meme.')
            else:
                target_user.is_active = should_be_active
                target_user.save(update_fields=['is_active'])
                sync_user_client_accesses(target_user)
                messages.success(
                    request,
                    f"Utilisateur {target_user.username} {'active' if should_be_active else 'desactive'}.",
                )

            return redirect('manage_societe_users')

        elif action == 'toggle_tenant_active':
            tenant_id = request.POST.get('tenant_id')
            target_tenant = get_object_or_404(tenants_qs, pk=tenant_id)
            should_be_active = request.POST.get('is_active') == '1'
            target_tenant.is_active = should_be_active
            target_tenant.save(update_fields=['is_active'])

            for user in societe_users_qs:
                sync_user_client_accesses(user)

            messages.success(
                request,
                f"Tenant {target_tenant.name} {'active' if should_be_active else 'desactive'}.",
            )
            return redirect('manage_societe_users')

        elif action == 'revoke_tenant_access':
            user_id = request.POST.get('user_id')
            tenant_id = request.POST.get('tenant_id')
            access = get_object_or_404(
                UserClientAccess.objects.select_related('user', 'client'),
                user_id=user_id,
                client_id=tenant_id,
                user_id__in=societe_user_ids,
                client_id__in=tenant_ids,
            )
            target_user = access.user
            access.delete()

            fallback_default = UserClientAccess.objects.filter(user=target_user).order_by('client__name', 'id').first()
            if fallback_default and not UserClientAccess.objects.filter(user=target_user, is_default=True).exists():
                fallback_default.is_default = True
                fallback_default.save(update_fields=['is_default'])

            sync_user_client_accesses(target_user)

            messages.success(request, f'Acces retire: {target_user.username} -> tenant.')
            return redirect('manage_societe_users')

        else:
            messages.error(request, 'Action inconnue.')

    expert_user_ids = set(
        Group.objects.filter(name='expert', user__id__in=societe_user_ids).values_list('user__id', flat=True)
    )
    access_counts = {
        row['user_id']: row['total']
        for row in UserClientAccess.objects.filter(user_id__in=societe_user_ids, client_id__in=tenant_ids)
        .values('user_id')
        .annotate(total=Count('id'))
    }
    tenant_user_counts = {
        row['client_id']: row['total']
        for row in UserClientAccess.objects.filter(user_id__in=societe_user_ids, client_id__in=tenant_ids)
        .values('client_id')
        .annotate(total=Count('user_id', distinct=True))
    }

    societe_users = [
        {
            'user': access.user,
            'is_default_societe': access.is_default,
            'is_expert': access.user_id in expert_user_ids,
            'tenant_access_count': access_counts.get(access.user_id, 0),
        }
        for access in societe_user_accesses_qs
    ]

    tenant_accesses = UserClientAccess.objects.select_related('user', 'client').filter(
        user_id__in=societe_user_ids,
        client_id__in=tenant_ids,
    ).order_by('user__username', 'client__name', 'id')

    tenants = [
        {
            'tenant': tenant,
            'user_count': tenant_user_counts.get(tenant.id, 0),
        }
        for tenant in tenants_qs
    ]

    return render(request, 'tenancy/manage_societe_users.html', {
        'title': 'Gestion des utilisateurs',
        'managed_societe': managed_societe,
        'create_user_form': create_user_form,
        'assign_tenant_form': assign_tenant_form,
        'societe_users': societe_users,
        'tenants': tenants,
        'tenant_accesses': tenant_accesses,
    })
