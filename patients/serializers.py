from .models import Patient
from rest_framework import serializers
from django.utils import timezone
from .constants import FHIRSystems

class PatientSerializer(serializers.ModelSerializer):
    # FHIR standard fields
    resourceType = serializers.CharField(source='get_resource_type', read_only=True)

    name = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        help_text="FHIR: Patient.name — [{use, text, family, given[]}]"
    )
    telecom = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
        help_text="FHIR: Patient.telecom — [{system: phone|email, value}]"
    )
    address = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
        help_text="FHIR: Patient.address — [{use, text, line[], city, district, country}]"
    )
    contact = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
        help_text="FHIR: Patient.contact — [{name, telecom, relationship}]"
    )
    identifier = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
        help_text="FHIR: Patient.identifier — [{system, value}]"
    )
    managingOrganization = serializers.DictField(
        write_only=True,
        required=False,
        help_text="FHIR: Patient.managingOrganization — {reference, display}"
    )
    generalPractitioner = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
        help_text="FHIR: Patient.generalPractitioner — [{reference, display}]"
    )

    class Meta:
        model = Patient
        fields = [
            'resourceType',
            'id',
            'active',
            'identifier',
            'name',
            'gender',
            # 'birthDate',
            'telecom',
            'address',
            'contact',
            'managingOrganization',
            'generalPractitioner',
        ]
        extra_kwargs = {
            'id': {'read_only': True},
            'gender': {'required': True},
        }
        
    def to_representation(self, instance):
        return {
            "resourceType": "Patient",
            "id": str(instance.id),
            "active": instance.active,
            "identifier": self._build_identifiers(instance),
            "name": self._build_name(instance),
            "gender": instance.gender,
            "birthDate": str(instance.date_of_birth) if instance.date_of_birth else None,
            "telecom": self._build_telecom(instance),
            "address": self._build_address(instance),
            "contact": self._build_contact(instance),
            "managingOrganization": self._build_managing_organization(instance),
            "generalPractitioner": self._build_general_practitioner(instance),
        }
    
    # validate incoming fhir JSON to ensure it has required fields and correct formats
    def validate_name(self, value):
        if not value:
            raise serializers.ValidationError("Patient.name is required.")

        official = next((n for n in value if n.get('use') == 'official'), value[0])

        if not official.get('family'):
            raise serializers.ValidationError("Patient.name.family (last name) is required.")
        if not official.get('given'):
            raise serializers.ValidationError("Patient.name.given (first name) is required.")

        return value

    def validate_gender(self, value):
        allowed = ['male', 'female', 'other', 'unknown']
        if value not in allowed:
            raise serializers.ValidationError(
                f"Patient.gender must be one of: {', '.join(allowed)}."
            )
        return value

    def validate(self, attrs):
        if 'name' not in attrs:
            raise serializers.ValidationError({"name": "Patient.name is required."})
        if 'gender' not in attrs:
            raise serializers.ValidationError({"gender": "Patient.gender is required."})
        return attrs
    
    def create(self, validated_data):
        name_data = validated_data.pop('name', [])
        telecom_data = validated_data.pop('telecom', [])
        address_data = validated_data.pop('address', [])
        contact_data = validated_data.pop('contact', [])
        identifier_data = validated_data.pop('identifier', [])
        org_data = validated_data.pop('managingOrganization', {})
        practitioner_data = validated_data.pop('generalPractitioner', [])

        # Extract name
        official_name = next((n for n in name_data if n.get('use') == 'official'), name_data[0])
        validated_data['first_name'] = official_name.get('given', [''])[0]
        validated_data['last_name']  = official_name.get('family', '')
        validated_data['full_name']  = official_name.get('text') or \
            f"{validated_data['first_name']} {validated_data['last_name']}".strip()

        # Extract telecom
        for t in telecom_data:
            if t.get('system') == 'phone':
                validated_data['phone_number'] = t.get('value')
            elif t.get('system') == 'email':
                validated_data['email'] = t.get('value')

        # Extract address
        if address_data:
            addr = address_data[0]
            # FHIR 'text' → place_of_residence
            validated_data['place_of_residence'] = addr.get('text')
            # FHIR 'line' (first line) → village
            validated_data['village'] = addr.get('line', [''])[0] if addr.get('line') else None
            # FHIR 'district' → district_of_origin
            validated_data['district_of_origin'] = addr.get('district')
            # FHIR 'city' → traditional_authority (or ignore if not needed)
            if 'city' in addr:
                validated_data['traditional_authority'] = addr.get('city')

        # Extract emergency contact
        if contact_data:
            c = contact_data[0]
            validated_data['close_relative_name']         = c.get('name', {}).get('text')
            validated_data['close_relative_phone']        = next(
                (t.get('value') for t in c.get('telecom', []) if t.get('system') == 'phone'), None
            )
            validated_data['close_relative_relationship'] = next(
                (r.get('text') for r in c.get('relationship', [])), None
            )

        # Extract identifiers (national_id if present)
        # for ident in identifier_data:
        #     if ident.get('system') == 'https://phs.mw/national-id':
        #         validated_data['national_id'] = ident.get('value')

        # Extract managing organization
        if org_data:
            ref = org_data.get('reference', '')
            validated_data['managing_organization_id']      = ref.split('/')[-1] if '/' in ref else ref
            validated_data['managing_organization_display'] = org_data.get('display')

        # Extract general practitioner
        if practitioner_data:
            ref = practitioner_data[0].get('reference', '')
            validated_data['registered_by_staff_id']      = ref.split('/')[-1] if '/' in ref else ref
            validated_data['registered_by_staff_display'] = practitioner_data[0].get('display')

        return Patient.objects.create(**validated_data)
    
    def update(self, instance, validated_data):
        name_data  = validated_data.pop('name', None)
        telecom_data = validated_data.pop('telecom', None)
        address_data = validated_data.pop('address', None)
        contact_data = validated_data.pop('contact', None)
        validated_data.pop('identifier', None)
        org_data          = validated_data.pop('managingOrganization', None)
        practitioner_data = validated_data.pop('generalPractitioner', None)

        if name_data:
            official_name = next((n for n in name_data if n.get('use') == 'official'), name_data[0])
            instance.first_name = official_name.get('given', [instance.first_name])[0]
            instance.last_name  = official_name.get('family', instance.last_name)
            instance.full_name  = official_name.get('text') or \
                f"{instance.first_name} {instance.last_name}".strip()

        if telecom_data:
            for t in telecom_data:
                if t.get('system') == 'phone':
                    instance.phone_number = t.get('value')
                elif t.get('system') == 'email':
                    instance.email = t.get('value')

        if address_data:
            addr = address_data[0]
            instance.place_of_residence   = addr.get('text', instance.place_of_residence)
            instance.village               = addr.get('line', [instance.village])[0]
            instance.traditional_authority = addr.get('city', instance.traditional_authority)
            instance.district_of_origin    = addr.get('district', instance.district_of_origin)

        if contact_data:
            c = contact_data[0]
            instance.close_relative_name         = c.get('name', {}).get('text', instance.close_relative_name)
            instance.close_relative_phone        = next(
                (t.get('value') for t in c.get('telecom', []) if t.get('system') == 'phone'),
                instance.close_relative_phone
            )
            instance.close_relative_relationship = next(
                (r.get('text') for r in c.get('relationship', [])),
                instance.close_relative_relationship
            )

        if org_data:
            ref = org_data.get('reference', '')
            instance.managing_organization_id      = ref.split('/')[-1] if '/' in ref else ref
            instance.managing_organization_display = org_data.get('display', instance.managing_organization_display)

        if practitioner_data:
            ref = practitioner_data[0].get('reference', '')
            instance.registered_by_staff_id      = ref.split('/')[-1] if '/' in ref else ref
            instance.registered_by_staff_display = practitioner_data[0].get('display', instance.registered_by_staff_display)

        # Apply any remaining flat fields (e.g. gender, date_of_birth, active)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance

    def _build_identifiers(self, instance):
        identifiers = []

        if instance.national_id:
            # national_id IS the patient_id in this case
            identifiers.append({
                "system": FHIRSystems.national_id(),
                "type": {"text": "NI"},
                "use": "official",
                "value": instance.national_id
            })
        else:
            # digital_id IS the patient_id in this case
            identifiers.append({
                "system": FHIRSystems.digital_id(),
                "type": {"text": "DIG"},
                "use": "usual",
                "value": instance.digital_id
            })

        return identifiers

    def _build_name(self, instance):
        return [{
            "use": "official",
            "text": instance.full_name,
            "family": instance.last_name or "",
            "given": [instance.first_name] if instance.first_name else []
        }]

    def _build_telecom(self, instance):
        telecom = []
        if instance.phone_number:
            telecom.append({"system": "phone", "value": instance.phone_number})
        if instance.email:
            telecom.append({"system": "email", "value": instance.email})
        return telecom

    def _build_address(self, instance):
        return [{
            "use": "home",
            "text": instance.place_of_residence or "",
            "line": [instance.village or ""],
            "city": instance.traditional_authority or "",
            "district": instance.district_of_origin or "",
            "country": "MW"
        }]

    def _build_contact(self, instance):
        if not instance.close_relative_name:
            return []
        return [{
            "name": {"text": instance.close_relative_name},
            "telecom": [{"system": "phone", "value": instance.close_relative_phone}],
            "relationship": [{"text": instance.close_relative_relationship}]
        }]

    def _build_managing_organization(self, instance):
        if not instance.managing_organization_id:
            return None
        return {
            "reference": f"Organization/{instance.managing_organization_id}",
            "display": instance.managing_organization_display
        }

    def _build_general_practitioner(self, instance):
        if not instance.registered_by_staff_id:
            return []
        return [{
            "reference": f"Practitioner/{instance.registered_by_staff_id}",
            "display": instance.registered_by_staff_display
        }]


# =============================================================================
# Clinical record serializers
# Follow the same FHIR write-in / FHIR read-out pattern as PatientSerializer.
# Cross-service HMS references are accepted as FHIR Reference objects
# { "reference": "ResourceType/uuid", "display": "..." }
# and stored as flat uuid + display string pairs.
# =============================================================================

def _parse_reference(ref_dict, field_name='reference'):
    """
    Utility: extract UUID from a FHIR Reference dict.
    Accepts  { "reference": "Practitioner/uuid", "display": "Dr. Smith" }
    Returns  (uuid_str, display_str)
    """
    if not ref_dict:
        return None, None
    ref = ref_dict.get(field_name, '')
    uid = ref.split('/')[-1] if '/' in ref else ref
    display = ref_dict.get('display', '')
    return uid or None, display or None


# ── Encounter (OPD Visit) ─────────────────────────────────────────────────────

class EncounterSerializer(serializers.ModelSerializer):
    """
    FHIR R4 Encounter serializer.

    Write (POST/PUT):
        {
          "resourceType": "Encounter",
          "status": "finished",
          "class": {"code": "AMB"},
          "subject": {"reference": "Patient/uuid"},
          "participant": [{"individual": {"reference": "Practitioner/uuid", "display": "Dr. Banda"}}],
          "serviceProvider": {"reference": "Organization/uuid", "display": "KCH"},
          "period": {"start": "2026-01-01T08:00:00Z"},
          "reasonCode": [{"text": "Headache, fever"}],
          "diagnosis": [{"condition": {"display": "Malaria"}}],
          "note": [{"text": "Patient stable."}]
        }

    Read (GET): Returns a complete FHIR Encounter resource.
    """

    from .models import Encounter as _Encounter

    # ── FHIR write-only input fields ──────────────────────────────────────────
    subject = serializers.DictField(
        write_only=True,
        help_text="FHIR: Encounter.subject — {reference: 'Patient/uuid'}"
    )
    participant = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        help_text="FHIR: Encounter.participant — [{individual: {reference, display}}]"
    )
    serviceProvider = serializers.DictField(
        write_only=True,
        help_text="FHIR: Encounter.serviceProvider — {reference: 'Organization/uuid', display}"
    )
    period = serializers.DictField(
        write_only=True, required=False,
        help_text="FHIR: Encounter.period — {start: datetime}"
    )
    reasonCode = serializers.ListField(
        child=serializers.DictField(),
        write_only=True, required=False,
        help_text="FHIR: Encounter.reasonCode — [{text: 'chief complaint'}]"
    )
    diagnosis = serializers.ListField(
        child=serializers.DictField(),
        write_only=True, required=False,
        help_text="FHIR: Encounter.diagnosis — [{condition: {display: 'Malaria'}}]"
    )
    note = serializers.ListField(
        child=serializers.DictField(),
        write_only=True, required=False,
        help_text="FHIR: Encounter.note — [{text: 'clinician notes'}]"
    )

    class Meta:
        from .models import Encounter
        model = Encounter
        fields = [
            'id', 'status', 'subject', 'participant',
            'serviceProvider', 'period', 'reasonCode', 'diagnosis', 'note',
        ]
        extra_kwargs = {'id': {'read_only': True}}

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_participant(self, value):
        if not value:
            raise serializers.ValidationError("At least one participant (clinician) is required.")
        return value

    def validate(self, attrs):
        if 'subject' not in attrs:
            raise serializers.ValidationError({"subject": "Encounter.subject (Patient reference) is required."})
        if 'participant' not in attrs:
            raise serializers.ValidationError({"participant": "Encounter.participant (clinician) is required."})
        if 'serviceProvider' not in attrs:
            raise serializers.ValidationError({"serviceProvider": "Encounter.serviceProvider (hospital) is required."})
        return attrs

    # ── Create / Update ───────────────────────────────────────────────────────

    def create(self, validated_data):
        from .models import Patient, Encounter
        subject_data       = validated_data.pop('subject')
        participant_data   = validated_data.pop('participant')
        service_prov_data  = validated_data.pop('serviceProvider')
        period_data        = validated_data.pop('period', {})
        reason_data        = validated_data.pop('reasonCode', [])
        diagnosis_data     = validated_data.pop('diagnosis', [])
        note_data          = validated_data.pop('note', [])

        # Patient
        patient_uuid = subject_data.get('reference', '').split('/')[-1]
        patient = Patient.objects.get(id=patient_uuid)

        # Clinician (first participant)
        individual = participant_data[0].get('individual', {})
        clinician_id, clinician_display = _parse_reference(individual)

        # Organisation
        org_id, org_display = _parse_reference(service_prov_data)


        # Period
        visit_date = period_data.get('start') if period_data else None
        # Convert visit_date to datetime if it's a string
        from django.utils.dateparse import parse_datetime
        if isinstance(visit_date, str):
            visit_date = parse_datetime(visit_date)

        # Flat text fields
        symptoms  = '; '.join(r.get('text', '') for r in reason_data) or None
        diagnosis = '; '.join(
            d.get('condition', {}).get('display', '') for d in diagnosis_data
        ) or None
        notes = '; '.join(n.get('text', '') for n in note_data) or None

        return Encounter.objects.create(
            patient=patient,
            clinician_id=clinician_id,
            clinician_display=clinician_display,
            organization_id=org_id,
            organization_display=org_display,
            visit_date=visit_date or validated_data.pop('visit_date', None),
            symptoms=symptoms,
            diagnosis=diagnosis,
            notes=notes,
            **validated_data,
        )

    def update(self, instance, validated_data):
        validated_data.pop('subject', None)          # patient is immutable after creation

        participant_data  = validated_data.pop('participant', None)
        service_prov_data = validated_data.pop('serviceProvider', None)
        period_data       = validated_data.pop('period', None)
        reason_data       = validated_data.pop('reasonCode', None)
        diagnosis_data    = validated_data.pop('diagnosis', None)
        note_data         = validated_data.pop('note', None)

        if participant_data:
            individual = participant_data[0].get('individual', {})
            cid, cdisplay = _parse_reference(individual)
            if cid:
                instance.clinician_id = cid
                instance.clinician_display = cdisplay

        if service_prov_data:
            oid, odisplay = _parse_reference(service_prov_data)
            if oid:
                instance.organization_id = oid
                instance.organization_display = odisplay

        if period_data and period_data.get('start'):
            instance.visit_date = period_data['start']

        if reason_data is not None:
            instance.symptoms = '; '.join(r.get('text', '') for r in reason_data) or None

        if diagnosis_data is not None:
            instance.diagnosis = '; '.join(
                d.get('condition', {}).get('display', '') for d in diagnosis_data
            ) or None

        if note_data is not None:
            instance.notes = '; '.join(n.get('text', '') for n in note_data) or None

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance

    # ── FHIR output ───────────────────────────────────────────────────────────

    def to_representation(self, instance):
        return {
            "resourceType": "Encounter",
            "id": str(instance.id),
            "identifier": [{"system": "http://phs.mw/cpr/encounters", "value": instance.encounter_id}],
            "status": instance.status,
            "class": {"code": instance.encounter_class, "display": instance.get_encounter_class_display()},
            "subject": {
                "reference": f"Patient/{instance.patient.id}",
                "display": instance.patient.full_name,
            },
            "participant": [{
                "individual": {
                    "reference": f"Practitioner/{instance.clinician_id}",
                    "display": instance.clinician_display,
                }
            }],
            "period": {"start": instance.visit_date.isoformat() if instance.visit_date else None},
            "reasonCode": [{"text": instance.symptoms}] if instance.symptoms else [],
            "diagnosis": [{"condition": {"display": instance.diagnosis}}] if instance.diagnosis else [],
            "note": [{"text": instance.notes}] if instance.notes else [],
            "serviceProvider": {
                "reference": f"Organization/{instance.organization_id}",
                "display": instance.organization_display,
            },
        }


# ── ServiceRequest (Lab Test Request) ────────────────────────────────────────

class ServiceRequestSerializer(serializers.ModelSerializer):
    """
    FHIR R4 ServiceRequest serializer.

    Write:
        {
          "resourceType": "ServiceRequest",
          "status": "active",
          "category": [{"coding": [{"code": "laboratory"}]}],
          "code": {"text": "Full Blood Count"},
          "subject": {"reference": "Patient/uuid"},
          "encounter": {"reference": "Encounter/uuid"},   // optional
          "requester": {"reference": "Practitioner/uuid", "display": "Dr. Banda"},
          "performer": [{"reference": "Organization/uuid", "display": "KCH"}],
          "note": [{"text": "Fasting sample required"}]
        }
    """

    # ── write-only FHIR fields ────────────────────────────────────────────────
    subject = serializers.DictField(write_only=True)
    encounter_ref = serializers.DictField(
        write_only=True, required=False, source='encounter',
        help_text="FHIR: ServiceRequest.encounter — {reference: 'Encounter/uuid'}"
    )
    requester = serializers.DictField(write_only=True)
    performer = serializers.ListField(child=serializers.DictField(), write_only=True)
    code = serializers.DictField(
        write_only=True,
        help_text="FHIR: ServiceRequest.code — {text: 'test name'}"
    )
    category = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False,
        help_text="FHIR: ServiceRequest.category — [{coding: [{code: 'laboratory'}]}]"
    )
    note = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False
    )

    class Meta:
        from .models import ServiceRequest
        model = ServiceRequest
        fields = [
            'id', 'status',
            'subject', 'encounter_ref', 'requester', 'performer',
            'code', 'category', 'note',
        ]
        extra_kwargs = {'id': {'read_only': True}}

    def validate(self, attrs):
        for required in ('subject', 'requester', 'performer', 'code'):
            if required not in attrs:
                raise serializers.ValidationError(
                    {required: f"ServiceRequest.{required} is required."}
                )
        return attrs

    def create(self, validated_data):
        from .models import Patient, Encounter, ServiceRequest
        subject_data   = validated_data.pop('subject')
        encounter_data = validated_data.pop('encounter', None)
        requester_data = validated_data.pop('requester')
        performer_data = validated_data.pop('performer')
        code_data      = validated_data.pop('code')
        category_data  = validated_data.pop('category', [])
        note_data      = validated_data.pop('note', [])

        # Patient
        patient_uuid = subject_data.get('reference', '').split('/')[-1]
        patient = Patient.objects.get(id=patient_uuid)

        # Encounter (optional)
        encounter = None
        if encounter_data:
            enc_uuid = encounter_data.get('reference', '').split('/')[-1]
            encounter = Encounter.objects.filter(id=enc_uuid).first()

        # Clinician
        ordered_by_id, ordered_by_display = _parse_reference(requester_data)

        # Organisation
        org_id, org_display = _parse_reference(performer_data[0]) if performer_data else (None, None)

        # Category
        category = 'laboratory'
        if category_data:
            codings = category_data[0].get('coding', [])
            if codings:
                category = codings[0].get('code', 'laboratory')

        # Notes
        notes = '; '.join(n.get('text', '') for n in note_data) or None

        return ServiceRequest.objects.create(
            patient=patient,
            encounter=encounter,
            ordered_by_id=ordered_by_id,
            ordered_by_display=ordered_by_display,
            organization_id=org_id,
            organization_display=org_display,
            test_type=code_data.get('text', ''),
            category=category,
            notes=notes,
            **validated_data,
        )

    def update(self, instance, validated_data):
        validated_data.pop('subject', None)
        code_data      = validated_data.pop('code', None)
        requester_data = validated_data.pop('requester', None)
        performer_data = validated_data.pop('performer', None)
        note_data      = validated_data.pop('note', None)
        validated_data.pop('encounter', None)
        validated_data.pop('category', None)

        if code_data:
            instance.test_type = code_data.get('text', instance.test_type)
        if requester_data:
            rid, rdisplay = _parse_reference(requester_data)
            if rid:
                instance.ordered_by_id = rid
                instance.ordered_by_display = rdisplay
        if performer_data:
            oid, odisplay = _parse_reference(performer_data[0])
            if oid:
                instance.organization_id = oid
                instance.organization_display = odisplay
        if note_data is not None:
            instance.notes = '; '.join(n.get('text', '') for n in note_data) or None

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance

    def to_representation(self, instance):
        return {
            "resourceType": "ServiceRequest",
            "id": str(instance.id),
            "identifier": [{"system": "http://phs.mw/cpr/service-requests", "value": instance.service_request_id}],
            "status": instance.status,
            "intent": "order",
            "category": [{
                "coding": [{"code": instance.category, "display": instance.get_category_display()}]
            }],
            "code": {"text": instance.test_type},
            "subject": {
                "reference": f"Patient/{instance.patient.id}",
                "display": instance.patient.full_name,
            },
            "encounter": {
                "reference": f"Encounter/{instance.encounter.id}",
            } if instance.encounter_id else None,
            "occurrenceDateTime": instance.request_date.isoformat(),
            "requester": {
                "reference": f"Practitioner/{instance.ordered_by_id}",
                "display": instance.ordered_by_display,
            },
            "performer": [{
                "reference": f"Organization/{instance.organization_id}",
                "display": instance.organization_display,
            }],
            "note": [{"text": instance.notes}] if instance.notes else [],
        }


# ── DiagnosticObservation (inline) ───────────────────────────────────────────

class DiagnosticObservationSerializer(serializers.ModelSerializer):
    """
    FHIR R4 Observation — nested inside DiagnosticReport.

    Write (as part of DiagnosticReport.result):
        {
          "code": {"text": "Haemoglobin", "coding": [{"code": "718-7"}]},
          "valueQuantity": {"value": 11.5, "unit": "g/dL"},
          "valueString": "Positive",          // alternative to valueQuantity
          "referenceRange": [{"text": "12–16 g/dL"}],
          "interpretation": [{"coding": [{"code": "L"}], "text": "abnormal"}],
          "note": [{"text": "Borderline"}]
        }
    """

    code = serializers.DictField(write_only=True, help_text="FHIR: Observation.code")
    valueQuantity = serializers.DictField(write_only=True, required=False)
    valueString   = serializers.CharField(write_only=True, required=False, allow_blank=True)
    referenceRange = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)
    interpretation = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)
    note = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)

    class Meta:
        from .models import DiagnosticObservation
        model = DiagnosticObservation
        fields = ['id', 'code', 'valueQuantity', 'valueString', 'referenceRange', 'interpretation', 'note']
        extra_kwargs = {'id': {'read_only': True}}

    def to_internal_value(self, data):
        result = super().to_internal_value(data)
        # Map FHIR fields → model fields
        code_data = result.pop('code', {})
        result['test_name']  = code_data.get('text', '')
        codings = code_data.get('coding', [])
        result['loinc_code'] = codings[0].get('code') if codings else None

        vq = result.pop('valueQuantity', None)
        if vq:
            result['value_quantity'] = vq.get('value')
            result['value_unit']     = vq.get('unit')

        result['value_string'] = result.pop('valueString', None)

        rr = result.pop('referenceRange', [])
        result['reference_range'] = rr[0].get('text') if rr else None

        interp_list = result.pop('interpretation', [])
        if interp_list:
            interp_code = interp_list[0].get('coding', [{}])[0].get('code', '').lower()
            interp_text = interp_list[0].get('text', '').lower()
            # Map HL7 codes to model choices
            mapping = {'h': 'abnormal', 'l': 'abnormal', 'a': 'abnormal',
                       'aa': 'critical', 'hh': 'critical', 'll': 'critical',
                       'n': 'normal', 'normal': 'normal', 'abnormal': 'abnormal', 'critical': 'critical'}
            result['interpretation'] = mapping.get(interp_code) or mapping.get(interp_text, 'normal')

        note_list = result.pop('note', [])
        result['comments'] = '; '.join(n.get('text', '') for n in note_list) or None

        return result

    def to_representation(self, instance):
        rep = {
            "resourceType": "Observation",
            "id": str(instance.id),
            "status": "final",
            "code": {"text": instance.test_name},
            "interpretation": [{"text": instance.get_interpretation_display()}],
        }
        if instance.loinc_code:
            rep["code"]["coding"] = [{"system": "http://loinc.org", "code": instance.loinc_code}]
        if instance.value_quantity is not None:
            rep["valueQuantity"] = {"value": float(instance.value_quantity), "unit": instance.value_unit}
        if instance.value_string:
            rep["valueString"] = instance.value_string
        if instance.reference_range:
            rep["referenceRange"] = [{"text": instance.reference_range}]
        if instance.comments:
            rep["note"] = [{"text": instance.comments}]
        return rep


# ── DiagnosticReport (Lab Results) ───────────────────────────────────────────

class DiagnosticReportSerializer(serializers.ModelSerializer):
    """
    FHIR R4 DiagnosticReport serializer with nested Observations.

    Write:
        {
          "resourceType": "DiagnosticReport",
          "status": "final",
          "basedOn": [{"reference": "ServiceRequest/uuid"}],
          "performer": [{"reference": "Practitioner/uuid", "display": "Lab Tech"}],
          "conclusion": "All values within normal range.",
          "result": [                  // DiagnosticObservation list
            { "code": {...}, "valueQuantity": {...}, ... }
          ]
        }
    """

    basedOn = serializers.ListField(
        child=serializers.DictField(), write_only=True,
        help_text="FHIR: DiagnosticReport.basedOn — [{reference: 'ServiceRequest/uuid'}]"
    )
    performer = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False,
        help_text="FHIR: DiagnosticReport.performer — [{reference, display}]"
    )
    result = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False,
        help_text="FHIR: DiagnosticReport.result — list of Observation objects"
    )

    # nested read output
    observations = DiagnosticObservationSerializer(many=True, read_only=True)

    class Meta:
        from .models import DiagnosticReport
        model = DiagnosticReport
        fields = [
            'id', 'status', 'interpretation', 'conclusion',
            'basedOn', 'performer', 'result', 'observations',
        ]
        extra_kwargs = {
            'id': {'read_only': True},
            'interpretation': {'required': False},
        }

    def validate(self, attrs):
        if 'basedOn' not in attrs:
            raise serializers.ValidationError({"basedOn": "DiagnosticReport.basedOn (ServiceRequest ref) is required."})
        return attrs

    def create(self, validated_data):
        from .models import ServiceRequest, DiagnosticReport, DiagnosticObservation
        based_on_data  = validated_data.pop('basedOn')
        performer_data = validated_data.pop('performer', [])
        result_data    = validated_data.pop('result', [])

        # ServiceRequest
        sr_uuid = based_on_data[0].get('reference', '').split('/')[-1]
        service_request = ServiceRequest.objects.get(id=sr_uuid)

        # Performer
        performer_id, performer_display = _parse_reference(performer_data[0]) if performer_data else (None, None)

        report = DiagnosticReport.objects.create(
            service_request=service_request,
            performer_id=performer_id,
            performer_display=performer_display,
            **validated_data,
        )

        # Create nested Observations
        for obs_data in result_data:
            obs_serializer = DiagnosticObservationSerializer(data=obs_data)
            obs_serializer.is_valid(raise_exception=True)
            DiagnosticObservation.objects.create(
                report=report,
                **obs_serializer.validated_data,
            )

        return report

    def to_representation(self, instance):
        obs_qs = instance.observations.all()
        return {
            "resourceType": "DiagnosticReport",
            "id": str(instance.id),
            "status": instance.status,
            "basedOn": [{"reference": f"ServiceRequest/{instance.service_request.id}"}],
            "subject": {
                "reference": f"Patient/{instance.service_request.patient.id}",
                "display": instance.service_request.patient.full_name,
            },
            "issued": instance.issued.isoformat(),
            "performer": [{
                "reference": f"Practitioner/{instance.performer_id}",
                "display": instance.performer_display,
            }] if instance.performer_id else [],
            "interpretation": [{"text": instance.get_interpretation_display()}],
            "conclusion": instance.conclusion,
            "result": DiagnosticObservationSerializer(obs_qs, many=True).data,
        }


# ── MedicationDispense (inline) ───────────────────────────────────────────────

class MedicationDispenseSerializer(serializers.ModelSerializer):
    """
    FHIR R4 MedicationDispense — nested inside MedicationRequest.

    Write (as part of MedicationRequest.contained):
        {
          "medicationCodeableConcept": {"text": "Amoxicillin 500mg"},
          "dosageInstruction": [{
            "timing": {
              "repeat": {"frequency": 3, "period": 1, "periodUnit": "d",
                         "when": ["MORN","AFT","EVE"]}
            },
            "additionalInstruction": [{"text": "After meals"}],
            "patientInstruction": "Take 3 times daily after meals for 7 days",
            "doseAndRate": [{"doseQuantity": {"value": 1, "unit": "tablet"}}]
          }],
          "daysSupply": {"value": 7},
          "performer": [{"actor": {"reference": "Practitioner/uuid", "display": "Pharm. Mwale"}}],
          "whenHandedOver": "2026-01-01T10:00:00Z",
          "note": [{"text": "Special instructions"}]
        }
    """

    medicationCodeableConcept = serializers.DictField(write_only=True)
    dosageInstruction = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)
    daysSupply = serializers.DictField(write_only=True, required=False)
    performer  = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)
    whenHandedOver = serializers.DateTimeField(write_only=True, required=False)
    note       = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)

    class Meta:
        from .models import MedicationDispense
        model = MedicationDispense
        fields = [
            'id',
            'medicationCodeableConcept', 'dosageInstruction',
            'daysSupply', 'performer', 'whenHandedOver', 'note',
        ]
        extra_kwargs = {'id': {'read_only': True}}

    def to_internal_value(self, data):
        result = super().to_internal_value(data)

        # Drug name
        med = result.pop('medicationCodeableConcept', {})
        result['drug_name'] = med.get('text', '')

        # Dosage schedule
        dose_instructions = result.pop('dosageInstruction', [])
        if dose_instructions:
            di   = dose_instructions[0]
            when = di.get('timing', {}).get('repeat', {}).get('when', [])
            dose_rate = di.get('doseAndRate', [{}])[0].get('doseQuantity', {}).get('value', 1)
            result['dosage_morning']   = int(dose_rate) if 'MORN' in when else 0
            result['dosage_afternoon'] = int(dose_rate) if 'AFT'  in when else 0
            result['dosage_evening']   = int(dose_rate) if 'EVE'  in when else 0

            # Meal timing from additionalInstruction
            ai_text = ''
            if di.get('additionalInstruction'):
                ai_text = di['additionalInstruction'][0].get('text', '').lower()
            if 'before' in ai_text:
                result['meal_timing'] = 'before_meal'
            elif 'after' in ai_text:
                result['meal_timing'] = 'after_meal'
            elif 'with' in ai_text:
                result['meal_timing'] = 'with_meal'
            else:
                result['meal_timing'] = 'anytime'

            result['special_instructions'] = di.get('patientInstruction')

        # Duration
        days = result.pop('daysSupply', None)
        result['duration_days'] = days.get('value') if days else None

        # Pharmacist
        performers = result.pop('performer', [])
        if performers:
            actor = performers[0].get('actor', {})
            pid, pdisplay = _parse_reference(actor)
            result['dispensed_by_id']      = pid
            result['dispensed_by_display'] = pdisplay

        # Dispensed date
        result['dispensed_date'] = result.pop('whenHandedOver', None)

        # Note → special_instructions (append if already set)
        notes = result.pop('note', [])
        if notes:
            note_text = '; '.join(n.get('text', '') for n in notes)
            existing  = result.get('special_instructions')
            result['special_instructions'] = f"{existing}. {note_text}" if existing else note_text

        return result

    def to_representation(self, instance):
        when = []
        if instance.dosage_morning:   when.append('MORN')
        if instance.dosage_afternoon: when.append('AFT')
        if instance.dosage_evening:   when.append('EVE')
        dose_val = max(instance.dosage_morning, instance.dosage_afternoon, instance.dosage_evening) or 1

        rep = {
            "resourceType": "MedicationDispense",
            "id": str(instance.id),
            "status": instance.status,
            "medicationCodeableConcept": {"text": instance.drug_name},
            "dosageInstruction": [{
                "timing": {"repeat": {
                    "frequency": len(when),
                    "period": 1,
                    "periodUnit": "d",
                    "when": when,
                }},
                "additionalInstruction": [{"text": instance.get_meal_timing_display()}],
                "patientInstruction": instance.special_instructions,
                "doseAndRate": [{"doseQuantity": {"value": dose_val, "unit": "tablet"}}],
            }],
        }

        if instance.duration_days:
            rep["daysSupply"] = {"value": instance.duration_days, "unit": "d"}

        if instance.dispensed_by_id:
            rep["performer"] = [{"actor": {
                "reference": f"Practitioner/{instance.dispensed_by_id}",
                "display": instance.dispensed_by_display,
            }}]

        if instance.dispensed_date:
            rep["whenHandedOver"] = instance.dispensed_date.isoformat()

        return rep


# ── MedicationRequest (Prescription) ─────────────────────────────────────────

class MedicationRequestSerializer(serializers.ModelSerializer):
    """
    FHIR R4 MedicationRequest serializer with nested MedicationDispenses.

    Write:
        {
          "resourceType": "MedicationRequest",
          "status": "active",
          "intent": "order",
          "subject": {"reference": "Patient/uuid"},
          "encounter": {"reference": "Encounter/uuid"},    // optional
          "requester": {"reference": "Practitioner/uuid", "display": "Dr. Banda"},
          "dispenseRequest": {"performer": {"reference": "Organization/uuid", "display": "KCH"}},
          "note": [{"text": "Take with plenty of water"}],
          "contained": [                         // MedicationDispense objects
            { "medicationCodeableConcept": {...}, "dosageInstruction": [...], ... }
          ]
        }
    """

    subject = serializers.DictField(write_only=True)
    encounter_ref = serializers.DictField(
        write_only=True, required=False,
        help_text="FHIR: MedicationRequest.encounter — {reference: 'Encounter/uuid'}"
    )
    requester = serializers.DictField(write_only=True)
    dispenseRequest = serializers.DictField(write_only=True)
    note = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)
    contained = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False,
        help_text="List of MedicationDispense FHIR objects (one per drug)"
    )

    # nested read output
    dispenses = MedicationDispenseSerializer(many=True, read_only=True)

    class Meta:
        from .models import MedicationRequest
        model = MedicationRequest
        fields = [
            'id', 'status', 'intent',
            'subject', 'encounter_ref', 'requester', 'dispenseRequest',
            'note', 'contained', 'dispenses',
        ]
        extra_kwargs = {'id': {'read_only': True}}

    def validate(self, attrs):
        for required in ('subject', 'requester', 'dispenseRequest'):
            if required not in attrs:
                raise serializers.ValidationError(
                    {required: f"MedicationRequest.{required} is required."}
                )
        return attrs

    def create(self, validated_data):
        from .models import Patient, Encounter, MedicationRequest, MedicationDispense
        subject_data       = validated_data.pop('subject')
        encounter_ref_data = validated_data.pop('encounter_ref', None)
        requester_data     = validated_data.pop('requester')
        dispense_req_data  = validated_data.pop('dispenseRequest')
        note_data          = validated_data.pop('note', [])
        contained_data     = validated_data.pop('contained', [])

        # Patient
        patient_uuid = subject_data.get('reference', '').split('/')[-1]
        patient = Patient.objects.get(id=patient_uuid)

        # Encounter (optional)
        encounter = None
        if encounter_ref_data:
            enc_uuid = encounter_ref_data.get('reference', '').split('/')[-1]
            encounter = Encounter.objects.filter(id=enc_uuid).first()

        # Prescriber
        prescribed_by_id, prescribed_by_display = _parse_reference(requester_data)

        # Organisation (from dispenseRequest.performer)
        org_ref = dispense_req_data.get('performer', {})
        org_id, org_display = _parse_reference(org_ref)

        notes = '; '.join(n.get('text', '') for n in note_data) or None

        med_request = MedicationRequest.objects.create(
            patient=patient,
            encounter=encounter,
            prescribed_by_id=prescribed_by_id,
            prescribed_by_display=prescribed_by_display,
            organization_id=org_id,
            organization_display=org_display,
            notes=notes,
            **validated_data,
        )

        # Create nested MedicationDispenses
        for dispense_data in contained_data:
            disp_serializer = MedicationDispenseSerializer(data=dispense_data)
            disp_serializer.is_valid(raise_exception=True)
            MedicationDispense.objects.create(
                medication_request=med_request,
                **disp_serializer.validated_data,
            )

        return med_request

    def update(self, instance, validated_data):
        validated_data.pop('subject', None)
        validated_data.pop('encounter_ref', None)
        validated_data.pop('contained', None)

        requester_data    = validated_data.pop('requester', None)
        dispense_req_data = validated_data.pop('dispenseRequest', None)
        note_data         = validated_data.pop('note', None)

        if requester_data:
            rid, rdisplay = _parse_reference(requester_data)
            if rid:
                instance.prescribed_by_id = rid
                instance.prescribed_by_display = rdisplay

        if dispense_req_data:
            org_ref = dispense_req_data.get('performer', {})
            oid, odisplay = _parse_reference(org_ref)
            if oid:
                instance.organization_id = oid
                instance.organization_display = odisplay

        if note_data is not None:
            instance.notes = '; '.join(n.get('text', '') for n in note_data) or None

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance

    def to_representation(self, instance):
        dispenses_qs = instance.dispenses.all()
        return {
            "resourceType": "MedicationRequest",
            "id": str(instance.id),
            "identifier": [{"system": "http://phs.mw/cpr/prescriptions", "value": instance.prescription_id}],
            "status": instance.status,
            "intent": instance.intent,
            "subject": {
                "reference": f"Patient/{instance.patient.id}",
                "display": instance.patient.full_name,
            },
            "encounter": {
                "reference": f"Encounter/{instance.encounter.id}",
            } if instance.encounter_id else None,
            "authoredOn": instance.prescription_date.isoformat(),
            "requester": {
                "reference": f"Practitioner/{instance.prescribed_by_id}",
                "display": instance.prescribed_by_display,
            },
            "dispenseRequest": {
                "performer": {
                    "reference": f"Organization/{instance.organization_id}",
                    "display": instance.organization_display,
                }
            },
            "note": [{"text": instance.notes}] if instance.notes else [],
            "contained": MedicationDispenseSerializer(dispenses_qs, many=True).data,
        }


def _extract_code_display_fhir(codeable_concept, field_name):
    if not isinstance(codeable_concept, dict):
        raise serializers.ValidationError({field_name: "Must be an object."})

    coding = codeable_concept.get('coding') or []
    if coding and isinstance(coding[0], dict):
        code = coding[0].get('code')
        display = coding[0].get('display') or codeable_concept.get('text')
        if code and display:
            return code, display

    code = codeable_concept.get('code')
    display = codeable_concept.get('display') or codeable_concept.get('text')
    if code and display:
        return code, display

    raise serializers.ValidationError({
        field_name: "coding[0].code and display/text are required."
    })


class VaccinationSerializer(serializers.ModelSerializer):
    resourceType = serializers.CharField(required=False, write_only=True)
    patient = serializers.DictField(write_only=True)
    vaccineCode = serializers.DictField(write_only=True)
    occurrenceDateTime = serializers.DateTimeField(write_only=True)
    performer = serializers.ListField(
        child=serializers.DictField(), required=False, write_only=True
    )
    lotNumber = serializers.CharField(
        required=False, allow_blank=True, write_only=True
    )
    note = serializers.ListField(
        child=serializers.DictField(), required=False, write_only=True
    )

    class Meta:
        from .models import Vaccination
        model = Vaccination
        fields = [
            'id', 'resourceType', 'status', 'patient', 'vaccineCode',
            'occurrenceDateTime', 'performer', 'lotNumber', 'note',
        ]
        extra_kwargs = {'id': {'read_only': True}}

    def validate(self, attrs):
        resource_type = attrs.get('resourceType')
        if resource_type and resource_type != 'Immunization':
            raise serializers.ValidationError({'resourceType': 'Must be Immunization.'})
        return attrs

    def create(self, validated_data):
        from .models import Patient, Vaccination

        validated_data.pop('resourceType', None)
        patient_ref = validated_data.pop('patient', {}).get('reference', '')
        patient_id = patient_ref.split('/')[-1]
        patient = Patient.objects.get(id=patient_id)

        vaccine_code, vaccine_display = _extract_code_display_fhir(
            validated_data.pop('vaccineCode'), 'vaccineCode'
        )
        performer_data = validated_data.pop('performer', [])
        note_data = validated_data.pop('note', [])

        performer_id = None
        performer_display = None
        if performer_data:
            actor = performer_data[0].get('actor', {})
            performer_id, performer_display = _parse_reference(actor)

        return Vaccination.objects.create(
            patient=patient,
            status=validated_data.get('status', 'completed'),
            vaccine_code=vaccine_code,
            vaccine_display=vaccine_display,
            occurrence_date=validated_data.get('occurrenceDateTime'),
            performer_id=performer_id,
            performer_display=performer_display,
            lot_number=validated_data.get('lotNumber'),
            note='; '.join(n.get('text', '') for n in note_data) or None,
        )

    def update(self, instance, validated_data):
        validated_data.pop('resourceType', None)

        if 'patient' in validated_data:
            from .models import Patient
            patient_ref = validated_data['patient'].get('reference', '')
            patient_id = patient_ref.split('/')[-1]
            instance.patient = Patient.objects.get(id=patient_id)

        if 'status' in validated_data:
            instance.status = validated_data['status']

        if 'vaccineCode' in validated_data:
            instance.vaccine_code, instance.vaccine_display = _extract_code_display_fhir(
                validated_data['vaccineCode'], 'vaccineCode'
            )

        if 'occurrenceDateTime' in validated_data:
            instance.occurrence_date = validated_data['occurrenceDateTime']

        if 'performer' in validated_data:
            performers = validated_data['performer']
            if performers:
                actor = performers[0].get('actor', {})
                instance.performer_id, instance.performer_display = _parse_reference(actor)
            else:
                instance.performer_id = None
                instance.performer_display = None

        if 'lotNumber' in validated_data:
            instance.lot_number = validated_data['lotNumber']

        if 'note' in validated_data:
            notes = validated_data['note']
            instance.note = '; '.join(n.get('text', '') for n in notes) or None

        instance.save()
        return instance

    def to_representation(self, instance):
        payload = {
            "resourceType": "Immunization",
            "id": str(instance.id),
            "status": instance.status,
            "vaccineCode": {
                "coding": [{
                    "code": instance.vaccine_code,
                    "display": instance.vaccine_display,
                }],
                "text": instance.vaccine_display,
            },
            "patient": {
                "reference": f"Patient/{instance.patient.id}",
                "display": instance.patient.full_name,
            },
            "occurrenceDateTime": instance.occurrence_date.isoformat() if instance.occurrence_date else None,
        }

        if instance.performer_id or instance.performer_display:
            payload["performer"] = [{
                "actor": {
                    "reference": f"Practitioner/{instance.performer_id}" if instance.performer_id else None,
                    "display": instance.performer_display,
                }
            }]

        if instance.lot_number:
            payload["lotNumber"] = instance.lot_number

        if instance.note:
            payload["note"] = [{"text": instance.note}]

        return payload


class DiagnosticTestSerializer(serializers.ModelSerializer):
    resourceType = serializers.CharField(required=False, write_only=True)
    code = serializers.DictField(write_only=True)
    subject = serializers.DictField(write_only=True)
    effectiveDateTime = serializers.DateTimeField(required=False, write_only=True)
    issued = serializers.DateTimeField(
        required=False, allow_null=True, write_only=True
    )
    conclusionCode = serializers.ListField(
        child=serializers.DictField(), required=False, write_only=True
    )
    performer = serializers.ListField(
        child=serializers.DictField(), required=False, write_only=True
    )

    class Meta:
        from .models import DiagnosticTest
        model = DiagnosticTest
        fields = [
            'id', 'resourceType', 'status', 'code', 'subject',
            'effectiveDateTime', 'issued', 'performer',
            'conclusion', 'conclusionCode',
        ]
        extra_kwargs = {'id': {'read_only': True}}

    def validate(self, attrs):
        resource_type = attrs.get('resourceType')
        if resource_type and resource_type != 'DiagnosticReport':
            raise serializers.ValidationError({'resourceType': 'Must be DiagnosticReport.'})
        return attrs

    def create(self, validated_data):
        from .models import Patient, DiagnosticTest

        validated_data.pop('resourceType', None)
        subject_ref = validated_data.pop('subject', {}).get('reference', '')
        patient_id = subject_ref.split('/')[-1]
        patient = Patient.objects.get(id=patient_id)

        code, display = _extract_code_display_fhir(validated_data.pop('code'), 'code')
        conclusion_code = validated_data.pop('conclusionCode', [])
        performer_data = validated_data.pop('performer', [])

        performer_id = None
        performer_display = None
        if performer_data:
            performer_id, performer_display = _parse_reference(performer_data[0])

        return DiagnosticTest.objects.create(
            patient=patient,
            status=validated_data.get('status', 'final'),
            test_code=code,
            test_display=display,
            effective_datetime=validated_data.get('effectiveDateTime') or timezone.now(),
            issued_at=validated_data.get('issued'),
            conclusion=validated_data.get('conclusion'),
            result_value=(conclusion_code[0].get('text') if conclusion_code else None),
            performer_id=performer_id,
            performer_display=performer_display,
        )

    def update(self, instance, validated_data):
        validated_data.pop('resourceType', None)

        if 'subject' in validated_data:
            from .models import Patient
            subject_ref = validated_data['subject'].get('reference', '')
            patient_id = subject_ref.split('/')[-1]
            instance.patient = Patient.objects.get(id=patient_id)

        if 'status' in validated_data:
            instance.status = validated_data['status']

        if 'code' in validated_data:
            instance.test_code, instance.test_display = _extract_code_display_fhir(
                validated_data['code'], 'code'
            )

        if 'effectiveDateTime' in validated_data:
            instance.effective_datetime = validated_data['effectiveDateTime']

        if 'issued' in validated_data:
            instance.issued_at = validated_data['issued']

        if 'conclusion' in validated_data:
            instance.conclusion = validated_data['conclusion']

        if 'conclusionCode' in validated_data:
            conclusion_code = validated_data['conclusionCode']
            instance.result_value = conclusion_code[0].get('text') if conclusion_code else None

        if 'performer' in validated_data:
            performer_data = validated_data['performer']
            if performer_data:
                instance.performer_id, instance.performer_display = _parse_reference(performer_data[0])
            else:
                instance.performer_id = None
                instance.performer_display = None

        instance.save()
        return instance

    def to_representation(self, instance):
        payload = {
            "resourceType": "DiagnosticReport",
            "id": str(instance.id),
            "status": instance.status,
            "code": {
                "coding": [{
                    "code": instance.test_code,
                    "display": instance.test_display,
                }],
                "text": instance.test_display,
            },
            "subject": {
                "reference": f"Patient/{instance.patient.id}",
                "display": instance.patient.full_name,
            },
            "conclusion": instance.conclusion,
        }

        if instance.effective_datetime:
            payload["effectiveDateTime"] = instance.effective_datetime.isoformat()
        if instance.issued_at:
            payload["issued"] = instance.issued_at.isoformat()
        if instance.result_value:
            payload["conclusionCode"] = [{"text": instance.result_value}]
        if instance.performer_id or instance.performer_display:
            payload["performer"] = [{
                "reference": f"Practitioner/{instance.performer_id}" if instance.performer_id else None,
                "display": instance.performer_display,
            }]

        return payload


class AppointmentSerializer(serializers.ModelSerializer):
    resourceType = serializers.CharField(required=False, write_only=True)
    appointmentType = serializers.DictField(required=False, write_only=True)
    participant = serializers.ListField(
        child=serializers.DictField(), write_only=True
    )
    start = serializers.DateTimeField(write_only=True)
    end = serializers.DateTimeField(required=False, allow_null=True, write_only=True)

    class Meta:
        from .models import Appointment
        model = Appointment
        fields = [
            'id', 'resourceType', 'status', 'appointmentType',
            'description', 'comment', 'start', 'end', 'participant',
        ]
        extra_kwargs = {'id': {'read_only': True}}

    def _request_patient(self):
        request = self.context.get('request')
        if request and request.user.is_authenticated and hasattr(request.user, 'patient_profile'):
            return request.user.patient_profile
        return None

    def _extract_patient_from_participants(self, participants, fallback_patient=None):
        if not participants:
            if fallback_patient is not None:
                return fallback_patient
            raise serializers.ValidationError({'participant': 'At least one participant is required.'})

        from .models import Patient
        for participant in participants:
            actor = participant.get('actor', {})
            reference = actor.get('reference')
            if isinstance(reference, str) and reference.startswith('Patient/'):
                patient_id = reference.split('/')[-1]
                return Patient.objects.get(id=patient_id)

        if fallback_patient is not None:
            return fallback_patient

        raise serializers.ValidationError({
            'participant': 'A Patient participant is required (Patient/<id>).'
        })

    def _extract_secondary_participants(self, participants):
        practitioner_id = None
        practitioner_display = None
        location_id = None
        location_display = None

        for participant in participants:
            actor = participant.get('actor', {})
            reference = actor.get('reference', '')
            display = actor.get('display')

            if isinstance(reference, str) and reference.startswith('Practitioner/') and not practitioner_id:
                practitioner_id = reference.split('/')[-1]
                practitioner_display = display or reference

            if isinstance(reference, str) and reference.startswith('Location/') and not location_id:
                location_id = reference.split('/')[-1]
                location_display = display or reference

        return practitioner_id, practitioner_display, location_id, location_display

    def validate(self, attrs):
        resource_type = attrs.get('resourceType')
        if resource_type and resource_type != 'Appointment':
            raise serializers.ValidationError({'resourceType': 'Must be Appointment.'})

        if attrs.get('end') and attrs.get('start') and attrs['end'] <= attrs['start']:
            raise serializers.ValidationError({'end': 'Appointment end must be after start.'})

        request = self.context.get('request')
        participants = attrs.get('participant')
        if request and request.user.is_authenticated and hasattr(request.user, 'patient_profile') and participants:
            requested_patient = self._extract_patient_from_participants(participants, fallback_patient=None)
            if requested_patient and requested_patient.id != request.user.patient_profile.id:
                raise serializers.ValidationError({
                    'participant': 'You can only create or update appointments for your own patient profile.'
                })

        return attrs

    def create(self, validated_data):
        from .models import Appointment

        validated_data.pop('resourceType', None)
        participants = validated_data.pop('participant', [])
        patient = self._extract_patient_from_participants(
            participants,
            fallback_patient=self._request_patient(),
        )
        practitioner_id, practitioner_display, location_id, location_display = \
            self._extract_secondary_participants(participants)

        appointment_type = validated_data.pop('appointmentType', {})
        type_code = None
        type_display = None
        if appointment_type:
            type_code, type_display = _extract_code_display_fhir(appointment_type, 'appointmentType')

        return Appointment.objects.create(
            patient=patient,
            status=validated_data.get('status', 'booked'),
            appointment_type_code=type_code,
            appointment_type_display=type_display,
            description=validated_data.get('description'),
            comment=validated_data.get('comment'),
            start=validated_data['start'],
            end=validated_data.get('end'),
            practitioner_id=practitioner_id,
            practitioner_display=practitioner_display,
            location_id=location_id,
            location_display=location_display,
        )

    def update(self, instance, validated_data):
        validated_data.pop('resourceType', None)

        if 'status' in validated_data:
            instance.status = validated_data['status']

        if 'appointmentType' in validated_data:
            appointment_type = validated_data['appointmentType']
            instance.appointment_type_code, instance.appointment_type_display = _extract_code_display_fhir(
                appointment_type, 'appointmentType'
            )

        if 'description' in validated_data:
            instance.description = validated_data['description']
        if 'comment' in validated_data:
            instance.comment = validated_data['comment']
        if 'start' in validated_data:
            instance.start = validated_data['start']
        if 'end' in validated_data:
            instance.end = validated_data['end']

        if 'participant' in validated_data:
            participants = validated_data['participant']
            instance.patient = self._extract_patient_from_participants(
                participants,
                fallback_patient=self._request_patient(),
            )
            (
                instance.practitioner_id,
                instance.practitioner_display,
                instance.location_id,
                instance.location_display,
            ) = self._extract_secondary_participants(participants)

        if instance.end and instance.end <= instance.start:
            raise serializers.ValidationError({'end': 'Appointment end must be after start.'})

        instance.save()
        return instance

    def to_representation(self, instance):
        participant = [{
            "actor": {
                "reference": f"Patient/{instance.patient.id}",
                "display": instance.patient.full_name,
            },
            "status": "accepted",
        }]

        if instance.practitioner_id or instance.practitioner_display:
            participant.append({
                "actor": {
                    "reference": f"Practitioner/{instance.practitioner_id}" if instance.practitioner_id else None,
                    "display": instance.practitioner_display,
                },
                "status": "accepted",
            })

        if instance.location_id or instance.location_display:
            participant.append({
                "actor": {
                    "reference": f"Location/{instance.location_id}" if instance.location_id else None,
                    "display": instance.location_display,
                },
                "status": "accepted",
            })

        payload = {
            "resourceType": "Appointment",
            "id": str(instance.id),
            "status": instance.status,
            "description": instance.description,
            "comment": instance.comment,
            "start": instance.start.isoformat() if instance.start else None,
            "participant": participant,
        }

        if instance.end:
            payload["end"] = instance.end.isoformat()

        if instance.appointment_type_code or instance.appointment_type_display:
            payload["appointmentType"] = {
                "coding": [{
                    "code": instance.appointment_type_code,
                    "display": instance.appointment_type_display,
                }],
                "text": instance.appointment_type_display,
            }

        return payload


class ObservationSerializer(serializers.ModelSerializer):
    resourceType = serializers.CharField(required=False, write_only=True)
    subject = serializers.DictField(write_only=True)
    code = serializers.DictField(write_only=True)
    category = serializers.ListField(
        child=serializers.DictField(), required=False, write_only=True
    )
    effectiveDateTime = serializers.DateTimeField(
        required=False, allow_null=True, write_only=True
    )
    issued = serializers.DateTimeField(
        required=False, allow_null=True, write_only=True
    )
    valueString = serializers.CharField(
        required=False, allow_blank=True, write_only=True
    )
    valueQuantity = serializers.DictField(required=False, write_only=True)
    interpretation = serializers.ListField(
        child=serializers.DictField(), required=False, write_only=True
    )
    note = serializers.ListField(
        child=serializers.DictField(), required=False, write_only=True
    )

    class Meta:
        from .models import Observation
        model = Observation
        fields = [
            'id', 'resourceType', 'status', 'subject', 'code', 'category',
            'effectiveDateTime', 'issued', 'valueString', 'valueQuantity',
            'interpretation', 'note',
        ]
        extra_kwargs = {'id': {'read_only': True}}

    def validate(self, attrs):
        resource_type = attrs.get('resourceType')
        if resource_type and resource_type != 'Observation':
            raise serializers.ValidationError({'resourceType': 'Must be Observation.'})

        if 'valueString' in attrs and 'valueQuantity' in attrs:
            raise serializers.ValidationError({
                'valueQuantity': 'Use either valueString or valueQuantity, not both.'
            })

        value_quantity = attrs.get('valueQuantity')
        if value_quantity is not None and value_quantity.get('value') in (None, ''):
            raise serializers.ValidationError({
                'valueQuantity': 'valueQuantity.value is required when valueQuantity is provided.'
            })

        return attrs

    def create(self, validated_data):
        from .models import Observation, Patient

        validated_data.pop('resourceType', None)
        subject_ref = validated_data.pop('subject', {}).get('reference', '')
        patient_id = subject_ref.split('/')[-1]
        patient = Patient.objects.get(id=patient_id)

        observation_code, observation_display = _extract_code_display_fhir(
            validated_data.pop('code'),
            'code',
        )

        category_code = None
        category_display = None
        category_data = validated_data.pop('category', [])
        if category_data:
            category_code, category_display = _extract_code_display_fhir(
                category_data[0],
                'category[0]',
            )

        interpretation_code = None
        interpretation_display = None
        interpretation_data = validated_data.pop('interpretation', [])
        if interpretation_data:
            interpretation_code, interpretation_display = _extract_code_display_fhir(
                interpretation_data[0],
                'interpretation[0]',
            )

        value_quantity_value = None
        value_quantity_unit = None
        value_quantity_system = None
        value_quantity_code = None
        if 'valueQuantity' in validated_data:
            quantity = validated_data.pop('valueQuantity')
            value_quantity_value = quantity.get('value')
            value_quantity_unit = quantity.get('unit')
            value_quantity_system = quantity.get('system')
            value_quantity_code = quantity.get('code')

        notes = validated_data.pop('note', [])

        return Observation.objects.create(
            patient=patient,
            status=validated_data.get('status', 'final'),
            category_code=category_code,
            category_display=category_display,
            observation_code=observation_code,
            observation_display=observation_display,
            effective_datetime=validated_data.get('effectiveDateTime'),
            issued_at=validated_data.get('issued'),
            value_string=validated_data.get('valueString'),
            value_quantity_value=value_quantity_value,
            value_quantity_unit=value_quantity_unit,
            value_quantity_system=value_quantity_system,
            value_quantity_code=value_quantity_code,
            interpretation_code=interpretation_code,
            interpretation_display=interpretation_display,
            note=(notes[0].get('text') if notes else None),
        )

    def update(self, instance, validated_data):
        validated_data.pop('resourceType', None)

        if 'status' in validated_data:
            instance.status = validated_data['status']

        if 'subject' in validated_data:
            from .models import Patient
            subject_ref = validated_data['subject'].get('reference', '')
            patient_id = subject_ref.split('/')[-1]
            instance.patient = Patient.objects.get(id=patient_id)

        if 'code' in validated_data:
            instance.observation_code, instance.observation_display = _extract_code_display_fhir(
                validated_data['code'],
                'code',
            )

        if 'category' in validated_data:
            category_data = validated_data['category']
            if category_data:
                instance.category_code, instance.category_display = _extract_code_display_fhir(
                    category_data[0],
                    'category[0]',
                )
            else:
                instance.category_code = None
                instance.category_display = None

        if 'effectiveDateTime' in validated_data:
            instance.effective_datetime = validated_data['effectiveDateTime']
        if 'issued' in validated_data:
            instance.issued_at = validated_data['issued']

        if 'valueString' in validated_data:
            instance.value_string = validated_data['valueString']
            instance.value_quantity_value = None
            instance.value_quantity_unit = None
            instance.value_quantity_system = None
            instance.value_quantity_code = None

        if 'valueQuantity' in validated_data:
            quantity = validated_data['valueQuantity']
            instance.value_quantity_value = quantity.get('value')
            instance.value_quantity_unit = quantity.get('unit')
            instance.value_quantity_system = quantity.get('system')
            instance.value_quantity_code = quantity.get('code')
            instance.value_string = None

        if 'interpretation' in validated_data:
            interpretation_data = validated_data['interpretation']
            if interpretation_data:
                (
                    instance.interpretation_code,
                    instance.interpretation_display,
                ) = _extract_code_display_fhir(
                    interpretation_data[0],
                    'interpretation[0]',
                )
            else:
                instance.interpretation_code = None
                instance.interpretation_display = None

        if 'note' in validated_data:
            notes = validated_data['note']
            instance.note = notes[0].get('text') if notes else None

        instance.save()
        return instance

    def to_representation(self, instance):
        payload = {
            "resourceType": "Observation",
            "id": str(instance.id),
            "status": instance.status,
            "subject": {
                "reference": f"Patient/{instance.patient.id}",
                "display": instance.patient.full_name,
            },
            "code": {
                "coding": [{
                    "code": instance.observation_code,
                    "display": instance.observation_display,
                }],
                "text": instance.observation_display,
            },
        }

        if instance.category_code or instance.category_display:
            payload["category"] = [{
                "coding": [{
                    "code": instance.category_code,
                    "display": instance.category_display,
                }],
                "text": instance.category_display,
            }]

        if instance.effective_datetime:
            payload["effectiveDateTime"] = instance.effective_datetime.isoformat()
        if instance.issued_at:
            payload["issued"] = instance.issued_at.isoformat()

        if instance.value_quantity_value is not None:
            payload["valueQuantity"] = {
                "value": float(instance.value_quantity_value),
                "unit": instance.value_quantity_unit,
                "system": instance.value_quantity_system,
                "code": instance.value_quantity_code,
            }
        elif instance.value_string:
            payload["valueString"] = instance.value_string

        if instance.interpretation_code or instance.interpretation_display:
            payload["interpretation"] = [{
                "coding": [{
                    "code": instance.interpretation_code,
                    "display": instance.interpretation_display,
                }],
                "text": instance.interpretation_display,
            }]

        if instance.note:
            payload["note"] = [{"text": instance.note}]

        return payload
