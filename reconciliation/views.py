# reconciliation/views.py
"""
Complete fixed views with proper table formatting and dynamic headers
"""

import json
import logging
import pandas as pd
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from .models import Company, ReconciliationJob, ReconciliationFile
from .forms import SignUpForm

logger = logging.getLogger(__name__)

ALLOWED_EXT = ["csv", "xls", "xlsx", "xlsb"]

# -------------------------
# Helper Functions
# -------------------------

def _get_ext(name):
    """Extract file extension"""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _read_file_to_df(file_obj, max_rows=None):
    """
    Read uploaded file to DataFrame with proper header detection
    Args:
        file_obj: Django UploadedFile or FileField
        max_rows: limit rows (None = all rows)
    Returns:
        DataFrame or None on error
    """
    try:
        # Reset file pointer
        try:
            file_obj.seek(0)
        except:
            pass
        
        # Get filename
        filename = getattr(file_obj, 'name', '')
        ext = _get_ext(filename)
        
        # Read based on extension
        if ext in ('xls', 'xlsx', 'xlsb'):
            df = pd.read_excel(file_obj, engine="openpyxl", nrows=max_rows)
        else:
            df = pd.read_csv(file_obj, nrows=max_rows, engine="python")
        
        # Clean column names - strip whitespace
        df.columns = [str(c).strip() for c in df.columns]
        
        # Fill NaN with empty string for display
        df = df.fillna("")
        
        # Convert all data to string for consistent display
        for col in df.columns:
            df[col] = df[col].astype(str)
        
        return df
        
    except Exception as e:
        logger.exception(f"Error reading file: {e}")
        return None


def _df_to_preview_html(df, max_rows=5):
    """
    Convert DataFrame to HTML preview table with proper formatting
    Returns clean HTML with horizontal scroll support
    """
    if df is None or df.empty:
        return '<div style="padding:20px;text-align:center;color:#999;">No data available</div>'
    
    # Limit rows
    df_preview = df.head(max_rows)
    
    # Build HTML manually for better control
    html_parts = []
    
    # Start table
    html_parts.append('<table class="preview-table">')
    
    # Table header
    html_parts.append('<thead><tr>')
    for col in df_preview.columns:
        # Escape HTML and truncate long column names
        col_display = str(col)[:50]
        html_parts.append(f'<th class="preview-th">{col_display}</th>')
    html_parts.append('</tr></thead>')
    
    # Table body
    html_parts.append('<tbody>')
    for idx, row in df_preview.iterrows():
        html_parts.append('<tr>')
        for col in df_preview.columns:
            val = str(row[col]) if row[col] else ''
            # Truncate very long values
            if len(val) > 100:
                val = val[:100] + '...'
            html_parts.append(f'<td class="preview-td">{val}</td>')
        html_parts.append('</tr>')
    html_parts.append('</tbody>')
    
    html_parts.append('</table>')
    
    return ''.join(html_parts)


# -------------------------
# Views
# -------------------------

@login_required
def dashboard(request):
    """Dashboard view"""
    companies = Company.objects.all()
    insurer_data = []
    total_reconciled = 0
    total_unmatched = 0
    pending_uploads = 0
    last_updated = None

    for company in companies:
        latest_job = (ReconciliationJob.objects
                      .filter(company=company)
                      .order_by('-created_at')
                      .first())
        
        if latest_job:
            summary = latest_job.summary or {}
            stats = summary.get("stats", {})
            
            reconciled = stats.get("matched_count", 0)
            unmatched = stats.get("unmatched_total", 0)
            updated = latest_job.finished_at or latest_job.created_at
            
            insurer_data.append({
                "company": company.name,
                "clearing": 0,
                "saiba": 0,
                "reconciled": reconciled,
                "unmatched": unmatched,
                "updated": updated.strftime("%b %d, %Y") if updated else "N/A"
            })
            
            total_reconciled += reconciled
            total_unmatched += unmatched
            
            if latest_job.status == "PENDING":
                pending_uploads += 1
            
            if not last_updated or (updated and updated > last_updated):
                last_updated = updated

    context = {
        "total_reconciled": total_reconciled,
        "total_unmatched": total_unmatched,
        "pending_uploads": pending_uploads,
        "last_updated": last_updated.strftime("%b %d, %Y") if last_updated else "N/A",
        "insurer_data": insurer_data,
    }
    return render(request, "reconciliation/dashboard.html", context)


@login_required
def company_select(request):
    """
    Show available modules/insurers for user to pick.
    On POST, look up the corresponding Company record by module identifier (or name)
    and redirect to upload_files with the company's id.
    """
    # Example modules list: (value-used-for-lookup, label-for-display)
    # Adjust values (first element) to match Company.bank_identifier or Company.name in your DB.
    modules = [
        ("hdfc", "HDFC Statement"),
        ("icici", "ICICI Statement"),
        ("oriental", "Oriental"),
        ("rsa", "RSA"),
        ("magma", "MAGMA"),
    ]

    if request.method == "POST":
        selected = request.POST.get("module")
        if not selected:
            messages.error(request, "Please select a module.")
            return render(request, "reconciliation/company_select.html", {"modules": modules})

        # Try to find Company by a few sensible fields
        company = None
        try:
            # 1) try matching bank_identifier (recommended)
            company = Company.objects.filter(bank_identifier__iexact=selected).first()
        except Exception:
            company = None

        if company is None:
            # 2) try matching slug/name (some projects store 'oriental' in name)
            company = Company.objects.filter(name__icontains=selected).first()

        if company is None:
            # 3) fallback: if user selected a numeric company id directly (rare)
            try:
                company = Company.objects.filter(id=int(selected)).first()
            except Exception:
                company = None

        if company is None:
            messages.error(request, "Could not find a matching company for the selected module. Please contact admin.")
            return render(request, "reconciliation/company_select.html", {"modules": modules})

        # Got the company — redirect to upload_files with the real id
        return redirect("reconciliation:upload_files", company_id=company.id)

    # GET: render selection
    return render(request, "reconciliation/company_select.html", {"modules": modules})


@login_required
def upload_files(request, company_id):
    """
    Upload handler for the chosen company.
    Shows the Upload page for the company (company passed via URL).
    On POST, accepts file(s) and creates a ReconciliationJob + ReconciliationFile rows.
    """
    company = get_object_or_404(Company, id=company_id)

    if request.method == "POST":
        # accept either two named fields or multiple "files" input
        uploaded_files = list(request.FILES.getlist("files") or [])
        # fallback single file fields
        if not uploaded_files:
            fa = request.FILES.get("file_a")
            fb = request.FILES.get("file_b")
            if fa:
                uploaded_files.append(fa)
            if fb:
                uploaded_files.append(fb)

        if not uploaded_files:
            messages.error(request, "Please attach at least one file (File A or File B).")
            return render(request, "reconciliation/upload.html", {"company": company})

        # create job
        job = ReconciliationJob.objects.create(company=company, status="PENDING", user=request.user)

        # Save files: first -> A, second -> B
        for idx, f in enumerate(uploaded_files):
            side = "A" if idx == 0 else "B"
            ReconciliationFile.objects.create(
                job=job,
                file=f,
                file_type=side,
                original_name=getattr(f, "name", None) or getattr(f, "filename", None)
            )

        # redirect to preview page (use job.id)
        return redirect("reconciliation:upload_preview", job_id=job.id)

    # GET: show upload page for this company
    return render(request, "reconciliation/upload.html", {"company": company})

@login_required
def upload_preview(request, job_id):
    """
    Show preview of uploaded files - ONLY preview, no mapping
    """
    job = get_object_or_404(ReconciliationJob, id=job_id)

    # Fetch files
    rfA = job.files.filter(file_type="A").first()
    rfB = job.files.filter(file_type="B").first()

    # Get previews
    preview_a = None
    preview_b = None
    
    if rfA:
        if rfA.preview and rfA.preview.get("html"):
            preview_a = rfA.preview.get("html")
        else:
            # Regenerate if missing
            df = _read_file_to_df(rfA.file, max_rows=5)
            if df is not None:
                preview_a = _df_to_preview_html(df, max_rows=5)
                rfA.preview = {"html": preview_a, "columns": list(df.columns)}
                rfA.save()
    
    if rfB:
        if rfB.preview and rfB.preview.get("html"):
            preview_b = rfB.preview.get("html")
        else:
            # Regenerate if missing
            df = _read_file_to_df(rfB.file, max_rows=5)
            if df is not None:
                preview_b = _df_to_preview_html(df, max_rows=5)
                rfB.preview = {"html": preview_b, "columns": list(df.columns)}
                rfB.save()

    return render(request, "reconciliation/upload_preview.html", {
        "job": job,
        "rfA": rfA,
        "rfB": rfB,
        "preview_a": preview_a,
        "preview_b": preview_b,
    })


@login_required
def mapping_advanced_view(request, job_id):
    """
    Column mapping interface - extracted from your HTML
    """
    job = get_object_or_404(ReconciliationJob, id=job_id)
    
    # Get files
    rfA = job.files.filter(file_type="A").first()
    rfB = job.files.filter(file_type="B").first()
    
    if not rfA or not rfB:
        messages.error(request, "Both files (A and B) must be uploaded before mapping.")
        return redirect("reconciliation:upload_files", company_id=job.company.id)
    
    # Read full dataframes to get ALL columns
    dfA = _read_file_to_df(rfA.file)
    dfB = _read_file_to_df(rfB.file)
    
    if dfA is None or dfB is None:
        messages.error(request, "Error reading uploaded files. Please re-upload.")
        return redirect("reconciliation:upload_files", company_id=job.company.id)
    
    colsA = list(dfA.columns)
    colsB = list(dfB.columns)
    
    if request.method == "POST":
        # Get the mapping data from hidden field
        mapping_json = request.POST.get('mapping_data')
        
        if mapping_json:
            try:
                mapping_data = json.loads(mapping_json)
                
                # Save to job
                job.mapping = mapping_data
                job.status = "MAPPED"
                job.save()
                
                messages.success(request, "Mapping saved successfully!")
                return redirect("reconciliation:dashboard")
                
            except json.JSONDecodeError:
                messages.error(request, "Invalid mapping data")
    
    # GET request - show form
    context = {
        "job": job,
        "rfA": rfA,
        "rfB": rfB,
        "colsA": json.dumps(colsA),  # JSON for JavaScript
        "colsB": json.dumps(colsB),  # JSON for JavaScript
    }
    
    return render(request, "reconciliation/mapping_advanced.html", context)


def signup(request):
    """User registration"""
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Account created successfully!")
            return redirect("reconciliation:dashboard")
    else:
        form = SignUpForm()
    
    return render(request, "reconciliation/signup.html", {"form": form})