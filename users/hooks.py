from django.urls import reverse

from core import hooks

from .models import User


@hooks.register('dashboard_widgets')
def active_users(request):
    return hooks.Widget('Active users', User.objects.filter(is_active=True).count(), reverse('users:list'))
