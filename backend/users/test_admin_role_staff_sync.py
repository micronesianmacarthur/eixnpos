"""Regression tests: the Django admin change form syncs ``role`` -> ``is_staff``.

Follow-up S3 from review t_0d5800d0 (approve of 182059a). The API, seed and
post-migrate seed paths derive ``is_staff`` from ``role`` via
``accounts.roles.apply_role_to_staff``; the last un-synced write path was the
Django admin change form (``users.admin.UserAdmin``). These tests pin the same
invariant through that admin path:

* demoting an admin to a non-admin role via ``UserAdmin.save_model`` clears
  ``is_staff`` and the account loses ``/admin/``,
* promoting a cashier to admin via the same path sets ``is_staff`` and grants
  ``/admin/``,
* a superuser keeps ``is_staff`` through a role change (superusers are always
  staff), and
* a real ``admin.site`` change-form POST through
  ``/admin/accounts/user/<id>/change/`` exhibits the same behaviour.

The ModelAdmin-level ``save_model`` assertions (rather than only full admin
POSTs) give a hermetic check of the hook itself: a full admin POST depends on
the whole admin form/frame stack, and the superuser-created users under test
here have no explicit staff permissions, so the change-form render is checked
independently of the model save. The final test exercises the actual
``admin.site`` POST end-to-end so the registered ModelAdmin is covered too.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test import Client
from django.contrib import admin

User = get_user_model()


class AdminRoleStaffSyncTests(TestCase):
    def setUp(self):
        # A superuser is the only operator who can edit users in the admin.
        self.operator = User.objects.create_user(
            username='root', password='Str0ngPass1',
            role='admin', is_superuser=True, is_staff=True,
        )
        self.client = Client()
        self.client.force_login(self.operator)

    def _save_model(self, user, change=True):
        """Drive UserAdmin.save_model the way the change view would."""
        model_admin = admin.site._registry[User]
        # request is only used for message-triggering in a few ModelAdmins; a
        # minimal object with a POST attribute is enough here.
        request = type('Req', (), {'POST': {}})()
        model_admin.save_model(request, user, form=None, change=change)
        user.refresh_from_db()
        return user

    def _admin_index_status(self, user):
        client = Client()
        client.force_login(user)
        return client.get('/admin/').status_code

    def test_demote_via_save_model_clears_staff_and_loses_admin(self):
        admin = User.objects.create_user(
            username='adm1', password='***', role='admin', is_staff=True
        )
        self.assertTrue(admin.is_staff)

        admin.role = 'cashier'
        self._save_model(admin)

        self.assertEqual(admin.role, 'cashier')
        self.assertFalse(admin.is_staff)
        self.assertEqual(self._admin_index_status(admin), 302)

    def test_promote_via_save_model_sets_staff_and_gains_admin(self):
        cashier = User.objects.create_user(
            username='cash1', password='***', role='cashier'
        )
        self.assertFalse(cashier.is_staff)

        cashier.role = 'admin'
        self._save_model(cashier)

        self.assertEqual(cashier.role, 'admin')
        self.assertTrue(cashier.is_staff)
        self.assertEqual(self._admin_index_status(cashier), 200)

    def test_superuser_keeps_staff_through_save_model_role_change(self):
        su = User.objects.create_user(
            username='su1', password='***', role='admin',
            is_superuser=True, is_staff=True,
        )
        self.assertTrue(su.is_staff)

        su.role = 'cashier'  # superusers are never demoted off the backend
        self._save_model(su)

        self.assertEqual(su.role, 'cashier')
        self.assertTrue(su.is_staff)  # never cleared for a superuser
        self.assertTrue(su.is_superuser)
        self.assertEqual(self._admin_index_status(su), 200)

    def test_save_model_forces_staff_from_role_even_if_checkbox_unchecked(self):
        # The is_staff checkbox is left in the form (see UserAdmin docstring)
        # but is non-authoritative: promoting to admin grants staff even when
        # the operator leaves the box unchecked.
        cashier = User.objects.create_user(
            username='cash2', password='***', role='cashier'
        )
        cashier.role = 'admin'
        cashier.is_staff = False  # operator left the box unchecked
        self._save_model(cashier)

        self.assertTrue(cashier.is_staff)

    def test_admin_site_change_post_demotes_and_revokes_admin(self):
        # Build a cashier, then demote them through the real admin change form.
        target = User.objects.create_user(
            username='op1', password='Str0ngPass1', role='admin', is_staff=True
        )
        self.assertTrue(target.is_staff)

        url = f"/admin/accounts/user/{target.id}/change/"
        response = self.client.post(url, self._change_form_data(target, role='cashier'))
        # The change view redirects back to the changelist on success.
        self.assertEqual(response.status_code, 302)

        target.refresh_from_db()
        self.assertEqual(target.role, 'cashier')
        self.assertFalse(target.is_staff)

        # The demoted user's own session no longer reaches the admin index.
        demoted = Client()
        demoted.force_login(target)
        self.assertEqual(demoted.get('/admin/').status_code, 302)

    def test_admin_site_change_post_promote_grants_admin(self):
        target = User.objects.create_user(
            username='op2', password='Str0ngPass1', role='cashier'
        )
        self.assertFalse(target.is_staff)

        url = f"/admin/accounts/user/{target.id}/change/"
        response = self.client.post(url, self._change_form_data(target, role='admin'))
        self.assertEqual(response.status_code, 302)

        target.refresh_from_db()
        self.assertEqual(target.role, 'admin')
        self.assertTrue(target.is_staff)

    # helpers ----------------------------------------------------------------

    def _change_form_data(self, user, role):
        """Complete AdminUserChangeForm POST data to toggle *role* on *user*.

        Covers every field in BaseUserAdmin.fieldsets + the app's Role fieldset.
        The password is a read-only hash field in the change form, so a bogus
        non-empty value keeps the form valid without resetting the password.
        """
        return {
            'username': user.username,
            'password': 'x',  # read-only hashed field placeholder
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email or '',
            'is_active': True,
            'is_superuser': user.is_superuser,
            'is_staff': user.is_staff,
            'groups': [],
            'user_permissions': [],
            'role': role,
            'must_change_password': user.must_change_password,
            'password_changed': user.password_changed,
            'last_login_0': '',
            'last_login_1': '',
            'date_joined_0': user.date_joined.strftime('%Y-%m-%d'),
            'date_joined_1': user.date_joined.strftime('%H:%M:%S'),
        }
