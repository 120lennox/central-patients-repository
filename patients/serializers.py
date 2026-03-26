from .models import Patient
from rest_framework import serializers
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
            'birthDate',
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
            validated_data['place_of_residence']   = addr.get('text')
            validated_data['city']               = addr.get('line', [''])[0]
            validated_data['traditional_authority'] = addr.get('village')
            validated_data['district_of_origin']    = addr.get('district')

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
