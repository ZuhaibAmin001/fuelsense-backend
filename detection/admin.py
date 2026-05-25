from django.contrib import admin
from .models import Station, QueueStatus

@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ["name", "address", "total_pumps", "is_active"]

@admin.register(QueueStatus)
class QueueAdmin(admin.ModelAdmin):
    list_display = ["station", "vehicle_count", "congestion_level", "timestamp"]
    list_filter = ["congestion_level", "station"]