from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db.models import Max
import uuid
import random

# Create your models here.
User = get_user_model()

class Patient(models.Model):
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
        ('unknown', 'Unknown'),
    ]

    # identifiers 
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient_id = models.CharField(max_length=30, unique=True, editable=False, db_index=True)
    national_id = models.CharField(max_length=30, unique=True, null=True, blank=True, db_index=True)
    digital_id = models.CharField(max_length=30, unique=True, null=True, blank=True, db_index=True)

    # user account link
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='patient_profile')
    account_claimed = models.BooleanField(default=False)

    # patient names fhir standard
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    full_name = models.CharField(max_length=200, db_index=True)

    # demographics
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, default='unknown')
    date_of_birth = models.DateField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    district_of_origin = models.CharField(max_length=100, null=True, blank=True)
    traditional_authority = models.CharField(max_length=100, null=True, blank=True)
    village = models.CharField(max_length=100, null=True, blank=True)
    place_of_residence = models.CharField(max_length=200, null=True, blank=True)

    # emergency contact
    close_relative_name = models.CharField(max_length=255, null=True, blank=True)
    close_relative_phone = models.CharField(max_length=15, null=True, blank=True)
    close_relative_relationship = models.CharField(max_length=50, null=True, blank=True)

    # managing organization information
    managing_organization_id = models.UUIDField(null=True, blank=True)
    managing_organization_display = models.CharField(max_length=255, null=True, blank=True)

    # General practitioner — FHIR: Patient.generalPractitioner
    # Cross-service reference to HealthProfessional in HMS (no FK)

    registered_by_staff_id = models.UUIDField(null=True, blank=True)
    registered_by_staff_display = models.CharField(max_length=255, null=True, blank=True)
    registration_date = models.DateTimeField(auto_now_add=True)

    active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'patients'
        indexes = [
            models.Index(fields=['full_name']),
            models.Index(fields=['phone_number']),
            models.Index(fields=['registration_date']),
            models.Index(fields=['national_id']),
            models.Index(fields=['digital_id']),
            models.Index(fields=['managing_organization_id']),
            models.Index(fields=['registered_by_staff_id']),
        ]
    ordering = ['-registration_date']
    verbose_name = 'Patient'
    verbose_name_plural = 'Patients'

    def __str__(self):
        return f"{self.full_name} ({self.patient_id})"

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        # Auto-build full_name from parts
        if not self.full_name:
            self.full_name = f"{self.first_name} {self.last_name}".strip()

        # Assign patient_id
        if not self.patient_id:
            if self.national_id:
                self.patient_id = self.national_id
            else:
                self.digital_id = self._generate_digital_id()
                self.patient_id = self.digital_id

        super().save(*args, **kwargs)

        # Generate OTP on first save
        if is_new:
            PatientOTP.objects.create(
                patient=self,
                otp_code=PatientOTP.generate_otp()
            )

    def _generate_digital_id(self):
        last = Patient.objects.filter(
            digital_id__isnull=False
        ).aggregate(Max('digital_id'))['digital_id__max']

        if last:
            try:
                new_number = int(last.split('-')[1]) + 1
            except (IndexError, ValueError):
                new_number = 1000
        else:
            new_number = 1000

        digital_id = f"DIG-{new_number:04d}"
        while Patient.objects.filter(digital_id=digital_id).exists():
            new_number += 1
            digital_id = f"DIG-{new_number:04d}"

        return digital_id

class PatientOTP(models.Model):

    patient = models.OneToOneField(
        Patient,
        on_delete=models.CASCADE,
        related_name='otp'
    )
    otp_code = models.CharField(max_length=6, unique=True)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'patient_otps'
        ordering = ['-created_at']
        verbose_name = 'Patient OTP'
        verbose_name_plural = 'Patient OTPs'

    def __str__(self):
        status = 'Used' if self.is_used else 'Active'
        return f"{self.patient.full_name} — {self.otp_code} ({status})"

    def mark_as_used(self):
        self.is_used = True
        self.used_at = timezone.now()
        self.save()

    def is_valid(self):
        return not self.is_used

    @classmethod
    def generate_otp(cls):
        return str(random.randint(100000, 999999))

    @classmethod
    def verify_otp(cls, patient_id, otp_code):
        try:
            otp = cls.objects.select_related('patient').get(
                patient__patient_id=patient_id,
                otp_code=otp_code
            )
            if otp.is_used:
                return False, "OTP has already been used.", None, None
            return True, "OTP is valid.", otp.patient, otp
        except cls.DoesNotExist:
            return False, "Invalid OTP.", None, None


# =============================================================================
# Clinical record models
# All HMS-side entities (clinicians, hospitals, pharmacists) are stored as
# UUID + display-name pairs — no cross-service foreign keys.
# Only Patient (same DB) uses a real FK.
# =============================================================================

class Encounter(models.Model):
    """
    FHIR R4: Encounter
    Records a single OPD visit / clinical consultation.
    Equivalent to NDHS OPDVisit.
    """

    STATUS_CHOICES = [
        ('planned',   'Planned'),
        ('in-progress', 'In Progress'),
        ('finished',  'Finished'),
        ('cancelled', 'Cancelled'),
    ]

    CLASS_CHOICES = [
        ('AMB',  'Ambulatory (OPD)'),
        ('EMER', 'Emergency'),
        ('IMP',  'Inpatient'),
        ('SS',   'Short Stay'),
    ]

    # Identifiers
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encounter_id = models.CharField(
        max_length=30, unique=True, editable=False, db_index=True,
        help_text="Human-readable ID — auto-generated as ENC-{NNNN}"
    )

    # Subject
    patient = models.ForeignKey(
        Patient, on_delete=models.PROTECT,
        related_name='encounters', db_index=True
    )

    # Status & class
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default='finished', db_index=True)
    encounter_class = models.CharField(max_length=10, choices=CLASS_CHOICES, default='AMB')

    # Clinical details
    visit_date = models.DateTimeField(default=timezone.now, db_index=True)
    symptoms   = models.TextField(null=True, blank=True, help_text="Chief complaint / presenting symptoms")
    diagnosis  = models.TextField(null=True, blank=True, help_text="Working / final diagnosis")
    notes      = models.TextField(null=True, blank=True, help_text="Clinician notes")

    # Ghost reference — clinician in HMS
    clinician_id      = models.UUIDField(
        db_index=True,
        help_text="UUID of the HealthProfessional in HMS"
    )
    clinician_display = models.CharField(
        max_length=255,
        help_text="Cached full name of the clinician at time of encounter"
    )

    # Ghost reference — organisation (hospital) in HMS
    organization_id      = models.UUIDField(
        db_index=True,
        help_text="UUID of the Hospital (Organization) in HMS"
    )
    organization_display = models.CharField(
        max_length=255,
        help_text="Cached hospital name at time of encounter"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'encounters'
        ordering = ['-visit_date']
        verbose_name = 'Encounter'
        verbose_name_plural = 'Encounters'
        indexes = [
            models.Index(fields=['patient']),
            models.Index(fields=['visit_date']),
            models.Index(fields=['clinician_id']),
            models.Index(fields=['organization_id']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.encounter_id} — {self.patient.full_name} ({self.visit_date.strftime('%Y-%m-%d')})"

    def save(self, *args, **kwargs):
        if not self.encounter_id:
            self.encounter_id = self._generate_encounter_id()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_encounter_id():
        last = Encounter.objects.filter(
            encounter_id__startswith='ENC-'
        ).aggregate(models.Max('encounter_id'))['encounter_id__max']
        try:
            n = int(last.split('-')[1]) + 1 if last else 1
        except (IndexError, ValueError):
            n = 1
        eid = f"ENC-{n:04d}"
        while Encounter.objects.filter(encounter_id=eid).exists():
            n += 1
            eid = f"ENC-{n:04d}"
        return eid


class ServiceRequest(models.Model):
    """
    FHIR R4: ServiceRequest
    A request for a diagnostic test ordered by a clinician.
    Equivalent to NDHS LabTestRequest.
    """

    STATUS_CHOICES = [
        ('draft',     'Draft'),
        ('active',    'Active'),
        ('completed', 'Completed'),
        ('revoked',   'Revoked'),
    ]

    CATEGORY_CHOICES = [
        ('laboratory',  'Laboratory'),
        ('imaging',     'Imaging'),
        ('pathology',   'Pathology'),
        ('other',       'Other'),
    ]

    # Identifiers
    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service_request_id = models.CharField(
        max_length=30, unique=True, editable=False, db_index=True,
        help_text="Human-readable ID — auto-generated as LAB-{NNNN}"
    )

    # Subject & context
    patient   = models.ForeignKey(
        Patient, on_delete=models.PROTECT,
        related_name='service_requests', db_index=True
    )
    encounter = models.ForeignKey(
        Encounter, on_delete=models.PROTECT,
        related_name='service_requests', null=True, blank=True
    )

    # Request details
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', db_index=True)
    category      = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='laboratory', db_index=True)
    test_type     = models.CharField(max_length=255, db_index=True, help_text="e.g. Blood Count, Urinalysis, X-Ray")
    request_date  = models.DateTimeField(default=timezone.now, db_index=True)
    notes         = models.TextField(null=True, blank=True, help_text="Clinical reason / instructions for the lab")

    # Ghost reference — requesting clinician
    ordered_by_id      = models.UUIDField(
        db_index=True,
        help_text="UUID of the requesting HealthProfessional in HMS"
    )
    ordered_by_display = models.CharField(max_length=255)

    # Ghost reference — organisation
    organization_id      = models.UUIDField(db_index=True)
    organization_display = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'service_requests'
        ordering = ['-request_date']
        verbose_name = 'Service Request'
        verbose_name_plural = 'Service Requests'
        indexes = [
            models.Index(fields=['patient']),
            models.Index(fields=['encounter']),
            models.Index(fields=['request_date']),
            models.Index(fields=['ordered_by_id']),
            models.Index(fields=['organization_id']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.service_request_id} — {self.test_type} for {self.patient.full_name}"

    def save(self, *args, **kwargs):
        if not self.service_request_id:
            self.service_request_id = self._generate_id()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_id():
        last = ServiceRequest.objects.filter(
            service_request_id__startswith='LAB-'
        ).aggregate(models.Max('service_request_id'))['service_request_id__max']
        try:
            n = int(last.split('-')[1]) + 1 if last else 1
        except (IndexError, ValueError):
            n = 1
        sid = f"LAB-{n:04d}"
        while ServiceRequest.objects.filter(service_request_id=sid).exists():
            n += 1
            sid = f"LAB-{n:04d}"
        return sid


class DiagnosticReport(models.Model):
    """
    FHIR R4: DiagnosticReport + Observation (inline)
    The result(s) returned against a ServiceRequest.
    One DiagnosticReport per ServiceRequest, many Observations inline as JSON
    or via the related DiagnosticObservation model below.
    Equivalent to NDHS LabTestResult.
    """

    INTERPRETATION_CHOICES = [
        ('normal',   'Normal'),
        ('abnormal', 'Abnormal'),
        ('critical', 'Critical'),
        ('pending',  'Pending'),
    ]

    STATUS_CHOICES = [
        ('registered',   'Registered'),
        ('partial',      'Partial'),
        ('final',        'Final'),
        ('amended',      'Amended'),
        ('cancelled',    'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    service_request = models.OneToOneField(
        ServiceRequest, on_delete=models.PROTECT,
        related_name='diagnostic_report',
        help_text="The ServiceRequest this report answers"
    )

    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default='final', db_index=True)
    issued         = models.DateTimeField(default=timezone.now, db_index=True)
    interpretation = models.CharField(
        max_length=20, choices=INTERPRETATION_CHOICES, default='pending', db_index=True,
        help_text="Overall interpretation of the report"
    )
    conclusion     = models.TextField(null=True, blank=True, help_text="Narrative summary of findings")

    # Ghost reference — performing lab / clinician
    performer_id      = models.UUIDField(null=True, blank=True, db_index=True)
    performer_display = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'diagnostic_reports'
        ordering = ['-issued']
        verbose_name = 'Diagnostic Report'
        verbose_name_plural = 'Diagnostic Reports'
        indexes = [
            models.Index(fields=['service_request']),
            models.Index(fields=['issued']),
            models.Index(fields=['status']),
            models.Index(fields=['interpretation']),
        ]

    def __str__(self):
        return f"Report for {self.service_request.service_request_id} — {self.get_interpretation_display()}"


class DiagnosticObservation(models.Model):
    """
    FHIR R4: Observation
    Individual result line within a DiagnosticReport.
    e.g. Haemoglobin = 11.5 g/dL (reference 12–16), interpretation: Abnormal
    """

    id     = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        DiagnosticReport, on_delete=models.CASCADE,
        related_name='observations'
    )

    # What was measured
    test_name  = models.CharField(max_length=255, db_index=True)
    # LOINC code for the test (optional but FHIR-friendly)
    loinc_code = models.CharField(max_length=20, null=True, blank=True)

    # Result
    value_string    = models.CharField(max_length=255, null=True, blank=True,
                                       help_text="Free-text result (Positive / Negative / …)")
    value_quantity  = models.DecimalField(max_digits=12, decimal_places=4,
                                          null=True, blank=True,
                                          help_text="Numeric result value")
    value_unit      = models.CharField(max_length=50, null=True, blank=True,
                                       help_text="Unit e.g. g/dL, mmol/L")

    reference_range = models.CharField(max_length=100, null=True, blank=True,
                                       help_text="e.g. 12–16 g/dL")
    interpretation  = models.CharField(
        max_length=20,
        choices=DiagnosticReport.INTERPRETATION_CHOICES,
        default='normal'
    )
    comments        = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'diagnostic_observations'
        ordering = ['test_name']
        verbose_name = 'Diagnostic Observation'
        verbose_name_plural = 'Diagnostic Observations'
        indexes = [
            models.Index(fields=['report']),
            models.Index(fields=['test_name']),
        ]

    def __str__(self):
        val = self.value_quantity or self.value_string or '—'
        return f"{self.test_name}: {val} {self.value_unit or ''} ({self.get_interpretation_display()})"


class MedicationRequest(models.Model):
    """
    FHIR R4: MedicationRequest
    A prescription issued by a clinician.
    Equivalent to NDHS Prescription.
    """

    STATUS_CHOICES = [
        ('active',     'Active'),
        ('completed',  'Completed'),
        ('cancelled',  'Cancelled'),
        ('on-hold',    'On Hold'),
    ]

    INTENT_CHOICES = [
        ('proposal',   'Proposal'),
        ('plan',       'Plan'),
        ('order',      'Order'),
    ]

    # Identifiers
    id                  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    prescription_id     = models.CharField(
        max_length=30, unique=True, editable=False, db_index=True,
        help_text="Human-readable ID — auto-generated as PRESC-{YYYY}-{NNNN}"
    )

    # Subject & context
    patient   = models.ForeignKey(
        Patient, on_delete=models.PROTECT,
        related_name='medication_requests', db_index=True
    )
    encounter = models.ForeignKey(
        Encounter, on_delete=models.PROTECT,
        related_name='medication_requests', null=True, blank=True
    )

    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', db_index=True)
    intent            = models.CharField(max_length=20, choices=INTENT_CHOICES, default='order')
    prescription_date = models.DateTimeField(default=timezone.now, db_index=True)
    notes             = models.TextField(null=True, blank=True)

    # Ghost reference — prescribing clinician
    prescribed_by_id      = models.UUIDField(db_index=True)
    prescribed_by_display = models.CharField(max_length=255)

    # Ghost reference — organisation
    organization_id      = models.UUIDField(db_index=True)
    organization_display = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'medication_requests'
        ordering = ['-prescription_date']
        verbose_name = 'Medication Request'
        verbose_name_plural = 'Medication Requests'
        indexes = [
            models.Index(fields=['patient']),
            models.Index(fields=['encounter']),
            models.Index(fields=['prescription_date']),
            models.Index(fields=['prescribed_by_id']),
            models.Index(fields=['organization_id']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.prescription_id} — {self.patient.full_name} ({self.prescription_date.strftime('%Y-%m-%d')})"

    def save(self, *args, **kwargs):
        if not self.prescription_id:
            self.prescription_id = self._generate_id()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_id():
        year   = timezone.now().year
        prefix = f"PRESC-{year}-"
        last   = MedicationRequest.objects.filter(
            prescription_id__startswith=prefix
        ).aggregate(models.Max('prescription_id'))['prescription_id__max']
        try:
            n = int(last.split('-')[-1]) + 1 if last else 1
        except (IndexError, ValueError):
            n = 1
        pid = f"{prefix}{n:04d}"
        while MedicationRequest.objects.filter(prescription_id=pid).exists():
            n += 1
            pid = f"{prefix}{n:04d}"
        return pid


class MedicationDispense(models.Model):
    """
    FHIR R4: MedicationDispense
    Records the actual dispensing of a medication against a MedicationRequest.
    Equivalent to NDHS PrescriptionMedication.
    """

    MEAL_TIMING_CHOICES = [
        ('before_meal', 'Before Meal'),
        ('after_meal',  'After Meal'),
        ('with_meal',   'With Meal'),
        ('anytime',     'Anytime (empty stomach OK)'),
    ]

    STATUS_CHOICES = [
        ('preparation', 'Preparation'),
        ('in-progress', 'In Progress'),
        ('completed',   'Completed'),
        ('stopped',     'Stopped'),
    ]

    id                  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    medication_request  = models.ForeignKey(
        MedicationRequest, on_delete=models.CASCADE,
        related_name='dispenses'
    )

    # What was dispensed
    drug_name            = models.CharField(max_length=255, db_index=True,
                                            help_text="Medication name e.g. Amoxicillin 500mg")
    status               = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')

    # Dosage schedule (mirrors NDHS)
    dosage_morning       = models.PositiveSmallIntegerField(default=0, help_text="Tablets in the morning")
    dosage_afternoon     = models.PositiveSmallIntegerField(default=0, help_text="Tablets in the afternoon")
    dosage_evening       = models.PositiveSmallIntegerField(default=0, help_text="Tablets in the evening")
    duration_days        = models.PositiveSmallIntegerField(null=True, blank=True,
                                                            help_text="Number of days to take the medication")
    meal_timing          = models.CharField(max_length=20, choices=MEAL_TIMING_CHOICES, default='anytime')
    special_instructions = models.TextField(null=True, blank=True)

    # Ghost reference — pharmacist who dispensed it (optional — may not have been dispensed yet)
    dispensed_by_id      = models.UUIDField(null=True, blank=True, db_index=True)
    dispensed_by_display = models.CharField(max_length=255, null=True, blank=True)
    dispensed_date       = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'medication_dispenses'
        ordering = ['medication_request', 'drug_name']
        verbose_name = 'Medication Dispense'
        verbose_name_plural = 'Medication Dispenses'
        indexes = [
            models.Index(fields=['medication_request']),
            models.Index(fields=['drug_name']),
            models.Index(fields=['dispensed_by_id']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return (
            f"{self.drug_name} — "
            f"{self.dosage_morning}/{self.dosage_afternoon}/{self.dosage_evening} "
            f"({self.get_meal_timing_display()})"
        )


class Vaccination(models.Model):
    """
    FHIR R4: Immunization
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        Patient, on_delete=models.PROTECT,
        related_name='vaccinations', db_index=True
    )

    status = models.CharField(max_length=32, default='completed', db_index=True)
    vaccine_code = models.CharField(max_length=100, db_index=True)
    vaccine_display = models.CharField(max_length=255)
    occurrence_date = models.DateTimeField(default=timezone.now, db_index=True)

    performer_id = models.UUIDField(null=True, blank=True, db_index=True)
    performer_display = models.CharField(max_length=255, null=True, blank=True)
    lot_number = models.CharField(max_length=80, null=True, blank=True)
    note = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vaccinations'
        ordering = ['-occurrence_date', '-created_at']
        verbose_name = 'Vaccination'
        verbose_name_plural = 'Vaccinations'
        indexes = [
            models.Index(fields=['patient', 'occurrence_date']),
            models.Index(fields=['status']),
            models.Index(fields=['vaccine_code']),
        ]

    def __str__(self):
        return f"{self.patient.patient_id} - {self.vaccine_display}"


class DiagnosticTest(models.Model):
    """
    FHIR R4: DiagnosticReport (lightweight test/result record)
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        Patient, on_delete=models.PROTECT,
        related_name='diagnostic_tests', db_index=True
    )

    status = models.CharField(max_length=32, default='final', db_index=True)
    test_code = models.CharField(max_length=100, db_index=True)
    test_display = models.CharField(max_length=255)
    effective_datetime = models.DateTimeField(default=timezone.now, db_index=True)
    issued_at = models.DateTimeField(null=True, blank=True, db_index=True)
    conclusion = models.TextField(null=True, blank=True)
    result_value = models.CharField(max_length=255, null=True, blank=True)

    performer_id = models.UUIDField(null=True, blank=True, db_index=True)
    performer_display = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'diagnostic_tests'
        ordering = ['-effective_datetime', '-created_at']
        verbose_name = 'Diagnostic Test'
        verbose_name_plural = 'Diagnostic Tests'
        indexes = [
            models.Index(fields=['patient', 'effective_datetime']),
            models.Index(fields=['status']),
            models.Index(fields=['test_code']),
        ]

    def __str__(self):
        return f"{self.patient.patient_id} - {self.test_display}"


class Appointment(models.Model):
    """
    FHIR R4: Appointment
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        Patient, on_delete=models.PROTECT,
        related_name='appointments', db_index=True
    )

    status = models.CharField(max_length=32, default='booked', db_index=True)
    appointment_type_code = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    appointment_type_display = models.CharField(max_length=255, null=True, blank=True)
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    comment = models.TextField(null=True, blank=True)

    practitioner_id = models.UUIDField(null=True, blank=True, db_index=True)
    practitioner_display = models.CharField(max_length=255, null=True, blank=True)
    location_id = models.UUIDField(null=True, blank=True, db_index=True)
    location_display = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'appointments'
        ordering = ['-start', '-created_at']
        verbose_name = 'Appointment'
        verbose_name_plural = 'Appointments'
        indexes = [
            models.Index(fields=['patient', 'start']),
            models.Index(fields=['status']),
            models.Index(fields=['appointment_type_code']),
        ]

    def __str__(self):
        label = self.appointment_type_display or 'Appointment'
        return f"{self.patient.patient_id} - {label}"


class Observation(models.Model):
    """
    FHIR R4: Observation (standalone)
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        Patient, on_delete=models.PROTECT,
        related_name='observations', db_index=True
    )

    status = models.CharField(max_length=32, default='final', db_index=True)
    category_code = models.CharField(max_length=64, null=True, blank=True)
    category_display = models.CharField(max_length=100, null=True, blank=True)
    observation_code = models.CharField(max_length=100, db_index=True)
    observation_display = models.CharField(max_length=255)
    effective_datetime = models.DateTimeField(null=True, blank=True, db_index=True)
    issued_at = models.DateTimeField(null=True, blank=True, db_index=True)

    value_string = models.TextField(null=True, blank=True)
    value_quantity_value = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
    )
    value_quantity_unit = models.CharField(max_length=50, null=True, blank=True)
    value_quantity_system = models.CharField(max_length=255, null=True, blank=True)
    value_quantity_code = models.CharField(max_length=64, null=True, blank=True)

    interpretation_code = models.CharField(max_length=64, null=True, blank=True)
    interpretation_display = models.CharField(max_length=100, null=True, blank=True)
    note = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'observations'
        ordering = ['-effective_datetime', '-created_at']
        verbose_name = 'Observation'
        verbose_name_plural = 'Observations'
        indexes = [
            models.Index(fields=['patient', 'effective_datetime']),
            models.Index(fields=['status']),
            models.Index(fields=['observation_code']),
        ]

    def __str__(self):
        return f"{self.patient.patient_id} - {self.observation_display}"
