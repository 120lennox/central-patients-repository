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