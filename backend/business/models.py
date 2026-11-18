from django.db import models


class BusinessInfo(models.Model):
    # The live database table was renamed out-of-band from the Django default
    # ``business_businessinfo`` to ``business_info``. Map the model to the
    # renamed table so queries hit it (see migration 0003_rename_businessinfo_table).
    class Meta:
        db_table = 'business_info'

    company_name = models.CharField(max_length=200, blank=True, default='')
    phone = models.CharField(max_length=50, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    address = models.TextField(blank=True, default='')
    website = models.URLField(blank=True, default='')
    backup_path = models.CharField(max_length=500, blank=True, default='')
    timezone = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text='IANA timezone for the business day (e.g. America/Los_Angeles). Empty = UTC.',
    )
    singleton = models.BooleanField(default=True, unique=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(singleton=True)
        return obj

    def is_complete(self):
        return bool(self.company_name and self.phone and self.email)