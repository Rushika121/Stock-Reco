# reconciliation/urls.py
from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = "reconciliation"

urlpatterns = [
    # Dashboard (default)
    path("", views.dashboard, name="dashboard"),

    # Authentication
    path("login/", auth_views.LoginView.as_view(
        template_name="reconciliation/login.html",
        redirect_authenticated_user=True
    ), name="login"),

    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("signup/", views.signup, name="signup"),

    # Password reset
    # Password reset (use a custom email template that uses the app namespace)
    path(
        "password_reset/",
        auth_views.PasswordResetView.as_view(
            template_name="reconciliation/password_reset_form.html",
            email_template_name="reconciliation/password_reset_email.txt",
            subject_template_name="reconciliation/password_reset_subject.txt",
        ),
        name="password_reset",
    ),

    path("password_reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="reconciliation/password_reset_done.html"
    ), name="password_reset_done"),

    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="reconciliation/password_reset_confirm.html"
    ), name="password_reset_confirm"),

    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(
        template_name="reconciliation/password_reset_complete.html"
    ), name="password_reset_complete"),
    # Company selection
    path("reconciliation/", views.company_select, name="company_select"),

    # Upload + Mapping
    path("reconciliation/upload/<int:company_id>/", views.upload_files, name="upload_files"),
    # preview page after upload (shows 5-row previews)
    path("reconciliation/upload-preview/<uuid:job_id>/", views.upload_preview, name="upload_preview"),
    # advanced mapping page (the new UI)
    path("reconciliation/mapping/<uuid:job_id>/", views.mapping_advanced_view, name="mapping_advanced"),

]
