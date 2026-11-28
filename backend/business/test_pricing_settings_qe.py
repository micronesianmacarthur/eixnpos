"""QE black-box tests for the pricing settings API (task t_bfe754c3).

Complements `test_pricing_settings.py` with edge cases written from the
acceptance criteria rather than the implementation: unusual-but-parseable
inputs, write-order interactions, and the JSON-number wire contract the
product form depends on.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import AppSetting
from .services import (
    DEFAULT_TARGET_MARGIN_PERCENT,
    DEFAULT_TARGET_MARGIN_PERCENT_KEY,
    get_default_target_margin_percent,
)

User = get_user_model()

URL = '/api/settings/pricing/'
KEY = DEFAULT_TARGET_MARGIN_PERCENT_KEY


class QePricingSettingsEdgeCaseTests(TestCase):
    """Acceptance-criterion edge cases against the HTTP contract only."""

    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def assert_setting_unchanged(self, expected=Decimal('15')):
        self.assertEqual(get_default_target_margin_percent(), expected)

    def test_default_value_is_fifteen_on_a_fresh_install(self):
        response = self.client.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[KEY], 15)
        self.assert_setting_unchanged()

    def test_update_persists_across_separate_clients(self):
        first = APIClient()
        first.force_authenticate(self.admin)
        response = first.patch(URL, {KEY: 33.33}, format='json')

        self.assertEqual(response.status_code, 200)

        second = APIClient()
        second.force_authenticate(self.admin)
        follow_up = second.get(URL)

        self.assertEqual(follow_up.status_code, 200)
        self.assertEqual(follow_up.data[KEY], 33.33)
        self.assertEqual(AppSetting.objects.get(key=KEY).value, '33.33')

    def test_numeric_string_edge_inputs(self):
        accepted = [
            ('0', 0),
            ('0.00', 0),
            ('100', 100),
            ('100.00', 100),
            ('12.34', 12.34),
            ('.5', 0.5),
        ]
        for raw, expected in accepted:
            with self.subTest(raw=raw):
                response = self.client.patch(URL, {KEY: raw}, format='json')
                self.assertEqual(response.status_code, 200, f'PATCH {raw!r} should be accepted')
                self.assertEqual(response.data[KEY], expected)

    def test_rejected_edge_inputs_leave_the_setting_intact(self):
        rejected = [
            '-0.001',
            '-5',
            '100.001',
            '0x1f',
            '15%',
            '1,5',
            '12.345',
            1e10,
            'Infinity',
            'NaN',
            ['-1'],
            {'value': 15},
        ]
        for raw in rejected:
            with self.subTest(raw=raw):
                # Isolate each probe: start from the documented default.
                self.client.patch(URL, {KEY: 15}, format='json')
                response = self.client.patch(URL, {KEY: raw}, format='json')
                self.assertEqual(response.status_code, 400, f'PATCH {raw!r} should be rejected')
                self.assertIn(KEY, response.data)
                self.assertEqual(get_default_target_margin_percent(), Decimal('15'))

    def test_exponent_notation_is_normalised_rather_than_rejected(self):
        # Observed backend tolerance: '1e2' parses to 100 (in range, 0dp).
        # Pinned so a future tightening of the parser is a conscious choice.
        response = self.client.patch(URL, {KEY: '1e2'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[KEY], 100)

    def test_last_write_wins_between_put_and_patch(self):
        self.client.put(URL, {KEY: 40}, format='json')
        self.client.patch(URL, {KEY: 10}, format='json')

        self.assertEqual(get_default_target_margin_percent(), Decimal('10'))

        self.client.patch(URL, {KEY: 55}, format='json')
        self.client.put(URL, {KEY: 60}, format='json')

        self.assertEqual(get_default_target_margin_percent(), Decimal('60'))

    def test_failed_write_after_successful_write_keeps_saved_value(self):
        self.client.patch(URL, {KEY: 18.5}, format='json')
        bad = self.client.patch(URL, {KEY: 999}, format='json')

        self.assertEqual(bad.status_code, 400)
        self.assertEqual(get_default_target_margin_percent(), Decimal('18.5'))

    def test_wire_format_is_a_json_number_not_a_string(self):
        self.client.patch(URL, {KEY: '21.5'}, format='json')

        raw = self.client.get(URL).content
        self.assertIn(b'21.5', raw)
        self.assertNotIn(b'"21.5"', raw)

    def test_zero_margin_is_a_valid_configuration_not_a_fallback(self):
        # 0 must survive persistence: a fallback-to-15 bug turns 0 into 15.
        self.client.patch(URL, {KEY: 0}, format='json')

        self.assertEqual(get_default_target_margin_percent(), Decimal('0'))
        self.assertEqual(AppSetting.objects.get(key=KEY).value, '0')

    def test_corrupt_row_then_repair(self):
        AppSetting.objects.update_or_create(key=KEY, defaults={'value': 'oops'})
        self.assertEqual(get_default_target_margin_percent(), Decimal('15'))

        self.client.patch(URL, {KEY: 25}, format='json')

        self.assertEqual(get_default_target_margin_percent(), Decimal('25'))
        self.assertEqual(self.client.get(URL).data[KEY], 25)

    def test_default_constant_matches_service_fallback(self):
        # The whole feature leans on 15 being one number in one place.
        self.assertEqual(DEFAULT_TARGET_MARGIN_PERCENT, 15)
