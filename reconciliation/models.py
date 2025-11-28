from django.db import models
from django.contrib.auth import get_user_model
import uuid

User = get_user_model()


# -----------------------------
# COMPANY MASTER
# -----------------------------
class Company(models.Model):
    BANK_CHOICES = [
        ("GENERIC", "Generic"),
        ("MAGMA", "Magma"),
        ("RSA", "RSA"),
        ("ICICI", "ICICI"),
        ("Oriental", "Oriental"),
    ]

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=50, unique=True)
    bank_identifier = models.CharField(max_length=20, choices=BANK_CHOICES, default="GENERIC")

    def __str__(self):
        return self.name


# -----------------------------
# RECONCILIATION JOB
# -----------------------------
class ReconciliationJob(models.Model):
    STATUS = [
        ("PENDING", "Pending"),
        ("RUNNING", "Running"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS, default="PENDING")
    summary = models.JSONField(null=True, blank=True)
    mapping = models.JSONField(null=True, blank=True)  # mapping result stored here

    def __str__(self):
        return f"Job {self.id} - {self.company.name} - {self.status}"


# -----------------------------
# FILES UPLOADED PER JOB
# -----------------------------
class ReconciliationFile(models.Model):

    FILE_TYPE = [
        ("A", "File A"),
        ("B", "File B"),
    ]

    job = models.ForeignKey(
        ReconciliationJob,
        on_delete=models.CASCADE,
        related_name="files"
    )

    file = models.FileField(upload_to="uploads/%Y/%m/%d/")
    original_name = models.CharField(max_length=255, null=True, blank=True)  # <--- FIX
    file_type = models.CharField(max_length=2, choices=FILE_TYPE)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    preview = models.JSONField(null=True, blank=True)  # 2–5 rows preview

    def __str__(self):
        return f"{self.original_name or self.file.name} ({self.file_type})"
