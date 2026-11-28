from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import AppSetting
from .services import (
    DEFAULT_TARGET_MARGIN_PERCENT_KEY,
    DEFAULT_TARGET_MARGIN_PERCENT,
    get_default_target_margin_percent,
    normalize_target_margin_percent,
    set_default_target_margin_percent,
)

User = get_user_model()

URL = '/api/settings/pricing/'
KEY = DEFAULT_TARGET_MARGIN_PERCENT_KEY


class DefaultTargetMarginServiceTests(TestCase):
    def test_fresh_install_returns_fifteen(self):
        # The migration seeds the row, and the service reports 15 on a fresh DB.
        self.assertEqual(get_default_target_margin_percent(), Decimal('15'))

    def test_missing_row_falls_back_to_fifteen(self):
        AppSetting.objects.filter(key=KEY).delete()

        self.assertEqual(get_default_target_margin_percent(), Decimal('15'))

    def test_corrupt_stored_value_falls_back_to_fifteen(self):
        AppSetting.objects.update_or_create(key=KEY, defaults={'value': 'not-a-number'})

        self.assertEqual(get_default_target_margin_percent(), Decimal('15'))

    def test_out_of_range_stored_value_falls_back_to_fifteen(self):
        AppSetting.objects.update_or_create(key=KEY, defaults={'value': '250'})

        self.assertEqual(get_default_target_margin_percent(), Decimal('15'))

    def test_set_persists_and_reads_back(self):
        value = set_default_target_margin_percent('12.5')

        self.assertEqual(value, Decimal('12.5'))
        self.assertEqual(get_default_target_margin_percent(), Decimal('12.5'))
        self.assertEqual(AppSetting.objects.get(key=KEY).value, '12.5')

    def test_set_normalizes_trailing_zeros(self):
        set_default_target_margin_percent('20.00')

        self.assertEqual(AppSetting.objects.get(key=KEY).value, '20')

    def test_normalize_rejects_non_numeric_and_out_of_range(self):
        invalid = [
            None,
            True,
            False,
            '',
            '   ',
            'abc',
            'NaN',
            'Infinity',
            [],
            {},
            -0.01,
            100.01,
            101,
            '12.345',
        ]

        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_target_margin_percent(raw)

    def test_normalize_accepts_bounds_and_numeric_strings(self):
        self.assertEqual(normalize_target_margin_percent(0), Decimal('0'))
        self.assertEqual(normalize_target_margin_percent('100'), Decimal('100'))
        self.assertEqual(normalize_target_margin_percent(15), Decimal('15'))
        self.assertEqual(normalize_target_margin_percent('12.50'), Decimal('12.5'))


class PricingSettingsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_get_returns_default_fifteen(self):
        response = self.client.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {KEY: 15})

    def test_get_falls_back_to_fifteen_without_a_stored_row(self):
        AppSetting.objects.filter(key=KEY).delete()

        response = self.client.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {KEY: 15})

    def test_patch_updates_persists_and_is_returned_by_later_reads(self):
        response = self.client.patch(URL, {KEY: 20}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {KEY: 20})
        self.assertEqual(AppSetting.objects.get(key=KEY).value, '20')

        # A new client (fresh request cycle) still sees the persisted value.
        later = APIClient()
        later.force_authenticate(self.user)
        self.assertEqual(later.get(URL).data, {KEY: 20})

    def test_put_updates_the_value(self):
        response = self.client.put(URL, {KEY: 25}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {KEY: 25})
        self.assertEqual(get_default_target_margin_percent(), Decimal('25'))

    def test_patch_accepts_numeric_strings(self):
        response = self.client.patch(URL, {KEY: '18.5'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {KEY: 18.5})

    def test_patch_accepts_range_boundaries(self):
        for value in (0, 100):
            with self.subTest(value=value):
                response = self.client.patch(URL, {KEY: value}, format='json')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, {KEY: value})

    def test_invalid_values_are_rejected_and_do_not_change_the_setting(self):
        invalid = [None, True, '', 'abc', -1, 101, 12.345]

        for value in invalid:
            with self.subTest(value=value):
                response = self.client.patch(URL, {KEY: value}, format='json')

                self.assertEqual(response.status_code, 400)
                self.assertIn(KEY, response.data)
                self.assertTrue(response.data[KEY])
                self.assertEqual(get_default_target_margin_percent(), DEFAULT_TARGET_MARGIN_PERCENT)

    def test_missing_field_is_rejected(self):
        response = self.client.patch(URL, {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn(KEY, response.data)

    def test_unknown_key_is_rejected(self):
        response = self.client.patch(URL, {KEY: 20, 'surprise': 1}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(get_default_target_margin_percent(), DEFAULT_TARGET_MARGIN_PERCENT)

    def test_non_object_body_is_rejected(self):
        response = self.client.patch(URL, [KEY], format='json')

        self.assertEqual(response.status_code, 400)

    def test_requires_authentication(self):
        anonymous = APIClient()

        self.assertIn(anonymous.get(URL).status_code, (401, 403))
        self.assertIn(anonymous.patch(URL, {KEY: 20}, format='json').status_code, (401, 403))


class PricingSettingsAuthorizationTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')

    def test_cashier_can_read_the_margin(self):
        client = APIClient()
        client.force_authenticate(self.cashier)

        response = client.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {KEY: 15})

    def test_cashier_cannot_update_the_margin(self):
        client = APIClient()
        client.force_authenticate(self.cashier)

        response = client.patch(URL, {KEY: 50}, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(get_default_target_margin_percent(), DEFAULT_TARGET_MARGIN_PERCENT)

    def test_manager_can_update_the_margin(self):
        client = APIClient()
        client.force_authenticate(self.manager)

        response = client.patch(URL, {KEY: 22}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {KEY: 22})
