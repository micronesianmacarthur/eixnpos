"""Unit tests for the single source of truth: ``accounts.roles``.

These pin down the predicate / derivation semantics the rest of the app relies
on: manager/admin tiers are decided by ``role`` (plus a superuser bypass), and
``apply_role_to_staff`` derives ``is_staff`` on every role write, never clearing
it for a superuser.
"""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase

from accounts.roles import (
    apply_role_to_staff,
    has_admin_role,
    has_manager_role,
)

User = get_user_model()


class HasRolePredicateTests(SimpleTestCase):
    def test_none_and_unauthenticated_are_denied(self):
        self.assertFalse(has_manager_role(None))
        self.assertFalse(has_admin_role(None))

    def test_cashier_has_neither_tier(self):
        user = User(username='c', role='cashier', is_staff=False, is_superuser=False)
        self.assertFalse(has_manager_role(user))
        self.assertFalse(has_admin_role(user))

    def test_manager_has_manager_tier_only(self):
        user = User(username='m', role='manager')
        self.assertTrue(has_manager_role(user))
        self.assertFalse(has_admin_role(user))

    def test_admin_has_both_tiers(self):
        user = User(username='a', role='admin')
        self.assertTrue(has_manager_role(user))
        self.assertTrue(has_admin_role(user))

    def test_staff_flag_alone_grants_no_tier(self):
        # Demotion invariant: a stale is_staff flag must not grant authority.
        user = User(username='s', role='cashier', is_staff=True, is_superuser=False)
        self.assertFalse(has_manager_role(user))
        self.assertFalse(has_admin_role(user))

    def test_superuser_bypasses_both_tiers_regardless_of_role(self):
        user = User(username='u', role='cashier', is_superuser=True)
        self.assertTrue(has_manager_role(user))
        self.assertTrue(has_admin_role(user))


class ApplyRoleToStaffTests(SimpleTestCase):
    def test_admin_role_becomes_staff(self):
        user = User(username='a', role='admin', is_staff=False)
        result = apply_role_to_staff(user)
        self.assertIs(result, user)
        self.assertTrue(user.is_staff)

    def test_non_admin_role_clears_staff(self):
        user = User(username='m', role='manager', is_staff=True)
        apply_role_to_staff(user)
        self.assertFalse(user.is_staff)

    def test_superuser_keeps_staff_when_role_changes(self):
        user = User(username='u', role='admin', is_staff=True, is_superuser=True)
        user.is_staff = True  # superusers are always staff in Django
        user.role = 'cashier'
        apply_role_to_staff(user)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_superuser_even_with_staff_cleared_is_restored_to_staff(self):
        # Simulates a row where a superuser's is_staff was incorrectly cleared.
        user = User(username='u', role='cashier', is_staff=False, is_superuser=True)
        apply_role_to_staff(user)
        # Django superusers are always staff, so a role change restores it.
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
