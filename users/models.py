from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.urls import reverse


class User(AbstractUser):
    job_title = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    images = GenericRelation('core.Image')  # pictures, shown with {% image_gallery %}

    class Meta:
        ordering = ['first_name', 'last_name', 'username']

    def __str__(self):
        return self.get_full_name() or self.username

    def get_absolute_url(self):
        return reverse('users:detail', args=[self.pk])

    def images_editable_by(self, user):
        """Your photos are yours to change, and managers'."""
        return user == self or user.has_perm('users.change_user')
