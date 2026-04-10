"""
dhw/hms_authentication.py
──────────────────────────────────────────────────────────────────────────────
Custom DRF authentication backend for the CPR.

Accepts JWTs issued by the phs-hms backend (port 8000).
HMS tokens carry a `user_type = 'practitioner'` claim which maps to the CPR's
'clinician' role — granting access to patient registration and clinical flows.

This is needed because HMS and CPR have separate User databases.
Instead of requiring a CPR user account, we accept the HMS token directly and
synthesise a lightweight virtual user that satisfies CPR's permission classes.
"""

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


class VirtualClinicianUser:
    """
    A lightweight, non-database user object that represents an HMS practitioner
    authenticated via a cross-service JWT.

    CPR permission classes check: is_authenticated, is_clinician, is_admin,
    is_patient.  This object satisfies exactly what a clinician needs.
    """

    def __init__(self, payload: dict):
        self.id              = payload.get('user_id')
        self.pk              = self.id
        self.license_number  = payload.get('license_number', '')
        self.staff_id        = payload.get('staff_id', '')
        self.hospital_id     = payload.get('hospital_id', '')
        self.username        = self.license_number or str(self.id)
        # Role flags expected by CPR permission classes
        self.role            = 'clinician'

    # ── Django auth interface ───────────────────────────────────────────────
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        return True

    # ── CPR role properties (mirrors accounts.User) ─────────────────────────
    @property
    def is_clinician(self):
        return True

    @property
    def is_admin(self):
        return False

    @property
    def is_patient(self):
        return False

    def __str__(self):
        return f"HMSPractitioner({self.license_number})"


class HMSJWTAuthentication(BaseAuthentication):
    """
    Attempts to decode the Bearer token using the HMS_SECRET_KEY.
    Falls through (returns None) if the token does not look like an HMS token
    so that the next authentication class (standard JWTAuthentication) can try.
    """

    def authenticate(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return None

        raw_token = auth_header.split(' ', 1)[1]
        hms_secret = getattr(settings, 'HMS_SECRET_KEY', None)

        if not hms_secret:
            return None  # HMS integration not configured

        try:
            payload = jwt.decode(
                raw_token,
                hms_secret,
                algorithms=['HS256'],
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('HMS token has expired.')
        except jwt.InvalidTokenError:
            # Not an HMS token — let the next authenticator try
            return None

        # Only accept tokens issued for practitioners
        if payload.get('user_type') != 'practitioner':
            return None

        user = VirtualClinicianUser(payload)
        logger.debug('HMSJWTAuthentication: authenticated practitioner %s', user.license_number)
        return (user, raw_token)

    def authenticate_header(self, request):
        return 'Bearer realm="CPR"'
