from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    ROLE_CHOICES = [('cashier', 'cashier'), ('manager', 'manager'), ('admin', 'admin')]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')
    must_change_password = models.BooleanField(default=False)
    password_changed = models.BooleanField(default=False)