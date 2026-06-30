from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from compte.models import Compte, Total
from facture.models import Compagnie, Setting, Tr_desc, Tr_detail
from tenancy.models import ClientDatabase, UserClientAccess
from tenancy.services import SESSION_CLIENT_ALIAS_KEY, SESSION_CLIENT_ID_KEY


class FactureMultiTenantTests(TestCase):
	databases = "__all__"

	def _tenant_aliases(self):
		return [alias for alias in settings.DATABASES.keys() if alias != 'default']

	def _build_compte(self, alias, numero, libelle, no_total='A'):
		if isinstance(no_total, Total):
			total = no_total
		else:
			total, _ = Total.objects.using(alias).get_or_create(
				no_total=0,
				defaults={'desc': 'Total test'},
			)
		return Compte.objects.using(alias).create(numero=numero, libelle=libelle, no_total=total)

	def test_post_facture_is_saved_on_active_tenant_only(self):
		tenant_aliases = self._tenant_aliases()
		if len(tenant_aliases) < 2:
			self.skipTest("Ce test requiert au moins deux alias tenant configures.")

		active_alias = tenant_aliases[0]
		other_alias = tenant_aliases[1]

		# Setup central auth/access data.
		user_model = get_user_model()
		user = user_model.objects.create_user(username='mt_admin', password='Pass1234!')
		active_client = ClientDatabase.objects.create(
			slug='client-active-test',
			name='Client Active Test',
			db_alias=active_alias,
			is_active=True,
		)
		UserClientAccess.objects.create(user=user, client=active_client, is_default=True)

		# Setup active tenant accounting/config data.
		total = Total.objects.using(active_alias).create(no_total=0, desc='Total test')
		cap = self._build_compte(active_alias, 2150, 'Compte CAP', no_total=total)
		car = self._build_compte(active_alias, 1200, 'Compte CAR', no_total=total)
		tps_percue = self._build_compte(active_alias, 1250, 'TPS percue', no_total=total)
		tvq_percue = self._build_compte(active_alias, 1270, 'TVQ percue', no_total=total)
		tps_payee = self._build_compte(active_alias, 1240, 'TPS payee', no_total=total)
		tvq_payee = self._build_compte(active_alias, 1260, 'TVQ payee', no_total=total)
		fr_retard = self._build_compte(active_alias, 5865, 'Frais de retard', no_total=total)
		vente = self._build_compte(active_alias, 4100, 'Ventes', no_total=total)

		Setting.objects.using(active_alias).create(
			nom='Client Active',
			logo='images.png',
			adresse='1 rue Test',
			ville='Quebec',
			code_postal='G1G1G1',
			pays='Canada',
			phone='4180000000',
			email='active@example.com',
			annee_financiere=date(2000, 12, 31),
			cap=cap,
			car=car,
			compte_tps_percue=tps_percue,
			compte_tps_payee=tps_payee,
			compte_tvq_percue=tvq_percue,
			compte_tvq_payee=tvq_payee,
			compte_fr_retard=fr_retard,
		)

		compagnie = Compagnie.objects.using(active_alias).create(
			nom='Compagnie Test',
			logo='images.png',
			cap_ou_car=Compagnie.MODE_CAP,
		)

		before_active = Tr_desc.objects.using(active_alias).count()
		before_other = Tr_desc.objects.using(other_alias).count()

		self.client.force_login(user)
		session = self.client.session
		session[SESSION_CLIENT_ID_KEY] = active_client.id
		session[SESSION_CLIENT_ALIAS_KEY] = active_alias
		session.save()

		response = self.client.post(reverse('facture'), data={
			'action': 'add_tr_desc',
			'selected_company_id': str(compagnie.id),
			'editing_tr_desc_id': '',
			'facture_total': '100.00',
			'trdesc-date': '2026-06-01',
			'trdesc-description': 'FACT-TEST-MT',
			'detail-TOTAL_FORMS': '1',
			'detail-INITIAL_FORMS': '0',
			'detail-MIN_NUM_FORMS': '0',
			'detail-MAX_NUM_FORMS': '1000',
			'detail-0-compte': str(vente.pk),
			'detail-0-montant': '100.00',
		})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(Tr_desc.objects.using(active_alias).count(), before_active + 1)
		self.assertEqual(Tr_desc.objects.using(other_alias).count(), before_other)

		created = Tr_desc.objects.using(active_alias).order_by('-id').first()
		self.assertIsNotNone(created)
		details = list(Tr_detail.objects.using(active_alias).filter(tr_desc=created).order_by('id'))
		self.assertGreaterEqual(len(details), 2)

		comptes = {d.compte_id: d.montant for d in details}
		self.assertIn(vente.pk, comptes)
		self.assertEqual(comptes[vente.pk], Decimal('100.00'))
		self.assertIn(cap.pk, comptes)
		self.assertEqual(comptes[cap.pk], Decimal('-100.00'))
