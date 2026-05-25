from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import random, string


class Station(models.Model):
    name        = models.CharField(max_length=200)
    lat         = models.FloatField()
    lng         = models.FloatField()
    address     = models.TextField()
    total_pumps = models.IntegerField(default=4)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class QueueStatus(models.Model):
    CONGESTION_CHOICES = [('GREEN','Green'),('YELLOW','Yellow'),('RED','Red')]

    station         = models.ForeignKey(Station, on_delete=models.CASCADE, related_name='queue_statuses')
    vehicle_count   = models.IntegerField()
    congestion_level = models.CharField(max_length=10, choices=CONGESTION_CHOICES)
    timestamp       = models.DateTimeField(auto_now_add=True)

    # ── BUG FIX: original had broken elif, this is the correct version ──────
    def save(self, *args, **kwargs):
        if self.vehicle_count <= 10:
            self.congestion_level = 'GREEN'
        elif self.vehicle_count <= 25:
            self.congestion_level = 'YELLOW'
        else:
            self.congestion_level = 'RED'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.station.name} — {self.vehicle_count} vehicles ({self.congestion_level})"

    class Meta:
        ordering = ['-timestamp']


class UserProfile(models.Model):
    user             = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone            = models.CharField(max_length=15, unique=True)
    cnic             = models.CharField(max_length=15, unique=True)
    is_phone_verified = models.BooleanField(default=False)
    is_station_owner = models.BooleanField(default=False)
    owned_station    = models.ForeignKey(Station, null=True, blank=True, on_delete=models.SET_NULL, related_name='owner_profiles')
    created_at       = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.phone})"


class OTPRecord(models.Model):
    phone      = models.CharField(max_length=15)
    otp        = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used    = models.BooleanField(default=False)

    @staticmethod
    def generate_otp():
        return ''.join(random.choices(string.digits, k=6))

    def is_valid(self):
        from django.conf import settings
        expiry = getattr(settings, 'OTP_EXPIRY_SECONDS', 300)
        not_expired = (timezone.now() - self.created_at).total_seconds() < expiry
        return not self.is_used and not_expired

    class Meta:
        ordering = ['-created_at']


class FuelTransaction(models.Model):
    FUEL_TYPES = [('PETROL','Petrol'),('DIESEL','Diesel'),('HSD','High Speed Diesel'),('KEROSENE','Kerosene')]

    station        = models.ForeignKey(Station, on_delete=models.CASCADE, related_name='transactions')
    fuel_type      = models.CharField(max_length=20, choices=FUEL_TYPES, default='PETROL')
    liters         = models.FloatField()
    price_per_liter = models.FloatField()
    amount         = models.FloatField()
    vehicle_reg    = models.CharField(max_length=20, blank=True, default='')
    timestamp      = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.amount = round(self.liters * self.price_per_liter, 2)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-timestamp']