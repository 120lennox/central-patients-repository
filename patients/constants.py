# constants.py

from django.conf import settings


class FHIRSystems:

    @classmethod
    def _base(cls):
        return settings.FHIR_SYSTEM_BASE_URL.rstrip('/')

    @classmethod
    def national_id(cls):
        return f"{cls._base()}/fhir/systems/national-id"

    @classmethod
    def digital_id(cls):
        return f"{cls._base()}/fhir/systems/digital-id"