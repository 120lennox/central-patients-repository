from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PatientViewSet,
    PatientAuthViewSet,
    EncounterViewSet,
    ServiceRequestViewSet,
    DiagnosticReportViewSet,
    MedicationRequestViewSet,
    VaccinationViewSet,
    DiagnosticTestViewSet,
    AppointmentViewSet,
    ObservationViewSet,
)

router = DefaultRouter()

# Patient management
router.register(r'patients',  PatientViewSet,      basename='patient')
router.register(r'auth',      PatientAuthViewSet,  basename='patient-otp')

# Clinical record resources
router.register(r'vaccinations',       VaccinationViewSet,       basename='vaccination')
router.register(r'tests',              DiagnosticTestViewSet,    basename='diagnostic-test')
router.register(r'appointments',       AppointmentViewSet,       basename='appointment')
router.register(r'observations',       ObservationViewSet,       basename='observation')
router.register(r'encounters',          EncounterViewSet,         basename='encounter')
router.register(r'service-requests',    ServiceRequestViewSet,    basename='service-request')
router.register(r'diagnostic-reports',  DiagnosticReportViewSet,  basename='diagnostic-report')
router.register(r'medication-requests', MedicationRequestViewSet, basename='medication-request')

urlpatterns = [
    path('', include(router.urls)),
]

