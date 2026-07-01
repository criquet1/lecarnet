import csv
import io
from pathlib import Path
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from django.db.utils import OperationalError, ProgrammingError
from django.utils.connection import ConnectionDoesNotExist

from facture.models import Compagnie, Setting


TAX_AUTHORITY_COMPANY_TPS = 'Revenu Canada TPS'
TAX_AUTHORITY_COMPANY_TVQ = 'Revenu Quebec TVQ'
TAX_AUTHORITY_COMPANY_NAMES = [
	TAX_AUTHORITY_COMPANY_TPS,
	TAX_AUTHORITY_COMPANY_TVQ,
]


def get_available_logos():
	logos_dir = Path(settings.BASE_DIR) / 'static' / 'images' / 'logos'
	allowed_ext = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'}

	if logos_dir.exists():
		logo_files = sorted(
			p.name for p in logos_dir.iterdir()
			if p.is_file() and p.suffix.lower() in allowed_ext
		)
		if logo_files:
			return logo_files

	return ['images.png']


def is_expert(user):
	return user.is_superuser or user.groups.filter(name__iexact='expert').exists()


def expert_required(view_func):
	@wraps(view_func)
	@login_required
	def _wrapped(request, *args, **kwargs):
		if not is_expert(request.user):
			raise PermissionDenied("Accès réservé aux experts.")
		return view_func(request, *args, **kwargs)

	return _wrapped


def parse_decimal(raw_value, *, strip_spaces=False, none_if_blank=False):
	text = str(raw_value or '').strip()
	if strip_spaces:
		text = text.replace(' ', '')
	if not text:
		return None if none_if_blank else Decimal('0')
	try:
		return Decimal(text.replace(',', '.'))
	except (InvalidOperation, AttributeError, ValueError):
		return None


def split_debit_credit(amount):
	amount = amount or Decimal('0')
	if amount >= 0:
		return amount, Decimal('0')
	return Decimal('0'), abs(amount)

def get_settings():
	try:
		return Setting.objects.select_related(
			'cap',
			'car',
			'compte_tps_percue',
			'compte_tps_payee',
			'compte_tvq_percue',
			'compte_tvq_payee',
			'compte_fr_retard',
		).first()
	except (OperationalError, ProgrammingError, ConnectionDoesNotExist):
		return None


def get_setting(*select_related_fields):
	queryset = Setting.objects
	if select_related_fields:
		queryset = queryset.select_related(*select_related_fields)
	return queryset.first()


def tax_target_mode_from_setting(settings_instance):
	if not settings_instance or settings_instance.taxes_mode == Setting.TAX_MODE_RECLAMER:
		return Compagnie.MODE_CAR
	return Compagnie.MODE_CAP


def ensure_tax_authority_companies(settings_instance=None):
	settings_instance = settings_instance or get_setting()
	target_mode = tax_target_mode_from_setting(settings_instance)

	for company_name in TAX_AUTHORITY_COMPANY_NAMES:
		compagnie, created = Compagnie.objects.get_or_create(
			nom=company_name,
			defaults={
				'logo': 'images.png',
				'cap_ou_car': target_mode,
			},
		)
		if not created and compagnie.cap_ou_car != target_mode:
			compagnie.cap_ou_car = target_mode
			compagnie.save(update_fields=['cap_ou_car'])


def decode_csv_bytes(raw_bytes):
	for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
		try:
			return raw_bytes.decode(encoding)
		except UnicodeDecodeError:
			continue
	raise UnicodeDecodeError('csv', b'', 0, 1, 'Encodage non supporte')


def read_csv_rows(raw_bytes):
	text = decode_csv_bytes(raw_bytes)
	sample = text[:4096]
	delimiter = ','
	try:
		dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
		delimiter = dialect.delimiter
	except csv.Error:
		delimiter = ','

	stream = io.StringIO(text)
	reader = csv.DictReader(stream, delimiter=delimiter)
	return list(reader)