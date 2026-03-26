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
from patients.models import Patient, PatientOTP
from patients.serializers import PatientSerializer
from .permissions import IsAdmin, IsClinician, IsPatientOwner

User = get_user_model()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# Patient ViewSet
class PatientViewSet(viewsets.ModelViewSet):
    """
    FHIR-compliant Patient resource ViewSet.

    list:        GET  /patients/
    create:      POST /patients/
    retrieve:    GET  /patients/{id}/
    update:      PUT  /patients/{id}/
    destroy:     DELETE /patients/{id}/  → soft delete only
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
    """
    Handles DHW patient activation flow.

    verify_otp:              POST /auth/verify-otp/
    complete_registration:   POST /auth/complete-registration/
    """
    permission_classes = [AllowAny]

    # STEP 1 — Patient submits OTP
    # POST /auth/verify-otp/
    # -------------------------------------------------------------------------
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
    # -------------------------------------------------------------------------
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