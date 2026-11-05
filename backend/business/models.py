from django.db import models


class BusinessInfo(models.Model):
    company_name = models.CharField(max_length=200, blank=True, default='')
    phone = models.CharField(max_length=50, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    address = models.TextField(blank=True, default='')
    website = models.URLField(blank=True, default='')
    singleton = models.BooleanField(default=True, unique=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(singleton=True)
        return obj

    def is_complete(self):
        return bool(self.company_name and self.phone and self.email)