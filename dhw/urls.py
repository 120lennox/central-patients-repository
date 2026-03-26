from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PatientViewSet, PatientAuthViewSet

router = DefaultRouter()
router.register(r'patients', PatientViewSet, basename='patient')
router.register(r'auth', PatientAuthViewSet, basename='patient-otp')

urlpatterns = [
    path('', include(router.urls)),
]