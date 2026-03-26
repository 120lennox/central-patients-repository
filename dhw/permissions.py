from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """
    Grants access to admin users only.
    Role is read from the JWT token claim.
    """
    message = "You do not have admin privileges."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.role == 'admin'
        )


class IsClinician(BasePermission):
    """
    Grants access to clinicians only.
    Role is read from the JWT token claim.
    """
    message = "You do not have clinician privileges."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.role == 'clinician'
        )


class IsAdminOrClinician(BasePermission):
    """
    Grants access to either admin or clinician.
    Used for actions both roles can perform.
    """
    message = "You must be an admin or clinician to perform this action."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and
            request.user.role in ['admin', 'clinician']
        )


class IsPatientOwner(BasePermission):
    """
    Object-level permission.
    Grants access only if the requesting user is the patient themselves.
    """
    message = "You can only access your own records."

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return (
            hasattr(request.user, 'patient_profile') and
            request.user.patient_profile == obj
        )


class IsPatientOrClinician(BasePermission):
    """
    Grants access to the patient themselves or a clinician/admin.
    Used for retrieve — clinician can view any patient,
    patient can only view themselves (enforced by get_queryset).
    """
    message = "You do not have permission to access this record."

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return (
            request.user.role in ['admin', 'clinician'] or
            hasattr(request.user, 'patient_profile')
        )