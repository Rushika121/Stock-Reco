# Register your models here.
from django.contrib import admin
from .models import Company, ReconciliationJob, ReconciliationFile

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "bank_identifier")

@admin.register(ReconciliationJob)
class ReconciliationJobAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "user", "status", "created_at")
    readonly_fields = ("created_at", "started_at", "finished_at")

@admin.register(ReconciliationFile)
class ReconciliationFileAdmin(admin.ModelAdmin):
    list_display = ("job", "file_type", "uploaded_at")
