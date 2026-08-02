from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse
from unittest.mock import patch

from tenancy.models import ClientDatabase, UserClientAccess
from tenancy.services import SESSION_CLIENT_ALIAS_KEY, SESSION_CLIENT_ID_KEY


class TenantTopbarSwitcherTests(TestCase):
    def setUp(self):
        self.client_alpha = ClientDatabase.objects.create(
            slug='alpha',
            name='Alpha',
            db_alias='tenant_alpha',
        )
        self.client_beta = ClientDatabase.objects.create(
            slug='beta',
            name='Beta',
            db_alias='tenant_beta',
        )

    def _create_user(self, username, *, is_superuser=False, is_expert=False):
        user = get_user_model().objects.create_user(
            username=username,
            password='TestPass123!',
            is_superuser=is_superuser,
            is_staff=is_superuser,
        )
        if is_expert:
            expert_group, _ = Group.objects.get_or_create(name='expert')
            user.groups.add(expert_group)
        UserClientAccess.objects.create(user=user, client=self.client_alpha, is_default=True)
        UserClientAccess.objects.create(user=user, client=self.client_beta)
        return user

    def _render_topbar(self, user):
        request = RequestFactory().get('/dashboard/')
        request.user = user
        request.active_client = self.client_beta
        return render_to_string(
            'includes/navigation/app_topbar.html',
            {
                'site_settings': None,
                'working_period': {'tenant_key': 'tenant_beta', 'value': '2026-08'},
            },
            request=request,
        )

    def test_switcher_is_visible_only_to_experts_and_superusers(self):
        users = (
            (self._create_user('superuser', is_superuser=True), True),
            (self._create_user('expert', is_expert=True), True),
            (self._create_user('standard'), False),
        )

        for user, should_see_switcher in users:
            with self.subTest(username=user.username):
                html = self._render_topbar(user)
                self.assertEqual(
                    'class="topbar-tenant-form"' in html,
                    should_see_switcher,
                )
                if should_see_switcher:
                    self.assertIn(
                        f'<option value="{self.client_beta.pk}" selected>',
                        html,
                    )
                    self.assertIn("select.addEventListener('change'", html)
                    self.assertIn('form.requestSubmit()', html)
                    self.assertIn('window.location.reload()', html)

    def test_post_switches_active_tenant_and_returns_to_current_page(self):
        user = self._create_user('switcher', is_superuser=True)
        self.client.force_login(user)

        with patch('tenancy.views.resolve_database_alias', return_value='tenant_beta'):
            response = self.client.post(
                reverse('set_active_client'),
                {
                    'client_id': self.client_beta.pk,
                    'next': '/dashboard/',
                },
            )

        self.assertRedirects(response, '/dashboard/', fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSION_CLIENT_ID_KEY], self.client_beta.pk)
        self.assertEqual(self.client.session[SESSION_CLIENT_ALIAS_KEY], 'tenant_beta')

    def test_async_post_switches_tenant_without_redirecting_to_another_page(self):
        user = self._create_user('async-switcher', is_superuser=True)
        self.client.force_login(user)

        with patch('tenancy.views.resolve_database_alias', return_value='tenant_beta'):
            response = self.client.post(
                reverse('set_active_client'),
                {'client_id': self.client_beta.pk},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'ok': True,
            'client_id': self.client_beta.pk,
            'client_name': self.client_beta.name,
        })
        self.assertEqual(self.client.session[SESSION_CLIENT_ID_KEY], self.client_beta.pk)
        self.assertEqual(self.client.session[SESSION_CLIENT_ALIAS_KEY], 'tenant_beta')