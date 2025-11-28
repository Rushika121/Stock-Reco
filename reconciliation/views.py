# reconciliation/views.py
"""
Updated views.py — improved upload/preview robustness and consistent template context.
Replace your current reconciliation/views.py with this file (keep your models/forms unchanged).
"""

import difflib
import pandas as pd
import re
from dateutil import parser as dateparser
from urllib.parse import unquote_plus
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.urls import reverse
from pathlib import Path
from .models import Company, ReconciliationJob, ReconciliationFile
from .forms import SignUpForm
import os

# -------------------------
# Helpers: file read + normalization
# -------------------------
logger = logging.getLogger(__name__)

def _read_file_to_df(filefield):
    """Read a full file into a DataFrame (excel or csv)."""
    try:
        filefield.seek(0)
    except Exception:
        pass
    try:
        df = pd.read_excel(filefield, engine="openpyxl", dtype=object)
    except Exception:
        filefield.seek(0)
        df = pd.read_csv(filefield, dtype=object, engine="python")
    df.columns = [str(c).strip() for c in df.columns]
    return df

def _clean_numeric(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return x
    s = str(x).strip()
    # keep digits, dots and minus
    s = re.sub(r'[^\d\.\-]', '', s)
    if s in ('', '.', '-'):
        return None
    try:
        if '.' in s:
            return float(s)
        return int(s)
    except:
        try:
            return float(s)
        except:
            return None

def _clean_text(x):
    if x is None:
        return ''
    return str(x).strip()

def _parse_date(x):
    if x is None or x == '':
        return None
    try:
        return dateparser.parse(str(x))
    except:
        return None

def apply_mapping_to_df(df, mapping):
    """Given a df and mapping dict orig_col -> std_name, rename & normalize."""
    rename_map = {}
    for orig, std in mapping.items():
        if not std:
            continue
        matches = [c for c in df.columns if c.strip().lower() == orig.strip().lower()]
        if matches:
            rename_map[matches[0]] = std
    df = df.rename(columns=rename_map)
    if "Gross Premium" in df.columns:
        df["Gross Premium"] = df["Gross Premium"].apply(_clean_numeric)
    if "Brokerage Amount" in df.columns:
        df["Brokerage Amount"] = df["Brokerage Amount"].apply(_clean_numeric)
    if "Policy Number" in df.columns:
        df["Policy Number"] = df["Policy Number"].apply(_clean_text)
    if "Policy Start Date" in df.columns:
        df["Policy Start Date"] = df["Policy Start Date"].apply(_parse_date)
    return df

def auto_map_columns(colsA, colsB):
    mapped = {}
    lowerB = {c.lower(): c for c in colsB}
    for a in colsA:
        a_low = a.strip().lower()
        candidates = [b for b in colsB if a_low in b.lower() or b.lower() in a_low]
        if candidates:
            mapped[a] = candidates[0]; continue
        close = difflib.get_close_matches(a_low, [b.lower() for b in colsB], n=1, cutoff=0.6)
        if close:
            mapped[a] = lowerB[close[0]]; continue
        mapped[a] = ""
    return mapped

# -------------------------
# Simple bank reconcile stubs + dispatcher
# -------------------------
def reconcile_oriental(dfA, dfB, params=None):
    key = "Policy Number"
    prem = "Gross Premium"
    results = {"matches":0, "mismatches":0, "missing_in_A":0, "missing_in_B":0, "diffs":[]}
    a_keys = set(dfA[key].astype(str)) if key in dfA.columns else set()
    b_keys = set(dfB[key].astype(str)) if key in dfB.columns else set()
    keys = sorted(a_keys | b_keys)
    tol_pct = 0.01
    if params:
        tol_pct = float(params.get("amount_tolerance", 1.0))/100.0
    for k in keys:
        rowa = dfA[dfA.get(key, "") == k] if key in dfA.columns else None
        rowb = dfB[dfB.get(key, "") == k] if key in dfB.columns else None
        if (rowa is None or rowa.empty) and (rowb is not None and not rowb.empty):
            results["missing_in_A"] += 1; continue
        if (rowb is None or rowb.empty) and (rowa is not None and not rowa.empty):
            results["missing_in_B"] += 1; continue
        pa = None; pb = None
        try:
            pa = _clean_numeric(rowa.iloc[0].get(prem)) if (rowa is not None and not rowa.empty and prem in rowa.columns) else None
            pb = _clean_numeric(rowb.iloc[0].get(prem)) if (rowb is not None and not rowb.empty and prem in rowb.columns) else None
        except:
            pass
        if pa is None and pb is None:
            continue
        if pa is not None and pb is not None:
            if pa == pb or (abs(pa - pb) <= max(1e-9, abs(pa) * tol_pct)):
                results["matches"] += 1
            else:
                results["mismatches"] += 1
                results["diffs"].append({"policy": k, "a": pa, "b": pb})
        else:
            results["mismatches"] += 1
            results["diffs"].append({"policy": k, "a": pa, "b": pb})
    return results

def reconcile_generic(dfA, dfB, params=None):
    return reconcile_oriental(dfA, dfB, params=params)

def run_reconcile_by_bank(job, dfA, dfB, params=None):
    bank = job.company.bank_identifier if job and job.company else "GENERIC"
    if bank.upper() == "ORIENTAL":
        return reconcile_oriental(dfA, dfB, params=params)
    if bank.upper() == "MAGMA":
        return reconcile_generic(dfA, dfB, params=params)
    if bank.upper() == "RSA":
        return reconcile_generic(dfA, dfB, params=params)
    if bank.upper() == "ICICI":
        return reconcile_generic(dfA, dfB, params=params)
    return reconcile_generic(dfA, dfB, params=params)

# -------------------------
# Views
# -------------------------
@login_required
def dashboard(request):
    companies = Company.objects.all()
    insurer_data = []
    total_reconciled = 0
    total_unmatched = 0
    total_pending_uploads = 0
    last_updated = None

    for company in companies:
        latest_job = (ReconciliationJob.objects.filter(company=company).order_by('-created_at').first())
        if latest_job:
            summary = latest_job.summary or {}
            clearing = summary.get("clearing_total", 0)
            saiba = summary.get("saiba_total", 0)
            reconciled = summary.get("reconciled_value", 0)
            unmatched = summary.get("unmatched_value", 0)
            updated = latest_job.finished_at or latest_job.created_at
            insurer_data.append({
                "company": company.name,
                "clearing": clearing,
                "saiba": saiba,
                "reconciled": reconciled,
                "unmatched": unmatched,
                "updated": updated.strftime("%b %d, %Y") if updated else "N/A"
            })
            total_reconciled += reconciled or 0
            total_unmatched += unmatched or 0
            if latest_job.status == "PENDING":
                total_pending_uploads += 1
            if not last_updated or (updated and updated > last_updated):
                last_updated = updated
        else:
            insurer_data.append({
                "company": company.name,
                "clearing": 0,
                "saiba": 0,
                "reconciled": 0,
                "unmatched": 0,
                "updated": "Not started"
            })
            total_pending_uploads += 1

    if last_updated:
        last_updated = last_updated.strftime("%b %d, %Y")
    else:
        last_updated = "N/A"

    context = {
        "total_reconciled": total_reconciled,
        "total_unmatched": total_unmatched,
        "pending_uploads": total_pending_uploads,
        "last_updated": last_updated,
        "insurer_data": insurer_data,
    }
    return render(request, "reconciliation/dashboard.html", context)

@login_required
def company_select(request):
    modules = [
        ("hdfc", "Example.Hdfc"),
        ("icici", "ICICI Statement"),
        ("oriental", "oriental"),
        ("RSA", "RSA"),
        ("MAGMA", "MAGMA"),
    ]

    if request.method == "POST":
        module = request.POST.get("module")
        if not module:
            messages.error(request, "Please select a module.")
        else:
            # NOTE: company selection currently picks company_id=1 - keep this logic if desired.
            # You can change to real company id or map module->company.
            return redirect("reconciliation:upload_files", company_id=1)

    return render(request, "reconciliation/company_select.html", {
        "modules": modules
    })

# --- upload_files view (replace your existing) ---
@login_required
def upload_files(request, company_id):
    """
    Upload two files (File A + File B), create a job and ReconciliationFile rows,
    produce 5-row previews and render upload template with preview HTML.
    """
    company = get_object_or_404(Company, id=company_id)
    preview_a = None
    preview_b = None
    error = None
    job = None

    if request.method == "POST":
        # try both styles: multiple files 'files' or explicit 'file_a' 'file_b'
        files_list = list(request.FILES.getlist("files") or [])
        if not files_list:
            a = request.FILES.get("file_a")
            b = request.FILES.get("file_b")
            if a:
                files_list.append(a)
            if b:
                files_list.append(b)

        # If still empty, error
        if not files_list:
            error = "Please upload at least one file (ideally both File A and File B)."
            return render(request, "reconciliation/upload.html", {
                "company": company, "preview_a": preview_a, "preview_b": preview_b, "error": error
            })

        # Validate extensions of provided files
        for f in files_list:
            ext = _get_ext(getattr(f, "name", ""))
            if ext not in ALLOWED_EXT:
                error = f"File '{getattr(f,'name', '')}' has unsupported type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXT))}"
                return render(request, "reconciliation/upload.html", {
                    "company": company, "preview_a": preview_a, "preview_b": preview_b, "error": error
                })

        # create job
        job = ReconciliationJob.objects.create(user=request.user if request.user.is_authenticated else None,
                                               company=company, status="PENDING")
        # Save ReconciliationFile objects. We treat first as A, second as B.
        rf_objects = []
        for idx, uploaded in enumerate(files_list[:2]):  # only first 2 files matter for preview/mapping
            side = "A" if idx == 0 else "B"
            rf = ReconciliationFile.objects.create(
                job=job,
                file=uploaded,
                file_type=side,
                original_name=getattr(uploaded, "name", "") or None
            )
            rf_objects.append(rf)

        # get rfA / rfB
        rfA = rf_objects[0] if len(rf_objects) >= 1 else None
        rfB = rf_objects[1] if len(rf_objects) >= 2 else None

        # Build previews (use filefield or file.path if available)
        if rfA:
            try:
                # pass file-like for UploadedFile so pandas can read it directly
                preview_a_html, err_a = _read_preview(rfA.file, ext=_get_ext(rfA.file.name), max_rows=5)
                preview_a = preview_a_html
                if err_a:
                    logger.debug("Preview A error: %s", err_a)
            except Exception as e:
                logger.exception("Preview A exception: %s", e)

            # optional: persist preview HTML if model has 'preview' JSON/text field
            try:
                if hasattr(rfA, "preview"):
                    rfA.preview = {"html": preview_a} if preview_a else {}
                    rfA.save(update_fields=["preview"])
            except Exception:
                pass

        if rfB:
            try:
                preview_b_html, err_b = _read_preview(rfB.file, ext=_get_ext(rfB.file.name), max_rows=5)
                preview_b = preview_b_html
                if err_b:
                    logger.debug("Preview B error: %s", err_b)
            except Exception as e:
                logger.exception("Preview B exception: %s", e)
            try:
                if hasattr(rfB, "preview"):
                    rfB.preview = {"html": preview_b} if preview_b else {}
                    rfB.save(update_fields=["preview"])
            except Exception:
                pass

        # render same upload page but with previews and job info (post-redirect not required here)
        return render(request, "reconciliation/upload.html", {
            "company": company,
            "job": job,
            "preview_a": preview_a,
            "preview_b": preview_b,
            "error": error,
        })

    # GET
    return render(request, "reconciliation/upload.html", {
        "company": company,
        "preview_a": preview_a,
        "preview_b": preview_b,
        "error": error,
    })

ALLOWED_EXT = ["csv", "xls", "xlsx", "xlsb"]


def _get_ext(name):
    return name.rsplit(".", 1)[-1].lower()


def _read_preview(path, ext, max_rows=5):
    """Return preview HTML table (bootstrap friendly)"""
    try:
        if ext in ("xls", "xlsx", "xlsb"):
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path)

        df = df.fillna("")

        df_preview = df.head(max_rows)
        html = df_preview.to_html(
            classes="preview-table",
            index=False,
            border=0,
            justify="center"
        )
        return html, None
    except Exception as e:
        return None, str(e)


@login_required
def upload_files(request, company_id):

    company = get_object_or_404(Company, id=company_id)
    preview_a = None
    preview_b = None
    error = None

    if request.method == "POST":
        file_a = request.FILES.get("file_a")
        file_b = request.FILES.get("file_b")

        if not file_a or not file_b:
            return render(request, "reconciliation/upload.html", {
                "company": company,
                "error": "Please upload both File A and File B."
            })

        ext_a = _get_ext(file_a.name)
        ext_b = _get_ext(file_b.name)

        if ext_a not in ALLOWED_EXT or ext_b not in ALLOWED_EXT:
            return render(request, "reconciliation/upload.html", {
                "company": company,
                "error": "Allowed types: CSV, XLS, XLSX"
            })

        # Create job
        job = ReconciliationJob.objects.create(company=company, status="PENDING")

        # Save files in DB
        rf_a = ReconciliationFile.objects.create(job=job, file=file_a, file_type="A")
        rf_b = ReconciliationFile.objects.create(job=job, file=file_b, file_type="B")

        # Generate preview
        preview_a, err_a = _read_preview(rf_a.file.path, ext_a)
        preview_b, err_b = _read_preview(rf_b.file.path, ext_b)

        # Save previews in DB
        if preview_a:
            rf_a.preview = {"html": preview_a}
            rf_a.save()
        if preview_b:
            rf_b.preview = {"html": preview_b}
            rf_b.save()

        return redirect("reconciliation:upload_preview", job_id=job.id)

    return render(request, "reconciliation/upload.html", {
        "company": company
    })


@login_required
def upload_preview(request, job_id):

    job = get_object_or_404(ReconciliationJob, id=job_id)

    # fetch files
    rfA = job.files.filter(file_type="A").first()
    rfB = job.files.filter(file_type="B").first()

    previewA = rfA.preview.get("html") if rfA and rfA.preview else None
    previewB = rfB.preview.get("html") if rfB and rfB.preview else None

    return render(request, "reconciliation/upload_preview.html", {
        "rfA": rfA,
        "rfB": rfB,
        "preview_a": previewA,
        "preview_b": previewB,
        "job": job
    })

@login_required
def mapping_view(request, job_id):
    # Simple mapping view
    job = get_object_or_404(ReconciliationJob, id=job_id)
    files = list(job.files.all().order_by('uploaded_at'))
    if len(files) < 2:
        messages.error(request, "This job needs two uploaded files to do mapping. Please upload two files.")
        return redirect("reconciliation:company_select")

    rfA = files[0]; rfB = files[1]
    dfA = _read_file_to_df(rfA.file); dfB = _read_file_to_df(rfB.file)
    colsA = list(dfA.columns); colsB = list(dfB.columns)
    standardized_fields = ["Policy Number","Endorsement Number","Gross Premium","Brokerage Amount","GST","Policy Start Date"]
    suggestions = auto_map_columns(colsA, colsB)

    if request.method == "POST":
        # read mapping from inputs like "map__<rfA.id>__<colname>" (older mapping style)
        mappingAtoB = {}
        for key, val in request.POST.items():
            if not key.startswith("map__"):
                continue
            parts = key.split("__", 2)
            if len(parts) < 3:
                continue
            _, fileaid, raw_colA = parts
            colA = unquote_plus(raw_colA)
            mappingAtoB[colA] = val.strip()

        # build job mapping structure
        job_mapping = {
            "files": {
                str(rfA.id): {"original_filename": rfA.original_name or rfA.file.name, "mapping": {k: mappingAtoB.get(k, "") for k in colsA}},
                str(rfB.id): {"original_filename": rfB.original_name or rfB.file.name, "mapping": {v: "" for v in colsB}}
            },
            "paired": {"pairs": [ {"a": a, "b": mappingAtoB.get(a, "")} for a in colsA ]}
        }
        job.mapping = job_mapping
        job.status = "MAPPED"
        job.save()

        # apply mapping and run reconcile (use full files)
        dfA_mapped = apply_mapping_to_df(_read_file_to_df(rfA.file), job_mapping["files"][str(rfA.id)]["mapping"])
        mapping_for_B = {}
        for a_col, b_col in mappingAtoB.items():
            if b_col:
                mapping_for_B[b_col] = b_col
        dfB_mapped = apply_mapping_to_df(_read_file_to_df(rfB.file), mapping_for_B)

        summary = run_reconcile_by_bank(job, dfA_mapped, dfB_mapped)
        job.summary = summary
        job.status = "COMPLETED"
        job.finished_at = pd.Timestamp.now().to_pydatetime()
        job.save()

        messages.success(request, "Mapping saved and reconciliation completed.")
        return redirect("reconciliation:dashboard")

    context = {"job": job, "rfA": rfA, "rfB": rfB, "colsA": colsA, "colsB": colsB, "suggestions": suggestions, "standardized_fields": standardized_fields}
    return render(request, "reconciliation/mapping.html", context)


@login_required
def mapping_advanced_view(request, job_id):
    # Advanced mapping UI (add/remove refs, params)
    job = get_object_or_404(ReconciliationJob, id=job_id)
    files = list(job.files.all().order_by('uploaded_at'))
    if len(files) < 2:
        messages.error(request, "Upload two files (A and B) to proceed with mapping.")
        return redirect("reconciliation:company_select")

    rfA = files[0]; rfB = files[1]
    dfA, errA = _read_preview(rfA.file, nrows=5)
    dfB, errB = _read_preview(rfB.file, nrows=5)
    colsA = list(dfA.columns) if dfA is not None else []
    colsB = list(dfB.columns) if dfB is not None else []
    suggestions = auto_map_columns(colsA, colsB)

    if request.method == "POST":
        mapping = {"files": {str(rfA.id): {"mapping": {}}, str(rfB.id): {"mapping": {}}}}
        # collect primary field mappings
        for fld in ["amount", "date", "description"]:
            a_name = request.POST.get(f"map_a_{fld}", "").strip()
            b_name = request.POST.get(f"map_b_{fld}", "").strip()
            if a_name: mapping["files"][str(rfA.id)]["mapping"][a_name] = fld
            if b_name: mapping["files"][str(rfB.id)]["mapping"][b_name] = fld

        # collect reference fields
        refs_a = []; refs_b = []
        for key, val in request.POST.items():
            if key.startswith("map_a_ref_"):
                if val.strip(): refs_a.append(val.strip())
            if key.startswith("map_b_ref_"):
                if val.strip(): refs_b.append(val.strip())
        for r in refs_a:
            mapping["files"][str(rfA.id)]["mapping"][r] = "Reference"
        for r in refs_b:
            mapping["files"][str(rfB.id)]["mapping"][r] = "Reference"

        # params
        keywords = request.POST.get("keywords", "").strip()
        enforce_b_unique = request.POST.get("enforce_b_unique") == "on"
        use_references = request.POST.get("use_references") == "on"
        amount_tolerance = request.POST.get("amount_tolerance", "1")
        date_window = request.POST.get("date_window", "7")
        fuzzy_pct = request.POST.get("fuzzy_pct", "80")
        params = {
            "keywords": keywords,
            "enforce_b_unique": enforce_b_unique,
            "use_references": use_references,
            "amount_tolerance": float(amount_tolerance),
            "date_window": int(date_window),
            "fuzzy_pct": int(fuzzy_pct)
        }

        job.mapping = {"mapping_details": mapping, "params": params}
        job.status = "MAPPED"
        job.save()

        dfA_full = _read_file_to_df(rfA.file)
        dfB_full = _read_file_to_df(rfB.file)
        dfA_mapped = apply_mapping_to_df(dfA_full, mapping["files"][str(rfA.id)]["mapping"])
        dfB_mapped = apply_mapping_to_df(dfB_full, mapping["files"][str(rfB.id)]["mapping"])
        summary = run_reconcile_by_bank(job, dfA_mapped, dfB_mapped, params)
        job.summary = summary
        job.status = "COMPLETED"
        job.finished_at = pd.Timestamp.now().to_pydatetime()
        job.save()

        messages.success(request, "Mapping saved and reconciliation completed.")
        return redirect("reconciliation:dashboard")

    context = {
        "job": job,
        "rfA": rfA, "rfB": rfB,
        "previewA": dfA.head(5).to_dict(orient="records") if dfA is not None else None,
        "previewB": dfB.head(5).to_dict(orient="records") if dfB is not None else None,
        "colsA": colsA, "colsB": colsB,
        "suggestions": suggestions,
    }
    return render(request, "reconciliation/mapping_advanced.html", context)


# -------------------------
# Signup view
# -------------------------
def signup(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Account created successfully.")
            return redirect("reconciliation:dashboard")
    else:
        form = SignUpForm()
    return render(request, "reconciliation/signup.html", {"form": form})
