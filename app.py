from flask import (
    Flask, render_template_string,
    redirect, request, session, url_for,
    send_from_directory, send_file, jsonify, Response
)
import sqlite3
import os
import json
import base64
import io
import re
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps
from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)
from werkzeug.utils import secure_filename
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from pywebpush import webpush, WebPushException
from pypdf import PdfReader, PdfWriter

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "act-bedflow-dev-key-change-later"
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf"}
VAPID_RUNTIME_PRIVATE_FILE = Path(os.environ.get(
    "ACT_VAPID_RUNTIME_KEY",
    "/tmp/act_vapid_private.pem"
))

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    ""
).strip()

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = (
        "postgresql://"
        + DATABASE_URL[len("postgres://"):]
    )

USE_POSTGRES = bool(DATABASE_URL)
LOCAL_DB = Path(__file__).with_name("bedflow.db")

if USE_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row

    DATABASE_INTEGRITY_ERRORS = (
        psycopg.IntegrityError,
    )
else:
    DATABASE_INTEGRITY_ERRORS = (
        sqlite3.IntegrityError,
    )

# =========================
# DATABASE ADAPTER
# =========================

class DatabaseConnection:

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        if USE_POSTGRES:
            sql = sql.replace("?", "%s")

        return self.connection.execute(sql, params)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def get_db():
    if USE_POSTGRES:
        connection = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            connect_timeout=15
        )
    else:
        connection = sqlite3.connect(
            LOCAL_DB,
            timeout=30
        )
        connection.row_factory = sqlite3.Row

    return DatabaseConnection(connection)

def add_log(
    conn,
    action,
    bed_name=None,
    old_value=None,
    new_value=None
):
    conn.execute("""
        INSERT INTO activity_log
        (
            timestamp,
            username,
            role,
            action,
            bed_name,
            old_value,
            new_value
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        now_text(),
        session.get("username", "system"),
        session.get("role", "system"),
        action,
        bed_name,
        old_value,
        new_value
    ))
# Saudi Arabia timezone
SAUDI_TZ = ZoneInfo("Asia/Riyadh")


def now_dt():
    """Return the current Saudi Arabia time as a naive datetime for DB comparisons."""
    return datetime.now(SAUDI_TZ).replace(tzinfo=None)


def now_text():
    """Return the current Saudi Arabia time in the database timestamp format."""
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")



# =========================
# INTEGRATED TEMPLATE STRINGS (mobile upload edition)
# =========================

MODULES_HTML_V31 = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel="icon" href="/app-icon.svg?v=4-4-1">
<meta name="theme-color" content="#0b5fd7">
<title>ACT Operations</title>
<style>
:root{
  --bg:#eef4fb;--surface:#fff;--text:#152238;--muted:#6b7890;
  --blue:#1267e5;--blue2:#0b4fb3;--green:#1da66f;--pink:#ef5c7a;
  --violet:#7156e8;--line:#dfe7f2;--shadow:0 14px 34px rgba(30,71,121,.09);
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{
  margin:0;font-family:Inter,Arial,sans-serif;color:var(--text);
  background:
    radial-gradient(circle at 85% 0,#dbeaff 0,transparent 28%),
    linear-gradient(180deg,#edf5ff 0,#f7f9fc 430px);
  min-height:100vh;
}
a{color:inherit}
.shell{max-width:1220px;margin:auto;padding:18px 18px 34px}
.appbar{
  display:flex;justify-content:space-between;align-items:center;gap:14px;
  padding:14px 18px;background:linear-gradient(135deg,#0b63df,#0878ef);
  color:#fff;border-radius:20px;box-shadow:0 14px 32px rgba(12,96,214,.22);
  margin-bottom:18px;
}
.appbar-brand{display:flex;align-items:center;gap:12px}
.logo{width:52px;height:52px;border-radius:15px;background:#fff1;padding:5px}
.appbar h1{margin:0;font-size:25px}
.user-chip{
  display:flex;align-items:center;gap:8px;padding:10px 14px;border-radius:999px;
  background:#ffffff16;border:1px solid #ffffff35;font-weight:800;white-space:nowrap
}
.hero{
  background:linear-gradient(135deg,#fff 0,#fbfdff 65%,#e9f3ff 100%);
  border:1px solid var(--line);border-radius:24px;padding:24px;
  box-shadow:var(--shadow);margin-bottom:18px;position:relative;overflow:hidden;
}
.hero:after{
  content:"✚";position:absolute;right:42px;top:16px;font-size:110px;
  color:#cfe3ff;opacity:.8;font-weight:900
}
.hero h2{margin:0;font-size:34px;position:relative;z-index:1}
.hero p{margin:8px 0 0;color:var(--muted);font-size:19px;position:relative;z-index:1}
.module-grid{
  display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;
}
.module-card{
  background:var(--surface);border:1px solid var(--line);border-radius:24px;
  padding:22px;box-shadow:var(--shadow);position:relative;overflow:hidden;
}
.module-card:after{
  content:"";position:absolute;width:180px;height:180px;border-radius:50%;
  right:-70px;top:-70px;background:#eef5ff
}
.module-head{display:flex;align-items:flex-start;gap:16px;position:relative;z-index:1}
.module-icon{
  width:72px;height:72px;border-radius:20px;display:grid;place-items:center;
  font-size:38px;background:#eff5ff;flex:0 0 auto
}
.module-icon.warm{background:#fff4e9}
.module-title{font-size:27px;font-weight:900;margin:6px 0 6px}
.module-desc{color:var(--muted);font-size:16px;line-height:1.5;max-width:560px}
.metric{
  margin-top:18px;padding:16px;border-radius:17px;background:#f6f9fd;
  display:flex;align-items:center;justify-content:space-between;gap:12px;
  position:relative;z-index:1
}
.metric strong{font-size:34px;margin-right:8px}
.metric-copy{font-weight:800}
.open-btn{
  margin-top:14px;width:100%;display:flex;align-items:center;justify-content:center;
  padding:14px 16px;border-radius:14px;text-decoration:none;font-weight:900;
  color:#fff;background:linear-gradient(135deg,#1267e5,#2785f5);
  box-shadow:0 9px 20px rgba(18,103,229,.2);position:relative;z-index:1
}
.actions{
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px
}
.action{
  display:flex;align-items:center;justify-content:space-between;gap:12px;
  background:#fff;border:1px solid var(--line);border-radius:18px;padding:16px 18px;
  text-decoration:none;font-weight:900;box-shadow:0 8px 20px rgba(30,71,121,.05)
}
.action-icon{
  width:46px;height:46px;border-radius:14px;display:grid;place-items:center;
  font-size:23px;background:#eeeaff;color:var(--violet)
}
.action.manage .action-icon{background:#e8fbf3;color:var(--green)}
.action.logout .action-icon{background:#ffeaf0;color:var(--pink)}
.chev{font-size:24px;color:#8ba0bb}
.role-note{text-align:center;color:#91a0b4;font-size:12px;margin-top:20px}
@media(max-width:820px){
  .module-grid{grid-template-columns:1fr}
  .actions{grid-template-columns:1fr}
}
@media(max-width:620px){
  .shell{padding:10px 10px calc(24px + env(safe-area-inset-bottom))}
  .appbar{border-radius:16px;padding:12px 13px;align-items:flex-start}
  .logo{width:44px;height:44px;border-radius:13px}
  .appbar h1{font-size:20px}
  .user-chip{font-size:12px;padding:8px 10px;max-width:48vw;overflow:hidden;text-overflow:ellipsis}
  .hero{padding:19px;border-radius:19px}
  .hero h2{font-size:27px}
  .hero p{font-size:16px}
  .hero:after{font-size:80px;right:14px}
  .module-card{padding:17px;border-radius:19px}
  .module-icon{width:58px;height:58px;border-radius:16px;font-size:30px}
  .module-title{font-size:23px}
  .module-desc{font-size:14px}
  .metric strong{font-size:27px}
}
</style>
</head>
<body>
<div class="shell">
  <header class="appbar">
    <div class="appbar-brand">
      <img class="logo" src="/app-icon.svg?v=4-4-1" alt="ACT">
      <h1>ACT Operations</h1>
    </div>
    <div class="user-chip">👤 {{ display_name }}</div>
  </header>

  <section class="hero">
    <h2>ACT Operations</h2>
    <p>BedFlow + Doctor Call</p>
  </section>

  <section class="module-grid">
    {% if show_bedflow %}
    <article class="module-card">
      <div class="module-head">
        <div class="module-icon">🛏️</div>
        <div>
          <div class="module-title">ACT BedFlow</div>
          <div class="module-desc">ICU bed availability, confirmation and live status tracking.</div>
        </div>
      </div>
      <div class="metric">
        <div><strong>{{ available_beds }}</strong><span class="metric-copy">beds available now</span></div>
        <span class="chev">›</span>
      </div>
      <a class="open-btn" href="/">Open BedFlow →</a>
    </article>
    {% endif %}

    {% if show_doctor_call %}
    <article class="module-card">
      <div class="module-head">
        <div class="module-icon warm">🔔</div>
        <div>
          <div class="module-title">ACT Doctor Call</div>
          <div class="module-desc">Case PDF review, doctor-specific notifications and Accept / Reject workflow.</div>
        </div>
      </div>
      <div class="metric">
        <div><strong>{{ pending_cases }}</strong><span class="metric-copy">cases awaiting action</span></div>
        <span class="chev">›</span>
      </div>
      <a class="open-btn" href="{% if role == 'doctor' %}/doctor-call/doctor{% else %}/doctor-call{% endif %}">Open Doctor Call →</a>
    </article>
    {% endif %}
  </section>

  <section class="actions">
    <a class="action" href="/change-password">
      <span style="display:flex;align-items:center;gap:12px">
        <span class="action-icon">🔐</span>
        <span>Change Password</span>
      </span>
      <span class="chev">›</span>
    </a>

    {% if role == 'admin' %}
    <a class="action manage" href="/doctor-call/admin/doctors">
      <span style="display:flex;align-items:center;gap:12px">
        <span class="action-icon">👥</span>
        <span>Manage Doctors</span>
      </span>
      <span class="chev">›</span>
    </a>
    {% endif %}

    <a class="action logout" href="/logout">
      <span style="display:flex;align-items:center;gap:12px">
        <span class="action-icon">↪</span>
        <span>Sign Out</span>
      </span>
      <span class="chev">›</span>
    </a>
  </section>

  <div class="role-note">
    Signed in as {{ role|upper }}<br>
    <strong>Powered by Dr. Abdulfatah Sulieman · Insurance Department</strong>
  </div>
</div>
</body>
</html>
"""

DOCTOR_CALL_INSURANCE_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ACT Doctor Call</title>
<style>
:root{
  --bg:#eef4fb;--surface:#fff;--text:#152238;--muted:#6b7890;
  --blue:#1267e5;--blue2:#0b4fb3;--violet:#7057e8;--green:#178754;
  --amber:#e69618;--red:#d94242;--line:#dfe7f2;
  --shadow:0 14px 34px rgba(30,71,121,.09);
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{
  margin:0;font-family:Inter,Arial,sans-serif;color:var(--text);min-height:100vh;
  background:radial-gradient(circle at 88% 0,#dcecff 0,transparent 27%),
  linear-gradient(180deg,#edf5ff 0,#f7f9fc 430px);
}
button,a,input,select{font:inherit}
.wrap{max-width:1220px;margin:auto;padding:18px 18px 34px}
.topbar{
  display:flex;justify-content:space-between;align-items:center;gap:14px;
  padding:14px 18px;background:linear-gradient(135deg,#0b63df,#0878ef);
  color:#fff;border-radius:20px;box-shadow:0 14px 32px rgba(12,96,214,.22);
  margin-bottom:16px;
}
.brand-wrap{display:flex;align-items:center;gap:12px;min-width:0}
.brand-icon{width:52px;height:52px;border-radius:15px;background:#ffffff18;padding:5px;flex:0 0 auto}
.brand h1{margin:0;font-size:25px}.sub{color:#e7f1ff;margin-top:4px;font-size:13px}
.top-actions,.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:7px;border:0;
  border-radius:12px;padding:11px 15px;font-weight:800;text-decoration:none;
  cursor:pointer;transition:.18s transform,.18s box-shadow;
}
.btn:hover{transform:translateY(-1px)}
.btn:disabled{opacity:.6;cursor:not-allowed;transform:none}
.primary{background:linear-gradient(135deg,var(--blue),#2785f5);color:white;box-shadow:0 8px 18px rgba(18,103,229,.18)}
.smart{background:linear-gradient(135deg,#6d4ee8,#8b5cf6);color:white;box-shadow:0 8px 18px rgba(109,78,232,.18)}
.light{background:#fff;color:var(--text);border:1px solid var(--line)}
.topbar .light{background:#ffffff14;color:#fff;border-color:#ffffff35}
.card{
  background:var(--surface);border:1px solid var(--line);border-radius:22px;
  padding:20px;margin-bottom:16px;box-shadow:var(--shadow)
}
.hero{
  display:grid;grid-template-columns:1.35fr .65fr;gap:14px;margin-bottom:16px
}
.hero-main{
  background:linear-gradient(135deg,#fff 0,#fbfdff 65%,#eaf3ff 100%);
  border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:var(--shadow)
}
.hero-main h2{margin:0;font-size:27px}.hero-main p{margin:8px 0 0;color:var(--muted);line-height:1.5}
.role-pill{
  display:inline-flex;padding:6px 10px;border-radius:999px;background:#eaf2ff;
  color:#1459b8;font-size:12px;font-weight:900;margin-bottom:10px
}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.stat{
  background:#fff;border:1px solid var(--line);border-radius:18px;padding:16px;
  box-shadow:0 8px 20px rgba(30,71,121,.05)
}
.stat strong{display:block;font-size:27px}.stat span{color:var(--muted);font-size:12px}
.smart-card{border:1px solid #d8cffb;background:linear-gradient(180deg,#fbf9ff,#fff)}
.smart-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px}
.smart-title{display:flex;align-items:center;gap:11px}
.smart-icon{width:46px;height:46px;border-radius:14px;display:grid;place-items:center;background:#eee9ff;font-size:25px}
.beta{display:inline-flex;padding:6px 10px;border-radius:999px;background:#ede9fe;color:#5b21b6;font-size:11px;font-weight:900}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
label{display:block;color:var(--muted);font-size:12px;font-weight:800;margin-bottom:6px}
input,select{
  width:100%;padding:12px 13px;border:1px solid #d5dfec;border-radius:12px;
  background:#fff;font-size:15px;outline:none
}
input:focus,select:focus{border-color:#7eaef1;box-shadow:0 0 0 4px rgba(18,103,229,.08)}
.file-wrap{padding:13px;border:1px dashed #bfd0e6;border-radius:15px;background:#f8fbff}
.help{color:var(--muted);font-size:13px;line-height:1.55;margin-top:8px}
.progress{display:none;margin-top:12px;padding:12px 14px;border-radius:12px;background:#f1ecff;color:#5b21b6;font-size:13px;line-height:1.5}
.progress.show{display:block}
.fallback{display:none;margin-top:14px;padding:15px;border:1px solid #f0cf96;border-radius:15px;background:#fff9ec}
.fallback.show{display:block}
.fallback-title{font-weight:900;margin-bottom:6px}
.fallback .help{margin-bottom:12px}
.detected{display:none;margin:10px 0 12px;padding:10px 12px;border-radius:11px;background:#eef4ff;border:1px solid #c7d7ff;color:#244a9b;font-weight:900}
.detected.show{display:block}
.notice{background:#e9f8ef;border:1px solid #9bd6ad;color:#176b36;padding:13px 15px;border-radius:13px;margin-bottom:14px;line-height:1.5}
.cases-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
.cases-head h2{margin:0;font-size:21px}.cases-head .small{color:var(--muted);font-size:13px;margin-top:4px}
.desktop-table{width:100%;border-collapse:collapse}
.desktop-table th{text-align:left;padding:11px 9px;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid var(--line)}
.desktop-table td{padding:12px 9px;border-bottom:1px solid #edf1f6;font-size:13px;vertical-align:middle}
.case-no{font-weight:900}
.badge{display:inline-flex;padding:6px 9px;border-radius:999px;background:#edf2f8;font-weight:900;font-size:11px}
.badge.sent{background:#fff0e0;color:#9b5a00}.badge.opened{background:#eaf2ff;color:#1356a8}.badge.accepted{background:#e7f7ee;color:#176b3b}.badge.rejected{background:#fdecec;color:#a82929}
.mobile-cases{display:none}
.case-card{border:1px solid var(--line);border-radius:17px;padding:14px;background:#fff;box-shadow:0 7px 18px rgba(30,71,121,.05)}
.case-card+.case-card{margin-top:10px}
.case-card-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.case-card h3{margin:0;font-size:17px}.case-card .meta{margin:6px 0 11px;color:var(--muted);font-size:13px;line-height:1.55}
.case-card .btn{width:100%}
.empty{padding:30px;text-align:center;color:var(--muted);border:1px dashed #cfd9e7;border-radius:16px}
@media(max-width:880px){.hero{grid-template-columns:1fr}.stats{grid-template-columns:repeat(4,1fr)}}
@media(max-width:720px){
  .wrap{padding:10px 10px calc(24px + env(safe-area-inset-bottom))}
  .topbar{border-radius:17px;padding:12px;align-items:flex-start;flex-direction:column}
  .brand-icon{width:46px;height:46px}.brand h1{font-size:21px}
  .top-actions{width:100%;display:grid;grid-template-columns:repeat(2,1fr)}
  .top-actions .btn{width:100%;font-size:12px;padding:10px}
  .hero-main{padding:18px;border-radius:18px}.hero-main h2{font-size:23px}
  .stats{grid-template-columns:repeat(2,1fr)}
  .card{padding:15px;border-radius:18px}
  .grid{grid-template-columns:1fr}
  .actions{display:grid;grid-template-columns:1fr 1fr;width:100%}
  .actions .btn{width:100%;padding:12px 8px}
  .desktop-table{display:none}.mobile-cases{display:block}
}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js"></script>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brand-wrap">
      <img class="brand-icon" src="/app-icon.svg?v=4-4-1" alt="ACT">
      <div class="brand">
        <h1>ACT Doctor Call</h1>
        <div class="sub">{{ 'Admin' if role == 'admin' else 'Insurance' }} · Smart Clinical Referral</div>
      </div>
    </div>
    <div class="top-actions">
      <a class="btn light" href="/modules">🏠 Operations</a>
      <a class="btn light" href="/">🛏️ BedFlow</a>
      <a class="btn light" href="/doctor-call/admin/doctors">👥 Doctors</a>
      <a class="btn light" href="/logout">Logout</a>
    </div>
  </div>

  {% if message %}<div id="flashNotice" class="notice">{{ message }}</div>{% endif %}

  <section class="hero">
    <div class="hero-main">
      <span class="role-pill">{{ 'ADMIN CONTROL' if role == 'admin' else 'INSURANCE CONTROL' }}</span>
      <h2>Clinical Referral Workspace</h2>
      <p>Upload once, let ACT triage when confidence is high, or choose the specialty and doctor manually at any time.</p>
    </div>
    <div class="stats">
      <div class="stat"><strong>{{ pending_count }}</strong><span>Pending cases</span></div>
      <div class="stat"><strong>{{ completed_count }}</strong><span>Completed</span></div>
      <div class="stat"><strong>{{ active_doctors }}</strong><span>Active doctors</span></div>
      <div class="stat"><strong>{{ specialty_count }}</strong><span>Specialties</span></div>
    </div>
  </section>

  <div class="card smart-card">
    <div class="smart-head">
      <div class="smart-title">
        <div class="smart-icon">🤖</div>
        <div>
          <span class="beta">CLINICAL TRIAGE · SMART + MANUAL</span>
          <h2 style="margin:7px 0 0">Smart Referral</h2>
        </div>
      </div>
    </div>
    <div class="help">
      Upload the report once. ACT reads it, routes it automatically when confident, or keeps the same file ready so Insurance can choose the specialty and doctor manually.
    </div>

    <form id="smartReferralForm" method="post" action="/doctor-call/smart-referral" enctype="multipart/form-data" style="margin-top:16px">
      <input type="hidden" id="clientExtractedText" name="client_extracted_text">
      <input type="hidden" id="clientOcrPages" name="client_ocr_pages" value="0">
      <input type="hidden" id="manualSpecialtyValue" name="manual_specialty">
      <input type="hidden" id="manualDoctorValue" name="manual_doctor_username">
      <div class="grid">
        <div>
          <label>Case No. (optional)</label>
          <input name="case_no" placeholder="Auto-generated if empty">
        </div>
        <div>
          <label>Patient Ref. (optional)</label>
          <input name="patient_ref" placeholder="Patient / approval reference">
        </div>
        <div class="file-wrap" style="grid-column:1/-1">
          <label>Medical report + attachments (PDF) *</label>
          <input id="smartPdfs" type="file" name="pdfs" accept="application/pdf" multiple required>
          <div class="help">
            ACT reads text from the full report (up to 30 pages). If pages are scanned images, Browser OCR is applied to selected scanned pages so the diagnosis can be found even when it is not near the beginning.
          </div>
        </div>
      </div>

      <div id="manualFallback" class="fallback">
        <div class="fallback-title">Manual Send / Fallback</div>
        <div id="fallbackReason" class="help">
          If automatic routing cannot identify the specialty, choose it here and select the doctor from the existing Doctors Directory.
        </div>
        <div id="detectedSpecialtyBox" class="detected"></div>
        <div class="grid">
          <div>
            <label>Specialty *</label>
            <select id="fallbackSpecialty">
              <option value="">Choose specialty</option>
              {% for specialty in specialties %}
              <option value="{{ specialty }}">{{ specialty }}</option>
              {% endfor %}
            </select>
          </div>
          <div>
            <label>Doctor *</label>
            <select id="fallbackDoctor">
              <option value="">Choose doctor</option>
              {% for d in doctors %}
              <option value="{{ d['username'] }}" data-specialty="{{ d['specialty'] }}">
                {{ d['display_name'] }} — {{ d['specialty'] }}
              </option>
              {% endfor %}
            </select>
          </div>
        </div>
        <div style="margin-top:12px">
          <button id="manualSend" class="btn primary" type="button">
            Send to Selected Doctor 🔔
          </button>
        </div>
      </div>

      <div id="smartProgress" class="progress"></div>
      <div style="margin-top:14px">
        <div class="actions">
          <button id="smartSubmit" class="btn smart" type="submit">Analyze & Auto-Send 🔔</button>
          <button id="manualOpen" class="btn primary" type="button">Manual Send ✋</button>
        </div>
      </div>
    </form>
  </div>

  <div class="card">
    <div class="cases-head">
      <div>
        <h2>Referral Cases</h2>
        <div class="small">Latest 200 cases · newest first</div>
      </div>
      <span class="badge">{{ cases|length }} total</span>
    </div>

    {% if cases %}
    <table class="desktop-table">
      <thead><tr><th>Case</th><th>Specialty</th><th>Doctor</th><th>Status</th><th>Sent</th><th>Opened</th><th>Decision</th><th></th></tr></thead>
      <tbody>
      {% for c in cases %}
        <tr>
          <td class="case-no">{{ c['case_no'] }}</td>
          <td>{{ c['specialty'] }}</td>
          <td>{{ c['doctor_display'] or c['doctor_username'] }}</td>
          <td><span class="badge {{ (c['status'] or '')|lower }}">{{ c['status'] }}</span></td>
          <td>{{ c['sent_at'] }}</td>
          <td>{{ c['opened_at'] or '-' }}</td>
          <td>{{ c['decided_at'] or '-' }}</td>
          <td><a class="btn light" href="/doctor-call/case/{{ c['id'] }}">View →</a></td>
        </tr>
      {% endfor %}
      </tbody>
    </table>

    <div class="mobile-cases">
      {% for c in cases %}
      <article class="case-card">
        <div class="case-card-top">
          <div>
            <h3>{{ c['case_no'] }}</h3>
            <div class="meta">
              {{ c['specialty'] }}<br>
              {{ c['doctor_display'] or c['doctor_username'] }}<br>
              Sent: {{ c['sent_at'] }}
            </div>
          </div>
          <span class="badge {{ (c['status'] or '')|lower }}">{{ c['status'] }}</span>
        </div>
        <a class="btn primary" href="/doctor-call/case/{{ c['id'] }}">View Case →</a>
      </article>
      {% endfor %}
    </div>
    {% else %}
      <div class="empty">No referral cases yet.</div>
    {% endif %}
  </div>
  <div style="text-align:center;color:#8a95a8;font-size:12px;margin:20px 0 4px;font-weight:700">
    Powered by Dr. Abdulfatah Sulieman · Insurance Department
  </div>
<script>
const fallbackBox=document.getElementById("manualFallback");
const fallbackReason=document.getElementById("fallbackReason");
const fallbackSpecialty=document.getElementById("fallbackSpecialty");
const fallbackDoctor=document.getElementById("fallbackDoctor");
const detectedSpecialtyBox=document.getElementById("detectedSpecialtyBox");
const flashNotice=document.getElementById("flashNotice");
const manualSend=document.getElementById("manualSend");
const manualOpen=document.getElementById("manualOpen");
const manualSpecialtyValue=document.getElementById("manualSpecialtyValue");
const manualDoctorValue=document.getElementById("manualDoctorValue");

function filterFallbackDoctors(){
  const s=fallbackSpecialty.value;
  fallbackDoctor.value="";
  [...fallbackDoctor.options].forEach((o,i)=>{
    if(i===0)return;
    o.hidden=!!s&&o.dataset.specialty!==s;
  });
}
fallbackSpecialty.addEventListener("change",filterFallbackDoctors);

function clearOldResult(){
  if(flashNotice){
    flashNotice.style.display="none";
  }
  if(window.location.search.includes("message=")){
    window.history.replaceState({},document.title,window.location.pathname);
  }
}

function selectDetectedSpecialty(detectedSpecialty){
  detectedSpecialtyBox.classList.remove("show");
  detectedSpecialtyBox.textContent="";

  if(!detectedSpecialty) return;

  detectedSpecialtyBox.textContent="Detected Specialty: "+detectedSpecialty;
  detectedSpecialtyBox.classList.add("show");

  const wanted=detectedSpecialty.trim().toLowerCase();
  const match=[...fallbackSpecialty.options].find(
    o=>o.value && o.value.trim().toLowerCase()===wanted
  );

  if(match){
    fallbackSpecialty.value=match.value;
    filterFallbackDoctors();

    const visibleDoctors=[...fallbackDoctor.options].filter(
      (o,i)=>i>0 && !o.hidden
    );

    if(visibleDoctors.length===1){
      fallbackDoctor.value=visibleDoctors[0].value;
    }
  }
}

function showFallback(reason,detectedSpecialty){
  if(reason) fallbackReason.textContent=reason;
  selectDetectedSpecialty(detectedSpecialty);
  fallbackBox.classList.add("show");
  fallbackBox.scrollIntoView({behavior:"smooth",block:"center"});
}

function hideFallback(){
  fallbackBox.classList.remove("show");
  fallbackSpecialty.value="";
  fallbackDoctor.value="";
  detectedSpecialtyBox.classList.remove("show");
  detectedSpecialtyBox.textContent="";
  manualSpecialtyValue.value="";
  manualDoctorValue.value="";
}

const smartForm=document.getElementById("smartReferralForm");
const smartPdfs=document.getElementById("smartPdfs");
const smartSubmit=document.getElementById("smartSubmit");
const smartProgress=document.getElementById("smartProgress");
const clientText=document.getElementById("clientExtractedText");
const clientOcrPages=document.getElementById("clientOcrPages");

if(window.pdfjsLib){
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
}

function progress(msg){
  smartProgress.textContent=msg;
  smartProgress.classList.add("show");
}

async function readPdfOnDevice(file, state){
  const data=new Uint8Array(await file.arrayBuffer());
  const pdf=await pdfjsLib.getDocument({data}).promise;
  const pagesToCheck=Math.min(pdf.numPages,30);
  const chunks=[];
  const imageOnlyPages=[];

  // First pass: direct text extraction is lightweight, so scan the whole
  // report instead of only the opening pages.
  for(let p=1;p<=pagesToCheck;p++){
    progress("Reading "+file.name+" — page "+p+" of "+pagesToCheck+"...");
    const page=await pdf.getPage(p);
    const textContent=await page.getTextContent();
    const directText=textContent.items.map(i=>i.str||"").join(" ").trim();

    if(directText.length>=40){
      chunks.push(directText);
    }else{
      imageOnlyPages.push(p);
    }
  }

  if(!imageOnlyPages.length || !window.Tesseract){
    return chunks.join("\\n");
  }

  // OCR selected image-only pages. Prefer the beginning and end of the
  // report, where referral diagnosis / impression / discharge summary
  // commonly appears, while keeping browser processing practical.
  const selected=[];
  const pushUnique=p=>{
    if(p && imageOnlyPages.includes(p) && !selected.includes(p)){
      selected.push(p);
    }
  };

  imageOnlyPages.slice(0,3).forEach(pushUnique);
  imageOnlyPages.slice(-3).forEach(pushUnique);

  if(imageOnlyPages.length>6){
    pushUnique(imageOnlyPages[Math.floor(imageOnlyPages.length/2)]);
  }

  for(const p of selected){
    if(state.ocrPages>=7) break;

    progress("OCR on "+file.name+" — scanned page "+p+"...");
    const page=await pdf.getPage(p);
    const viewport=page.getViewport({scale:1.35});
    const canvas=document.createElement("canvas");
    const ctx=canvas.getContext("2d",{alpha:false});
    canvas.width=Math.ceil(viewport.width);
    canvas.height=Math.ceil(viewport.height);

    await page.render({
      canvasContext:ctx,
      viewport:viewport
    }).promise;

    const result=await Tesseract.recognize(
      canvas,
      "eng",
      {
        logger:m=>{
          if(m && m.status==="recognizing text"){
            progress(
              "OCR "+file.name+" — page "+p+
              " ("+Math.round((m.progress||0)*100)+"%)"
            );
          }
        }
      }
    );

    const ocrText=((result||{}).data||{}).text||"";
    if(ocrText.trim()) chunks.push(ocrText.trim());
    state.ocrPages+=1;

    canvas.width=1;
    canvas.height=1;
  }

  return chunks.join("\\n");
}

let extractionReady=false;

async function prepareReportText(){
  if(extractionReady) return true;

  const files=[...smartPdfs.files];
  if(!files.length){
    progress("Please choose at least one PDF.");
    return false;
  }

  smartSubmit.disabled=true;
  smartSubmit.textContent="Reading report...";
  progress("Preparing referral on this device...");

  try{
    if(!window.pdfjsLib){
      throw new Error("PDF reader library did not load.");
    }

    const state={ocrPages:0};
    const texts=[];

    for(const file of files){
      const text=await readPdfOnDevice(file,state);
      if(text.trim()) texts.push(text.trim());
    }

    const combined=texts.join("\\n");
    clientText.value=combined.slice(0,60000);
    clientOcrPages.value=String(state.ocrPages);
    extractionReady=true;

    if(combined.replace(/\s+/g," ").trim().length<80){
      progress("ACT could not read enough clinical text for automatic routing.");
      showFallback(
        "ACT could not read enough clinical text to route automatically. " +
        "Choose the specialty and doctor below; the same PDF will be sent."
      );
      smartSubmit.disabled=false;
      smartSubmit.textContent="Try Auto Routing Again 🔁";
      return false;
    }

    return true;

  }catch(err){
    console.error(err);
    progress("Automatic reading stopped: "+(err.message||"Could not read the PDF."));
    showFallback(
      "Automatic reading could not complete. Choose the specialty and doctor below; " +
      "you do not need to upload the report again."
    );
    smartSubmit.disabled=false;
    smartSubmit.textContent="Try Auto Routing Again 🔁";
    return false;
  }
}

async function postReferral(manualMode){
  const formData=new FormData(smartForm);

  if(manualMode){
    const specialty=fallbackSpecialty.value;
    const doctor=fallbackDoctor.value;

    if(!specialty || !doctor){
      progress("Choose both specialty and doctor.");
      return;
    }

    manualSpecialtyValue.value=specialty;
    manualDoctorValue.value=doctor;
    formData.set("manual_specialty",specialty);
    formData.set("manual_doctor_username",doctor);
    manualSend.disabled=true;
    manualSend.textContent="Sending...";
    progress("Sending the same report to the selected doctor...");
  }else{
    manualSpecialtyValue.value="";
    manualDoctorValue.value="";
    formData.set("manual_specialty","");
    formData.set("manual_doctor_username","");
    smartSubmit.disabled=true;
    smartSubmit.textContent="Routing...";
    progress("ACT is matching the report to the Doctors Directory...");
  }

  try{
    const response=await fetch("/doctor-call/smart-referral",{
      method:"POST",
      body:formData,
      headers:{"X-Requested-With":"fetch"}
    });

    const data=await response.json();

    if(data.ok){
      progress(data.message||"Case sent successfully.");
      window.location.href=data.redirect_url||"/doctor-call";
      return;
    }

    if(data.fallback){
      progress("Automatic routing needs your selection.");
      showFallback(
        data.reason||"Choose specialty and doctor.",
        data.detected_specialty||""
      );
    }else{
      progress(data.error||"Referral could not be completed.");
    }

  }catch(err){
    console.error(err);
    progress("Could not contact ACT. Please try again.");
  }finally{
    smartSubmit.disabled=false;
    smartSubmit.textContent="Analyze & Auto-Send 🔔";
    manualSend.disabled=false;
    manualSend.textContent="Send to Selected Doctor 🔔";
  }
}

smartPdfs.addEventListener("change",()=>{
  clearOldResult();
  extractionReady=false;
  clientText.value="";
  clientOcrPages.value="0";
  hideFallback();
});

smartForm.addEventListener("submit",async event=>{
  event.preventDefault();
  clearOldResult();
  hideFallback();

  const ready=await prepareReportText();
  if(!ready) return;

  await postReferral(false);
});

manualOpen.addEventListener("click",()=>{
  clearOldResult();

  if(!smartPdfs.files.length){
    progress("Choose at least one PDF first, then select specialty and doctor.");
  }else{
    progress("Manual mode ready. Choose specialty and doctor below.");
  }

  fallbackReason.textContent=
    "Manual Send: choose the specialty and doctor yourself. " +
    "ACT will send the same selected PDF directly without automatic routing.";
  fallbackBox.classList.add("show");
  fallbackBox.scrollIntoView({behavior:"smooth",block:"center"});
});

manualSend.addEventListener("click",async ()=>{
  // Manual Send is always available to Insurance and Admin.
  await postReferral(true);
});
</script>
</body>
</html>
"""

DOCTOR_CALL_DOCTOR_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel="manifest" href="/manifest.json?v=4-4-1">
<link rel="apple-touch-icon" href="/app-icon.svg?v=4-4-1">
<meta name="theme-color" content="#0b5fd7">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="ACT Doctor Call">
<title>ACT Doctor Call</title>
<style>
:root{
  --bg:#eef4fb;--surface:#fff;--text:#152238;--muted:#6b7890;
  --blue:#1267e5;--blue2:#0b4fb3;--green:#178754;--red:#d94242;
  --line:#dfe7f2;--shadow:0 14px 34px rgba(30,71,121,.09);
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;font-family:Inter,Arial,sans-serif;background:linear-gradient(180deg,#eaf2fb 0,#f7f9fc 420px);color:var(--text);min-height:100vh}
button,a,input{font:inherit}
.app-shell{max-width:1180px;margin:auto;padding:18px 18px 34px}
.app-header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 18px;border:1px solid rgba(255,255,255,.8);background:rgba(255,255,255,.88);backdrop-filter:blur(12px);border-radius:22px;box-shadow:var(--shadow);position:sticky;top:10px;z-index:10}
.brand-wrap{display:flex;align-items:center;gap:13px;min-width:0}
.app-icon{width:56px;height:56px;border-radius:16px;box-shadow:0 8px 18px rgba(11,95,215,.22);flex:0 0 auto}
.brand h1{margin:0;font-size:24px;line-height:1.1}
.doctor-meta{margin-top:5px;color:var(--muted);font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.header-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
.btn{border:0;border-radius:12px;padding:11px 14px;font-weight:800;text-decoration:none;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:7px;transition:.18s transform,.18s box-shadow}
.btn:hover{transform:translateY(-1px)}
.btn.primary{background:linear-gradient(135deg,var(--blue),#2785f5);color:white;box-shadow:0 8px 18px rgba(18,103,229,.2)}
.btn.success{background:var(--green);color:white}
.btn.light{background:#fff;border:1px solid var(--line);color:var(--text)}
.btn.danger{background:var(--red);color:white}
.hero{margin:18px 0;display:grid;grid-template-columns:1.35fr .65fr;gap:14px}
.welcome-card,.notify-card,.panel,.stat{background:var(--surface);border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow)}
.welcome-card{padding:22px;background:linear-gradient(135deg,#0b5fd7 0,#1b78ea 60%,#5ab6ff 120%);color:white;position:relative;overflow:hidden}
.welcome-card:after{content:"";position:absolute;width:210px;height:210px;border-radius:50%;background:rgba(255,255,255,.11);right:-70px;top:-90px}
.welcome-card h2{margin:0;font-size:25px;position:relative;z-index:1}
.welcome-card p{margin:8px 0 0;opacity:.9;line-height:1.5;position:relative;z-index:1}
.notify-card{padding:18px;display:flex;flex-direction:column;justify-content:center}
.notify-title{display:flex;align-items:center;gap:9px;font-size:17px;font-weight:900}
.status-dot{width:11px;height:11px;border-radius:50%;background:#f0a000;box-shadow:0 0 0 5px rgba(240,160,0,.12)}
.status-text{color:var(--muted);font-size:13px;margin:7px 0 12px;line-height:1.45}
.notification-actions{display:flex;gap:8px;flex-wrap:wrap}
.notification-actions .btn{flex:1;min-width:145px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px}
.stat{padding:17px 18px;display:flex;align-items:center;gap:13px}
.stat-icon{width:43px;height:43px;border-radius:13px;display:grid;place-items:center;font-size:22px;background:#eaf2ff}
.stat strong{display:block;font-size:24px}
.stat span{color:var(--muted);font-size:12px}
.panel{padding:20px}
.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.panel-head h2{margin:0;font-size:20px}
.panel-sub{color:var(--muted);font-size:13px;margin-top:4px}
.notice{display:none;background:#fff8df;border:1px solid #f2d477;color:#72570b;padding:14px 16px;border-radius:15px;margin:14px 0}
.notice.show{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.install-guide{display:none;background:#edf5ff;border:1px solid #bcd6ff;border-radius:15px;padding:14px 16px;margin:14px 0;line-height:1.55}
.install-guide.show{display:block}
.install-guide strong{color:#0b4fb3}
.small{color:var(--muted);font-size:13px}
.desktop-table{width:100%;border-collapse:collapse}
.desktop-table th{text-align:left;padding:11px 10px;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--line)}
.desktop-table td{padding:13px 10px;border-bottom:1px solid #edf1f6;font-size:14px}
.case-no{font-weight:900}
.badge{display:inline-flex;padding:6px 10px;border-radius:999px;background:#edf2f8;font-weight:800;font-size:12px}
.badge.sent{background:#fff0e0;color:#9b5a00}
.badge.opened{background:#eaf2ff;color:#1356a8}
.badge.accepted{background:#e7f7ee;color:#176b3b}
.badge.rejected{background:#fdecec;color:#a82929}
.mobile-cases{display:none}
.case-card{border:1px solid var(--line);border-radius:17px;padding:15px;background:#fff;box-shadow:0 7px 18px rgba(30,71,121,.05)}
.case-card+.case-card{margin-top:10px}
.case-card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.case-card h3{margin:0;font-size:17px}
.case-card .meta{margin:6px 0 12px;color:var(--muted);font-size:13px;line-height:1.5}
.case-card .btn{width:100%}
.empty{padding:34px;text-align:center;color:var(--muted);border:1px dashed #cfd9e7;border-radius:16px}
.footer-note{text-align:center;color:#8a95a8;font-size:12px;margin:20px 0 4px}
@media(max-width:850px){.hero{grid-template-columns:1fr}.stats{grid-template-columns:repeat(3,1fr)}}
@media(max-width:700px){
  body{background:#f4f7fb}
  .app-shell{padding:10px 10px calc(24px + env(safe-area-inset-bottom))}
  .app-header{top:6px;padding:12px;border-radius:18px;align-items:flex-start}
  .app-icon{width:48px;height:48px;border-radius:14px}
  .brand h1{font-size:20px}
  .doctor-meta{font-size:12px;max-width:190px}
  .header-actions{gap:6px}
  .header-actions .btn{padding:9px 11px;font-size:12px}
  .hero{margin:12px 0;gap:10px}
  .welcome-card{padding:18px;border-radius:18px}
  .welcome-card h2{font-size:21px}
  .notify-card{border-radius:18px}
  .stats{grid-template-columns:1fr 1fr 1fr;gap:8px}
  .stat{padding:12px 10px;display:block;text-align:center;border-radius:16px}
  .stat-icon{width:36px;height:36px;margin:0 auto 7px}
  .stat strong{font-size:20px}
  .stat span{font-size:10px}
  .panel{padding:14px;border-radius:18px}
  .desktop-table{display:none}
  .mobile-cases{display:block}
  .notification-actions{width:100%}
  .notification-actions .btn{min-width:0}
}
@media(max-width:430px){
  .app-header{display:block}
  .header-actions{margin-top:10px;justify-content:stretch}
  .header-actions .btn{flex:1}
  .doctor-meta{max-width:100%}
  .notification-actions{display:grid;grid-template-columns:1fr 1fr}
}
</style>
</head>
<body>
<div class="app-shell">
  <header class="app-header">
    <div class="brand-wrap">
      <img class="app-icon" src="/app-icon.svg?v=4-4-1" alt="ACT">
      <div class="brand">
        <h1>ACT Doctor Call</h1>
        <div class="doctor-meta">{{ display_name }} · {{ doctor_specialty }}</div>
      </div>
    </div>
    <div class="header-actions">
      <button id="installApp" class="btn primary" type="button">📲 Install</button>
      <a class="btn light" href="/change-password">🔐 Password</a>
      <a class="btn light" href="/logout">Logout</a>
    </div>
  </header>

  <div id="installGuide" class="install-guide">
    <strong>Install ACT Doctor Call</strong>
    <div id="installGuideText" class="small" style="margin-top:5px">
      Android: tap Install. iPhone: Safari → Share → Add to Home Screen.
    </div>
  </div>

  <div id="newCaseBanner" class="notice">
    <div>
      <strong>🔔 New case received</strong>
      <div id="newCaseText" class="small" style="margin-top:4px"></div>
    </div>
    <a id="newCaseLink" class="btn primary" href="/doctor-call/doctor">Open Case</a>
  </div>

  <section class="hero">
    <div class="welcome-card">
      <h2>Good day, {{ display_name }}</h2>
      <p>Your {{ doctor_specialty }} cases appear here only. Open the PDF, review the case, then Accept or Reject.</p>
    </div>
    <div class="notify-card">
      <div class="notify-title"><span id="statusDot" class="status-dot"></span> Phone Notifications</div>
      <div id="notificationStatus" class="status-text">Checking notification status...</div>
      <div class="notification-actions">
        <button id="enableNotifications" class="btn primary" type="button">Enable</button>
        <button id="testNotification" class="btn light" type="button">Test 🔔</button>
      </div>
    </div>
  </section>

  <section class="stats">
    <div class="stat">
      <div class="stat-icon">🆕</div>
      <div><strong>{{ new_count }}</strong><span>New cases</span></div>
    </div>
    <div class="stat">
      <div class="stat-icon">👀</div>
      <div><strong>{{ review_count }}</strong><span>In review</span></div>
    </div>
    <div class="stat">
      <div class="stat-icon">✅</div>
      <div><strong>{{ completed_count }}</strong><span>Completed</span></div>
    </div>
  </section>

  <section class="panel">
    <div class="panel-head">
      <div>
        <h2>Doctor Inbox</h2>
        <div class="panel-sub">Cases assigned specifically to your account</div>
      </div>
      <span class="badge">{{ cases|length }} total</span>
    </div>

    {% if cases %}
    <table class="desktop-table">
      <thead><tr><th>Case</th><th>Specialty</th><th>Status</th><th>Sent</th><th></th></tr></thead>
      <tbody>
      {% for c in cases %}
        <tr>
          <td class="case-no">{{ c['case_no'] }}</td>
          <td>{{ c['specialty'] }}</td>
          <td><span class="badge {{ (c['status'] or '')|lower }}">{{ c['status'] }}</span></td>
          <td>{{ c['sent_at'] }}</td>
          <td><a class="btn primary" href="/doctor-call/case/{{ c['id'] }}">Open Case →</a></td>
        </tr>
      {% endfor %}
      </tbody>
    </table>

    <div class="mobile-cases">
      {% for c in cases %}
      <article class="case-card">
        <div class="case-card-top">
          <div>
            <h3>{{ c['case_no'] }}</h3>
            <div class="meta">{{ c['specialty'] }}<br>{{ c['sent_at'] }}</div>
          </div>
          <span class="badge {{ (c['status'] or '')|lower }}">{{ c['status'] }}</span>
        </div>
        <a class="btn primary" href="/doctor-call/case/{{ c['id'] }}">Open Case →</a>
      </article>
      {% endfor %}
    </div>
    {% else %}
      <div class="empty">No cases assigned to you right now.</div>
    {% endif %}
  </section>

  <div class="footer-note">
    ACT Doctor Call · Secure doctor-specific case review<br>
    <strong>Powered by Dr. Abdulfatah Sulieman · Insurance Department</strong>
  </div>
</div>

<script>
const VAPID_PUBLIC_KEY = "{{ vapid_public_key }}";
let deferredInstallPrompt = null;
const installButton = document.getElementById("installApp");
const installGuide = document.getElementById("installGuide");
const installGuideText = document.getElementById("installGuideText");
const statusDot = document.getElementById("statusDot");

function isStandaloneMode(){
  return window.matchMedia("(display-mode: standalone)").matches
    || window.navigator.standalone === true;
}

function isIOSDevice(){
  return /iphone|ipad|ipod/i.test(window.navigator.userAgent);
}

function refreshInstallUI(){
  if(isStandaloneMode()){
    installButton.style.display="none";
    installGuide.classList.remove("show");
    return;
  }

  installButton.style.display="inline-flex";

  if(isIOSDevice()){
    installGuideText.textContent =
      "iPhone/iPad: open ACT in Safari → Share → Add to Home Screen → Add.";
  }else{
    installGuideText.textContent =
      "Android: tap Install. If no prompt appears, Chrome menu (⋮) → Install app / Add to Home screen.";
  }
}

window.addEventListener("beforeinstallprompt", event=>{
  event.preventDefault();
  deferredInstallPrompt = event;
  refreshInstallUI();
});

window.addEventListener("appinstalled", ()=>{
  deferredInstallPrompt = null;
  installButton.style.display="none";
  installGuide.classList.remove("show");
});

installButton.addEventListener("click", async ()=>{
  if(isStandaloneMode()) return;

  if(deferredInstallPrompt){
    deferredInstallPrompt.prompt();
    try{
      await deferredInstallPrompt.userChoice;
    }catch(e){
      console.warn("Install prompt result unavailable", e);
    }
    deferredInstallPrompt = null;
    return;
  }

  refreshInstallUI();
  installGuide.classList.toggle("show");
});

let latestCaseId = Number("{{ latest_case_id }}") || 0;
let audioContext = null;

function b64arr(s){
  const p="=".repeat((4-s.length%4)%4);
  const b=(s+p).replace(/-/g,"+").replace(/_/g,"/");
  const r=atob(b);
  return Uint8Array.from([...r].map(c=>c.charCodeAt(0)));
}

function setStatus(message, enabled=false){
  const st=document.getElementById("notificationStatus");
  const bt=document.getElementById("enableNotifications");
  st.textContent=message;
  if(enabled){
    bt.textContent="Enabled ✅";
    bt.className="btn success";
    statusDot.style.background="#18a05e";
    statusDot.style.boxShadow="0 0 0 5px rgba(24,160,94,.12)";
  } else {
    bt.textContent="Enable";
    bt.className="btn primary";
    bt.disabled=false;
    statusDot.style.background="#f0a000";
    statusDot.style.boxShadow="0 0 0 5px rgba(240,160,0,.12)";
  }
}

function ensureAudio(){
  try{
    if(!audioContext){
      const AC=window.AudioContext||window.webkitAudioContext;
      if(AC) audioContext=new AC();
    }
    if(audioContext && audioContext.state==="suspended") audioContext.resume();
  }catch(e){console.warn("Audio init failed",e);}
}

function playDoctorAlert(){
  ensureAudio();
  try{
    if(audioContext){
      const now=audioContext.currentTime;
      [0,0.75,1.5].forEach((offset,i)=>{
        const osc=audioContext.createOscillator();
        const gain=audioContext.createGain();
        osc.type="sine";
        osc.frequency.value=i===2?980:820;
        gain.gain.setValueAtTime(0.0001,now+offset);
        gain.gain.exponentialRampToValueAtTime(0.22,now+offset+0.03);
        gain.gain.exponentialRampToValueAtTime(0.0001,now+offset+0.55);
        osc.connect(gain); gain.connect(audioContext.destination);
        osc.start(now+offset); osc.stop(now+offset+0.6);
      });
    }
  }catch(e){console.warn("Alert tone failed",e);}

  if("vibrate" in navigator){
    navigator.vibrate([900,180,900,180,900,180,1600]);
  }

  try{
    if("speechSynthesis" in window){
      window.speechSynthesis.cancel();
      const msg=new SpeechSynthesisUtterance("There is a case for review");
      msg.lang="en-US";
      msg.rate=0.9;
      msg.volume=1;
      window.speechSynthesis.speak(msg);
    }
  }catch(e){console.warn("Speech alert failed",e);}
}

function showNewCase(caseData){
  if(!caseData) return;
  latestCaseId=Math.max(latestCaseId,Number(caseData.id)||0);
  document.getElementById("newCaseText").textContent =
    (caseData.specialty || "Doctor") + " · Case " + (caseData.case_no || "");
  document.getElementById("newCaseLink").href =
    caseData.url || ("/doctor-call/case/" + caseData.id);
  document.getElementById("newCaseBanner").classList.add("show");
  if(document.visibilityState==="visible") playDoctorAlert();
}

async function saveSubscription(sub){
  const resp=await fetch("/doctor-call/api/push/subscribe",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(sub)
  });
  return await resp.json();
}

async function registerPush(){
  ensureAudio();

  if(!("serviceWorker" in navigator)||!("PushManager" in window)){
    setStatus("Push notifications are not supported on this browser.");
    return false;
  }

  const perm=await Notification.requestPermission();
  if(perm!=="granted"){
    setStatus("Notification permission was not granted.");
    return false;
  }

  const reg=await navigator.serviceWorker.register("/sw.js");
  let sub=await reg.pushManager.getSubscription();

  if(!sub){
    sub=await reg.pushManager.subscribe({
      userVisibleOnly:true,
      applicationServerKey:b64arr(VAPID_PUBLIC_KEY)
    });
  }

  const data=await saveSubscription(sub);
  if(data.ok){
    setStatus("Active on "+data.devices+" device(s).",true);
    return true;
  }

  setStatus("Could not register this device.");
  return false;
}

async function refreshPushStatus(){
  try{
    const resp=await fetch("/doctor-call/api/push/status",{cache:"no-store"});
    const data=await resp.json();
    if(data.ok && data.devices>0 && Notification.permission==="granted"){
      setStatus("Active on "+data.devices+" device(s).",true);
    }
  }catch(e){console.warn(e);}
}

async function testNotification(){
  ensureAudio();
  const ready=await registerPush();
  if(!ready) return;

  const btn=document.getElementById("testNotification");
  const old=btn.textContent;
  btn.disabled=true;
  btn.textContent="Sending...";

  try{
    const resp=await fetch("/doctor-call/api/push/test",{method:"POST"});
    const data=await resp.json();
    if(data.ok){
      setStatus("Test delivered to "+data.sent+" of "+data.registered+" device(s).",true);
      if(document.visibilityState==="visible") playDoctorAlert();
    }else{
      setStatus(data.error||"Could not send test notification.");
    }
  }catch(e){
    setStatus("Could not send test notification.");
  }finally{
    btn.disabled=false;
    btn.textContent=old;
  }
}

async function checkLatestCase(){
  try{
    const resp=await fetch("/doctor-call/api/doctor/latest",{cache:"no-store"});
    const data=await resp.json();
    if(data.ok && data.case && Number(data.case.id)>latestCaseId){
      showNewCase(data.case);
    }
  }catch(e){console.warn("Case polling error",e);}
}

document.getElementById("enableNotifications").addEventListener("click",registerPush);
document.getElementById("testNotification").addEventListener("click",testNotification);

if("serviceWorker" in navigator){
  navigator.serviceWorker.addEventListener("message",event=>{
    const msg=event.data||{};
    if(msg.type==="ACT_DOCTOR_CASE" && msg.data){
      showNewCase(msg.data);
    }
  });
}

window.addEventListener("load",async()=>{
  refreshInstallUI();
  try{
    if(!("serviceWorker" in navigator)||!("PushManager" in window)){
      setStatus("Push notifications are not supported on this browser.");
      return;
    }

    const reg=await navigator.serviceWorker.register("/sw.js");
    const sub=await reg.pushManager.getSubscription();

    if(sub && Notification.permission==="granted"){
      const data=await saveSubscription(sub);
      if(data.ok){
        setStatus("Active on "+data.devices+" device(s).",true);
      }
    }else if(Notification.permission==="denied"){
      setStatus("Notifications are blocked in browser settings.");
    }else{
      setStatus("Tap Enable on this device.");
    }

    await refreshPushStatus();
  }catch(e){
    console.error(e);
    setStatus("Notification setup requires HTTPS.");
  }
});

setInterval(checkLatestCase,8000);
</script>
</body>
</html>
"""

DOCTOR_CALL_CASE_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
{% if role == 'doctor' %}
<link rel="manifest" href="/manifest.json?v=4-4-1">
<link rel="apple-touch-icon" href="/app-icon.svg?v=4-4-1">
{% endif %}
<meta name="theme-color" content="#0b5fd7">
<title>Case {{ case['case_no'] }}</title>
<style>
:root{--bg:#eef4fb;--surface:#fff;--text:#152238;--muted:#6b7890;--blue:#1267e5;--green:#178754;--red:#d94242;--line:#dfe7f2;--shadow:0 14px 34px rgba(30,71,121,.09)}
*{box-sizing:border-box}body{margin:0;font-family:Inter,Arial,sans-serif;background:linear-gradient(180deg,#eaf2fb,#f7f9fc 420px);color:var(--text)}
.shell{max-width:1180px;margin:auto;padding:18px}
.header{display:flex;justify-content:space-between;align-items:center;gap:12px;background:rgba(255,255,255,.9);border:1px solid var(--line);border-radius:20px;padding:15px 17px;box-shadow:var(--shadow);position:sticky;top:10px;z-index:10}
.brand{display:flex;align-items:center;gap:12px}.brand img{width:48px;height:48px;border-radius:14px}.brand h1{margin:0;font-size:21px}.sub{color:var(--muted);font-size:13px;margin-top:4px}
.btn{border:0;border-radius:12px;padding:11px 14px;font-weight:800;text-decoration:none;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:7px}
.primary{background:linear-gradient(135deg,#1267e5,#2785f5);color:white}.success{background:var(--green);color:white}.danger{background:var(--red);color:white}.light{background:white;color:var(--text);border:1px solid var(--line)}
.info-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}
.info{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:0 7px 18px rgba(30,71,121,.05)}.label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}.value{font-weight:900;margin-top:5px;word-break:break-word}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}.actions form{margin:0}.actions .btn{min-width:150px}
.pdf-card{background:#fff;border:1px solid var(--line);border-radius:20px;padding:13px;box-shadow:var(--shadow)}
.pdf-head{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:4px 4px 12px}.pdf-head h2{margin:0;font-size:18px}
iframe{width:100%;height:72vh;border:1px solid #e2e8f0;border-radius:14px;background:#f8fafc}
@media(max-width:760px){.shell{padding:10px 10px calc(20px + env(safe-area-inset-bottom))}.header{top:6px;border-radius:17px}.brand img{width:42px;height:42px}.brand h1{font-size:18px}.info-grid{grid-template-columns:1fr 1fr}.actions{display:grid;grid-template-columns:1fr 1fr;position:sticky;bottom:8px;z-index:8;background:rgba(247,249,252,.9);padding:8px;border-radius:16px;backdrop-filter:blur(10px)}.actions form,.actions .btn{width:100%;min-width:0}iframe{height:66vh}.pdf-head{align-items:flex-start;flex-direction:column}.pdf-head .btn{width:100%}}
</style>
</head>
<body>
<div class="shell">
  <header class="header">
    <div class="brand">
      <img src="/app-icon.svg?v=4-4-1" alt="ACT">
      <div>
        <h1>Case {{ case['case_no'] }}</h1>
        <div class="sub">{{ case['specialty'] }} · {{ doctor_name }}</div>
      </div>
    </div>
    {% if role == 'doctor' %}
      <a class="btn light" href="/doctor-call/doctor">← Inbox</a>
    {% else %}
      <a class="btn light" href="/doctor-call">← Cases</a>
    {% endif %}
  </header>

  <section class="info-grid">
    <div class="info"><div class="label">Status</div><div class="value">{{ case['status'] }}</div></div>
    <div class="info"><div class="label">Specialty</div><div class="value">{{ case['specialty'] }}</div></div>
    <div class="info"><div class="label">Patient Ref.</div><div class="value">{{ case['patient_ref'] or '-' }}</div></div>
    <div class="info"><div class="label">Sent</div><div class="value">{{ case['sent_at'] }}</div></div>
  </section>

  {% if role == 'doctor' and case['status'] not in ['Accepted','Rejected'] %}
  <div class="actions">
    <form method="post" action="/doctor-call/case/{{ case['id'] }}/decision/Accepted">
      <button class="btn success" type="submit">✓ Accept Case</button>
    </form>
    <form method="post" action="/doctor-call/case/{{ case['id'] }}/decision/Rejected">
      <button class="btn danger" type="submit">✕ Reject Case</button>
    </form>
  </div>
  {% endif %}

  <section class="pdf-card">
    <div class="pdf-head">
      <h2>Medical Report PDF</h2>
      <a class="btn primary" target="_blank" href="/doctor-call/case/{{ case['id'] }}/pdf">Open PDF ↗</a>
    </div>
    <iframe src="/doctor-call/case/{{ case['id'] }}/pdf"></iframe>
  </section>
  <div style="text-align:center;color:#8a95a8;font-size:12px;margin:18px 0 2px;font-weight:700">
    Powered by Dr. Abdulfatah Sulieman · Insurance Department
  </div>
</div>
</body>
</html>
"""

DOCTOR_CALL_INSTALL_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel="manifest" href="/manifest.json?v=4-4-1">
<link rel="apple-touch-icon" href="/app-icon.svg?v=4-4-1">
<meta name="theme-color" content="#0b5fd7">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="ACT Doctor Call">
<title>Install ACT Doctor Call</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Inter,Arial,sans-serif;background:radial-gradient(circle at top,#dcecff,#f6f8fc 52%);color:#142238;min-height:100vh;display:grid;place-items:center;padding:22px}
.card{width:min(620px,100%);background:rgba(255,255,255,.94);border:1px solid #dce5f1;border-radius:28px;padding:28px;box-shadow:0 24px 70px rgba(25,72,132,.15);text-align:center}
.icon{width:110px;height:110px;border-radius:28px;box-shadow:0 14px 30px rgba(11,95,215,.22)}
h1{font-size:31px;margin:17px 0 7px}.sub{color:#66758c;line-height:1.6;margin-bottom:20px}
.btn{width:100%;border:0;border-radius:14px;padding:14px 18px;font-weight:900;font-size:17px;cursor:pointer;text-decoration:none;display:flex;align-items:center;justify-content:center;gap:8px}
.primary{background:linear-gradient(135deg,#0b5fd7,#2785f5);color:#fff;box-shadow:0 10px 24px rgba(11,95,215,.22)}
.light{margin-top:9px;background:#fff;color:#142238;border:1px solid #dce5f1}
.guide{margin-top:18px;text-align:left;background:#f4f8fd;border:1px solid #dae7f7;border-radius:16px;padding:16px;color:#5c6c82;line-height:1.7}
.guide strong{color:#173f75}.tiny{font-size:12px;color:#8793a5;margin-top:17px}
@media(max-width:520px){.card{padding:22px 17px;border-radius:22px}.icon{width:92px;height:92px;border-radius:24px}h1{font-size:26px}}
</style>
</head>
<body>
<main class="card">
  <img class="icon" src="/app-icon.svg?v=4-4-1" alt="ACT Doctor Call">
  <h1>ACT Doctor Call</h1>
  <div class="sub">Install the doctor app on your phone for faster access, case alerts, PDF review, and Accept / Reject.</div>
  <button id="installNow" class="btn primary" type="button">📲 Install ACT Doctor Call</button>
  <a class="btn light" href="/login">Open Login Page</a>

  <a id="openChrome" class="btn light" style="display:none" href="#">
    🌐 Open this page in Chrome
  </a>

  <div id="browserWarning" class="guide" style="display:none;background:#fff7df;border-color:#efd37c;color:#6f5600">
    This QR opened inside an in-app browser. Installation may not appear here.
    Open this page in <b>Chrome on Android</b> or <b>Safari on iPhone</b>, then install.
  </div>

  <div id="guide" class="guide">
    <strong>Android:</strong> Chrome → tap <b>Install ACT Doctor Call</b>. If no prompt appears, Chrome menu (⋮) → Install app / Add to Home screen.<br>
    <strong>iPhone:</strong> Safari → Share → Add to Home Screen → Add.
  </div>
  <div class="tiny">
    After installation, the ACT icon opens directly to the Doctor Inbox. Login is still required for security.<br><br>
    <strong>Powered by Dr. Abdulfatah Sulieman · Insurance Department</strong>
  </div>
</main>
<script>
let deferredInstallPrompt=null;
const installNow=document.getElementById("installNow");
const guide=document.getElementById("guide");
const browserWarning=document.getElementById("browserWarning");
const openChrome=document.getElementById("openChrome");

function isIOS(){
  return /iphone|ipad|ipod/i.test(navigator.userAgent);
}

function isAndroid(){
  return /android/i.test(navigator.userAgent);
}

function isLikelyInAppBrowser(){
  const ua=navigator.userAgent.toLowerCase();
  return /(fbav|fban|instagram|line\/|wv\)|; wv|whatsapp)/i.test(ua);
}

function configureBrowserHelp(){
  if(isAndroid()){
    const target=location.host + location.pathname;
    openChrome.href=
      "intent://" + target +
      "#Intent;scheme=https;package=com.android.chrome;end";
    openChrome.style.display="flex";
  }

  if(isLikelyInAppBrowser()){
    browserWarning.style.display="block";
  }

  if(isIOS()){
    openChrome.style.display="none";
  }
}

configureBrowserHelp();

function standalone(){
  return window.matchMedia("(display-mode: standalone)").matches
    || window.navigator.standalone===true;
}
window.addEventListener("beforeinstallprompt",event=>{
  event.preventDefault();
  deferredInstallPrompt=event;
});
window.addEventListener("appinstalled",()=>{
  deferredInstallPrompt=null;
  installNow.textContent="Installed ✅";
  installNow.disabled=true;
});
installNow.addEventListener("click",async()=>{
  if(standalone()){
    installNow.textContent="Already Installed ✅";
    return;
  }
  if(deferredInstallPrompt){
    deferredInstallPrompt.prompt();
    try{await deferredInstallPrompt.userChoice;}catch(e){}
    deferredInstallPrompt=null;
  }else{
    if(isLikelyInAppBrowser()){
      browserWarning.style.display="block";
      browserWarning.scrollIntoView({behavior:"smooth",block:"center"});
    }else{
      guide.scrollIntoView({behavior:"smooth",block:"center"});
    }
  }
});
if("serviceWorker" in navigator){
  navigator.serviceWorker.register("/sw.js").catch(()=>{});
}
</script>
</body>
</html>
"""

DOCTOR_CALL_ADMIN_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Doctors Directory</title>
<style>
:root{--bg:#eef4fb;--surface:#fff;--text:#152238;--muted:#6b7890;--blue:#1267e5;--green:#178754;--red:#d94242;--line:#dfe7f2;--shadow:0 14px 34px rgba(30,71,121,.09)}
*{box-sizing:border-box}body{margin:0;font-family:Inter,Arial,sans-serif;background:radial-gradient(circle at 88% 0,#dcecff 0,transparent 27%),linear-gradient(180deg,#edf5ff,#f7f9fc 430px);color:var(--text);min-height:100vh}
.wrap{max-width:1220px;margin:auto;padding:18px 18px 34px}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:14px 18px;background:linear-gradient(135deg,#0b63df,#0878ef);color:#fff;border-radius:20px;box-shadow:0 14px 32px rgba(12,96,214,.22);margin-bottom:16px}
.brand-wrap{display:flex;align-items:center;gap:12px}.brand-icon{width:52px;height:52px;border-radius:15px;background:#ffffff18;padding:5px}.brand h1{margin:0;font-size:25px}.sub{color:#e7f1ff;margin-top:4px;font-size:13px}
.actions{display:flex;gap:8px;flex-wrap:wrap}.btn{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:12px;padding:10px 13px;font-weight:800;text-decoration:none;cursor:pointer}.primary{background:linear-gradient(135deg,#1267e5,#2785f5);color:white}.light{background:white;border:1px solid var(--line);color:var(--text)}.topbar .light{background:#ffffff14;color:#fff;border-color:#ffffff35}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px}.stat{background:#fff;border:1px solid var(--line);border-radius:18px;padding:17px;box-shadow:0 8px 20px rgba(30,71,121,.05)}.stat strong{display:block;font-size:28px}.stat span{color:var(--muted);font-size:12px}
.card{background:white;border:1px solid var(--line);border-radius:20px;padding:19px;margin-bottom:16px;box-shadow:var(--shadow)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}label{display:block;color:var(--muted);font-size:12px;font-weight:800;margin-bottom:5px}input{width:100%;padding:11px 12px;border:1px solid #d5dfec;border-radius:11px;font-size:14px}input:focus{outline:none;border-color:#7eaef1;box-shadow:0 0 0 4px rgba(18,103,229,.08)}
.notice{background:#e9f8ef;border:1px solid #9bd6ad;color:#176b36;padding:12px 14px;border-radius:12px;margin-bottom:14px}.info{background:#eef4ff;border:1px solid #c7d7ff;color:#294e9b;padding:12px 14px;border-radius:12px;margin-bottom:14px}
.directory-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}.directory-head h2{margin:0}.small{font-size:12px;color:var(--muted)}
.doctor-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.doctor{border:1px solid var(--line);border-radius:17px;padding:15px;background:#fff}.doctor-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px}.name{font-weight:900;font-size:17px}.status{font-size:11px;padding:6px 9px;border-radius:999px;background:#e7f7ee;color:#176b3b;font-weight:900}.status.off{background:#fdecec;color:#a82929}.specialty{font-size:15px;font-weight:900;margin-top:6px;color:#1d4f91}
.edit-grid{display:grid;grid-template-columns:1.2fr 1fr 1fr auto;gap:8px;align-items:end}.toggle-form{margin-top:8px}.toggle-form .btn{width:100%}
@media(max-width:900px){.doctor-grid{grid-template-columns:1fr}.edit-grid{grid-template-columns:1fr 1fr}}
@media(max-width:700px){.wrap{padding:10px}.topbar{align-items:flex-start;flex-direction:column;border-radius:17px}.actions{width:100%;display:grid;grid-template-columns:1fr 1fr}.actions .btn{width:100%;font-size:12px}.stats{grid-template-columns:repeat(3,1fr);gap:8px}.stat{padding:12px}.stat strong{font-size:22px}.grid,.edit-grid{grid-template-columns:1fr}.card{padding:15px;border-radius:18px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brand-wrap">
      <img class="brand-icon" src="/app-icon.svg?v=4-4-1" alt="ACT">
      <div class="brand">
        <h1>Doctors Directory</h1>
        <div class="sub">ACT Doctor Call · specialties, availability and account management</div>
      </div>
    </div>
    <div class="actions">
      <a class="btn light" href="/doctor-call">🔔 Doctor Call</a>
      <a class="btn light" href="/modules">🏠 Operations</a>
    </div>
  </div>

  {% if message %}<div class="notice">{{ message }}</div>{% endif %}
  {% if not can_manage %}
    <div class="info">Insurance view: you can see doctors and specialties. Only Admin can add, edit, activate or disable doctor accounts.</div>
  {% endif %}

  <section class="stats">
    <div class="stat"><strong>{{ doctor_count }}</strong><span>Total doctors</span></div>
    <div class="stat"><strong>{{ active_count }}</strong><span>Active</span></div>
    <div class="stat"><strong>{{ specialty_count }}</strong><span>Specialties</span></div>
  </section>

  {% if can_manage %}
  <div class="card">
    <h2 style="margin-top:0">Add Doctor</h2>
    <form method="post" action="/doctor-call/admin/doctors/add">
      <div class="grid">
        <div><label>Username *</label><input name="username" required></div>
        <div><label>Display Name *</label><input name="display_name" required></div>
        <div><label>Specialty *</label><input name="specialty" placeholder="e.g. Cardiology" required></div>
        <div><label>Temporary Password *</label><input type="password" name="password" minlength="6" required></div>
      </div>
      <div style="margin-top:14px"><button class="btn primary">Add Doctor</button></div>
    </form>
  </div>
  {% endif %}

  <div class="card">
    <div class="directory-head">
      <div>
        <h2>Doctors</h2>
        <div class="small">Each doctor receives only cases assigned to their own username.</div>
      </div>
    </div>
    <div class="doctor-grid">

    {% for d in doctors %}
    <div class="doctor">
      <div class="doctor-head">
        <div>
          <div class="name">{{ d['display_name'] }}</div>
          <div class="small">@{{ d['username'] }}</div>
          <div class="specialty">{{ d['specialty'] }}</div>
        </div>
        <div class="status {{ 'off' if d['active']!=1 else '' }}">{{ 'Active' if d['active']==1 else 'Disabled' }}</div>
      </div>

      {% if can_manage %}
      <form method="post" action="/doctor-call/admin/doctors/{{ d['username'] }}/edit">
        <div class="edit-grid">
          <div><label>Display Name</label><input name="display_name" value="{{ d['display_name'] }}" required></div>
          <div><label>Specialty</label><input name="specialty" value="{{ d['specialty'] }}" required></div>
          <div><label>New Password (optional)</label><input type="password" name="new_password" minlength="6" placeholder="Leave blank to keep"></div>
          <button class="btn primary">Save</button>
        </div>
      </form>
      <form class="toggle-form" method="post" action="/doctor-call/admin/doctors/{{ d['username'] }}/toggle">
        <button class="btn light">{{ 'Disable Doctor' if d['active']==1 else 'Activate Doctor' }}</button>
      </form>
      {% endif %}
    </div>
    {% else %}
      <div>No doctors.</div>
    {% endfor %}
    </div>
  </div>
  <div style="text-align:center;color:#8a95a8;font-size:12px;margin:20px 0 4px;font-weight:700">
    Powered by Dr. Abdulfatah Sulieman · Insurance Department
  </div>
</div>
</body>
</html>
"""

DEFAULT_USERS = {
    "insurance": {
        "password": "1234",
        "role": "insurance",
        "display": "Insurance Department"
    },
    "icu": {
        "password": "1234",
        "role": "icu",
        "display": "ICU Department"
    },
    "admin": {
        "password": "1234",
        "role": "admin",
        "display": "ACT BedFlow Admin"
    }
}

DEFAULT_DOCTOR_PASSWORD = os.getenv(
    "ACT_DEFAULT_DOCTOR_PASSWORD",
    "ACT-Doctor-Temporary-Password"
)

# Restored from the hospital Doctors/Specialty list.
# Usernames are stable IDs; Admin can change display names, specialty,
# activation status, and passwords later without this seed overwriting them.
DEFAULT_DOCTORS = [
    ("talal.elhrby", "Dr. Talal Elhrby", "Orthopedics"),
    ("sayed.waer", "Dr. Sayed Waer", "Internal Medicine"),
    ("hashim.elkinani", "Dr. Hashim Elkinani", "Internal Medicine"),
    ("elhussini.elshahwi", "Dr. El-Hussini Elshahwi", "Cardiology"),
    ("ayman.morsy", "Dr. Ayman Morsy", "Cardiology"),
    ("waqas.muhammad", "Dr. Waqas Muhammad", "Cardiology"),
    ("mujeebulrhman", "Dr. Mujeebulrhman", "Pulmonology"),
    ("najawa.eltayeb", "Dr. Najawa Eltayeb", "General Surgery"),
    ("ibrahim.sulmai", "Dr. Ibrahim Sulmai", "General Surgery"),
    ("rehab.hashim", "Dr. Rehab Hashim", "Pediatrics"),
    ("marwa", "Dr. Marwa", "Neonatology"),
    ("mohamed.elshekh", "Dr. Mohamed Elshekh", "Neurology"),
    ("ekhlas.elnjeeb", "Dr. Ekhlas Elnjeeb", "OB/GYN"),
    ("mon.sulieman", "Dr. Mon Sulieman", "OB/GYN"),
    ("amjad.badawod", "Dr. Amjad Badawod", "Gastroenterology"),
    ("belo", "Dr. Belo", "ENT"),
    ("jaber", "Dr. Jaber", "ICU/Critical Care"),
    ("waleed", "Dr. Waleed", "General Surgery"),
    ("ahmed.helaly", "Dr. Ahmed Helaly", "Pediatrics"),
    ("hazem.hamdan", "Dr. Hazem Hamdan", "General Surgery"),
    ("mervt", "Dr. Mervt", "OB/GYN"),
    ("tarig.eljamal", "Dr. Tarig El-Jamal", "ENT"),
    ("tamador.osman", "Dr. Tamador Osman", "Internal Medicine"),
    ("abdulmajed.abuali", "Dr. Abdulmajed Abu-Ali", "Urology"),
    ("mohammed.elfaki", "Dr. Mohammed El-Faki", "ICU/Critical Care")
]


def init_db():
    conn = get_db()

    bed_id_definition = (
        "BIGSERIAL PRIMARY KEY"
        if USE_POSTGRES
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    log_id_definition = (
        "BIGSERIAL PRIMARY KEY"
        if USE_POSTGRES
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS beds (
            id {bed_id_definition},
            name TEXT UNIQUE NOT NULL,
            bed_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Available'
        )
    """)

    if USE_POSTGRES:
        column_rows = conn.execute("""
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'beds'
        """).fetchall()
    else:
        column_rows = conn.execute(
            "PRAGMA table_info(beds)"
        ).fetchall()

    columns = [
        row["name"]
        for row in column_rows
    ]

    if "last_updated" not in columns:
        if USE_POSTGRES:
            conn.execute("""
                ALTER TABLE beds
                ADD COLUMN IF NOT EXISTS last_updated TEXT
            """)
        else:
            conn.execute("""
                ALTER TABLE beds
                ADD COLUMN last_updated TEXT
            """)

    if "active" not in columns:
        if USE_POSTGRES:
            conn.execute("""
                ALTER TABLE beds
                ADD COLUMN IF NOT EXISTS active INTEGER NOT NULL DEFAULT 1
            """)
        else:
            conn.execute("""
                ALTER TABLE beds
                ADD COLUMN active INTEGER NOT NULL DEFAULT 1
            """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_status (
            id INTEGER PRIMARY KEY,
            last_icu_confirmation TEXT
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS activity_log (
            id {log_id_definition},
            timestamp TEXT NOT NULL,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            action TEXT NOT NULL,
            bed_name TEXT,
            old_value TEXT,
            new_value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            display_name TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    for username, user in DEFAULT_USERS.items():
        user_values = (
            username,
            generate_password_hash(user["password"]),
            user["role"],
            user["display"],
            now_text()
        )

        if USE_POSTGRES:
            conn.execute("""
                INSERT INTO users
                (
                    username,
                    password_hash,
                    role,
                    display_name,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (username) DO NOTHING
            """, user_values)
        else:
            conn.execute("""
                INSERT OR IGNORE INTO users
                (
                    username,
                    password_hash,
                    role,
                    display_name,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
            """, user_values)

    # Seed the full doctor directory without overwriting later Admin changes.
    for username, display_name, specialty in DEFAULT_DOCTORS:
        doctor_values = (
            username,
            generate_password_hash(DEFAULT_DOCTOR_PASSWORD),
            "doctor",
            display_name,
            now_text()
        )

        if USE_POSTGRES:
            conn.execute("""
                INSERT INTO users
                (
                    username,
                    password_hash,
                    role,
                    display_name,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (username) DO NOTHING
            """, doctor_values)
        else:
            conn.execute("""
                INSERT OR IGNORE INTO users
                (
                    username,
                    password_hash,
                    role,
                    display_name,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
            """, doctor_values)

    default_beds = [
        (
            f"ICU-{i:02d}",
            "Regular ICU"
        )
        for i in range(1, 11)
    ]
    default_beds.append((
        "Isolation Room",
        "Isolation"
    ))

    for bed_name, bed_type in default_beds:
        bed_values = (
            bed_name,
            bed_type,
            "Available",
            now_text(),
            1
        )

        if USE_POSTGRES:
            conn.execute("""
                INSERT INTO beds
                (
                    name,
                    bed_type,
                    status,
                    last_updated,
                    active
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (name) DO NOTHING
            """, bed_values)
        else:
            conn.execute("""
                INSERT OR IGNORE INTO beds
                (
                    name,
                    bed_type,
                    status,
                    last_updated,
                    active
                )
                VALUES (?, ?, ?, ?, ?)
            """, bed_values)

    conn.execute("""
        UPDATE beds
        SET last_updated = ?
        WHERE last_updated IS NULL
           OR last_updated = ''
    """, (now_text(),))

    if USE_POSTGRES:
        conn.execute("""
            INSERT INTO system_status
            (id, last_icu_confirmation)
            VALUES (1, ?)
            ON CONFLICT (id) DO NOTHING
        """, (now_text(),))
    else:
        conn.execute("""
            INSERT OR IGNORE INTO system_status
            (id, last_icu_confirmation)
            VALUES (1, ?)
        """, (now_text(),))

    conn.commit()
    conn.close()


# =========================
# LOGIN HELPERS
# =========================

def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


def icu_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):

        if "username" not in session:
            return redirect(url_for("login"))

        if session.get("role") not in ("icu", "admin"):
            return redirect(url_for("home"))

        return function(*args, **kwargs)

    return wrapper


# =========================
# LOGIN PAGE
# =========================

LOGIN_HTML = """
<!DOCTYPE html>
<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>ACT Operations Login</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f4f7fb;
    color: #172033;
}

.page {
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 20px;
}

.card {
    width: 100%;
    max-width: 420px;
    background: white;
    padding: 35px;
    border-radius: 18px;
    border: 1px solid #e5e7eb;
    box-shadow: 0 10px 30px rgba(0,0,0,.06);}

.logo {
    font-size: 31px;
    font-weight: bold;
    margin-bottom: 5px;
}

.subtitle {
    color: #6b7280;
    margin-bottom: 30px;
}

label {
    display: block;
    font-size: 14px;
    font-weight: bold;
    margin: 15px 0 7px;
}

input {
    width: 100%;
    padding: 13px;
    border: 1px solid #d5dae2;
    border-radius: 9px;
    font-size: 16px;
}

button {
    width: 100%;
    margin-top: 22px;
    padding: 13px;
    border: none;
    border-radius: 9px;
    background: #172033;
    color: white;
    font-size: 16px;
    font-weight: bold;
    cursor: pointer;
}

.error {
    background: #fdecec;
    color: #a52a2a;
    border: 1px solid #f1b7b7;
    padding: 11px;
    border-radius: 8px;
    margin-bottom: 15px;
}

.success {
    background: #e9f8ef;
    color: #176b36;
    border: 1px solid #9bd6ad;
    padding: 11px;
    border-radius: 8px;
    margin-bottom: 15px;
}

.footer {
    text-align: center;
    color: #9aa0aa;
    font-size: 12px;
    margin-top: 25px;
}

</style>

</head>

<body>

<div class="page">

    <div class="card">

        <div class="logo">
            ACT Operations
        </div>

        <div class="subtitle">
            BedFlow + Doctor Call
        </div>

        {% if error %}

        <div class="error">
            Incorrect username or password.
        </div>

        {% endif %}

        {% if success_message %}

        <div class="success">
            {{ success_message }}
        </div>

        {% endif %}

        <form method="POST">

            <label>
                Username
            </label>

            <input
                name="username"
                autocomplete="username"
                required
            >

            <label>
                Password
            </label>

            <input
                type="password"
                name="password"
                autocomplete="current-password"
                required
            >

            <button type="submit">
                Sign In
            </button>

        </form>

        <div class="footer">
            ACT Integrated v3.1<br><br>
            <strong>Powered by Dr. Abdulfatah Sulieman · Insurance Department</strong>
        </div>

    </div>

</div>

</body>

</html>
"""


# =========================
# CHANGE PASSWORD PAGE
# =========================

CHANGE_PASSWORD_HTML = """
<!DOCTYPE html>
<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Change Password - ACT BedFlow</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f4f7fb;
    color: #172033;
}

.page {
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 20px;
}

.card {
    width: 100%;
    max-width: 440px;
    background: white;
    padding: 35px;
    border-radius: 18px;
    border: 1px solid #e5e7eb;
    box-shadow: 0 10px 30px rgba(0,0,0,.06);
}

h1 {
    margin: 0 0 6px;
    font-size: 28px;
}

.subtitle {
    color: #6b7280;
    margin-bottom: 24px;
}

label {
    display: block;
    font-size: 14px;
    font-weight: bold;
    margin: 15px 0 7px;
}

input {
    width: 100%;
    padding: 13px;
    border: 1px solid #d5dae2;
    border-radius: 9px;
    font-size: 16px;
}

button {
    width: 100%;
    margin-top: 22px;
    padding: 13px;
    border: none;
    border-radius: 9px;
    background: #172033;
    color: white;
    font-size: 16px;
    font-weight: bold;
    cursor: pointer;
}

.error {
    background: #fdecec;
    color: #a52a2a;
    border: 1px solid #f1b7b7;
    padding: 11px;
    border-radius: 8px;
    margin-bottom: 15px;
}

.back {
    display: block;
    text-align: center;
    margin-top: 18px;
    color: #6b7280;
    text-decoration: none;
}

</style>

</head>

<body>

<div class="page">

    <div class="card">

        <h1>Change Password</h1>

        <div class="subtitle">
            {{ display_name }} — username cannot be changed.
        </div>

        {% if error_message %}

        <div class="error">
            {{ error_message }}
        </div>

        {% endif %}

        <form method="POST">

            <label>Current Password</label>

            <input
                type="password"
                name="current_password"
                autocomplete="current-password"
                required
            >

            <label>New Password</label>

            <input
                type="password"
                name="new_password"
                autocomplete="new-password"
                minlength="6"
                required
            >

            <label>Confirm New Password</label>

            <input
                type="password"
                name="confirm_password"
                autocomplete="new-password"
                minlength="6"
                required
            >

            <button type="submit">
                Save New Password
            </button>

        </form>

        <a class="back" href="/modules">
            Back to Apps
        </a>

        <div style="text-align:center;color:#9aa0aa;font-size:12px;margin-top:22px;font-weight:700">
            Powered by Dr. Abdulfatah Sulieman · Insurance Department
        </div>

    </div>

</div>

</body>

</html>
"""


# =========================
# DASHBOARD
# =========================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<meta http-equiv="refresh" content="30">

<title>ACT BedFlow</title>

<style>

* {
    box-sizing: border-box;
}

body {
    font-family: Arial, sans-serif;
    background: #f4f7fb;
    margin: 0;
    color: #172033;
}

.container {
    max-width: 1150px;
    margin: auto;
    padding: 24px;
}

/* =========================
   CLEAN HEADER
   ========================= */

.app-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    margin-bottom: 16px;
}

.brand h1 {
    margin: 0;
    font-size: 30px;
    line-height: 1.1;
}

.subtitle {
    color: #6b7280;
    margin-top: 5px;
    font-size: 15px;
}

.header-actions {
    display: flex;
    align-items: center;
    gap: 10px;
}

.module-link {
    display: inline-flex;
    align-items: center;
    min-height: 40px;
    padding: 9px 12px;
    border-radius: 12px;
    background: #172033;
    color: white;
    text-decoration: none;
    font-size: 13px;
    font-weight: bold;
    white-space: nowrap;
}

.role-pill {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    min-height: 40px;
    padding: 9px 12px;
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    font-size: 14px;
    font-weight: bold;
    white-space: nowrap;
}

.account-menu {
    position: relative;
}

.account-menu summary {
    list-style: none;
    width: 40px;
    height: 40px;
    display: grid;
    place-items: center;
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    cursor: pointer;
    font-size: 23px;
    line-height: 1;
    user-select: none;
}

.account-menu summary::-webkit-details-marker {
    display: none;
}

.account-menu[open] summary {
    background: #eef2f7;
}

.menu-card {
    position: absolute;
    right: 0;
    top: 48px;
    z-index: 20;
    min-width: 190px;
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 7px;
    box-shadow: 0 12px 30px rgba(23, 32, 51, .12);
}

.menu-card a {
    display: block;
    padding: 11px 12px;
    border-radius: 8px;
    color: #172033;
    text-decoration: none;
    font-size: 14px;
}

.menu-card a:hover {
    background: #f4f7fb;
}

.menu-card .signout {
    color: #b42318;
}

/* =========================
   ICU STATUS CARD
   ========================= */

.status-card {
    border-radius: 14px;
    padding: 16px;
    margin-bottom: 20px;
}

.status-card.fresh {
    background: #e9f8ef;
    border: 1px solid #9bd6ad;
    color: #176b36;
}

.status-card.warning {
    background: #fff3cd;
    border: 1px solid #f1c453;
    color: #7a5600;
}

.status-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 15px;
}

.status-title {
    font-size: 17px;
    font-weight: bold;
    line-height: 1.35;
}

.status-time {
    margin-top: 5px;
    font-size: 13px;
    opacity: .85;
}

.status-note {
    margin-top: 8px;
    font-size: 13px;
    line-height: 1.45;
}

.confirm-button {
    border: none;
    border-radius: 9px;
    padding: 11px 15px;
    cursor: pointer;
    font-weight: bold;
    white-space: nowrap;
    background: #172033;
    color: white;
}

/* =========================
   SUMMARY
   ========================= */

.summary {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
    margin-bottom: 25px;
}

.summary-box {
    background: white;
    border-radius: 12px;
    padding: 18px;
    border: 1px solid #e5e7eb;
}

.summary-box span {
    color: #6b7280;
    font-size: 14px;
}

.summary-box strong {
    display: block;
    font-size: 28px;
    margin-top: 5px;
}

/* =========================
   BED CARDS
   ========================= */

.beds {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 14px;
}

.bed {
    background: white;
    border-radius: 14px;
    padding: 18px;
    border: 1px solid #e5e7eb;
}

.bed h3 {
    margin: 0 0 12px 0;
}

.status {
    font-weight: bold;
    margin-bottom: 5px;
}

.Available {
    color: #159447;
}

.Occupied {
    color: #d14343;
}

.Reserved {
    color: #d18b00;
}

.Cleaning {
    color: #2878c7;
}

.updated {
    color: #8a919e;
    font-size: 12px;
    margin-bottom: 13px;
}

select {
    width: 100%;
    padding: 10px;
    border-radius: 8px;
    border: 1px solid #ccc;
    font-size: 15px;
    background: white;
}

.update-button {
    width: 100%;
    margin-top: 8px;
    padding: 10px;
    border: 0;
    border-radius: 8px;
    background: #172033;
    color: white;
    cursor: pointer;
}

.read-only {
    background: #f7f8fa;
    border-radius: 8px;
    padding: 10px;
    color: #6b7280;
    font-size: 13px;
}

.isolation {
    border: 2px solid #805ad5;
}

.inactive {
    opacity: .55;
    background: #f1f3f5;
}

/* =========================
   ADMIN
   ========================= */

.admin-panel {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 20px;
}

.admin-panel form {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
}

.admin-panel input,
.admin-panel select {
    flex: 1;
    min-width: 180px;
    padding: 10px;
    border: 1px solid #ccc;
    border-radius: 8px;
}

.admin-button {
    padding: 10px 14px;
    border: 0;
    border-radius: 8px;
    background: #172033;
    color: white;
    cursor: pointer;
    font-weight: bold;
}

.toggle-button {
    width: 100%;
    margin-top: 8px;
    padding: 9px;
    border: 1px solid #d5dae2;
    border-radius: 8px;
    background: white;
    cursor: pointer;
}

.footer {
    text-align: center;
    color: #8a919e;
    font-size: 12px;
    margin-top: 25px;
}

/* =========================
   MOBILE
   ========================= */

@media (max-width: 800px) {

    .container {
        padding: 18px 16px 24px;
    }

    .app-header {
        align-items: flex-start;
        margin-bottom: 14px;
    }

    .brand h1 {
        font-size: 27px;
    }

    .subtitle {
        font-size: 14px;
    }

    .header-actions {
        gap: 7px;
    }

    .role-pill {
        max-width: 145px;
        min-height: 38px;
        padding: 8px 10px;
        overflow: hidden;
        text-overflow: ellipsis;
        font-size: 12px;
    }

    .account-menu summary {
        width: 38px;
        height: 38px;
    }

    .menu-card {
        top: 45px;
    }

    .status-row {
        display: block;
    }

    .confirm-button {
        width: 100%;
        margin-top: 12px;
    }

    .summary {
        grid-template-columns: repeat(2, 1fr);
        gap: 10px;
        margin-bottom: 22px;
    }

    .summary-box {
        padding: 16px;
    }

    .summary-box strong {
        font-size: 27px;
    }

    .beds {
        grid-template-columns: 1fr;
    }
}

@media (max-width: 430px) {

    .role-pill {
        max-width: 125px;
    }

    .brand h1 {
        font-size: 25px;
    }
}

</style>

</head>

<body>

<div class="container">

    <header class="app-header">

        <div class="brand">
            <h1>ACT BedFlow</h1>

            <div class="subtitle">
                ICU Bed Availability System
            </div>
        </div>

        <div class="header-actions">

            {% if role in ["insurance", "admin"] %}
            <a class="module-link" href="/modules">Apps</a>
            {% endif %}

            <div class="role-pill">
                👤 {{ display_name }}
            </div>

            <details class="account-menu">
                <summary aria-label="Account menu">⋮</summary>

                <div class="menu-card">
                    <a href="/change-password">
                        Change Password
                    </a>

                    <a class="signout" href="/logout">
                        Sign Out
                    </a>
                </div>
            </details>

        </div>

    </header>


    {% if stale %}

    <div class="status-card warning">

        <div class="status-row">

            <div>
                <div class="status-title">
                    ⚠ ICU status needs confirmation
                </div>

                <div class="status-time">
                    Last confirmed: <strong>{{ last_confirmation_display }}</strong>
                </div>

                <div class="status-note">
                    ICU bed availability has not been confirmed for more than 30 minutes.
                    Please contact ICU for an updated status.
                </div>
            </div>

            {% if role in ["icu", "admin"] %}

            <form method="POST" action="/confirm">
                <button class="confirm-button" type="submit">
                    ✓ Confirm ICU Status
                </button>
            </form>

            {% endif %}

        </div>

    </div>

    {% else %}

    <div class="status-card fresh">

        <div class="status-row">

            <div>
                <div class="status-title">
                    ✓ ICU availability confirmed
                </div>

                <div class="status-time">
                    Updated: <strong>{{ last_confirmation_display }}</strong>
                </div>
            </div>

            {% if role in ["icu", "admin"] %}

            <form method="POST" action="/confirm">
                <button class="confirm-button" type="submit">
                    ✓ Confirm ICU Status
                </button>
            </form>

            {% endif %}

        </div>

    </div>

    {% endif %}


    {% if role == "admin" %}

    <div class="admin-panel">

        <h3>Admin — Bed Management</h3>

        <form method="POST"
              action="/admin/add-bed">

            <input
                name="name"
                placeholder="Bed name e.g. ICU-11"
                required
            >

            <select name="bed_type">
                <option value="Regular ICU">
                    Regular ICU
                </option>

                <option value="Isolation">
                    Isolation
                </option>
            </select>

            <button
                class="admin-button"
                type="submit">

                + Add Bed

            </button>

        </form>

        <a href="/activity-log"
           class="admin-button"
           style="
               display:inline-block;
               margin-top:15px;
               text-decoration:none;
               text-align:center;
           ">
            Activity Log
        </a>

    </div>

    {% endif %}


    <div class="summary">

        <div class="summary-box">
            <span>Total Beds</span>
            <strong>{{ total }}</strong>
        </div>

        <div class="summary-box">
            <span>Available</span>
            <strong>{{ available }}</strong>
        </div>

        <div class="summary-box">
            <span>Occupied</span>
            <strong>{{ occupied }}</strong>
        </div>

        <div class="summary-box">
            <span>Reserved</span>
            <strong>{{ reserved }}</strong>
        </div>

        <div class="summary-box">
            <span>Cleaning</span>
            <strong>{{ cleaning }}</strong>
        </div>

    </div>


    <div class="beds">

        {% for bed in beds %}

        <div class="
            bed
            {% if bed['bed_type'] == 'Isolation' %}
                isolation
            {% endif %}
            {% if bed['active'] == 0 %}
                inactive
            {% endif %}
        ">

            <h3>
                {{ bed['name'] }}
            </h3>

            <div class="status {{ bed['status'] }}">
                ● {{ bed['status'] }}
            </div>

            <div class="updated">
                Updated:
                {{ bed['last_updated'] }}
            </div>


            {% if role in ["icu", "admin"]
                  and bed['active'] == 1 %}

            <form method="POST"
                  action="/update/{{ bed['id'] }}">

                <select name="status">

                    {% for status in
                    ['Available',
                     'Occupied',
                     'Reserved',
                     'Cleaning'] %}

                    <option
                        value="{{ status }}"
                        {% if bed['status'] == status %}
                            selected
                        {% endif %}
                    >
                        {{ status }}
                    </option>

                    {% endfor %}

                </select>

                <button
                    class="update-button"
                    type="submit">

                    Update Status

                </button>

            </form>

            {% else %}

            <div class="read-only">

                {% if bed['active'] == 0 %}
                    Bed Disabled
                {% else %}
                    View only — updated by ICU
                {% endif %}

            </div>

            {% endif %}


            {% if role == "admin" %}

            <form method="POST"
                  action="/admin/toggle-bed/{{ bed['id'] }}">

                <button
                    class="toggle-button"
                    type="submit">

                    {% if bed['active'] == 1 %}
                        Disable Bed
                    {% else %}
                        Reactivate Bed
                    {% endif %}

                </button>

            </form>

            {% endif %}

        </div>

        {% endfor %}

    </div>


    <div class="footer">
        Auto refresh every 30 seconds<br><br>
        <strong>Powered by Dr. Abdulfatah Sulieman · Insurance Department</strong>
    </div>

</div>

</body>

</html>
"""


# =========================
# APP LANDING
# =========================

def landing_url():
    if session.get("role") == "doctor":
        return url_for("doctor_call_doctor")

    return url_for("modules")


# =========================
# ROUTES
# =========================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if "username" in session:
        return redirect(landing_url())

    error = False

    if request.method == "POST":

        username = (
            request.form.get("username", "")
            .strip()
            .lower()
        )

        password = request.form.get(
            "password", ""
        )

        conn = get_db()

        user = conn.execute("""
            SELECT username, password_hash, role, display_name
            FROM users
            WHERE username = ?
        """, (username,)).fetchone()

        conn.close()

        if user and check_password_hash(
            user["password_hash"],
            password
        ):

            session["username"] = username
            session["role"] = user["role"]
            session["display"] = user["display_name"]

            return redirect(landing_url())

        error = True

    return render_template_string(
        LOGIN_HTML,
        error=error,
        success_message=None
    )


@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


@app.route(
    "/change-password",
    methods=["GET", "POST"]
)
@login_required
def change_password():

    error_message = None
    username = session["username"]

    conn = get_db()

    user = conn.execute("""
        SELECT username, password_hash
        FROM users
        WHERE username = ?
    """, (username,)).fetchone()

    if user is None:
        conn.close()
        session.clear()
        return redirect(url_for("login"))

    if request.method == "POST":

        current_password = request.form.get(
            "current_password", ""
        )
        new_password = request.form.get(
            "new_password", ""
        )
        confirm_password = request.form.get(
            "confirm_password", ""
        )

        if not check_password_hash(
            user["password_hash"],
            current_password
        ):
            error_message = "Current password is incorrect."

        elif len(new_password) < 6:
            error_message = (
                "New password must contain at least 6 characters."
            )

        elif new_password != confirm_password:
            error_message = (
                "New password and confirmation do not match."
            )

        elif check_password_hash(
            user["password_hash"],
            new_password
        ):
            error_message = (
                "New password must be different from the current password."
            )

        else:
            conn.execute("""
                UPDATE users
                SET password_hash = ?,
                    updated_at = ?
                WHERE username = ?
            """, (
                generate_password_hash(new_password),
                now_text(),
                username
            ))

            add_log(
                conn,
                action="Password changed"
            )

            conn.commit()
            conn.close()
            session.clear()

            return render_template_string(
                LOGIN_HTML,
                error=False,
                success_message=(
                    "Password changed successfully. "
                    "Please sign in again."
                )
            )

    conn.close()

    return render_template_string(
        CHANGE_PASSWORD_HTML,
        error_message=error_message,
        display_name=session["display"]
    )


@app.route("/")
@login_required
def home():

    if session.get("role") == "doctor":
        return redirect(url_for("doctor_call_doctor"))

    conn = get_db()

    if session.get("role") == "admin":

        beds = conn.execute("""
            SELECT *
            FROM beds
            ORDER BY id
        """).fetchall()

    else:

        beds = conn.execute("""
            SELECT *
            FROM beds
            WHERE active = 1
            ORDER BY id
        """).fetchall()

    system = conn.execute("""
        SELECT *
        FROM system_status
        WHERE id = 1
    """).fetchone()

    conn.close()

    active_beds = [
        b for b in beds
        if b["active"] == 1
    ]

    total = len(active_beds)

    available = sum(
        b["status"] == "Available"
        for b in active_beds
    )

    occupied = sum(
        b["status"] == "Occupied"
        for b in active_beds
    )

    reserved = sum(
        b["status"] == "Reserved"
        for b in active_beds
    )

    cleaning = sum(
        b["status"] == "Cleaning"
        for b in active_beds
    )

    last_confirmation = datetime.strptime(
        system["last_icu_confirmation"],
        "%Y-%m-%d %H:%M:%S"
    )

    stale = (
        now_dt() - last_confirmation
        > timedelta(minutes=30)
    )

    return render_template_string(
        DASHBOARD_HTML,

        beds=beds,

        total=total,
        available=available,
        occupied=occupied,
        reserved=reserved,
        cleaning=cleaning,

        stale=stale,

        last_confirmation_display=
            last_confirmation.strftime(
                "%d/%m/%Y %I:%M %p"
            ),

        role=session["role"],
        display_name=session["display"]
    )

@app.route(
    "/update/<int:bed_id>",
    methods=["POST"]
)
@icu_required
def update_bed(bed_id):

    status = request.form.get(
        "status", ""
    )

    allowed = [
        "Available",
        "Occupied",
        "Reserved",
        "Cleaning"
    ]

    if status in allowed:

        conn = get_db()

        bed = conn.execute("""
            SELECT name, status, active
            FROM beds
            WHERE id = ?
        """, (
            bed_id,
        )).fetchone()

        if bed is not None and bed["active"] == 1:

            old_status = bed["status"]
            bed_name = bed["name"]
            update_time = now_text()

            conn.execute("""
                UPDATE beds
                SET status = ?,
                    last_updated = ?
                WHERE id = ?
                  AND active = 1
            """, (
                status,
                update_time,
                bed_id
            ))

            # Any valid ICU/Admin bed update also confirms that the
            # displayed ICU availability has just been reviewed.
            conn.execute("""
                UPDATE system_status
                SET last_icu_confirmation = ?
                WHERE id = 1
            """, (
                update_time,
            ))

            if old_status != status:
                add_log(
                    conn,
                    action="Bed status changed",
                    bed_name=bed_name,
                    old_value=old_status,
                    new_value=status
                )

            add_log(
                conn,
                action="ICU status confirmed via bed update",
                bed_name=bed_name,
                old_value=None,
                new_value="Confirmed"
            )

            conn.commit()

        conn.close()

    return redirect(url_for("home"))

@app.route(
    "/confirm",
    methods=["POST"]
)
@icu_required
def confirm_icu():

    conn = get_db()

    conn.execute("""
        UPDATE system_status
        SET last_icu_confirmation = ?
        WHERE id = 1
    """, (
        now_text(),
    ))

    add_log(
        conn,
        action="ICU status confirmed",
        bed_name=None,
        old_value=None,
        new_value="Confirmed"
    )

    conn.commit()
    conn.close()

    return redirect(url_for("home"))

# =========================
# ADMIN
# =========================

@app.route(
    "/admin/add-bed",
    methods=["POST"]
)
@login_required
def admin_add_bed():

    if session.get("role") != "admin":
        return redirect(url_for("home"))

    name = request.form.get(
        "name", ""
    ).strip()

    bed_type = request.form.get(
        "bed_type",
        "Regular ICU"
    ).strip()

    if bed_type not in (
        "Regular ICU",
        "Isolation"
    ):
        bed_type = "Regular ICU"

    if name:

        conn = get_db()

        try:

            conn.execute("""
                INSERT INTO beds
                (
                    name,
                    bed_type,
                    status,
                    last_updated,
                    active
                )
                VALUES (
                    ?,
                    ?,
                    'Available',
                    ?,
                    1
                )
            """, (
                name,
                bed_type,
                now_text()
            ))

            add_log(
                conn,
                action="Bed added",
                bed_name=name,
                old_value=None,
                new_value=bed_type
            )

            conn.commit()

        except DATABASE_INTEGRITY_ERRORS:
            pass

        finally:
            conn.close()

    return redirect(url_for("home"))

@app.route(
    "/admin/toggle-bed/<int:bed_id>",
    methods=["POST"]
)
@login_required
def admin_toggle_bed(bed_id):

    if session.get("role") != "admin":
        return redirect(url_for("home"))

    conn = get_db()

    bed = conn.execute("""
        SELECT name, active
        FROM beds
        WHERE id = ?
    """, (
        bed_id,
    )).fetchone()

    if bed is not None:

        old_active = bed["active"]
        bed_name = bed["name"]

        new_active = (
            0
            if old_active == 1
            else 1
        )

        conn.execute("""
            UPDATE beds
            SET active = ?,
                last_updated = ?
            WHERE id = ?
        """, (
            new_active,
            now_text(),
            bed_id
        ))

        if new_active == 0:
            action_name = "Bed disabled"
            old_value = "Active"
            new_value = "Disabled"
        else:
            action_name = "Bed reactivated"
            old_value = "Disabled"
            new_value = "Active"

        add_log(
            conn,
            action=action_name,
            bed_name=bed_name,
            old_value=old_value,
            new_value=new_value
        )

        conn.commit()

    conn.close()

    return redirect(url_for("home"))
# =========================
# ACTIVITY LOG PAGE
# =========================

ACTIVITY_LOG_HTML = """
<!DOCTYPE html>
<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>ACT BedFlow Activity Log</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f4f7fb;
    color: #172033;
}

.container {
    max-width: 1200px;
    margin: auto;
    padding: 25px;
}

.topbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 20px;
    margin-bottom: 25px;
}

h1 {
    margin: 0 0 5px 0;
}

.subtitle {
    color: #6b7280;
}

.back {
    display: inline-block;
    text-decoration: none;
    background: #172033;
    color: white;
    padding: 11px 16px;
    border-radius: 9px;
    font-weight: bold;
}

.card {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    overflow-x: auto;
}

table {
    width: 100%;
    border-collapse: collapse;
}

th,
td {
    padding: 14px;
    text-align: left;
    border-bottom: 1px solid #e5e7eb;
    white-space: nowrap;
}

th {
    background: #f7f8fa;
}

.empty {
    padding: 30px;
    text-align: center;
    color: #6b7280;
}

@media (max-width: 800px) {

    .topbar {
        display: block;
    }

    .back {
        margin-top: 15px;
    }
}

</style>

</head>

<body>

<div class="container">

    <div class="topbar">

        <div>

            <h1>
                Activity Log
            </h1>

            <div class="subtitle">
                ACT BedFlow — System Activity History
            </div>

        </div>

        <a class="back"
           href="/">
            ← Back to Dashboard
        </a>

    </div>


    <div class="card">

        {% if logs %}

        <table>

            <thead>

                <tr>
                    <th>Date & Time</th>
                    <th>User</th>
                    <th>Role</th>
                    <th>Action</th>
                    <th>Bed</th>
                    <th>Old Value</th>
                    <th>New Value</th>
                </tr>

            </thead>

            <tbody>

                {% for log in logs %}

                <tr>

                    <td>
                        {{ log['timestamp'] }}
                    </td>

                    <td>
                        {{ log['username'] }}
                    </td>

                    <td>
                        {{ log['role'] }}
                    </td>

                    <td>
                        {{ log['action'] }}
                    </td>

                    <td>
                        {{ log['bed_name'] or '-' }}
                    </td>

                    <td>
                        {{ log['old_value'] or '-' }}
                    </td>

                    <td>
                        {{ log['new_value'] or '-' }}
                    </td>

                </tr>

                {% endfor %}

            </tbody>

        </table>

        {% else %}

        <div class="empty">
            No activity recorded yet.
        </div>

        {% endif %}

    </div>

    <div style="text-align:center;color:#9aa0aa;font-size:12px;margin-top:24px;font-weight:700">
        Powered by Dr. Abdulfatah Sulieman · Insurance Department
    </div>

</div>

</body>

</html>
"""


@app.route("/activity-log")
@login_required
def activity_log():

    if session.get("role") != "admin":
        return redirect(url_for("home"))

    conn = get_db()

    logs = conn.execute("""
        SELECT *
        FROM activity_log
        ORDER BY id DESC
        LIMIT 200
    """).fetchall()

    conn.close()

    return render_template_string(
        ACTIVITY_LOG_HTML,
        logs=logs
    )
# =========================
# ACT MODULE HUB
# =========================

@app.route("/modules")
@login_required
def modules():
    role = session.get("role")
    show_bedflow = role in ("insurance", "icu", "admin")
    show_doctor_call = role in ("insurance", "doctor", "admin")

    conn = get_db()
    available_beds = conn.execute(
        "SELECT COUNT(*) AS c FROM beds WHERE active = 1 AND status = 'Available'"
    ).fetchone()["c"]

    if role == "doctor":
        pending_cases = conn.execute(
            "SELECT COUNT(*) AS c FROM doctor_cases WHERE doctor_username = ? AND status IN ('Sent','Opened')",
            (session.get("username"),)
        ).fetchone()["c"]
    else:
        pending_cases = conn.execute(
            "SELECT COUNT(*) AS c FROM doctor_cases WHERE status IN ('Sent','Opened')"
        ).fetchone()["c"]
    conn.close()

    return render_template_string(
        MODULES_HTML_V31,
        display_name=session.get("display", session.get("username")),
        role=role,
        show_bedflow=show_bedflow,
        show_doctor_call=show_doctor_call,
        available_beds=available_beds,
        pending_cases=pending_cases
    )


# =========================
# DOCTOR CALL DATABASE
# =========================

def init_doctor_call_db():
    conn = get_db()
    id_definition = (
        "BIGSERIAL PRIMARY KEY"
        if USE_POSTGRES
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS doctor_cases (
            id {id_definition},
            case_no TEXT NOT NULL,
            patient_ref TEXT,
            specialty TEXT NOT NULL,
            doctor_username TEXT NOT NULL,
            pdf_filename TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Sent',
            sent_at TEXT NOT NULL,
            opened_at TEXT,
            decided_at TEXT,
            decision TEXT
        )
    """)

    if USE_POSTGRES:
        conn.execute("ALTER TABLE doctor_cases ADD COLUMN IF NOT EXISTS pdf_original_name TEXT")
        conn.execute("ALTER TABLE doctor_cases ADD COLUMN IF NOT EXISTS pdf_data BYTEA")
    else:
        doctor_case_columns = [
            row["name"] for row in conn.execute("PRAGMA table_info(doctor_cases)").fetchall()
        ]
        if "pdf_original_name" not in doctor_case_columns:
            conn.execute("ALTER TABLE doctor_cases ADD COLUMN pdf_original_name TEXT")
        if "pdf_data" not in doctor_case_columns:
            conn.execute("ALTER TABLE doctor_cases ADD COLUMN pdf_data BLOB")

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id {id_definition},
            username TEXT NOT NULL,
            endpoint TEXT UNIQUE NOT NULL,
            subscription_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS doctor_profiles (
            username TEXT PRIMARY KEY,
            specialty TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
    """)

    # One-time password repair for the restored doctor accounts. This fixes
    # accounts that were created before ACT_DEFAULT_DOCTOR_PASSWORD was set.
    password_seed_key = "doctor_seed_password_v1_applied"
    password_seed_row = conn.execute(
        "SELECT setting_value FROM app_settings WHERE setting_key = ?",
        (password_seed_key,)
    ).fetchone()

    if not password_seed_row:
        doctor_password_hash = generate_password_hash(
            DEFAULT_DOCTOR_PASSWORD
        )
        for username, display_name, specialty in DEFAULT_DOCTORS:
            conn.execute("""
                UPDATE users
                SET password_hash = ?, updated_at = ?
                WHERE username = ?
                  AND role = 'doctor'
            """, (
                doctor_password_hash,
                now_text(),
                username
            ))

        conn.execute("""
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
        """, (
            password_seed_key,
            now_text()
        ))

    for username, display_name, specialty in DEFAULT_DOCTORS:
        profile_values = (
            username,
            specialty
        )

        if USE_POSTGRES:
            conn.execute("""
                INSERT INTO doctor_profiles (username, specialty, active)
                VALUES (?, ?, 1)
                ON CONFLICT (username) DO NOTHING
            """, profile_values)
        else:
            conn.execute("""
                INSERT OR IGNORE INTO doctor_profiles
                (username, specialty, active)
                VALUES (?, ?, 1)
            """, profile_values)

    # Keep any historical Demo Doctor record for old case history,
    # but remove it from active Smart Referral routing.
    conn.execute("""
        UPDATE doctor_profiles
        SET active = 0
        WHERE username = 'doctor'
    """)

    conn.commit()
    conn.close()


def setting_get(key):
    conn = get_db()
    row = conn.execute(
        "SELECT setting_value FROM app_settings WHERE setting_key = ?",
        (key,)
    ).fetchone()
    conn.close()
    return row["setting_value"] if row else None


def setting_set(key, value):
    conn = get_db()
    conn.execute("""
        INSERT INTO app_settings (setting_key, setting_value)
        VALUES (?, ?)
        ON CONFLICT (setting_key) DO UPDATE SET
            setting_value = excluded.setting_value
    """, (key, value))
    conn.commit()
    conn.close()


def ensure_vapid_keys():
    private_pem = setting_get("vapid_private_pem")
    public_b64 = setting_get("vapid_public_b64")

    if not private_pem or not public_b64:
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ).decode("utf-8")

        numbers = private_key.public_key().public_numbers()
        raw_public = (
            b"\x04"
            + numbers.x.to_bytes(32, "big")
            + numbers.y.to_bytes(32, "big")
        )
        public_b64 = base64.urlsafe_b64encode(
            raw_public
        ).rstrip(b"=").decode("ascii")

        setting_set("vapid_private_pem", private_pem)
        setting_set("vapid_public_b64", public_b64)

    VAPID_RUNTIME_PRIVATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    VAPID_RUNTIME_PRIVATE_FILE.write_text(private_pem, encoding="utf-8")
    return public_b64


def allowed_pdf(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


SMART_REFERRAL_RULES = {
    "Cardiology": {
        "aliases": [
            "cardiology", "cardiac", "cardiologist", "heart"
        ],
        "keywords": [
            "chest pain", "angina", "acute coronary syndrome", "acs",
            "stemi", "nstemi", "troponin", "elevated troponin",
            "myocardial infarction", "ischemic heart disease",
            "coronary artery disease", "cad", "heart failure",
            "congestive heart failure", "chf", "cardiomyopathy",
            "arrhythmia", "atrial fibrillation", "afib", "ecg", "ekg",
            "bradycardia", "tachycardia", "palpitations", "pericarditis"
        ]
    },
    "Pulmonology": {
        "aliases": [
            "pulmonology", "pulmonary", "respiratory",
            "chest medicine", "pulmonologist"
        ],
        "keywords": [
            "shortness of breath", "sob", "dyspnea", "hypoxia",
            "hypoxemia", "spo2", "oxygen saturation", "pneumonia",
            "asthma", "copd", "bronchiectasis", "pleural effusion",
            "hemoptysis", "lung mass", "lung lesion", "pulmonary embolism",
            "interstitial lung disease", "ild", "bronchitis",
            "respiratory distress", "pulmonary edema"
        ]
    },
    "Neurology": {
        "aliases": [
            "neurology", "neurologist", "neuro"
        ],
        "keywords": [
            "stroke", "cva", "tia", "ischemic stroke", "seizure",
            "epilepsy", "hemiparesis", "hemiplegia", "facial droop",
            "facial weakness", "aphasia", "dysarthria",
            "multiple sclerosis", "neuropathy", "migraine",
            "status epilepticus", "encephalopathy"
        ]
    },
    "Neurosurgery": {
        "aliases": [
            "neurosurgery", "neurosurgeon", "neuro surgery"
        ],
        "keywords": [
            "subdural hematoma", "epidural hematoma",
            "intracranial hemorrhage", "intracerebral hemorrhage",
            "subarachnoid hemorrhage", "sah", "brain tumor",
            "brain mass", "hydrocephalus", "spinal cord compression",
            "cauda equina", "skull fracture", "neurosurgical"
        ]
    },
    "General Surgery": {
        "aliases": [
            "general surgery", "general surgeon"
        ],
        "keywords": [
            "appendicitis", "cholecystitis", "bowel obstruction",
            "intestinal obstruction", "small bowel obstruction",
            "large bowel obstruction", "perforation", "acute abdomen",
            "peritonitis", "incarcerated hernia", "strangulated hernia",
            "gallbladder", "surgical abdomen", "abdominal abscess"
        ]
    },
    "Orthopedics": {
        "aliases": [
            "orthopedics", "orthopaedics", "orthopedic",
            "orthopaedic", "ortho"
        ],
        "keywords": [
            "fracture", "dislocation", "femur", "tibia", "fibula",
            "radius", "ulna", "humerus", "hip fracture", "pelvic fracture",
            "joint injury", "ligament tear", "tendon rupture",
            "orthopedic", "orthopaedic", "bone injury"
        ]
    },
    "Gastroenterology": {
        "aliases": [
            "gastroenterology", "gastroenterologist", "gastro", "gi"
        ],
        "keywords": [
            "hematemesis", "melena", "gi bleed", "gastrointestinal bleed",
            "upper gi bleed", "lower gi bleed", "pancreatitis",
            "cirrhosis", "hepatitis", "endoscopy", "colitis",
            "crohn", "ulcerative colitis", "liver disease",
            "jaundice", "esophageal varices", "ascites"
        ]
    },
    "Nephrology": {
        "aliases": [
            "nephrology", "nephrologist", "renal medicine", "kidney"
        ],
        "keywords": [
            "acute kidney injury", "aki", "chronic kidney disease", "ckd",
            "end stage renal disease", "esrd", "renal failure",
            "renal impairment", "dialysis", "hemodialysis",
            "haemodialysis", "nephrotic", "nephritic", "uremia",
            "high creatinine", "elevated creatinine", "hyperkalemia"
        ]
    },
    "Urology": {
        "aliases": [
            "urology", "urologist", "uro"
        ],
        "keywords": [
            "urinary retention", "ureteric stone", "ureteral stone",
            "renal stone", "kidney stone", "hydronephrosis", "hematuria",
            "bph", "prostate", "testicular torsion", "testicular",
            "bladder", "ureter", "urolithiasis"
        ]
    },
    "ENT": {
        "aliases": [
            "ent", "otolaryngology", "ear nose throat"
        ],
        "keywords": [
            "epistaxis", "tonsillitis", "otitis", "mastoiditis",
            "ear discharge", "hearing loss", "sinusitis", "nasal",
            "larynx", "laryngeal", "pharynx", "foreign body ear",
            "foreign body nose"
        ]
    },
    "OB/GYN": {
        "aliases": [
            "ob/gyn", "obgyn", "obstetrics", "gynecology",
            "gynaecology", "obstetrician", "gynecologist"
        ],
        "keywords": [
            "pregnancy", "pregnant", "labor", "labour", "cesarean",
            "caesarean", "preeclampsia", "eclampsia", "ectopic pregnancy",
            "vaginal bleeding", "postpartum", "placenta", "fetal",
            "ovarian", "uterus", "uterine", "cervical pregnancy"
        ]
    },
    "Pediatrics": {
        "aliases": [
            "pediatrics", "paediatrics", "pediatric",
            "paediatric", "pediatrician"
        ],
        "keywords": [
            "pediatric", "paediatric", "child", "infant", "baby",
            "years old child", "months old"
        ]
    },
    "Neonatology": {
        "aliases": [
            "neonatology", "neonatologist", "nicu", "neonatal"
        ],
        "keywords": [
            "neonate", "neonatal", "newborn", "prematurity", "preterm",
            "nicu", "respiratory distress syndrome", "rds",
            "neonatal jaundice", "meconium aspiration",
            "birth asphyxia", "low birth weight", "very low birth weight",
            "birth weight"
        ]
    },
    "ICU/Critical Care": {
        "aliases": [
            "icu", "critical care", "intensive care", "intensivist"
        ],
        "keywords": [
            "septic shock", "cardiogenic shock", "hypovolemic shock",
            "shock", "vasopressor", "norepinephrine", "noradrenaline",
            "mechanical ventilation", "ventilated", "intubated",
            "intubation", "respiratory failure", "cardiac arrest",
            "multi organ failure", "multiple organ failure",
            "critical care", "icu admission", "unstable hemodynamics"
        ]
    },
    "Internal Medicine": {
        "aliases": [
            "internal medicine", "internist", "general medicine"
        ],
        "keywords": [
            "hyponatremia", "hypernatremia", "electrolyte imbalance",
            "sepsis", "fever", "infection", "anemia", "anaemia",
            "hypertension", "medical management", "general medical"
        ]
    },
    "Endocrinology": {
        "aliases": [
            "endocrinology", "endocrine", "endocrinologist"
        ],
        "keywords": [
            "diabetes mellitus", "diabetic ketoacidosis", "dka",
            "hyperglycemia", "hypoglycemia", "thyroid", "thyrotoxicosis",
            "hypothyroidism", "hyperthyroidism", "adrenal insufficiency",
            "pituitary"
        ]
    },
    "Hematology": {
        "aliases": [
            "hematology", "haematology", "hematologist", "haematologist"
        ],
        "keywords": [
            "severe anemia", "severe anaemia", "thrombocytopenia",
            "pancytopenia", "hemolysis", "haemolysis", "sickle cell",
            "thalassemia", "coagulopathy", "bleeding disorder",
            "neutropenia"
        ]
    },
    "Oncology": {
        "aliases": [
            "oncology", "oncologist", "cancer"
        ],
        "keywords": [
            "malignancy", "metastasis", "metastatic", "chemotherapy",
            "radiotherapy", "cancer", "carcinoma", "lymphoma",
            "leukemia", "leukaemia", "tumor", "tumour"
        ]
    },
    "Infectious Disease": {
        "aliases": [
            "infectious disease", "infectious diseases",
            "infectious disease specialist"
        ],
        "keywords": [
            "bacteremia", "bacteraemia", "fungemia", "fungemia",
            "meningitis", "endocarditis", "tuberculosis", "tb",
            "hiv", "antimicrobial", "infectious disease consultation"
        ]
    },
    "Rheumatology": {
        "aliases": [
            "rheumatology", "rheumatologist"
        ],
        "keywords": [
            "rheumatoid arthritis", "systemic lupus", "sle",
            "vasculitis", "ankylosing spondylitis", "autoimmune",
            "connective tissue disease", "gout flare"
        ]
    },
    "Dermatology": {
        "aliases": [
            "dermatology", "dermatologist", "skin"
        ],
        "keywords": [
            "skin rash", "dermatitis", "eczema", "psoriasis",
            "urticaria", "cellulitis", "skin lesion", "bullous",
            "pemphigus"
        ]
    },
    "Ophthalmology": {
        "aliases": [
            "ophthalmology", "ophthalmologist", "eye"
        ],
        "keywords": [
            "vision loss", "visual loss", "retinal", "retina",
            "glaucoma", "cataract", "eye pain", "corneal",
            "ophthalmic", "ocular"
        ]
    }
}


def normalize_text(value):
    return " ".join((value or "").lower().replace("\n", " ").split())


def merge_uploaded_pdfs(files):
    payloads = []

    for uploaded in files:
        if not uploaded or not uploaded.filename:
            continue

        if not allowed_pdf(uploaded.filename):
            raise ValueError("Only PDF files are allowed.")

        raw = uploaded.read()
        if not raw:
            continue

        safe_name = secure_filename(
            uploaded.filename
        ) or "attachment.pdf"

        try:
            reader = PdfReader(io.BytesIO(raw))
            page_count = len(reader.pages)
        except Exception as exc:
            raise ValueError(
                f"Could not read PDF: {safe_name}"
            ) from exc

        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise ValueError(
                    f"Encrypted PDF is not supported: {safe_name}"
                ) from exc

        if page_count < 1:
            continue

        payloads.append({
            "name": safe_name,
            "bytes": raw,
            "pages": page_count
        })

    if not payloads:
        raise ValueError("No readable PDF pages were uploaded.")

    # Most referrals contain one PDF. Keep its original bytes unchanged
    # instead of rewriting every page on the small Render instance.
    if len(payloads) == 1:
        item = payloads[0]
        return {
            "pdf_bytes": item["bytes"],
            "page_count": item["pages"],
            "file_count": 1,
            "filename": item["name"]
        }

    writer = PdfWriter()
    total_pages = 0

    for item in payloads:
        reader = PdfReader(io.BytesIO(item["bytes"]))
        for page in reader.pages:
            writer.add_page(page)
            total_pages += 1

    output = io.BytesIO()
    writer.write(output)

    return {
        "pdf_bytes": output.getvalue(),
        "page_count": total_pages,
        "file_count": len(payloads),
        "filename": "ACT_Smart_Referral_Combined.pdf"
    }


ROUTING_WEAK_TERMS = {
    "ecg", "ekg", "troponin", "spo2", "oxygen saturation",
    "creatinine", "fever", "infection", "hypertension",
    "tachycardia", "bradycardia", "shortness of breath", "sob",
    "dyspnea", "pain", "weakness", "dizziness", "vomiting"
}

ROUTING_STRONG_ABBREVIATIONS = {
    "acs", "stemi", "nstemi", "afib", "cva", "tia", "sah",
    "aki", "ckd", "esrd", "dka", "copd", "ild", "bph", "sle"
}

TRIAGE_PRIORITY_HEADINGS = [
    "final diagnosis",
    "principal diagnosis",
    "primary diagnosis",
    "discharge diagnosis",
    "provisional diagnosis",
    "working diagnosis",
    "diagnosis",
    "clinical impression",
    "impression",
    "assessment",
    "reason for referral",
    "reason for consultation",
    "reason for admission",
    "plan"
]

TRIAGE_NEGATION_CUES = [
    "no evidence of",
    "negative for",
    "denies",
    "denied",
    "without",
    "ruled out",
    "rule out",
    "unlikely",
    "not suggestive of",
    "not consistent with"
]

TRIAGE_HISTORY_CUES = [
    "history of",
    "previous history of",
    "past history of",
    "known case of",
    "remote history of"
]

# Diagnoses / clinical states that should dominate generic symptoms, labs,
# comorbidities and screening tests. These anchors are intentionally
# conservative: automatic sending requires a real clinical anchor, not just
# a vague symptom or an isolated investigation.
TRIAGE_ANCHORS = {
    "Cardiology": {
        "stemi": 18,
        "nstemi": 18,
        "acute coronary syndrome": 17,
        "myocardial infarction": 17,
        "acute myocardial infarction": 18,
        "unstable angina": 14,
        "cardiogenic shock": 12,
        "acute heart failure": 13,
        "decompensated heart failure": 13,
        "heart failure": 10,
        "atrial fibrillation": 10,
        "ventricular tachycardia": 14,
        "complete heart block": 14,
        "symptomatic bradycardia": 10
    },
    "Pulmonology": {
        "copd exacerbation": 15,
        "acute exacerbation of copd": 16,
        "asthma exacerbation": 14,
        "status asthmaticus": 17,
        "pulmonary embolism": 15,
        "massive pulmonary embolism": 17,
        "hemoptysis": 11,
        "pleural effusion": 10,
        "interstitial lung disease": 13,
        "bronchiectasis": 11,
        "lung mass": 11,
        "lung lesion": 9
    },
    "Neurology": {
        "acute ischemic stroke": 20,
        "ischemic stroke": 18,
        "acute stroke": 18,
        "stroke": 15,
        "cva": 15,
        "transient ischemic attack": 15,
        "tia": 14,
        "status epilepticus": 18,
        "seizure": 12,
        "epilepsy": 10,
        "hemiparesis": 13,
        "hemiplegia": 13,
        "aphasia": 13,
        "facial droop": 11,
        "multiple sclerosis": 12,
        "encephalopathy": 9
    },
    "Neurosurgery": {
        "subdural hematoma": 20,
        "epidural hematoma": 20,
        "intracranial hemorrhage": 18,
        "intracerebral hemorrhage": 18,
        "subarachnoid hemorrhage": 20,
        "sah": 16,
        "brain tumor": 14,
        "brain mass": 14,
        "hydrocephalus": 14,
        "spinal cord compression": 17,
        "cauda equina": 18,
        "skull fracture": 14
    },
    "General Surgery": {
        "acute appendicitis": 18,
        "appendicitis": 16,
        "acute cholecystitis": 18,
        "cholecystitis": 16,
        "small bowel obstruction": 18,
        "large bowel obstruction": 18,
        "bowel obstruction": 17,
        "intestinal obstruction": 17,
        "perforated viscus": 20,
        "bowel perforation": 20,
        "peritonitis": 16,
        "acute abdomen": 15,
        "strangulated hernia": 18,
        "incarcerated hernia": 16,
        "abdominal abscess": 12
    },
    "Orthopedics": {
        "hip fracture": 18,
        "femur fracture": 18,
        "pelvic fracture": 18,
        "tibia fracture": 17,
        "humerus fracture": 17,
        "radius fracture": 16,
        "ulna fracture": 16,
        "fracture": 12,
        "dislocation": 13,
        "tendon rupture": 12,
        "ligament tear": 10
    },
    "Gastroenterology": {
        "upper gi bleed": 17,
        "lower gi bleed": 16,
        "gastrointestinal bleed": 16,
        "hematemesis": 16,
        "melena": 15,
        "acute pancreatitis": 16,
        "pancreatitis": 14,
        "esophageal varices": 15,
        "variceal bleeding": 17,
        "decompensated cirrhosis": 14,
        "ulcerative colitis": 12,
        "crohn disease": 12
    },
    "Nephrology": {
        "acute kidney injury": 17,
        "aki": 14,
        "chronic kidney disease": 14,
        "ckd": 12,
        "end stage renal disease": 17,
        "esrd": 14,
        "renal failure": 14,
        "hemodialysis": 13,
        "haemodialysis": 13,
        "nephrotic syndrome": 15,
        "nephritic syndrome": 15,
        "uremia": 13
    },
    "Urology": {
        "acute urinary retention": 16,
        "urinary retention": 14,
        "ureteric stone": 15,
        "ureteral stone": 15,
        "renal stone": 13,
        "kidney stone": 13,
        "hydronephrosis": 13,
        "testicular torsion": 20,
        "gross hematuria": 13,
        "urolithiasis": 14,
        "bph": 10
    },
    "ENT": {
        "severe epistaxis": 16,
        "epistaxis": 13,
        "mastoiditis": 15,
        "peritonsillar abscess": 16,
        "tonsillitis": 10,
        "otitis media": 10,
        "otitis externa": 10,
        "ear discharge": 9,
        "hearing loss": 9,
        "foreign body ear": 14,
        "foreign body nose": 14
    },
    "OB/GYN": {
        "ectopic pregnancy": 20,
        "preeclampsia": 18,
        "eclampsia": 20,
        "placental abruption": 20,
        "placenta previa": 18,
        "postpartum hemorrhage": 20,
        "obstructed labor": 18,
        "preterm labor": 16,
        "labor pain": 12,
        "pregnancy with vaginal bleeding": 17,
        "cesarean complication": 15,
        "caesarean complication": 15
    },
    "Neonatology": {
        "meconium aspiration": 20,
        "neonatal respiratory distress": 19,
        "respiratory distress syndrome": 18,
        "prematurity": 16,
        "preterm newborn": 18,
        "neonatal jaundice": 14,
        "birth asphyxia": 18,
        "low birth weight": 14,
        "very low birth weight": 16,
        "nicu": 15,
        "newborn": 10,
        "neonate": 12
    },
    "ICU/Critical Care": {
        "septic shock": 22,
        "cardiogenic shock": 21,
        "hypovolemic shock": 21,
        "cardiac arrest": 22,
        "mechanical ventilation": 20,
        "mechanically ventilated": 20,
        "intubated": 19,
        "on ventilator": 19,
        "vasopressor": 18,
        "norepinephrine": 17,
        "noradrenaline": 17,
        "multi organ failure": 22,
        "multiple organ failure": 22,
        "unstable hemodynamics": 18,
        "icu admission": 18,
        "admitted to icu": 18,
        "critical care": 15
    },
    "Internal Medicine": {
        "sepsis": 10,
        "severe hyponatremia": 12,
        "hyponatremia": 9,
        "severe hypernatremia": 12,
        "hypernatremia": 9,
        "electrolyte imbalance": 8,
        "medical management": 7
    },
    "Endocrinology": {
        "diabetic ketoacidosis": 18,
        "dka": 16,
        "thyroid storm": 18,
        "myxedema coma": 18,
        "adrenal crisis": 18,
        "severe hypoglycemia": 12,
        "thyrotoxicosis": 12
    },
    "Hematology": {
        "sickle cell crisis": 17,
        "pancytopenia": 14,
        "severe thrombocytopenia": 14,
        "hemolytic anemia": 14,
        "haemolytic anaemia": 14,
        "thalassemia": 12,
        "coagulopathy": 11
    },
    "Oncology": {
        "metastatic cancer": 16,
        "metastatic carcinoma": 16,
        "malignancy": 11,
        "chemotherapy": 10,
        "radiotherapy": 10,
        "lymphoma": 13,
        "leukemia": 13,
        "leukaemia": 13
    },
    "Infectious Disease": {
        "infective endocarditis": 16,
        "bacteremia": 12,
        "bacteraemia": 12,
        "fungemia": 13,
        "tuberculosis": 14,
        "hiv": 10
    },
    "Rheumatology": {
        "systemic lupus": 14,
        "sle": 12,
        "vasculitis": 14,
        "rheumatoid arthritis": 12,
        "ankylosing spondylitis": 12
    },
    "Dermatology": {
        "stevens johnson syndrome": 18,
        "toxic epidermal necrolysis": 20,
        "pemphigus": 13,
        "psoriasis": 10,
        "eczema": 8
    },
    "Ophthalmology": {
        "acute vision loss": 18,
        "vision loss": 15,
        "retinal detachment": 20,
        "acute angle closure glaucoma": 20,
        "glaucoma": 12,
        "corneal ulcer": 16,
        "eye trauma": 14
    }
}


def normalize_text(value):
    return " ".join((value or "").lower().replace("\n", " ").split())


def keyword_in_text(text, keyword):
    keyword = normalize_text(keyword)
    if not keyword:
        return False

    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def clinical_keyword_status(text, keyword):
    """Return current, historical, negated, or missing for a clinical term."""
    keyword = normalize_text(keyword)
    if not keyword:
        return "missing"

    pattern = re.compile(
        r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    )
    historical_found = False
    negated_found = False

    for match in pattern.finditer(text):
        before = text[max(0, match.start() - 90):match.start()]
        after = text[match.end():min(len(text), match.end() + 40)]

        # Only treat a cue as applying to this diagnosis when it is close to
        # the matched term. This avoids "history of hypertension ... stroke"
        # incorrectly turning the current stroke into a historical diagnosis.
        negation_pattern = (
            r"(?:(?:no|not)\s+"
            r"(?:(?!but\b|however\b)[a-z0-9-]+\s+){0,3}|"
            r"(?:no evidence of|negative for|denies|denied|without|"
            r"rule out|ruled out|unlikely|not suggestive of|"
            r"not consistent with)\s+(?:[a-z0-9-]+\s+){0,4})$"
        )
        history_pattern = (
            r"(?:history of|previous history of|past history of|"
            r"known case of|remote history of)\s+"
            r"(?:[a-z0-9-]+\s+){0,4}$"
        )

        if re.search(negation_pattern, before[-70:]):
            negated_found = True
            continue

        if re.match(
            r"^\s*(?:ruled out|excluded|unlikely|not confirmed)",
            after
        ):
            negated_found = True
            continue

        if re.search(history_pattern, before[-85:]):
            historical_found = True
            continue

        return "current"

    if historical_found:
        return "historical"
    if negated_found:
        return "negated"
    return "missing"


def extract_priority_context(raw_text):
    text = normalize_text(raw_text)
    chunks = []

    for heading in TRIAGE_PRIORITY_HEADINGS:
        start = 0
        while True:
            index = text.find(heading, start)
            if index < 0:
                break

            left = max(0, index - 120)
            right = min(len(text), index + len(heading) + 700)
            chunks.append(text[left:right])
            start = index + len(heading)

    return " ".join(chunks)


def specialty_rule_score(text, rule):
    """Low-weight supporting evidence only; never enough by itself to auto-send."""
    score = 0
    matches = []
    strong_matches = []

    for keyword in rule["keywords"]:
        status = clinical_keyword_status(text, keyword)
        if status not in ("current", "historical"):
            continue

        normalized = normalize_text(keyword)
        words = len(normalized.split())

        if normalized in ROUTING_WEAK_TERMS:
            weight = 0.5
        elif normalized in ROUTING_STRONG_ABBREVIATIONS:
            weight = 2
        elif words >= 3:
            weight = 2
        elif words == 2:
            weight = 1.5
        else:
            weight = 1

        if status == "historical":
            weight *= 0.35

        score += weight
        matches.append(keyword)

        if status == "current" and weight >= 2:
            strong_matches.append(keyword)

    return score, matches, strong_matches


def specialty_anchor_score(text, priority_text, specialty):
    anchors = TRIAGE_ANCHORS.get(specialty, {})
    score = 0
    matches = []
    current_matches = []

    for keyword, base_weight in anchors.items():
        status = clinical_keyword_status(text, keyword)
        if status not in ("current", "historical"):
            continue

        weight = float(base_weight)

        if status == "historical":
            weight *= 0.30
        else:
            current_matches.append(keyword)

        if priority_text and clinical_keyword_status(
            priority_text,
            keyword
        ) == "current":
            weight += 5

        score += weight
        matches.append(keyword)

    return score, matches, current_matches


def explicit_specialty_score(text, priority_text, specialty, rule):
    aliases = [specialty] + list(rule.get("aliases", []))
    best = 0
    evidence = []

    for alias in aliases:
        normalized_alias = normalize_text(alias)
        if len(normalized_alias) < 3:
            continue

        escaped = re.escape(normalized_alias)
        strong_patterns = [
            rf"(?:refer(?:red|ral)?|consult(?:ation)?(?: requested)?)"
            rf"\s+(?:by|to|with|for)?\s*.{{0,30}}{escaped}",
            rf"{escaped}\s*.{{0,30}}(?:consult(?:ation)? requested|referral)"
        ]
        review_patterns = [
            rf"(?:for|request(?:ed)?)\s+.{{0,20}}{escaped}"
            rf"\s+(?:review|opinion)",
            rf"{escaped}\s+(?:review|opinion)\s+requested"
        ]

        if any(re.search(pattern, text) for pattern in strong_patterns):
            best = max(best, 18)
            evidence.append(alias)
        elif any(re.search(pattern, text) for pattern in review_patterns):
            best = max(best, 14)
            evidence.append(alias)

        if priority_text and keyword_in_text(priority_text, normalized_alias):
            best = max(best, 12)
            evidence.append(alias)

    return best, list(dict.fromkeys(evidence))


def doctor_matches_specialty(doctor_specialty, canonical_specialty):
    normalized = normalize_text(doctor_specialty)
    rule = SMART_REFERRAL_RULES.get(canonical_specialty, {})
    aliases = rule.get("aliases", [])

    if normalized == normalize_text(canonical_specialty):
        return True

    for alias in aliases:
        alias_normalized = normalize_text(alias)

        if normalized == alias_normalized:
            return True

        if keyword_in_text(normalized, alias_normalized):
            return True

    return False


def select_least_busy_doctor(doctors):
    ordered = sorted(
        doctors,
        key=lambda d: (
            int(d["pending_count"] or 0),
            (d["display_name"] or d["username"]).lower()
        )
    )
    return ordered[0] if ordered else None


def detect_specialty_and_doctor(report_text, doctors):
    text = normalize_text(report_text)

    if len(text) < 80:
        return {
            "ok": False,
            "reason": (
                "The report does not contain enough readable clinical text "
                "for safe automatic triage."
            )
        }

    priority_text = extract_priority_context(report_text)
    active_doctors = list(doctors)
    scored = []

    # Critical-care override is intentionally narrow. It only triggers on
    # explicit current ICU-level states, not just hypoxia, infection or a
    # single abnormal vital sign.
    icu_score, icu_matches, icu_current = specialty_anchor_score(
        text,
        priority_text,
        "ICU/Critical Care"
    )

    if icu_score >= 18 and icu_current:
        candidates = [
            d for d in active_doctors
            if doctor_matches_specialty(
                d["specialty"],
                "ICU/Critical Care"
            )
        ]

        if not candidates:
            return {
                "ok": False,
                "reason": (
                    "ACT detected a current ICU/Critical Care indication "
                    f"({', '.join(icu_current[:4])}), but there is no active "
                    "ICU/Critical Care doctor."
                ),
                "detected_specialty": "ICU/Critical Care",
                "matches": icu_current[:8]
            }

        selected = select_least_busy_doctor(candidates)
        return {
            "ok": True,
            "specialty": selected["specialty"],
            "canonical_specialty": "ICU/Critical Care",
            "doctor_username": selected["username"],
            "doctor_display": selected["display_name"],
            "pending_count": selected["pending_count"],
            "matches": icu_current[:8],
            "score": round(icu_score, 1),
            "confidence": "High",
            "triage_basis": "critical-care clinical anchor"
        }

    for specialty, rule in SMART_REFERRAL_RULES.items():
        anchor_score, anchor_matches, current_anchors = specialty_anchor_score(
            text,
            priority_text,
            specialty
        )
        supporting_score, support_matches, _ = specialty_rule_score(
            text,
            rule
        )
        explicit_score, explicit_matches = explicit_specialty_score(
            text,
            priority_text,
            specialty,
            rule
        )

        total = anchor_score + supporting_score + explicit_score

        if total <= 0:
            continue

        scored.append({
            "score": total,
            "specialty": specialty,
            "anchor_score": anchor_score,
            "anchor_matches": anchor_matches,
            "current_anchors": current_anchors,
            "support_matches": support_matches,
            "explicit_score": explicit_score,
            "explicit_matches": explicit_matches
        })

    # Patient-group context is used only as a conservative fallback.
    # A clear organ-specific diagnosis can still outrank Pediatrics / OB-GYN.
    pediatric_age = False
    neonatal_age = False

    for match in re.finditer(
        r"\b(\d{1,2})\s*(day|days|month|months|year|years|yr|yrs)"
        r"(?:\s*[- ]?\s*old)?\b",
        text
    ):
        try:
            age_value = int(match.group(1))
        except (TypeError, ValueError):
            continue

        unit = match.group(2)
        if unit in ("day", "days") and age_value <= 28:
            neonatal_age = True
        elif unit in ("month", "months") and age_value <= 216:
            pediatric_age = True
        elif unit in ("year", "years", "yr", "yrs") and age_value < 18:
            pediatric_age = True

    if neonatal_age:
        scored.append({
            "score": 16,
            "specialty": "Neonatology",
            "anchor_score": 16,
            "anchor_matches": ["neonatal age"],
            "current_anchors": ["neonatal age"],
            "support_matches": [],
            "explicit_score": 0,
            "explicit_matches": []
        })
    elif pediatric_age:
        scored.append({
            "score": 12,
            "specialty": "Pediatrics",
            "anchor_score": 12,
            "anchor_matches": ["pediatric age"],
            "current_anchors": ["pediatric age"],
            "support_matches": [],
            "explicit_score": 0,
            "explicit_matches": []
        })

    pregnancy_status = (
        clinical_keyword_status(text, "pregnant") == "current"
        or clinical_keyword_status(text, "pregnancy") == "current"
    )
    if pregnancy_status:
        scored.append({
            "score": 8,
            "specialty": "OB/GYN",
            "anchor_score": 8,
            "anchor_matches": ["current pregnancy"],
            "current_anchors": ["current pregnancy"],
            "support_matches": [],
            "explicit_score": 0,
            "explicit_matches": []
        })

    # Exact custom specialty wording entered by Admin is useful evidence,
    # but only when it appears in a priority clinical section.
    for d in active_doctors:
        actual_specialty = normalize_text(d["specialty"])
        if len(actual_specialty) < 4:
            continue

        if priority_text and keyword_in_text(priority_text, actual_specialty):
            existing = next(
                (
                    item for item in scored
                    if normalize_text(item["specialty"]) == actual_specialty
                ),
                None
            )

            if existing:
                existing["score"] += 10
                existing["explicit_score"] += 10
                existing["explicit_matches"].append(d["specialty"])
            else:
                scored.append({
                    "score": 10,
                    "specialty": d["specialty"],
                    "anchor_score": 0,
                    "anchor_matches": [],
                    "current_anchors": [],
                    "support_matches": [],
                    "explicit_score": 10,
                    "explicit_matches": [d["specialty"]]
                })

    # Merge duplicate evidence rows (for example a pediatric age row plus
    # ordinary Pediatrics keyword evidence) before ranking.
    merged_by_specialty = {}
    for item in scored:
        key = normalize_text(item["specialty"])
        existing = merged_by_specialty.get(key)

        if not existing:
            merged_by_specialty[key] = item
            continue

        existing["score"] += item["score"]
        existing["anchor_score"] += item["anchor_score"]
        existing["explicit_score"] += item["explicit_score"]

        for field in (
            "anchor_matches",
            "current_anchors",
            "support_matches",
            "explicit_matches"
        ):
            existing[field] = list(dict.fromkeys(
                existing[field] + item[field]
            ))

    scored = list(merged_by_specialty.values())

    # Internal Medicine is deliberately a fallback. It must not beat a
    # specific specialty when that specialty has a current diagnostic anchor.
    specific_anchor_exists = any(
        item["specialty"] != "Internal Medicine"
        and item["anchor_score"] >= 10
        and item["current_anchors"]
        for item in scored
    )

    if specific_anchor_exists:
        for item in scored:
            if item["specialty"] == "Internal Medicine":
                item["score"] *= 0.45

    scored.sort(
        key=lambda item: (
            item["score"],
            item["anchor_score"],
            item["explicit_score"],
            len(item["current_anchors"])
        ),
        reverse=True
    )

    if not scored:
        return {
            "ok": False,
            "reason": (
                "ACT read the report but could not identify a safe "
                "specialty from the clinical diagnosis."
            )
        }

    best = scored[0]
    second = scored[1] if len(scored) > 1 else None
    margin = (
        best["score"] - second["score"]
        if second
        else best["score"]
    )

    # Pediatric / neonatal wording should not automatically override a clear
    # organ-specific diagnosis. It is used as a fallback only if there is no
    # stronger specialty anchor.
    if best["specialty"] == "Pediatrics":
        stronger_specific = next(
            (
                item for item in scored[1:]
                if item["anchor_score"] >= 12
                and item["current_anchors"]
                and item["specialty"] not in (
                    "Pediatrics",
                    "Internal Medicine"
                )
            ),
            None
        )
        if stronger_specific:
            best = stronger_specific
            second = scored[0]
            margin = best["score"] - second["score"]

    # Never auto-send from generic symptoms/labs alone. A safe auto-route
    # needs a current diagnostic anchor or explicit referral evidence.
    has_diagnostic_anchor = (
        bool(best["current_anchors"])
        and best["anchor_score"] >= 9
    )
    has_explicit_referral = best["explicit_score"] >= 12

    if not has_diagnostic_anchor and not has_explicit_referral:
        evidence = (
            best["support_matches"][:4]
            or best["anchor_matches"][:4]
        )
        return {
            "ok": False,
            "reason": (
                "ACT found only nonspecific findings"
                + (
                    f" ({', '.join(evidence)})"
                    if evidence
                    else ""
                )
                + " and will not auto-send to avoid a wrong specialty."
            )
        }

    # A close second specialty means the case is clinically ambiguous.
    if second and second["score"] >= 9 and margin < 4:
        return {
            "ok": False,
            "reason": (
                "Clinical triage is ambiguous between "
                f"{best['specialty']} and {second['specialty']}. "
                "Choose manually rather than risk a wrong referral."
            )
        }

    if best["score"] >= 18 and margin >= 6:
        confidence = "High"
    elif best["score"] >= 13 and margin >= 4:
        confidence = "Good"
    else:
        return {
            "ok": False,
            "reason": (
                f"ACT suspects {best['specialty']} but confidence is not "
                "high enough for automatic sending. Choose manually."
            ),
            "detected_specialty": best["specialty"]
        }

    candidates = [
        d for d in active_doctors
        if doctor_matches_specialty(d["specialty"], best["specialty"])
    ]

    evidence = (
        best["current_anchors"]
        + best["explicit_matches"]
        + best["support_matches"]
    )
    evidence = list(dict.fromkeys(evidence))[:8]

    if not candidates:
        return {
            "ok": False,
            "reason": (
                f"ACT detected {best['specialty']} with {confidence.lower()} "
                "confidence, but there is no active doctor in that specialty."
            ),
            "detected_specialty": best["specialty"],
            "matches": evidence
        }

    selected = select_least_busy_doctor(candidates)

    return {
        "ok": True,
        "specialty": selected["specialty"],
        "canonical_specialty": best["specialty"],
        "doctor_username": selected["username"],
        "doctor_display": selected["display_name"],
        "pending_count": selected["pending_count"],
        "matches": evidence,
        "score": round(best["score"], 1),
        "confidence": confidence,
        "triage_basis": "diagnosis / impression clinical anchors"
    }


def send_push_payload(username, payload):
    ensure_vapid_keys()
    conn = get_db()
    subscriptions = conn.execute("""
        SELECT endpoint, subscription_json
        FROM push_subscriptions
        WHERE username = ?
    """, (username,)).fetchall()
    conn.close()

    dead_endpoints = []
    sent = 0

    for row in subscriptions:
        try:
            webpush(
                subscription_info=json.loads(row["subscription_json"]),
                data=json.dumps(payload),
                vapid_private_key=str(VAPID_RUNTIME_PRIVATE_FILE),
                vapid_claims={"sub": "mailto:act-doctor-call@example.com"},
                timeout=10
            )
            sent += 1
        except WebPushException as exc:
            code = getattr(
                getattr(exc, "response", None),
                "status_code",
                None
            )
            if code in (404, 410):
                dead_endpoints.append(row["endpoint"])
            else:
                print("Doctor Call push error:", exc)
        except Exception as exc:
            print("Doctor Call push error:", exc)

    if dead_endpoints:
        conn = get_db()
        for endpoint in dead_endpoints:
            conn.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ?",
                (endpoint,)
            )
        conn.commit()
        conn.close()

    return {
        "registered": len(subscriptions),
        "sent": sent
    }


def send_case_push(case_id, case_no, specialty, doctor_username):
    payload = {
        "title": "🔔 ACT Doctor Call — Review Required",
        "body": f"{specialty} case {case_no} is waiting for review.",
        "url": f"/doctor-call/case/{case_id}",
        "case_id": case_id,
        "case_no": case_no,
        "specialty": specialty
    }
    return send_push_payload(doctor_username, payload)


# =========================
# DOCTOR CALL ROUTES
# =========================


@app.route("/doctor-call")
@login_required
def doctor_call_insurance():
    if session.get("role") == "doctor":
        return redirect(url_for("doctor_call_doctor"))
    if session.get("role") not in ("insurance", "admin"):
        return redirect(url_for("home"))

    conn = get_db()
    doctors = conn.execute("""
        SELECT u.username, u.display_name, p.specialty
        FROM users u
        JOIN doctor_profiles p ON p.username = u.username
        WHERE u.role = 'doctor' AND p.active = 1
        ORDER BY p.specialty, u.display_name
    """).fetchall()
    rows = conn.execute("""
        SELECT c.*, u.display_name AS doctor_display
        FROM doctor_cases c
        LEFT JOIN users u ON u.username = c.doctor_username
        ORDER BY c.id DESC
        LIMIT 200
    """).fetchall()
    conn.close()

    specialties = sorted({d["specialty"] for d in doctors})
    pending_count = sum(
        1 for item in rows
        if item["status"] in ("Sent", "Opened")
    )
    completed_count = sum(
        1 for item in rows
        if item["status"] in ("Accepted", "Rejected")
    )

    return render_template_string(
        DOCTOR_CALL_INSURANCE_HTML,
        doctors=doctors,
        specialties=specialties,
        cases=rows,
        message=request.args.get("message"),
        role=session.get("role"),
        pending_count=pending_count,
        completed_count=completed_count,
        active_doctors=len(doctors),
        specialty_count=len(specialties)
    )


@app.route("/doctor-call/smart-referral", methods=["POST"])
@login_required
def doctor_call_smart_referral():
    if session.get("role") not in ("insurance", "admin"):
        return redirect(landing_url())

    wants_json = (
        request.headers.get("X-Requested-With", "").lower() == "fetch"
    )

    def fail(
        message,
        fallback=False,
        status=400,
        detected_specialty=None
    ):
        if wants_json:
            return jsonify({
                "ok": False,
                "fallback": bool(fallback),
                "reason": message if fallback else None,
                "error": None if fallback else message,
                "detected_specialty": detected_specialty
            }), status

        return redirect(url_for(
            "doctor_call_insurance",
            message=message
        ))

    files = [
        f for f in request.files.getlist("pdfs")
        if f and f.filename
    ]
    case_no = request.form.get("case_no", "").strip()
    patient_ref = request.form.get("patient_ref", "").strip()
    client_text = request.form.get(
        "client_extracted_text",
        ""
    ).strip()
    manual_specialty = request.form.get(
        "manual_specialty",
        ""
    ).strip()
    manual_doctor_username = request.form.get(
        "manual_doctor_username",
        ""
    ).strip()

    try:
        client_ocr_pages = max(
            0,
            int(request.form.get("client_ocr_pages", "0") or 0)
        )
    except (TypeError, ValueError):
        client_ocr_pages = 0

    if not files:
        return fail("Please upload at least one PDF.")

    try:
        merged = merge_uploaded_pdfs(files)
    except ValueError as exc:
        return fail(f"Could not prepare the PDF: {exc}")

    conn = get_db()
    doctors = conn.execute("""
        SELECT
            u.username,
            u.display_name,
            p.specialty,
            COALESCE(SUM(
                CASE
                    WHEN c.status IN ('Sent', 'Opened') THEN 1
                    ELSE 0
                END
            ), 0) AS pending_count
        FROM users u
        JOIN doctor_profiles p
          ON p.username = u.username
        LEFT JOIN doctor_cases c
          ON c.doctor_username = u.username
        WHERE u.role = 'doctor'
          AND p.active = 1
        GROUP BY u.username, u.display_name, p.specialty
        ORDER BY u.display_name
    """).fetchall()

    manual_mode = bool(
        manual_specialty or manual_doctor_username
    )

    if manual_mode:
        if not manual_specialty or not manual_doctor_username:
            conn.close()
            return fail(
                "Choose both specialty and doctor.",
                fallback=True
            )

        selected = next(
            (
                d for d in doctors
                if d["username"] == manual_doctor_username
            ),
            None
        )

        if not selected:
            conn.close()
            return fail(
                "Selected doctor is not active. Choose another doctor.",
                fallback=True
            )

        if selected["specialty"] != manual_specialty:
            conn.close()
            return fail(
                "Doctor and specialty do not match. Choose again.",
                fallback=True
            )

        routing = {
            "ok": True,
            "specialty": selected["specialty"],
            "doctor_username": selected["username"],
            "doctor_display": selected["display_name"],
            "pending_count": selected["pending_count"],
            "matches": ["manual Insurance selection"],
            "score": None
        }

    else:
        if len(normalize_text(client_text)) < 80:
            conn.close()
            return fail(
                "ACT could not read enough clinical text for automatic routing. "
                "Choose the specialty and doctor manually below.",
                fallback=True
            )

        routing = detect_specialty_and_doctor(
            client_text,
            doctors
        )

        if not routing["ok"]:
            conn.close()
            return fail(
                routing["reason"],
                fallback=True,
                detected_specialty=routing.get("detected_specialty")
            )

    if not case_no:
        case_no = "AUTO-" + now_dt().strftime("%Y%m%d-%H%M%S")

    safe_name = secure_filename(
        merged["filename"]
    ) or "smart_referral.pdf"
    stamped = f"{int(datetime.now().timestamp())}_{safe_name}"
    sent_time = now_text()

    if USE_POSTGRES:
        cur = conn.execute("""
            INSERT INTO doctor_cases
            (
                case_no,
                patient_ref,
                specialty,
                doctor_username,
                pdf_filename,
                pdf_original_name,
                pdf_data,
                status,
                sent_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Sent', ?)
            RETURNING id
        """, (
            case_no,
            patient_ref,
            routing["specialty"],
            routing["doctor_username"],
            stamped,
            safe_name,
            merged["pdf_bytes"],
            sent_time
        ))
        case_id = cur.fetchone()["id"]
    else:
        cur = conn.execute("""
            INSERT INTO doctor_cases
            (
                case_no,
                patient_ref,
                specialty,
                doctor_username,
                pdf_filename,
                pdf_original_name,
                pdf_data,
                status,
                sent_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Sent', ?)
        """, (
            case_no,
            patient_ref,
            routing["specialty"],
            routing["doctor_username"],
            stamped,
            safe_name,
            merged["pdf_bytes"],
            sent_time
        ))
        case_id = cur.lastrowid

    conn.commit()
    conn.close()

    push_result = send_case_push(
        case_id,
        case_no,
        routing["specialty"],
        routing["doctor_username"]
    )

    if manual_mode:
        matched_terms = "manual Insurance selection"
        route_label = "Manual Referral"
        confidence_note = ""
    else:
        matched_terms = ", ".join(
            routing["matches"]
        ) or "clinical diagnosis"
        route_label = "Clinical Smart Referral"
        confidence_note = (
            f" Triage confidence: {routing.get('confidence', 'Good')}."
        )

    device_note = (
        f" Push delivered to {push_result['sent']} of "
        f"{push_result['registered']} registered device(s)."
        if push_result["registered"] > 0
        else " Doctor has no registered notification device yet."
    )

    message = (
        f"🤖 {route_label}: {case_no} → {routing['specialty']} → "
        f"{routing['doctor_display']}. "
        f"Clinical evidence: {matched_terms}."
        + confidence_note
        + " "
        + f"Merged {merged['file_count']} PDF(s), "
        f"{merged['page_count']} page(s). "
        f"Browser OCR processed {client_ocr_pages} scanned page(s)."
        + device_note
    )

    redirect_url = url_for(
        "doctor_call_insurance",
        message=message
    )

    if wants_json:
        return jsonify({
            "ok": True,
            "fallback": False,
            "manual": manual_mode,
            "message": message,
            "redirect_url": redirect_url
        })

    return redirect(redirect_url)


@app.route("/doctor-call/new", methods=["POST"])
@login_required
def doctor_call_new():
    if session.get("role") not in ("insurance", "admin"):
        return redirect(landing_url())

    case_no = request.form.get("case_no", "").strip()
    patient_ref = request.form.get("patient_ref", "").strip()
    specialty = request.form.get("specialty", "").strip()
    doctor_username = request.form.get("doctor_username", "").strip()
    pdf = request.files.get("pdf")

    if not case_no or not specialty or not doctor_username or not pdf:
        return redirect(url_for(
            "doctor_call_insurance",
            message="Please complete all required fields."
        ))
    if not allowed_pdf(pdf.filename):
        return redirect(url_for(
            "doctor_call_insurance",
            message="Only PDF files are allowed."
        ))

    conn = get_db()
    profile = conn.execute("""
        SELECT p.specialty
        FROM users u
        JOIN doctor_profiles p ON p.username = u.username
        WHERE u.username = ? AND u.role = 'doctor' AND p.active = 1
    """, (doctor_username,)).fetchone()

    if not profile:
        conn.close()
        return redirect(url_for(
            "doctor_call_insurance",
            message="Selected doctor is not active."
        ))

    if profile["specialty"] != specialty:
        conn.close()
        return redirect(url_for(
            "doctor_call_insurance",
            message="Doctor and specialty do not match."
        ))

    safe_name = secure_filename(pdf.filename) or "case.pdf"
    stamped = f"{int(datetime.now().timestamp())}_{safe_name}"
    pdf_bytes = pdf.read()
    if not pdf_bytes:
        conn.close()
        return redirect(url_for(
            "doctor_call_insurance",
            message="The PDF file is empty."
        ))

    if USE_POSTGRES:
        cur = conn.execute("""
            INSERT INTO doctor_cases
            (case_no, patient_ref, specialty, doctor_username,
             pdf_filename, pdf_original_name, pdf_data, status, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Sent', ?)
            RETURNING id
        """, (
            case_no, patient_ref, specialty, doctor_username,
            stamped, safe_name, pdf_bytes, now_text()
        ))
        case_id = cur.fetchone()["id"]
    else:
        cur = conn.execute("""
            INSERT INTO doctor_cases
            (case_no, patient_ref, specialty, doctor_username,
             pdf_filename, pdf_original_name, pdf_data, status, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Sent', ?)
        """, (
            case_no, patient_ref, specialty, doctor_username,
            stamped, safe_name, pdf_bytes, now_text()
        ))
        case_id = cur.lastrowid

    conn.commit()
    conn.close()

    push_result = send_case_push(
        case_id,
        case_no,
        specialty,
        doctor_username
    )

    if push_result["registered"] == 0:
        message = (
            "Case saved, but this doctor has no registered notification device yet."
        )
    else:
        message = (
            f"Case sent successfully. Notification delivered to "
            f"{push_result['sent']} of {push_result['registered']} registered device(s)."
        )

    return redirect(url_for(
        "doctor_call_insurance",
        message=message
    ))


@app.route("/doctor-call/install")
def doctor_call_install():
    return render_template_string(DOCTOR_CALL_INSTALL_HTML)


@app.route("/doctor-call/doctor")
@login_required
def doctor_call_doctor():
    if session.get("role") != "doctor":
        return redirect(url_for("doctor_call_insurance"))

    conn = get_db()
    cases = conn.execute("""
        SELECT * FROM doctor_cases
        WHERE doctor_username = ?
        ORDER BY
            CASE WHEN status='Sent' THEN 0
                 WHEN status='Opened' THEN 1
                 ELSE 2 END,
            id DESC
    """, (session["username"],)).fetchall()

    profile = conn.execute(
        "SELECT specialty FROM doctor_profiles WHERE username = ?",
        (session["username"],)
    ).fetchone()

    latest_row = conn.execute("""
        SELECT COALESCE(MAX(id), 0) AS latest_id
        FROM doctor_cases
        WHERE doctor_username = ?
    """, (session["username"],)).fetchone()

    conn.close()

    new_count = sum(
        1 for item in cases
        if item["status"] == "Sent"
    )
    review_count = sum(
        1 for item in cases
        if item["status"] == "Opened"
    )
    completed_count = sum(
        1 for item in cases
        if item["status"] in ("Accepted", "Rejected")
    )

    return render_template_string(
        DOCTOR_CALL_DOCTOR_HTML,
        cases=cases,
        display_name=session.get("display", session["username"]),
        doctor_specialty=(profile["specialty"] if profile else "Doctor"),
        vapid_public_key=ensure_vapid_keys(),
        latest_case_id=(latest_row["latest_id"] if latest_row else 0),
        new_count=new_count,
        review_count=review_count,
        completed_count=completed_count
    )


@app.route("/doctor-call/api/doctor/latest")
@login_required
def doctor_call_latest_case():
    if session.get("role") != "doctor":
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    conn = get_db()
    row = conn.execute("""
        SELECT id, case_no, specialty, status, sent_at
        FROM doctor_cases
        WHERE doctor_username = ?
        ORDER BY id DESC
        LIMIT 1
    """, (session["username"],)).fetchone()
    conn.close()

    if not row:
        return jsonify({"ok": True, "case": None})

    return jsonify({
        "ok": True,
        "case": {
            "id": row["id"],
            "case_no": row["case_no"],
            "specialty": row["specialty"],
            "status": row["status"],
            "sent_at": row["sent_at"],
            "url": f"/doctor-call/case/{row['id']}"
        }
    })


@app.route("/doctor-call/api/push/status")
@login_required
def doctor_call_push_status():
    if session.get("role") != "doctor":
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    conn = get_db()
    row = conn.execute("""
        SELECT COUNT(*) AS c
        FROM push_subscriptions
        WHERE username = ?
    """, (session["username"],)).fetchone()
    conn.close()

    return jsonify({
        "ok": True,
        "devices": row["c"] if row else 0
    })


@app.route("/doctor-call/api/push/test", methods=["POST"])
@login_required
def doctor_call_push_test():
    if session.get("role") != "doctor":
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    result = send_push_payload(
        session["username"],
        {
            "title": "🔔 ACT Doctor Call — Test",
            "body": "Notifications are working on this doctor account.",
            "url": "/doctor-call/doctor",
            "case_id": "test",
            "case_no": "TEST",
            "specialty": "Notification Test"
        }
    )

    if result["registered"] == 0:
        return jsonify({
            "ok": False,
            "error": "No notification device is registered for this doctor."
        }), 400

    return jsonify({
        "ok": True,
        "registered": result["registered"],
        "sent": result["sent"]
    })


@app.route("/doctor-call/case/<int:case_id>")
@login_required
def doctor_call_case(case_id):
    conn = get_db()
    case = conn.execute(
        "SELECT * FROM doctor_cases WHERE id = ?",
        (case_id,)
    ).fetchone()

    if not case:
        conn.close()
        return "Case not found", 404

    role = session.get("role")
    if role == "doctor":
        if case["doctor_username"] != session.get("username"):
            conn.close()
            return "Not authorized", 403
        if not case["opened_at"]:
            conn.execute("""
                UPDATE doctor_cases
                SET status='Opened', opened_at=?
                WHERE id=?
            """, (now_text(), case_id))
            conn.commit()
            case = conn.execute(
                "SELECT * FROM doctor_cases WHERE id = ?",
                (case_id,)
            ).fetchone()
    elif role not in ("insurance", "admin"):
        conn.close()
        return redirect(url_for("home"))

    doctor_name_row = conn.execute(
        "SELECT display_name FROM users WHERE username = ?",
        (case["doctor_username"],)
    ).fetchone()
    conn.close()

    return render_template_string(
        DOCTOR_CALL_CASE_HTML,
        case=case,
        role=role,
        doctor_name=(
            doctor_name_row["display_name"]
            if doctor_name_row
            else case["doctor_username"]
        )
    )


@app.route(
    "/doctor-call/case/<int:case_id>/decision/<decision>",
    methods=["POST"]
)
@login_required
def doctor_call_decision(case_id, decision):
    if session.get("role") != "doctor":
        return redirect(landing_url())
    if decision not in ("Accepted", "Rejected"):
        return "Invalid decision", 400

    conn = get_db()
    case = conn.execute(
        "SELECT doctor_username FROM doctor_cases WHERE id = ?",
        (case_id,)
    ).fetchone()
    if not case or case["doctor_username"] != session.get("username"):
        conn.close()
        return "Not authorized", 403

    conn.execute("""
        UPDATE doctor_cases
        SET status=?, decision=?, decided_at=?
        WHERE id=?
    """, (decision, decision, now_text(), case_id))
    conn.commit()
    conn.close()
    return redirect(url_for("doctor_call_doctor"))


@app.route("/doctor-call/api/push/subscribe", methods=["POST"])
@login_required
def doctor_call_push_subscribe():
    if session.get("role") != "doctor":
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    subscription = request.get_json(silent=True)
    if not subscription or "endpoint" not in subscription:
        return jsonify({"ok": False, "error": "Invalid subscription"}), 400

    conn = get_db()
    conn.execute("""
        INSERT INTO push_subscriptions
        (username, endpoint, subscription_json, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (endpoint) DO UPDATE SET
            username = excluded.username,
            subscription_json = excluded.subscription_json,
            created_at = excluded.created_at
    """, (
        session["username"],
        subscription["endpoint"],
        json.dumps(subscription),
        now_text()
    ))
    conn.commit()

    device_row = conn.execute("""
        SELECT COUNT(*) AS c
        FROM push_subscriptions
        WHERE username = ?
    """, (session["username"],)).fetchone()

    conn.close()
    return jsonify({
        "ok": True,
        "devices": device_row["c"] if device_row else 0
    })


@app.route("/doctor-call/case/<int:case_id>/pdf")
@login_required
def doctor_call_pdf(case_id):
    role = session.get("role")
    conn = get_db()
    case = conn.execute(
        "SELECT doctor_username, pdf_filename, pdf_original_name, pdf_data FROM doctor_cases WHERE id = ?",
        (case_id,)
    ).fetchone()
    conn.close()

    if not case:
        return "File not found", 404
    if role == "doctor" and case["doctor_username"] != session.get("username"):
        return "Not authorized", 403
    if role not in ("doctor", "insurance", "admin"):
        return "Not authorized", 403

    if case["pdf_data"]:
        return send_file(
            io.BytesIO(bytes(case["pdf_data"])),
            mimetype="application/pdf",
            download_name=case["pdf_original_name"] or "case.pdf",
            as_attachment=False
        )

    legacy_path = UPLOAD_FOLDER / case["pdf_filename"]
    if legacy_path.exists():
        return send_from_directory(UPLOAD_FOLDER, case["pdf_filename"])
    return "PDF is unavailable", 404


@app.route("/doctor-call/uploads/<path:filename>")
@login_required
def doctor_call_upload(filename):
    role = session.get("role")
    conn = get_db()
    case = conn.execute(
        "SELECT doctor_username FROM doctor_cases WHERE pdf_filename = ?",
        (filename,)
    ).fetchone()
    conn.close()

    if not case:
        return "File not found", 404

    if role == "doctor" and case["doctor_username"] != session.get("username"):
        return "Not authorized", 403
    if role not in ("doctor", "insurance", "admin"):
        return "Not authorized", 403

    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# =========================
# DOCTOR ADMIN
# =========================

@app.route("/doctor-call/admin/doctors")
@login_required
def doctor_call_admin_doctors():
    role = session.get("role")
    if role not in ("insurance", "admin"):
        return redirect(landing_url())

    conn = get_db()
    doctors = conn.execute("""
        SELECT u.username, u.display_name, p.specialty, p.active
        FROM users u
        JOIN doctor_profiles p ON p.username = u.username
        WHERE u.role = 'doctor'
        ORDER BY p.specialty, u.display_name
    """).fetchall()
    conn.close()

    doctor_count = len(doctors)
    active_count = sum(
        1 for item in doctors
        if item["active"] == 1
    )
    specialty_count = len({
        item["specialty"] for item in doctors
    })

    return render_template_string(
        DOCTOR_CALL_ADMIN_HTML,
        doctors=doctors,
        message=request.args.get("message"),
        can_manage=(role == "admin"),
        doctor_count=doctor_count,
        active_count=active_count,
        specialty_count=specialty_count
    )


@app.route("/doctor-call/admin/doctors/add", methods=["POST"])
@login_required
def doctor_call_admin_add_doctor():
    if session.get("role") != "admin":
        return redirect(landing_url())

    username = request.form.get("username", "").strip().lower()
    display_name = request.form.get("display_name", "").strip()
    specialty = request.form.get("specialty", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not display_name or not specialty or len(password) < 6:
        return redirect(url_for(
            "doctor_call_admin_doctors",
            message="Complete all fields. Password must be at least 6 characters."
        ))

    conn = get_db()
    existing = conn.execute(
        "SELECT username FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    if existing:
        conn.close()
        return redirect(url_for(
            "doctor_call_admin_doctors",
            message="Username already exists."
        ))

    conn.execute("""
        INSERT INTO users
        (username, password_hash, role, display_name, updated_at)
        VALUES (?, ?, 'doctor', ?, ?)
    """, (
        username,
        generate_password_hash(password),
        display_name,
        now_text()
    ))
    conn.execute("""
        INSERT INTO doctor_profiles (username, specialty, active)
        VALUES (?, ?, 1)
    """, (username, specialty))
    conn.commit()
    conn.close()

    return redirect(url_for(
        "doctor_call_admin_doctors",
        message="Doctor account added successfully."
    ))


@app.route("/doctor-call/admin/doctors/<username>/edit", methods=["POST"])
@login_required
def doctor_call_admin_edit_doctor(username):
    if session.get("role") != "admin":
        return redirect(landing_url())

    display_name = request.form.get("display_name", "").strip()
    specialty = request.form.get("specialty", "").strip()
    new_password = request.form.get("new_password", "").strip()

    if not display_name or not specialty:
        return redirect(url_for(
            "doctor_call_admin_doctors",
            message="Display name and specialty are required."
        ))
    if new_password and len(new_password) < 6:
        return redirect(url_for(
            "doctor_call_admin_doctors",
            message="New password must be at least 6 characters."
        ))

    conn = get_db()
    user = conn.execute(
        "SELECT username FROM users WHERE username = ? AND role = 'doctor'",
        (username,)
    ).fetchone()
    if not user:
        conn.close()
        return redirect(url_for(
            "doctor_call_admin_doctors",
            message="Doctor account not found."
        ))

    conn.execute(
        "UPDATE users SET display_name = ?, updated_at = ? WHERE username = ?",
        (display_name, now_text(), username)
    )
    conn.execute(
        "UPDATE doctor_profiles SET specialty = ? WHERE username = ?",
        (specialty, username)
    )
    if new_password:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?",
            (generate_password_hash(new_password), now_text(), username)
        )
    conn.commit()
    conn.close()

    return redirect(url_for(
        "doctor_call_admin_doctors",
        message="Doctor details updated successfully."
    ))


@app.route("/doctor-call/admin/doctors/<username>/toggle", methods=["POST"])
@login_required
def doctor_call_admin_toggle_doctor(username):
    if session.get("role") != "admin":
        return redirect(landing_url())

    conn = get_db()
    profile = conn.execute(
        "SELECT active FROM doctor_profiles WHERE username = ?",
        (username,)
    ).fetchone()
    if profile:
        new_active = 0 if profile["active"] == 1 else 1
        conn.execute(
            "UPDATE doctor_profiles SET active = ? WHERE username = ?",
            (new_active, username)
        )
        conn.commit()
    conn.close()
    return redirect(url_for("doctor_call_admin_doctors"))



SW_JS_V31 = r'''
self.addEventListener("push", event => {
  let data = {
    title: "ACT Doctor Call",
    body: "A new case is waiting for review.",
    url: "/doctor-call/doctor"
  };

  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }

  event.waitUntil(
    clients.matchAll({type: "window", includeUncontrolled: true}).then(windows => {
      const visibleClient = windows.find(c => c.visibilityState === "visible");

      if (visibleClient) {
        visibleClient.postMessage({type: "ACT_DOCTOR_CASE", data});
        return;
      }

      const options = {
        body: data.body,
        tag: "act-doctor-case-" + (data.case_id || "new"),
        renotify: true,
        requireInteraction: true,
        silent: false,
        icon: "/app-icon.svg?v=4-4-1",
        vibrate: [900, 180, 900, 180, 900, 180, 1600],
        data: {url: data.url || "/doctor-call/doctor"}
      };

      return self.registration.showNotification(
        data.title || "ACT Doctor Call",
        options
      );
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const target =
    (event.notification.data && event.notification.data.url)
    || "/doctor-call/doctor";

  event.waitUntil(
    clients.matchAll({type: "window", includeUncontrolled: true}).then(windows => {
      for (const client of windows) {
        if ("focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }

      if (clients.openWindow) return clients.openWindow(target);
    })
  );
});
'''

APP_ICON_192_B64 = """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[... ELLIPSIZATION ...]vYxYFNVkW8Bh+3O5WLg9/eT+wAvenYhx8ZGNgD4tpDwMzCfAqFJ28wCiTcANSZe2n7DU9AAePaCWYD6YG6gUlEAsBMJwOg4vgj3FIDLaJ8VAHkAwMctgeqcrUWcSjkvCMiL6tCBIdouIQDgT4K1fM4rFtQGb5Q+lh1gFcHsNTkXWOb/YXUozCQzFkcyBMpFD9JIc8w/WA3J//gC8OwZuABQDmQDMDYWBgCzwWNkA3oDACYBb4KD1dcfaBui+vuYFo/brmys+K7koY7Mp0oimisGIwWuPVrns36uLWMmf6rOAGDXV8SO5GrAypFcbgT+uulpnECC74jMI8Ad0FNUfg6rYzUAyuUC7BvCqi67krMNAFa7CEBobKQBwKtnsP4q8AadAIiy8HPnwu9B7QJg7oJxAQDFoEUEgOdoYUVYgFpZawe8ovoC5MEFj0xCRQYAJPfzqjQsmISqAG3Q7zqVh7NALjD8QKEHiiWM7xkEl2fpI0+j9CerpYDXJeQDCUDBx6LSuRfPN+Z+M9Y8ccmlByA5BxQZ44l2IJH1d7wMG/WAuMJoFkNuBcDJeADGRs/XmQq4yTf69gIAGvzpBTJGBx9rgPnxy7pFdp51kaHF6mAdAJgrdQ0IICWAyVwc3A8raEUsyF1BiCRoGWiNlrpPo+xBuUwOe+rZOZADilkgB3/BHHb4/2kAeAAArMxMnWfecKly2gRANIYY04LPn58cHzsDNuDmzTYA4OV+v0Y7AJQFqtcGxRhfuSJsEOr715eXI4f3cAJEhG8AUMbiUP4kqABYpMVfUBdQkH4lf4KkeAL8DlV2OFmrDtGvcw+jIEZXlgOPFg4yF/A3DQCY+vyY5P9ALI0yASjoAIQSQY/DbUAbdq5HiZpUvWMwmFobZ4yJIwAgKT5QLI2MkPittXEhAM6eGYc4AHbzanc6gwMQCwBf5zHo8cadAXyv432+N5axJzTuAB1MrBCjFXxeTk4awIfZkkzoEgB0A/jeUR0AbgdI0J4n9TxNqFC/LjoLCdKgVGPyf/H8IcR8OgCPEwDQNUD4MaibAJgz47AtcO0B5oExDSjufwIAkA+GwkACQIq0qwDA3FcDACjW4J3+NyP/GD83EAAfL6kCoODlIbRjkeDynWtyzv/6I6gNG2DmnKeDNABAwr6j/MiTSkKuHIZnRJ9vHIaKrrXHq38CANAOYBqw5nsnRkIAjPEwAEaEnD2rEzAGRQG/3jSWO2cA4Gb0Ee9AuNEr8HlXcBH6NgrQ4KPu/038n/Msr1PvnUezxfhDH/tRifeUXZObHngsCPZCLh/WEXAVIfbJuhT+GE0AFHHjMJX0QU2fnPEjAZBr4ywA+J4z13OwWQgYtRhYAfDE7gkxBgabYAAAVA9KtSCG9KMBOIcAnKvzOKDrANA7UIUA8LnyZTcR5kJdgY7Q2MNHwMD+AAzUAqECIJZgcYRoKhRFHRgLTtdPe9hRaADQh3qe16OLb+m/QGEiTXfisaNPFeUbzx+t47vP41AvZzYAHjvdvzQAOJ2+KADW4B3oHrpBDICR9ACMQ3E4eAE/dRkAngSAyaAetfnzBj8PB75BS7Aa9uWQ/g0JALXfagDgeEmsDuYAXOOb4DEbQLjkwwCoQE+sIuHageqSxL76XJF9esyDgw5Qe75XdgCAoMdpAfjdAIB2RKYB4OnTB7/hDkN2WQr+wEhlZPikDQD4ABgEWgBMIAAgyIwA/BonfSgE4etBIAb0+KQXUN/s6w3DoXF6RyQB4j/cgPHxkKvzcsoEFMR80dlFrbLzGnqCi1QjnsujEfAML0BWIBQKavqY+eAE62lyk/B+CFVhGgAilaP7AAqAAJ6Dg7yrJnBNrAt3DQIyAMAfP0Hp/yG+jT7i5j9lwr8HANAOQ4gBB0dOj5yk8O+kmBQaDcBZ2BzzPuxjye4FxshfAjA5yG6e7PYgF2uSJr0kAsDOMiySw/HxfkHMFiEA8jAFCMdBC/nDtoF17BIoUtOJJwHwBQD6IInAKj/z+nO8qxTejx/J9J2+4LkdANZ6CADfE/AATMCrezPMCQ4gBhyOBeCcDgAsD2KR4E88EvwpSwIoXgPcxI5Q6NnR6n6KKDpD/g4C1H+6gwTUCwgAV+AcIyj4W9YBuMZ+hH4ArZHL4RN03tV1pD6ffAD9p/TWjOmiEtWFPqIRANprsEwFrwkTIKOAatDny1WXZRgQQVtrxYCINRsAZyswARC1INgQvJoPK+dCrD1gLuAUC7kHmPVnAAzbAAAC3Ac4ZxBw/hw8CPwkUwHZ3gDiAbg8XT8BdRr4gkM7IgK03nd0+UcBwIu3YGRYCV9rpfkuFvtoBAT1/17TEEAdgNtHEQDu9AX5QFWdBooDrfwY3qj4cKmNjdX19fXwZHfSARtr+vZwBIDFpVCMQlmkcmBPCDEQ2IgF4PeoHfFuAMQjwO8P/nj6fA37IwZG5AtACgCAgEl8EPg1AwGpALiN70AXmAXAmERlXwKaDazd8wgTgPK/gluEagGMgYRXmlIZcWLmHV1JEwAo8FzEVqF6rVTggZ7K8QgAeG0JhZToC/bj7w0wuwBPgHPrc+vh5S4WAA8UAD773BiXZfwMmZtzxADA/X+GdS193mClxPV/GAAkQEsDqEiQ/MDMY59ibADudIWVjn6Bh6W0BtRXMeCNRAJApHdwfHyl4DPjRge+yqBwsSpgESYAaASQDsBHflTpfX5Q0LaUB1ID4H0t08fiOaIqVgFtYAp3TusNemQgoL0FcABq5Tx9pDJ9ftDBigBsMPk/kI1fXPQmABsbdunvg6gNoQYFNCNYlALOPXj28h77ag/2+YMjDAAtCKQBEbEAnBkbbwOAWPnzWkAwjwwALn+qmIZN0AjAjZQA0Pj4cl+uwGUv9olCye+6BECU+d2Ah4GHs7NU5iG6j3QASA2o3bR5qh0AmwIdfaur7Po7NscLAB4LANZMDVAmnti/NCc0wMYq+Is9A0C6APfYJ/ISq4ED+GqfOJENgMmz4+dEINBFAMADABn081ZvD+cCYoXWtUQA2H+9owCY5euhCyqsg9C+ggOGzDI/GCK4+OjR6hwogXqtMihyvnlHywHWjeFzEL0Wz3K9bU8IcALwQAJQ8vr6IA+gbQ8PAbDWTQDoPFUA3MMYEEKAYjQAY/bEeOkGTo5DOjCTCriZeCAGPD/AdHCfysF6Obkl1gbgRniazx0E4BptkquK6h7tyHmwV+xSbyazOaj2AV/Az4mQQD738YUFQvw4qASrgCF0C91/2wbQ6ugHBMAzDgCH06fWdA7AGgJACGwkzgKyyv9MCsREeEP+T1UtGMyFGch5TP+USkNDJ9SRmcAIAMgIUGVQBhWQAgDmAo4HzD3m7ZoYxrMvEI55umMCoDn9dvcWLpK7Njs1CXXlamqED19vsRrsxo1wv9f6o41HsHeAaYFqRXggeliIWYUS3pga3f41kNjcnKsnLB6AarlQVO5JqZgP0AS8eL72+GgAePby2RyEAF6A/9DBQQ2BZABUo/BN9Sro1PryZzeT9D/VAr4/ebpSNSr8qlWY9npt+Y7mA95xyt7o34TsDvzZGg2RqVTYh2VirYQBWNba/aBEAFt+4fH/LPyBYiCfBFm8XKnx8gAoEgHnjwEw5wTgoUEATnQFsfLsG1MztRr7K/CwDwk/g2DixfPf1xQAa2kAeKLlAf7QAHj6NAaAuXvPuCLKDxDpCQCEVcDk+JgWCEQCoBp/0hwGgHP8EygAK9+TCADkg90fjA8BsQngAx+o659DwCQDyoA2VlNtCE0kQ90PFnv1YdSuUA0APtOXiXX1MUxkmoWPbpYxYjTBAHiyJmZ6JAJg93xr70AGAMSAWhGCAGBDIPMAi4NhDeDwARw2YPIc+IHdBeAnKrwKDYC7fCcrAOQFOD/Y7JVr5mIg0fTHPQHs9afqT06BNogMq8NQ+i9eQPmn+/o7AHgsgntQGgTYlPEJzszOPmD/7cmD3gJwX/MAa4F3gkfbiQCcseVPyQBTBWgQEBDhvq8kFXD5spz9qM6VK7ISiGQfKX4uTtj/vXhtNuJccyyGuraopv7gvIeH/DdPi3JAkjw1lsDlx/v/cC5qIoTeIKYR8PgxmI0H0J9mn7l7EgDM8WzoseCGjcCTJyYAxjPw06hz7/6LF/fWoB8UDMAJ8ACZ/zc0CPKHEwOAmQ2mZ+FTY9IIxAKQ/mBN1+2QeO7c0IL9O/GH1wbCK4+7Z39x2fUn9MFvTGTi6w653Tl+VkHuL+Dmg/Af87RfrAOouYE8rQf/zz7Is2cvtPMMDnzgJ7+rQu4kAJ5kB4Bd//tP78Ej0KsVbL8OhkdODHHhZwfgTHUMCoRvCgK6BUBYPHduZwUAfuAAAH7BAGD5jj36j2S9uvE8fIS8uOs3N/ebm4BHzlW/FgDPDOnDz9mHJcH+jiLuPgD37t+HDTFrvBZ8JCgMEgAnTkQAIOYEmY8BAoBJeBeu60ZAAXCzzeOu8wzn+xMVAPxI9PQY870idoPqsz/nUGbwZX/uPkwGq6u/YfvGQ8dEkKht71gn+JisQBgt+D7c471hpX00AOyH3gcJBDD5v8BnwHtYClwKBrANcIjkfwK+PcF/yL5JAIC8gHNj6lHIrAy42a0T8eCTBAAXs/M3L8dMAOYenO7I6Qtcf+Nnldd8ZgMACwUfGyVDj9Xaj99/lwj0BAD0/WAgBBWC1TxvCMqAMO8XBcCYBMDwAgEA+PYC8wPHLQLQ4XuTAOhK4Eb4RLUVuKf/6gKEGg9S/AwAXvTpAuDhw0gAKC2M3gNitLoqmwjwZR8B+D0rAHYRGLr8f7jkjwCAA4DtoP5wZYTre9L9DgBUUYjSAvzy80ehMSoOSi756twWpHQC7hg6IHb0e3gCeDiMi93fGfIBkgGgmjF9348c8/GHff1jToT1dyWCNAAoAnw1O3W+FNBTqRX8ZQXgPA0QFxnhn7KEfNkE3w4A6U9o+rvS5M6BfXrlRxsAqE2vdO21SL7XALygN4Bhj88DSAJgTDSHnD1rFYbxMyFqQ25yAH768wHgGv+vJGnILrzOXUYa8MN1PhbqUbT8ebuwONwLNMWvBfmOX9KyQKoQkOaAKwYc8n8hu4Gh8GGA94JS6teSvgwDRYeoCYAQPnoD1dEJlQ1In/Q7NgBEr3945JSfJnkxBewRHw2GgpcAPHoUY0VUtbfZ5xkz3s/1q5kA4Pp/Zbp+dgiaQUdSA8AdQScA4Aac02qEb3ZoBY4RAJjQIwk+NMSvCTgGgFg3Qq/312949wCwGHjB3wBfrsxMMfkz+386HQB6LHg2ZAB4LAhrJLpjBG6nACB9JJgk++WkPfAOg/5InwCon4dxJ2n5X7oBn5bwudX4Q5P7H0ZViHb3X7ygglQm//oJb4CJn+Q/rMlfJYI0H0AAMOEGAHVARbkBnQFw+/bRAbAYcRIJsKZAtg+A0fPTAQDGG5BZFqR7//cePKVhAKcHBk5UYCiomAeVDYDJyUkLgMkx2Sp089eOnIAjA2Ax+jgzAS75gt4n7e/W+vitCYAGwmOhA3hIgJZA2YMnqtlLRglOAMx9YHEAQAIIEgAjAwOlkcrpEasKOBqAMQXAWQIAj24FaHDQT0IH9FD+vPavUzcgtfyFdO2jPy6s2zrgEQdgNaJMPLQAeEM/CABIHb+VMQJZeb33R22GCmUCjQ3RVAEwR0Oh6yPBwHDl9OkRBwBGLJgFgAvnx05LAv5qALgZ+NMBcO/ps5d4/4PgRGWU3X9BQAoAzC5hEr/hCFyAyUE8J3yzg0gwNQCJFHQXAJsB63lx3Q77HnYLgD+UAYAqAgsAp9RVC5ge/uEwKLj/p4NgsHQaxsIPJ2mAatUNAJ8YaoUC58dFkWgHViA1AEmOwHLPAUgVArgbxcJ5xcdGQKhavknwfygAXM2ecQDIHgBcCsDu/+mB4ATT/lL/dw8AZgUq0hO8+T8AjgMA96gLGPeCPXtGw8AGhk7DXhjVChgNwOioREDLBAg3AL81EOBW4GeY8dBWKHg7EwCdhQEZxR/nDmSJ/RK2Aa3ZaX6TgaiGb9eh2w+DoFiIee/lq5fQKsXs/9DIaZ4BtAAwq0HaAQCXymGleLsA3P4fAF0HAJrAH7x49RLyvycDjP9HMgCgW4EJa1SEFQmev3BhcqwydqEuzEBGX/D27T8TAA+POwDQBArShx5gDP+nzp7wAhb/EwDDIR/ABcBojBvgdAQm+bvAT6QEMhFwO+PpSQiQyIAu9TbSf0m7gAwfAK1+YrefsRbaqgmCRqR7z169gkFQtaGBgVJphB9D+iEfAMsD2wHgArMCp9p0Bf8HQHcB4Ifffwj/yiV2/UvD4RAgDoCqlQ2aOKuehV0ATI7BYjlpBtL6Ardv3+ye+HsPwMM0ZzXFSYoCNAC0dJCDAssa0AQgUP8Q/cHcvQHoa0sLAFUFEwAKgXGnEgh5gpPjmhlID8DR3f8kAlJ4AT0DwHgLyHDCg6ARgKdM/lD/XRscGGTmH5y/kZG2ARhLBcCFC+fHR7FEZLqH+v9/ACQ6g0+x5WAFsn+1IDcwUq3iNPAOAeBvAud0AEIMMB0wLszA7XRqIKv846KAdlX/kQOgzQFzzIJHjf88OgDQ7IJyALDqH3qQSftD9Tfc/2BgiKl/qACI9wBVVbATAPUopFoFXQBQjQglhW7e7oEGiA8D/4QAbHQHADH+DdT/cwYAjoGljaAo/pGk6x8CwHQD9QUScQBcuAD9AhVmBpgd+AkzfF30AJPmA/ylAEg6OgBa3Qe2Hq7Q2KuBnDdYAf0P738dAiD6RPQlIm4NABmB8YnzZAdoundsViD9A0CbzwCL6U9SDLDegfgfu47Z90dNAuGBb8agB8P//+OJsQ0ehr9I7V85MTh4wlAADgA0BrR0EAEw6gBgIgUAF/CFGJtGCIHOlP+NzsSfRf6LvQwCUwNgxXpPXeM+5DgQQwM8Be/v2b05pv3PDgdBEby/yoh2MgOgOwKyRFj2C0cDAB0DY6gEpi9zOWcuAL4dHgsUA4A5BXqZhsJ3Sfbr6S6+3Qr4OFb0j+35XwiA9OPDtd3hg9ME+B/FwU9M+T+Yo1lXwxj8o/pvHwBHMkC1CsUkBckXPDM+ce6C1AIWAbHX3SX4WACuRZyuIeCQcdLljwFgzXm07W4xUx70p/6ncp7Q2tqDp9h1/mAW51yNlIaY1a+A+ncCcPJkJABUFt4pABfAGTzD7ACGhBQTKkVA3yVq+2MEgMlApwBEghDT5HvPdcgMPEfN/+LFvXs46q5+diTwB0vVGuj/0x0DEIoFmPTNXiGXHUAAJs9gUoAhICyB6PVXA2Cyyt+NwrVUp8t6QB1sGhc/XF19mP4d0EGCudsv6ZDXz6I+0P04fopdf8j9VWg82mmNgOFhrRb0hPOYUcCoOxiURaKRj8O6IRhnf+g8Dwo1UTvEnwGAtBogsyZY79wTyCh7C4DwesfY8/SpGDGD4q9PVk9Dxr9Cs/HIBLiTAJ0CQASIl6FzEc4gxQNkCBgCMAHqZpLDfzu7BbjTsfaPlX07id6wtx9p+8OCd/sAL8zDRwtxzY+eX31sZGBg4MQIzFrsEIBKRQOASdAigM+NsB4GJp3xAHMGmTfIfljHdbzcFty5E7cFsFPxZwz+oqWvej31GNBoFo2SP06SkWN/kwSPBeAPRCW4W+Sm7Fm4z71+PtdwslaFet+R0xUxHZMsgETAKAQ5EXtsAEbdwaBSAhEA8NTg+XMT8CelOwDK/0aHzz5Jbz8d3PhHmU5c+3fEeZ58XqQ498jrw7s/OTo8ODR8ehQvP4is0ikAlQgAZI3gmYkzyhAIACbdduD82XGwBBNcD+A2XjIIN9sSfLYu4Lazvu2CsJH+PM9+WOA3J8YY0jRLZmdPDQ0MDI3QoN0xJrJKLACOfrAwAJoScDkCYmyAQcBktDvIYkUMH2C60AW1lJko4C+HMkNwo6NzJeLwadJXOokZzKGDkWfOOI55lXOpTtS0SzEHlTYak5yZvHjijkVuhgYIx4Ba7Y82IjgbAKAF0gNAemASFQHTBMwYMAiYPXh/+n8n8xFDbKEE6xRMejsJV5+JgS4/iKs7AFQMAOzigPFxehs+ezamcTREwBn6AKQIkAIAAXF+H87U/049xTk/ybyq0dFTJFnm9o0xAM7UxjgAozoAZhLATPy0CYAaIScBMBCIyQtIczAxPk57qSZUPlHLKtCH4N/EH/0DT4rmxW4c+EfhP20y8ZxVx/2rsWcCjzEnv8a/zupH/HAP7/Qp0Pmj1THzD6ABkOKvOK+/2/9zAqB7gokAnLUAcIeFF7gqOMeswRin9dToERzxj6HvbRN3Sh3jj9gXIeaj4wl9gFPpz4h1hp0HcrfsNo+cApNfq53loNW49AmAStcAMAgYCz8OnxEnHBFGGwN+WeF2TYyHz8TE+IQ8484zET6YoY77M0dwLBOp/1KKk4lnkkatRnevpuQ/Wh11OADOXkAnC3xGUMVFQAgA/jKkAoJzph2ItQZcGXCVoNRDj45KStj6KOFPZfjwXT9JFmdivGYYh6qhjroLQAQBcPW0iNCuGI1NEP3Xn7aQOK9BcGZcE74tfwcAJ9sAINoN0PPCZ0J7pqVnl84vNMzD+Zgvj32jxS09/+c+6RxMdeDLrSxM1Wn+FQDmI3B8EpA6gxwAhHWACwADgcwA/BWP/OeLL8JkpqBCEzg/DtfCcf87BOA05ZYQAMmA4Mw2BZofpoUEkoK48sG/vNiznrOJsg/Jvxqj/xGA2EaQiMYQDQDbCrj1gCDAyAuYyuCvqwky3O1Met6UvgQgLP5QOOp8A2gfAONlCAGo2jGMokD77I8vAFkzS/anrf/iZOcnOkdkAsC+zkZiqGqKf1QIv0sAnK64UyFhJaCHvmei9cDk5DntshhfzgT72ZF8jb9g8nicdClCkduIyCpUHQCcNoy/BkCEA5ASgIoNQDQCwh/QEIhUBuGLp32fdDt7YIG7L+W2ZY/5HfpqRiWUDNlr9z/iBagzAEIPAw4ExsYj3IF0AGQ4mt3Vng0mj9E52/nBDD98Qe3ngGjt32UAKhEARCqBMStZi0osGwLdASTzaZdM/eFI/RM7Ezx+yWrg8REAkdJX8k+SfogB/s1JAYBdJp4SgBhXQKmBM9FqIIRDV9VEorTNT0P9ne5P0/ikSLkL+9bpOWOd8KPg8QDAtgKu3BBjwHIHbApCEWLqp1krwxTCyGapW7q464e/n4TC+/FazfkmLKUeln0lRvzK/aPvhaANAGzp6wDEK4HI/KDLGLh8grN/uaNlRJV0RYYs5oGTvl7aVy8c60nBmwBUKrb0T58eiTX/JgDh639SdwI7AEDzCa1scUgfOL+Mib90hCJN/bs1jWcDIK65uNtRvp1b1YePWfbjAEC1AbUPQMU+0Y/TSWpgXES0xrOBaR3ClyfuZJNjijtrfzrGJxJvs21Vbsrc1u9jY3HmPVr64kvtMPwSAGc5iSZoOwCwXweFW9AOAFGawKzjMD0D59GeFsTP1dfUjYImQu17A6fUZNmfiEht2f8hRYEI3nRZ18W/4d/GAhB55R3lStrdj3P+UgOAJwKAWDuQloLImh7nl3UibUVQLFTjthQFjDaXMdKM+gztf13IoDsNe1aVX42QfrL4OwBAO6kUQbxHMMa/PL2vzHKo3yivyyHF2E8/W5FXCnnjtxlEP2pIwiF9Ry1h2MSbtt9mQncC2wEgGoEIbRB3fxxfcXcpniacbsupR6ea8kRJ3+X4O0tJuw5ACndgLJmBsf+qI74q2QQeKXvj8juVPxYPa4I3ogH1rQLACAySAYjVA8kE/OWlrb7XftI16TuzPiP2tY8G4IT7UcCRB2gbgDR64M8iR+0X3Fm5TEeL5cTPRmPdPFPq0fLvHQCmwkkHgvYPPYZ2d8zwvUwlHb6u2Uy2FGbStU7RZxKr97PZ/pOpCgKt52AnAKkZUIS771PHF7Lb3laGG6w9h5jZGesn2eUec/kjRP9fC0D1fwD0DID/B5koU3JaWpzVAAAAAElFTkSuQmCC"""

APP_ICON_SVG = r'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs>
<linearGradient id="bg" x2="1" y2="1"><stop stop-color="#fff"/><stop offset="1" stop-color="#d9f7ff"/></linearGradient>
<linearGradient id="navy" x2="1" y2="1"><stop stop-color="#079ddd"/><stop offset=".55" stop-color="#06548f"/><stop offset="1" stop-color="#062252"/></linearGradient>
<linearGradient id="bell" x2="1" y2="1"><stop stop-color="#ff762f"/><stop offset="1" stop-color="#ef1040"/></linearGradient>
</defs>
<rect x="4" y="4" width="504" height="504" rx="108" fill="url(#bg)"/>
<path d="M155 312c-43 7-68 38-68 84v7h27v-7c0-31 17-51 44-58M356 312c43 7 68 38 68 84v7h-27v-7c0-31-17-51-44-58" fill="none" stroke="url(#navy)" stroke-width="17" stroke-linecap="round"/>
<path d="M170 150c0-60 39-102 92-102 54 0 92 42 92 102v46c0 56-42 103-92 103s-92-47-92-103Z" fill="#fff" stroke="#07366d" stroke-width="16"/>
<path d="M174 166c-14-82 33-120 92-120 70 0 101 52 87 117-20-3-28-18-35-35-43 13-83-10-97-21-8 25-24 45-47 59Z" fill="url(#navy)"/>
<path d="M213 285v37l49 58 49-58v-37" fill="#fff" stroke="#0b4f91" stroke-width="9"/>
<path d="M232 318l30 43 30-43-30 10Z" fill="#00bde9"/>
<path d="M175 323v46c0 31 20 47 48 47" fill="none" stroke="#063c7e" stroke-width="13"/>
<circle cx="225" cy="419" r="14" fill="#fff" stroke="#063c7e" stroke-width="10"/>
<path d="M350 323v49c0 29-18 41-35 41" fill="none" stroke="#063c7e" stroke-width="13"/>
<circle cx="315" cy="413" r="19" fill="#05bce9" stroke="#063c7e" stroke-width="12"/>
<path d="M374 145c0-23 15-38 35-38s35 15 35 38v28l11 19h-92l11-19Z" fill="url(#bell)" stroke="#f33137" stroke-width="5"/>
<circle cx="409" cy="199" r="9" fill="#f33137"/>
<path d="M456 113c17 15 23 29 23 49M466 94c24 20 35 42 35 70" fill="none" stroke="#f33137" stroke-width="10" stroke-linecap="round"/>
<rect x="116" y="397" width="286" height="93" rx="18" fill="url(#bg)" opacity=".96"/>
<text x="256" y="449" font-family="Arial,sans-serif" font-weight="900" font-size="78" fill="#062252" text-anchor="middle">ACT</text>
<text x="256" y="478" font-family="Arial,sans-serif" font-weight="800" font-size="27" fill="#062252" text-anchor="middle">Doctor Call</text>
</svg>'''

MANIFEST_JSON_V31 = json.dumps({
    "id": "/doctor-call/doctor",
    "name": "ACT Doctor Call",
    "short_name": "ACT Doctor",
    "start_url": "/doctor-call/doctor?source=pwa",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#f4f7fb",
    "theme_color": "#0b5fd7",
    "description": (
        "Doctor case inbox, PDF review, notifications, "
        "and Accept/Reject workflow."
    ),
    "icons": [
        {
            "src": "/app-icon.svg?v=4-4-1",
            "sizes": "any",
            "type": "image/svg+xml",
            "purpose": "any"
        },
        {
            "src": "/app-icon.svg?v=4-4-1",
            "sizes": "any",
            "type": "image/svg+xml",
            "purpose": "maskable"
        }
    ],
    "shortcuts": [
        {
            "name": "Doctor Inbox",
            "short_name": "Inbox",
            "url": "/doctor-call/doctor",
            "icons": [
                {
                    "src": "/app-icon.svg?v=4-4-1",
                    "sizes": "any",
                    "type": "image/svg+xml"
                }
            ]
        }
    ]
})

# =========================
# WEB PUSH / PWA
# =========================

@app.route("/sw.js")
def service_worker():
    response = Response(SW_JS_V31, mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/manifest.json?v=4-4-1")
def manifest_json():
    response = Response(
        MANIFEST_JSON_V31,
        mimetype="application/manifest+json"
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/app-icon-192.png")
def app_icon_192():
    return redirect("/app-icon.svg?v=4-4-1", code=302)


@app.route("/app-icon.svg")
def app_icon_svg():
    response = Response(
        APP_ICON_SVG,
        mimetype="image/svg+xml"
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# =========================
# START
# =========================
init_db()
init_doctor_call_db()
ensure_vapid_keys()
if __name__ == "__main__":


    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
