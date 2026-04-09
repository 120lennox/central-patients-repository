from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import (
    Patient,
    Encounter,
    ServiceRequest,
    DiagnosticReport,
    DiagnosticObservation,
    MedicationRequest,
    MedicationDispense,
)
from .serializers import (
    EncounterSerializer,
    ServiceRequestSerializer,
    DiagnosticReportSerializer,
    DiagnosticObservationSerializer,
    MedicationRequestSerializer,
    MedicationDispenseSerializer,
)

# Re-use the same permission classes from the dhw app
from dhw.permissions import IsAdmin, IsClinician, IsAdminOrClinician, IsPatientOrClinician


# =============================================================================
# Encounter ViewSet  (FHIR: Encounter)
# =============================================================================

class EncounterViewSet(viewsets.ModelViewSet):
    """
    FHIR R4 Encounter resource.

    list      GET  /encounters/                   Clinician / Admin
    create    POST /encounters/                   Clinician / Admin
    retrieve  GET  /encounters/{id}/              Clinician / Admin / Patient (own)
    update    PUT  /encounters/{id}/              Clinician / Admin
    destroy   DELETE /encounters/{id}/            Admin only (soft delete)

    Nested actions:
      GET  /encounters/{id}/service-requests/     All lab requests for this encounter
      GET  /encounters/{id}/medication-requests/  All prescriptions for this encounter
    """

    serializer_class = EncounterSerializer
    lookup_field = 'id'

    def get_queryset(self):
        qs = Encounter.objects.select_related('patient').filter(
            patient__active=True
        )

        # Filter by patient UUID if ?patient=<uuid> supplied
        patient_id = self.request.query_params.get('patient')
        if patient_id:
            qs = qs.filter(patient__id=patient_id)

        # Filter by clinician if ?clinician=<uuid> supplied
        clinician_id = self.request.query_params.get('clinician')
        if clinician_id:
            qs = qs.filter(clinician_id=clinician_id)

        # Filter by status if ?status=<status> supplied
        enc_status = self.request.query_params.get('status')
        if enc_status:
            qs = qs.filter(status=enc_status)

        return qs.order_by('-visit_date')

    def get_permissions(self):
        if self.action in ('list', 'retrieve', 'service_requests', 'medication_requests'):
            return [IsPatientOrClinician()]
        if self.action == 'destroy':
            return [IsAdmin()]
        return [IsAdminOrClinician()]

    # LIST — FHIR Bundle
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "searchset",
            "total": queryset.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })

    # CREATE
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        encounter = serializer.save()
        return Response(
            serializer.to_representation(encounter),
            status=status.HTTP_201_CREATED,
        )

    # DESTROY — soft delete (set status = cancelled)
    def destroy(self, request, *args, **kwargs):
        encounter = self.get_object()
        encounter.status = 'cancelled'
        encounter.save()
        return Response(
            {"detail": "Encounter cancelled successfully."},
            status=status.HTTP_200_OK,
        )

    # Nested: GET /encounters/{id}/service-requests/
    @action(detail=True, methods=['get'], url_path='service-requests')
    def service_requests(self, request, id=None):
        encounter = self.get_object()
        qs = encounter.service_requests.select_related('patient').all()
        serializer = ServiceRequestSerializer(qs, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "collection",
            "total": qs.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })

    # Nested: GET /encounters/{id}/medication-requests/
    @action(detail=True, methods=['get'], url_path='medication-requests')
    def medication_requests(self, request, id=None):
        encounter = self.get_object()
        qs = encounter.medication_requests.select_related('patient').prefetch_related('dispenses').all()
        serializer = MedicationRequestSerializer(qs, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "collection",
            "total": qs.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })


# =============================================================================
# ServiceRequest ViewSet  (FHIR: ServiceRequest)
# =============================================================================

class ServiceRequestViewSet(viewsets.ModelViewSet):
    """
    FHIR R4 ServiceRequest resource.

    list      GET  /service-requests/             Clinician / Admin
    create    POST /service-requests/             Clinician / Admin
    retrieve  GET  /service-requests/{id}/        Clinician / Admin
    update    PUT  /service-requests/{id}/        Clinician / Admin
    destroy   DELETE /service-requests/{id}/      Admin only

    Nested actions:
      GET/POST  /service-requests/{id}/diagnostic-report/
    """

    serializer_class = ServiceRequestSerializer
    lookup_field = 'id'

    def get_queryset(self):
        qs = ServiceRequest.objects.select_related('patient', 'encounter').filter(
            patient__active=True
        )

        patient_id = self.request.query_params.get('patient')
        if patient_id:
            qs = qs.filter(patient__id=patient_id)

        encounter_id = self.request.query_params.get('encounter')
        if encounter_id:
            qs = qs.filter(encounter__id=encounter_id)

        req_status = self.request.query_params.get('status')
        if req_status:
            qs = qs.filter(status=req_status)

        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)

        return qs.order_by('-request_date')

    def get_permissions(self):
        if self.action == 'destroy':
            return [IsAdmin()]
        return [IsAdminOrClinician()]

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "searchset",
            "total": queryset.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service_request = serializer.save()
        return Response(
            serializer.to_representation(service_request),
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        service_request = self.get_object()
        service_request.status = 'revoked'
        service_request.save()
        return Response(
            {"detail": "ServiceRequest revoked successfully."},
            status=status.HTTP_200_OK,
        )

    # Nested: GET /service-requests/{id}/diagnostic-report/
    @action(detail=True, methods=['get'], url_path='diagnostic-report')
    def diagnostic_report(self, request, id=None):
        service_request = self.get_object()
        try:
            report = service_request.diagnostic_report
        except DiagnosticReport.DoesNotExist:
            return Response(
                {"detail": "No diagnostic report filed for this service request yet."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = DiagnosticReportSerializer(report)
        return Response(serializer.to_representation(report))

    # Nested: POST /service-requests/{id}/diagnostic-report/
    @action(detail=True, methods=['post'], url_path='diagnostic-report/file',
            url_name='file-diagnostic-report')
    def file_diagnostic_report(self, request, id=None):
        """File a DiagnosticReport against this ServiceRequest."""
        service_request = self.get_object()

        # Guard: only one report per service request
        if hasattr(service_request, 'diagnostic_report'):
            return Response(
                {"detail": "A diagnostic report already exists for this service request."},
                status=status.HTTP_409_CONFLICT,
            )

        # Inject the basedOn reference so callers don't have to repeat it
        data = request.data.copy()
        data.setdefault('basedOn', [{'reference': f'ServiceRequest/{service_request.id}'}])

        serializer = DiagnosticReportSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        report = serializer.save()

        # Mark ServiceRequest as completed
        service_request.status = 'completed'
        service_request.save()

        return Response(
            serializer.to_representation(report),
            status=status.HTTP_201_CREATED,
        )


# =============================================================================
# DiagnosticReport ViewSet  (FHIR: DiagnosticReport)
# =============================================================================

class DiagnosticReportViewSet(viewsets.ModelViewSet):
    """
    FHIR R4 DiagnosticReport resource.

    list      GET  /diagnostic-reports/           Clinician / Admin
    create    POST /diagnostic-reports/           Clinician / Admin
    retrieve  GET  /diagnostic-reports/{id}/      Clinician / Admin
    update    PUT  /diagnostic-reports/{id}/      Clinician / Admin
    destroy   DELETE /diagnostic-reports/{id}/    Admin only

    Nested actions:
      GET  /diagnostic-reports/{id}/observations/     List all observations
      POST /diagnostic-reports/{id}/observations/add/ Add a single observation
    """

    serializer_class = DiagnosticReportSerializer
    lookup_field = 'id'

    def get_queryset(self):
        qs = DiagnosticReport.objects.select_related(
            'service_request__patient'
        ).prefetch_related('observations').filter(
            service_request__patient__active=True
        )

        patient_id = self.request.query_params.get('patient')
        if patient_id:
            qs = qs.filter(service_request__patient__id=patient_id)

        interp = self.request.query_params.get('interpretation')
        if interp:
            qs = qs.filter(interpretation=interp)

        rpt_status = self.request.query_params.get('status')
        if rpt_status:
            qs = qs.filter(status=rpt_status)

        return qs.order_by('-issued')

    def get_permissions(self):
        if self.action == 'destroy':
            return [IsAdmin()]
        return [IsAdminOrClinician()]

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "searchset",
            "total": queryset.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = serializer.save()
        return Response(
            serializer.to_representation(report),
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        report = self.get_object()
        report.status = 'cancelled'
        report.save()
        return Response(
            {"detail": "DiagnosticReport cancelled."},
            status=status.HTTP_200_OK,
        )

    # Nested: GET /diagnostic-reports/{id}/observations/
    @action(detail=True, methods=['get'], url_path='observations')
    def observations(self, request, id=None):
        report = self.get_object()
        qs = report.observations.all()
        serializer = DiagnosticObservationSerializer(qs, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "collection",
            "total": qs.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })

    # Nested: POST /diagnostic-reports/{id}/observations/add/
    @action(detail=True, methods=['post'], url_path='observations/add',
            url_name='add-observation')
    def add_observation(self, request, id=None):
        """Append a single Observation to an existing DiagnosticReport."""
        report = self.get_object()
        serializer = DiagnosticObservationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        observation = DiagnosticObservation.objects.create(
            report=report,
            **serializer.validated_data,
        )
        return Response(
            serializer.to_representation(observation),
            status=status.HTTP_201_CREATED,
        )


# =============================================================================
# MedicationRequest ViewSet  (FHIR: MedicationRequest)
# =============================================================================

class MedicationRequestViewSet(viewsets.ModelViewSet):
    """
    FHIR R4 MedicationRequest resource.

    list      GET  /medication-requests/               Clinician / Admin
    create    POST /medication-requests/               Clinician / Admin
    retrieve  GET  /medication-requests/{id}/          Clinician / Admin
    update    PUT  /medication-requests/{id}/          Clinician / Admin
    destroy   DELETE /medication-requests/{id}/        Admin only

    Nested actions:
      GET  /medication-requests/{id}/dispenses/           List all dispenses
      POST /medication-requests/{id}/dispenses/add/       Add a single dispense
      POST /medication-requests/{id}/dispenses/{did}/dispense/  Mark as dispensed
    """

    serializer_class = MedicationRequestSerializer
    lookup_field = 'id'

    def get_queryset(self):
        qs = MedicationRequest.objects.select_related(
            'patient', 'encounter'
        ).prefetch_related('dispenses').filter(
            patient__active=True
        )

        patient_id = self.request.query_params.get('patient')
        if patient_id:
            qs = qs.filter(patient__id=patient_id)

        encounter_id = self.request.query_params.get('encounter')
        if encounter_id:
            qs = qs.filter(encounter__id=encounter_id)

        req_status = self.request.query_params.get('status')
        if req_status:
            qs = qs.filter(status=req_status)

        return qs.order_by('-prescription_date')

    def get_permissions(self):
        if self.action == 'destroy':
            return [IsAdmin()]
        return [IsAdminOrClinician()]

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "searchset",
            "total": queryset.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        med_request = serializer.save()
        return Response(
            serializer.to_representation(med_request),
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        med_request = self.get_object()
        med_request.status = 'cancelled'
        med_request.save()
        return Response(
            {"detail": "MedicationRequest cancelled."},
            status=status.HTTP_200_OK,
        )

    # Nested: GET /medication-requests/{id}/dispenses/
    @action(detail=True, methods=['get'], url_path='dispenses')
    def dispenses(self, request, id=None):
        med_request = self.get_object()
        qs = med_request.dispenses.all()
        serializer = MedicationDispenseSerializer(qs, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "collection",
            "total": qs.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })

    # Nested: POST /medication-requests/{id}/dispenses/add/
    @action(detail=True, methods=['post'], url_path='dispenses/add',
            url_name='add-dispense')
    def add_dispense(self, request, id=None):
        """Add a single MedicationDispense to an existing MedicationRequest."""
        med_request = self.get_object()
        serializer = MedicationDispenseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dispense = MedicationDispense.objects.create(
            medication_request=med_request,
            **serializer.validated_data,
        )
        return Response(
            serializer.to_representation(dispense),
            status=status.HTTP_201_CREATED,
        )

    # Nested: POST /medication-requests/{id}/dispenses/{did}/dispense/
    @action(
        detail=True, methods=['post'],
        url_path=r'dispenses/(?P<did>[^/.]+)/dispense',
        url_name='mark-dispensed',
    )
    def mark_dispensed(self, request, id=None, did=None):
        """
        Mark a specific MedicationDispense as dispensed by a pharmacist.

        Body:
          {
            "performer": [{"actor": {"reference": "Practitioner/uuid", "display": "Pharm. Mwale"}}],
            "whenHandedOver": "2026-01-15T10:00:00Z"   // optional, defaults to now
          }
        """
        med_request = self.get_object()

        try:
            dispense = med_request.dispenses.get(id=did)
        except MedicationDispense.DoesNotExist:
            return Response(
                {"detail": "Dispense record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if dispense.status == 'completed':
            return Response(
                {"detail": "This medication has already been dispensed."},
                status=status.HTTP_409_CONFLICT,
            )

        # Extract pharmacist reference
        performers = request.data.get('performer', [])
        if performers:
            actor = performers[0].get('actor', {})
            ref = actor.get('reference', '').split('/')[-1]
            dispense.dispensed_by_id = ref or None
            dispense.dispensed_by_display = actor.get('display', '')

        from django.utils import timezone
        dispense.dispensed_date = request.data.get('whenHandedOver') or timezone.now()
        dispense.status = 'completed'
        dispense.save()

        serializer = MedicationDispenseSerializer(dispense)
        return Response(
            serializer.to_representation(dispense),
            status=status.HTTP_200_OK,
        )
