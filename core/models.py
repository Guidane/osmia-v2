from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse


class Image(models.Model):
    """A picture attached to any record (a part, a task, a user, ...).

    A module opts in by giving its model ``images = GenericRelation('core.Image')``;
    pages then show them with ``{% image_gallery record %}`` (osmia_images).
    The first image (lowest position) is the record's cover.
    """
    content_type = models.ForeignKey('contenttypes.ContentType', on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey('content_type', 'object_id')
    file = models.ImageField(upload_to='images/%Y/%m/')
    thumb = models.ImageField(upload_to='images/thumbs/%Y/%m/')
    width = models.PositiveIntegerField(default=0, editable=False)
    height = models.PositiveIntegerField(default=0, editable=False)
    caption = models.CharField(max_length=200, blank=True)
    position = models.IntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'id']
        indexes = [models.Index(fields=['content_type', 'object_id'])]

    def __str__(self):
        return self.caption or f'Image {self.pk}'

    def audit_record(self):
        return self.record  # logged in the module of the part, task, ... it belongs to

    @property
    def url(self):
        return reverse('core:image', args=[self.pk])

    @property
    def thumb_url(self):
        return reverse('core:image', args=[self.pk]) + '?thumb=1'


@receiver(post_delete, sender=Image, dispatch_uid='core-image-delete-files')
def delete_image_files(sender, instance, **kwargs):
    # Only once the delete is committed: a rolled-back delete keeps its files.
    files = [f for f in (instance.file, instance.thumb) if f]
    transaction.on_commit(lambda: [f.delete(save=False) for f in files])
