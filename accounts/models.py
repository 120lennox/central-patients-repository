from django.db import models
from django.contrib.auth.models import AbstractUser
# Create your models here.

class User(AbstractUser):
    # Extend the default Django User model if needed
    ROLE_CHOICES = [
        ('patient', 'Patient'),
        ('clinician', 'Clinician'),
        ('admin', 'Admin'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='patient')

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.username} ({self.role})"
    
    @property
    def is_patient(self):
        return self.role == 'patient'
    
    @property
    def is_clinician(self):
        return self.role == 'clinician'
    
    @property
    def is_admin(self):
        return self.role == 'admin'