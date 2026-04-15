from django.urls import path

from .views import EnrollRedirectView

urlpatterns = [
    path('enroll-redirect/', EnrollRedirectView.as_view(), name='sso_gateway_enroll_redirect'),
]
