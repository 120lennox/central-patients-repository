from django.contrib.auth import get_user_model
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.utils import timezone
from datetime import timedelta
import jwt

from .permissions import IsAdmin, IsClinician, IsAdminOrClinician, IsPatientOrClinician, IsPatientOwner
from django.conf import settings

# Patient app models & serializers
from patients.models import (
    Patient, PatientOTP,
    Encounter, ServiceRequest,
    DiagnosticReport, DiagnosticObservation,
    MedicationRequest, MedicationDispense,
)
from patients.serializers import (
    PatientSerializer,
    EncounterSerializer, ServiceRequestSerializer,
    DiagnosticReportSerializer, DiagnosticObservationSerializer,
    MedicationRequestSerializer, MedicationDispenseSerializer,
)

User = get_user_model()

def issue_registration_token(patient):
    """
    Short-lived JWT (10 min) issued after OTP verification.
    Only accepted by /auth/complete-registration/.
    """
    payload = {
        'token_type': 'registration',
        'patient_id': str(patient.patient_id),
        'exp': timezone.now() + timedelta(minutes=10),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')


def decode_registration_token(token):
    """
    Decodes and validates the registration token.
    Raises jwt.ExpiredSignatureError or jwt.InvalidTokenError on failure.
    """
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    if payload.get('token_type') != 'registration':
        raise jwt.InvalidTokenError("Invalid token type.")
    return payload


def get_full_auth_tokens(user):
    """
    Issues simplejwt access + refresh tokens for a fully registered user.
    """
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }

# Patient ViewSet
class PatientViewSet(viewsets.ModelViewSet):
    """
    FHIR-compliant Patient resource ViewSet.

    list:        GET  /patients/
    create:      POST /patients/
    retrieve:    GET  /patients/{id}/
    update:      PUT  /patients/{id}/
    destroy:     DELETE /patients/{id}/ 
    everything:  GET  /patients/{id}/$everything/
    summary:     GET  /patients/{id}/$summary/
    search:      GET  /patients/?identifier=
    """
    serializer_class = PatientSerializer
    lookup_field = 'id'

    def get_queryset(self):
        user = self.request.user

        # Patient can only see their own record
        if hasattr(user, 'patient_profile'):
            return Patient.objects.filter(id=user.patient_profile.id)

        # Clinicians and admins see all active patients
        return Patient.objects.filter(active=True)

    def get_permissions(self):
        if self.action == 'create':
            return [IsAdminOrClinician()]
        if self.action == 'destroy':
            return [IsAdmin()]
        if self.action in ['update', 'partial_update']:
            return [IsAdminOrClinician()]
        if self.action == 'list':
            return [IsAdminOrClinician()]
        if self.action in ['retrieve', 'summary', 'everything']:
            return [IsPatientOrClinician()]
        return [IsAuthenticated()]
    

    # CREATE — clinician registers a patient
    # POST /patients/
    # -------------------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        patient = serializer.save()

        # Fetch the generated OTP
        otp = patient.otp.otp_code

        response_data = serializer.to_representation(patient)
        response_data['otp'] = otp

        return Response(response_data, status=status.HTTP_201_CREATED)

    # DESTROY — soft delete only, never hard delete
    # DELETE /patients/{id}/
    # -------------------------------------------------------------------------
    def destroy(self, request, *args, **kwargs):
        patient = self.get_object()
        patient.active = False
        patient.save()
        return Response(
            {"detail": "Patient deactivated successfully."},
            status=status.HTTP_200_OK
        )


    # SEARCH — FHIR-style identifier search
    # GET /patients/?identifier=12345678
    # -------------------------------------------------------------------------
    def list(self, request, *args, **kwargs):
        identifier = request.query_params.get('identifier')

        if identifier:
            queryset = Patient.objects.filter(active=True, patient_id=identifier)
        else:
            queryset = self.get_queryset()

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "searchset",
            "total": queryset.count(),
            "entry": serializer.data
        })

    # $everything — full patient health bundle
    # GET /patients/{id}/$everything/
    # -------------------------------------------------------------------------
    @action(detail=True, methods=['get'], url_path=r'\$everything')
    def everything(self, request, id=None):
        patient = self.get_object()
        patient_data = self.get_serializer(patient).data

        # More resources (Encounter, Condition etc.) will be added here
        # as those services are built
        bundle = {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [
                {
                    "resource": patient_data
                }
            ]
        }
        return Response(bundle, status=status.HTTP_200_OK)

    # $summary — lightweight patient card
    # GET /patients/{id}/$summary/
    # -------------------------------------------------------------------------
    @action(detail=True, methods=['get'], url_path=r'\$summary')
    def summary(self, request, id=None):
        patient = self.get_object()
        return Response({
            "resourceType": "Patient",
            "id": str(patient.id),
            "patient_id": patient.patient_id,
            "full_name": patient.full_name,
            "gender": patient.gender,
            "date_of_birth": str(patient.date_of_birth) if patient.date_of_birth else None,
            "phone_number": patient.phone_number,
            "active": patient.active,
            "managing_organization": patient.managing_organization_display,
        }, status=status.HTTP_200_OK)


# OTP + Account Activation ViewSet
# -----------------------------------------------------------------------------

class PatientAuthViewSet(viewsets.ViewSet):
    permission_classes = [AllowAny]
    # STEP 1 — Patient submits OTP
    # POST /auth/verify-otp/
    @action(detail=False, methods=['post'], url_path='verify-otp')
    def verify_otp(self, request):
        patient_id = request.data.get('patient_id')
        otp_code = request.data.get('otp_code')

        if not patient_id or not otp_code:
            return Response(
                {"detail": "patient_id and otp_code are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        is_valid, message, patient, otp = PatientOTP.verify_otp(patient_id, otp_code)

        if not is_valid:
            return Response(
                {"detail": message},
                status=status.HTTP_400_BAD_REQUEST
            )
        otp.mark_as_used()

        # Issue short-lived registration token
        registration_token = issue_registration_token(patient)

        return Response({
            "detail": "OTP verified successfully.",
            "registration_token": registration_token,
            "patient": {
                "patient_id": patient.patient_id,
                "full_name": patient.full_name,
            }
        }, status=status.HTTP_200_OK)

    # STEP 2 — Patient sets email (optional) + password
    # POST /auth/complete-registration/
    @action(detail=False, methods=['post'], url_path='complete-registration')
    def complete_registration(self, request):
        registration_token = request.data.get('registration_token')
        password1 = request.data.get('password1')
        password2 = request.data.get('password2')
        email = request.data.get('email', None)

        # Validate required fields
        if not registration_token:
            return Response(
                {"detail": "registration_token is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not password1 or not password2:
            return Response(
                {"detail": "password1 and password2 are required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if password1 != password2:
            return Response(
                {"detail": "Passwords do not match."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Decode and validate registration token
        try:
            payload = decode_registration_token(registration_token)
        except jwt.ExpiredSignatureError:
            return Response(
                {"detail": "Registration token has expired. Please contact your clinic."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except jwt.InvalidTokenError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch patient
        try:
            patient = Patient.objects.get(patient_id=payload['patient_id'])
        except Patient.DoesNotExist:
            return Response(
                {"detail": "Patient not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Guard — account already claimed
        if patient.account_claimed:
            return Response(
                {"detail": "Account has already been activated."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create user account
        username = email if email else patient.patient_id
        user = User.objects.create_user(
            username=username,
            email=email or '',
            password=password1
        )

        # Link user to patient
        patient.user = user
        patient.account_claimed = True
        patient.email = email or patient.email
        patient.save()

        # Issue full JWT tokens — patient is now logged in
        tokens = get_full_auth_tokens(user)

        return Response({
            "detail": "Account activated successfully.",
            "access": tokens['access'],
            "refresh": tokens['refresh'],
            "patient": {
                "patient_id": patient.patient_id,
                "full_name": patient.full_name,
                "username": username,
            }
        }, status=status.HTTP_201_CREATED)


# =============================================================================
# Encounter ViewSet  (FHIR: Encounter)
# =============================================================================

class EncounterViewSet(viewsets.ModelViewSet):
    """
    FHIR R4 Encounter resource.

    list      GET  /api/encounters/                      Clinician / Admin
    create    POST /api/encounters/                      Clinician / Admin
    retrieve  GET  /api/encounters/{id}/                 Clinician / Admin / Patient (own)
    update    PUT  /api/encounters/{id}/                 Clinician / Admin
    destroy   DELETE /api/encounters/{id}/               Admin only (soft-delete → cancelled)

    Nested:
      GET  /api/encounters/{id}/service-requests/        Lab requests for this encounter
      GET  /api/encounters/{id}/medication-requests/     Prescriptions for this encounter

    Query filters:
      ?patient=<uuid>     filter by patient UUID
      ?clinician=<uuid>   filter by clinician UUID
      ?status=<status>    filter by encounter status
    """

    serializer_class = EncounterSerializer
    lookup_field = 'id'

    def get_queryset(self):
        qs = Encounter.objects.select_related('patient').filter(patient__active=True)

        patient_id = self.request.query_params.get('patient')
        if patient_id:
            qs = qs.filter(patient__id=patient_id)

        clinician_id = self.request.query_params.get('clinician')
        if clinician_id:
            qs = qs.filter(clinician_id=clinician_id)

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
        encounter = serializer.save()
        return Response(
            serializer.to_representation(encounter),
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        encounter = self.get_object()
        encounter.status = 'cancelled'
        encounter.save()
        return Response({"detail": "Encounter cancelled."}, status=status.HTTP_200_OK)

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

    list      GET  /api/service-requests/               Clinician / Admin
    create    POST /api/service-requests/               Clinician / Admin
    retrieve  GET  /api/service-requests/{id}/          Clinician / Admin
    update    PUT  /api/service-requests/{id}/          Clinician / Admin
    destroy   DELETE /api/service-requests/{id}/        Admin only (soft-delete → revoked)

    Nested:
      GET  /api/service-requests/{id}/diagnostic-report/         Fetch the filed report
      POST /api/service-requests/{id}/diagnostic-report/file/    File a new report

    Query filters:
      ?patient=<uuid>       filter by patient UUID
      ?encounter=<uuid>     filter by encounter UUID
      ?status=<status>      filter by status
      ?category=<category>  filter by category (laboratory|imaging|pathology|other)
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
        return Response({"detail": "ServiceRequest revoked."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='diagnostic-report')
    def diagnostic_report(self, request, id=None):
        """Fetch the DiagnosticReport filed against this ServiceRequest."""
        service_request = self.get_object()
        try:
            report = service_request.diagnostic_report
        except DiagnosticReport.DoesNotExist:
            return Response(
                {"detail": "No diagnostic report filed for this service request yet."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(DiagnosticReportSerializer(report).to_representation(report))

    @action(detail=True, methods=['post'],
            url_path='diagnostic-report/file', url_name='file-diagnostic-report')
    def file_diagnostic_report(self, request, id=None):
        """File a DiagnosticReport against this ServiceRequest (one-shot)."""
        service_request = self.get_object()

        if hasattr(service_request, 'diagnostic_report'):
            return Response(
                {"detail": "A diagnostic report already exists for this service request."},
                status=status.HTTP_409_CONFLICT,
            )

        data = request.data.copy()
        data.setdefault('basedOn', [{'reference': f'ServiceRequest/{service_request.id}'}])

        serializer = DiagnosticReportSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        report = serializer.save()

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

    list      GET  /api/diagnostic-reports/              Clinician / Admin
    create    POST /api/diagnostic-reports/              Clinician / Admin
    retrieve  GET  /api/diagnostic-reports/{id}/         Clinician / Admin
    update    PUT  /api/diagnostic-reports/{id}/         Clinician / Admin
    destroy   DELETE /api/diagnostic-reports/{id}/       Admin only (soft-delete → cancelled)

    Nested:
      GET  /api/diagnostic-reports/{id}/observations/          All observations
      POST /api/diagnostic-reports/{id}/observations/add/      Append one observation

    Query filters:
      ?patient=<uuid>           filter by patient UUID
      ?interpretation=<value>   normal|abnormal|critical|pending
      ?status=<status>          registered|partial|final|amended|cancelled
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
        return Response({"detail": "DiagnosticReport cancelled."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='observations')
    def observations(self, request, id=None):
        """List all Observation records for this DiagnosticReport."""
        report = self.get_object()
        qs = report.observations.all()
        serializer = DiagnosticObservationSerializer(qs, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "collection",
            "total": qs.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })

    @action(detail=True, methods=['post'],
            url_path='observations/add', url_name='add-observation')
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

    list      GET  /api/medication-requests/                     Clinician / Admin
    create    POST /api/medication-requests/                     Clinician / Admin
    retrieve  GET  /api/medication-requests/{id}/                Clinician / Admin
    update    PUT  /api/medication-requests/{id}/                Clinician / Admin
    destroy   DELETE /api/medication-requests/{id}/              Admin only (soft-delete → cancelled)

    Nested:
      GET  /api/medication-requests/{id}/dispenses/                  All dispenses
      POST /api/medication-requests/{id}/dispenses/add/              Add a dispense line
      POST /api/medication-requests/{id}/dispenses/{did}/dispense/   Mark a line as dispensed

    Query filters:
      ?patient=<uuid>      filter by patient UUID
      ?encounter=<uuid>    filter by encounter UUID
      ?status=<status>     active|completed|cancelled|on-hold
    """

    serializer_class = MedicationRequestSerializer
    lookup_field = 'id'

    def get_queryset(self):
        qs = MedicationRequest.objects.select_related(
            'patient', 'encounter'
        ).prefetch_related('dispenses').filter(patient__active=True)

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
        return Response({"detail": "MedicationRequest cancelled."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='dispenses')
    def dispenses(self, request, id=None):
        """List all MedicationDispense records for this MedicationRequest."""
        med_request = self.get_object()
        qs = med_request.dispenses.all()
        serializer = MedicationDispenseSerializer(qs, many=True)
        return Response({
            "resourceType": "Bundle",
            "type": "collection",
            "total": qs.count(),
            "entry": [{"resource": r} for r in serializer.data],
        })

    @action(detail=True, methods=['post'],
            url_path='dispenses/add', url_name='add-dispense')
    def add_dispense(self, request, id=None):
        """Add a MedicationDispense line to an existing MedicationRequest."""
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

    @action(
        detail=True, methods=['post'],
        url_path=r'dispenses/(?P<did>[^/.]+)/dispense',
        url_name='mark-dispensed',
    )
    def mark_dispensed(self, request, id=None, did=None):
        """
        Mark a specific dispense line as completed by a pharmacist.

        Body:
          {
            "performer": [{"actor": {"reference": "Practitioner/uuid", "display": "Pharm. Mwale"}}],
            "whenHandedOver": "2026-04-09T10:00:00Z"   // optional — defaults to now
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

        performers = request.data.get('performer', [])
        if performers:
            actor = performers[0].get('actor', {})
            ref = actor.get('reference', '').split('/')[-1]
            dispense.dispensed_by_id = ref or None
            dispense.dispensed_by_display = actor.get('display', '')

        dispense.dispensed_date = request.data.get('whenHandedOver') or timezone.now()
        dispense.status = 'completed'
        dispense.save()

        serializer = MedicationDispenseSerializer(dispense)
        return Response(
            serializer.to_representation(dispense),
            status=status.HTTP_200_OK,
        )