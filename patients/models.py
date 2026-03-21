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
    

