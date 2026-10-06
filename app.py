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

MODULES_HTML_V31 = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>ACT Operations</title>\n<style>\n*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;background:#f4f7fb;color:#172033}.wrap{max-width:980px;margin:auto;padding:24px}.top{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:24px}.brand h1{margin:0;font-size:30px}.sub{color:#6b7280;margin-top:5px}.user{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:10px 12px;font-size:13px;font-weight:bold}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.card{display:block;text-decoration:none;color:#172033;background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:24px;box-shadow:0 8px 24px rgba(23,32,51,.05)}.icon{font-size:36px}.title{font-size:22px;font-weight:800;margin:12px 0 6px}.desc{color:#6b7280;line-height:1.5}.metric{margin-top:18px;padding-top:14px;border-top:1px solid #eef1f5;font-weight:800}.go{margin-top:8px;color:#1f6feb;font-weight:800}.footer{margin-top:22px;display:flex;gap:10px;flex-wrap:wrap}.btn{padding:10px 13px;border-radius:10px;background:#fff;border:1px solid #e5e7eb;color:#172033;text-decoration:none;font-weight:bold;font-size:13px}.role{color:#6b7280;font-size:12px;margin-top:3px}@media(max-width:700px){.wrap{padding:18px 16px}.grid{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}.user{width:100%}}\n</style></head>\n<body><div class="wrap">\n<div class="top"><div class="brand"><h1>ACT Operations</h1><div class="sub">BedFlow + Doctor Call</div><div class="role">Signed in as {{ role|upper }}</div></div><div class="user">👤 {{ display_name }}</div></div>\n<div class="grid">\n{% if show_bedflow %}<a class="card" href="/"><div class="icon">🛏️</div><div class="title">ACT BedFlow</div><div class="desc">ICU bed availability, confirmation and live status tracking.</div><div class="metric">{{ available_beds }} beds available now</div><div class="go">Open BedFlow →</div></a>{% endif %}\n{% if show_doctor_call %}<a class="card" href="{% if role == \'doctor\' %}/doctor-call/doctor{% else %}/doctor-call{% endif %}"><div class="icon">🔔</div><div class="title">ACT Doctor Call</div><div class="desc">Case PDF review, doctor-specific notifications and Accept / Reject workflow.</div><div class="metric">{{ pending_cases }} cases awaiting action</div><div class="go">Open Doctor Call →</div></a>{% endif %}\n</div>\n<div class="footer"><a class="btn" href="/change-password">Change Password</a>{% if role == \'admin\' %}<a class="btn" href="/doctor-call/admin/doctors">Manage Doctors</a>{% endif %}<a class="btn" href="/logout">Sign Out</a></div>\n</div></body></html>'

DOCTOR_CALL_INSURANCE_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ACT Doctor Call</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:Arial,sans-serif;background:#f4f7fb;color:#172033}
.wrap{max-width:1100px;margin:auto;padding:20px 16px}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px}
.brand h1{margin:0;font-size:27px}.sub{color:#6b7280;margin-top:4px}
.top-actions,.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.btn{display:inline-block;border:0;border-radius:10px;padding:11px 15px;font-weight:bold;text-decoration:none;cursor:pointer}
.btn:disabled{opacity:.6;cursor:not-allowed}
.primary{background:#1f6feb;color:white}.smart{background:#6d28d9;color:white}.light{background:white;border:1px solid #e5e7eb;color:#172033}
.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px;box-shadow:0 6px 18px rgba(0,0,0,.035)}
.smart-card{border:1px solid #c4b5fd;background:linear-gradient(180deg,#faf7ff,#fff)}
.beta{display:inline-block;padding:5px 8px;border-radius:999px;background:#ede9fe;color:#5b21b6;font-size:12px;font-weight:800}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
label{display:block;color:#6b7280;font-size:13px;margin-bottom:6px}
input,select{width:100%;padding:12px;border:1px solid #d5dae2;border-radius:10px;background:white;font-size:15px}
.help{color:#6b7280;font-size:13px;line-height:1.5;margin-top:8px}
.progress{display:none;margin-top:12px;padding:12px;border-radius:10px;background:#f3e8ff;color:#5b21b6;font-size:13px;line-height:1.5}
.progress.show{display:block}
.fallback{display:none;margin-top:14px;padding:14px;border:1px solid #f1c98d;border-radius:12px;background:#fff8eb}
.fallback.show{display:block}
.fallback-title{font-weight:800;margin-bottom:6px}
.fallback .help{margin-bottom:12px}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:11px 8px;border-bottom:1px solid #e5e7eb;font-size:14px}
th{color:#6b7280;font-size:12px;text-transform:uppercase}
.badge{display:inline-block;padding:6px 9px;border-radius:999px;background:#e8eef8;font-weight:bold;font-size:12px}
.notice{background:#e9f8ef;border:1px solid #9bd6ad;color:#176b36;padding:12px;border-radius:10px;margin-bottom:14px;line-height:1.5}
@media(max-width:700px){.grid{grid-template-columns:1fr}.topbar{align-items:flex-start;flex-direction:column}table{display:block;overflow-x:auto;white-space:nowrap}.btn{width:100%;text-align:center}}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js"></script>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brand">
      <h1>ACT Doctor Call</h1>
      <div class="sub">Insurance · Doctor Case Review</div>
    </div>
    <div class="top-actions">
      <a class="btn light" href="/modules">Apps</a>
      <a class="btn light" href="/">BedFlow</a>
      {% if role in ['insurance','admin'] %}<a class="btn light" href="/doctor-call/admin/doctors">Doctors</a>{% endif %}
      <a class="btn light" href="/logout">Logout</a>
    </div>
  </div>

  {% if message %}<div class="notice">{{ message }}</div>{% endif %}

  <div class="card smart-card">
    <span class="beta">BETA v3.6 · UNIFIED REFERRAL</span>
    <h2 style="margin:10px 0 6px">🤖 Smart Referral</h2>
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
        <div style="grid-column:1/-1">
          <label>Medical report + attachments (PDF) *</label>
          <input id="smartPdfs" type="file" name="pdfs" accept="application/pdf" multiple required>
          <div class="help">
            ACT reads text from the full report (up to 30 pages). If pages are scanned images, Browser OCR is applied to selected scanned pages so the diagnosis can be found even when it is not near the beginning.
          </div>
        </div>
      </div>

      <div id="manualFallback" class="fallback">
        <div class="fallback-title">Manual fallback</div>
        <div id="fallbackReason" class="help">
          If automatic routing cannot identify the specialty, choose it here and select the doctor from the existing Doctors Directory.
        </div>
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
        <button id="smartSubmit" class="btn smart" type="submit">Analyze & Auto-Send 🔔</button>
      </div>
    </form>
  </div>

  <div class="card">
    <h2 style="margin-top:0">Cases</h2>
    <table>
      <thead><tr><th>Case</th><th>Specialty</th><th>Doctor</th><th>Status</th><th>Sent</th><th>Opened</th><th>Decision</th><th></th></tr></thead>
      <tbody>
      {% for c in cases %}
        <tr>
          <td>{{ c['case_no'] }}</td>
          <td>{{ c['specialty'] }}</td>
          <td>{{ c['doctor_display'] or c['doctor_username'] }}</td>
          <td><span class="badge">{{ c['status'] }}</span></td>
          <td>{{ c['sent_at'] }}</td>
          <td>{{ c['opened_at'] or '-' }}</td>
          <td>{{ c['decided_at'] or '-' }}</td>
          <td><a class="btn light" href="/doctor-call/case/{{ c['id'] }}">View</a></td>
        </tr>
      {% else %}
        <tr><td colspan="8">No cases yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<script>
const fallbackBox=document.getElementById("manualFallback");
const fallbackReason=document.getElementById("fallbackReason");
const fallbackSpecialty=document.getElementById("fallbackSpecialty");
const fallbackDoctor=document.getElementById("fallbackDoctor");
const manualSend=document.getElementById("manualSend");
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

function showFallback(reason){
  if(reason) fallbackReason.textContent=reason;
  fallbackBox.classList.add("show");
  fallbackBox.scrollIntoView({behavior:"smooth",block:"center"});
}

function hideFallback(){
  fallbackBox.classList.remove("show");
  fallbackSpecialty.value="";
  fallbackDoctor.value="";
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
      showFallback(data.reason||"Choose specialty and doctor.");
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
  extractionReady=false;
  clientText.value="";
  clientOcrPages.value="0";
  hideFallback();
});

smartForm.addEventListener("submit",async event=>{
  event.preventDefault();
  hideFallback();

  const ready=await prepareReportText();
  if(!ready) return;

  await postReferral(false);
});

manualSend.addEventListener("click",async ()=>{
  // Manual fallback can send even when OCR/text extraction was insufficient.
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
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<link rel="manifest" href="/manifest.json">
<title>ACT Doctor Call</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:Arial,sans-serif;background:#f4f7fb;color:#172033}
.wrap{max-width:1100px;margin:auto;padding:20px 16px}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px}
.brand h1{margin:0;font-size:27px}.sub{color:#6b7280;margin-top:4px}
.top-actions,.notification-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.btn{display:inline-block;border:0;border-radius:10px;padding:11px 15px;font-weight:bold;text-decoration:none;cursor:pointer}
.primary{background:#1f6feb;color:white}.success{background:#14804a;color:white}.light{background:white;border:1px solid #e5e7eb;color:#172033}
.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px;box-shadow:0 6px 18px rgba(0,0,0,.035)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px 8px;border-bottom:1px solid #e5e7eb;font-size:14px}
th{color:#6b7280;font-size:12px;text-transform:uppercase}.badge{display:inline-block;padding:6px 9px;border-radius:999px;background:#e8eef8;font-weight:bold;font-size:12px}
.small{color:#6b7280;font-size:13px}.notice{display:none;background:#fff3cd;border:1px solid #f1c453;color:#6d5200;padding:14px;border-radius:12px;margin-bottom:16px}
.notice.show{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
@media(max-width:700px){.topbar{align-items:flex-start;flex-direction:column}table{display:block;overflow-x:auto;white-space:nowrap}.notification-actions{width:100%}.notification-actions .btn{flex:1}}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brand">
      <h1>ACT Doctor Call</h1>
      <div class="sub">{{ display_name }} · {{ doctor_specialty }}</div>
    </div>
    <div class="top-actions"><a class="btn light" href="/logout">Logout</a></div>
  </div>

  <div id="newCaseBanner" class="notice">
    <div>
      <strong>🔔 New case received</strong>
      <div id="newCaseText" class="small" style="margin-top:4px"></div>
    </div>
    <a id="newCaseLink" class="btn primary" href="/doctor-call/doctor">Open Case</a>
  </div>

  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <div>
        <h2 style="margin:0 0 6px">Phone Notifications 🔔</h2>
        <div id="notificationStatus" class="small">Checking notification status...</div>
      </div>
      <div class="notification-actions">
        <button id="enableNotifications" class="btn primary" type="button">Enable Notifications</button>
        <button id="testNotification" class="btn light" type="button">Test Notification</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2 style="margin-top:0">Doctor Inbox</h2>
    <table>
      <thead><tr><th>Case</th><th>Specialty</th><th>Status</th><th>Sent</th><th></th></tr></thead>
      <tbody>
      {% for c in cases %}
        <tr>
          <td>{{ c['case_no'] }}</td>
          <td>{{ c['specialty'] }}</td>
          <td><span class="badge">{{ c['status'] }}</span></td>
          <td>{{ c['sent_at'] }}</td>
          <td><a class="btn primary" href="/doctor-call/case/{{ c['id'] }}">Open Case</a></td>
        </tr>
      {% else %}
        <tr><td colspan="5" class="small">No cases assigned to you.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<script>
const VAPID_PUBLIC_KEY = "{{ vapid_public_key }}";
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
    bt.textContent="Notifications Enabled ✅";
    bt.className="btn success";
  } else {
    bt.textContent="Enable Notifications";
    bt.className="btn primary";
    bt.disabled=false;
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
    setStatus("Notifications active on "+data.devices+" device(s).",true);
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
      setStatus("Notifications active on "+data.devices+" device(s).",true);
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
      setStatus("Test sent to "+data.sent+" of "+data.registered+" registered device(s).",true);
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
        setStatus("Notifications active on "+data.devices+" device(s).",true);
      }
    }else if(Notification.permission==="denied"){
      setStatus("Notifications are blocked in browser settings.");
    }else{
      setStatus("Tap Enable Notifications on this device.");
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

DOCTOR_CALL_CASE_HTML = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Case {{ case[\'case_no\'] }}</title><style>*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;background:#f4f7fb;color:#172033}.wrap{max-width:1100px;margin:auto;padding:20px 16px}.topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px}.brand h1{margin:0;font-size:27px}.sub{color:#6b7280;margin-top:4px}.top-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.btn{display:inline-block;border:0;border-radius:10px;padding:11px 15px;font-weight:bold;text-decoration:none;cursor:pointer}.primary{background:#1f6feb;color:white}.success{background:#14804a;color:white}.danger{background:#c93c37;color:white}.light{background:white;border:1px solid #e5e7eb;color:#172033}.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px;box-shadow:0 6px 18px rgba(0,0,0,.035)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}label{display:block;color:#6b7280;font-size:13px;margin-bottom:6px}input,select{width:100%;padding:12px;border:1px solid #d5dae2;border-radius:10px;background:white;font-size:15px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px 8px;border-bottom:1px solid #e5e7eb;font-size:14px}th{color:#6b7280;font-size:12px;text-transform:uppercase}.badge{display:inline-block;padding:6px 9px;border-radius:999px;background:#e8eef8;font-weight:bold;font-size:12px}.small{color:#6b7280;font-size:13px}.notice{background:#e9f8ef;border:1px solid #9bd6ad;color:#176b36;padding:11px;border-radius:10px;margin-bottom:14px}.actions{display:flex;gap:10px;flex-wrap:wrap}iframe{width:100%;height:72vh;border:1px solid #e5e7eb;border-radius:12px}.module-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}@media(max-width:700px){.grid{grid-template-columns:1fr}.topbar{align-items:flex-start;flex-direction:column}table{display:block;overflow-x:auto;white-space:nowrap}}</style></head><body><div class="wrap"><div class="topbar"><div class="brand"><h1>Case {{ case[\'case_no\'] }}</h1><div class="sub">{{ case[\'specialty\'] }} · {{ doctor_name }} · {{ case[\'status\'] }}</div></div><div class="top-actions">{% if role == \'doctor\' %}<a class="btn light" href="/doctor-call/doctor">Inbox</a>{% else %}<a class="btn light" href="/doctor-call">Cases</a>{% endif %}</div></div>{% if role == \'doctor\' and case[\'status\'] not in [\'Accepted\',\'Rejected\'] %}<div class="card"><div class="actions"><form method="post" action="/doctor-call/case/{{ case[\'id\'] }}/decision/Accepted"><button class="btn success">Accept ✅</button></form><form method="post" action="/doctor-call/case/{{ case[\'id\'] }}/decision/Rejected"><button class="btn danger">Reject ❌</button></form></div></div>{% endif %}<div class="card"><iframe src="/doctor-call/case/{{ case[\'id\'] }}/pdf"></iframe></div></div></body></html>'

DOCTOR_CALL_ADMIN_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Doctors Directory</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:Arial,sans-serif;background:#f4f7fb;color:#172033}
.wrap{max-width:1150px;margin:auto;padding:20px 16px}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px}
.brand h1{margin:0;font-size:27px}.sub{color:#6b7280;margin-top:4px}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.btn{display:inline-block;border:0;border-radius:10px;padding:10px 13px;font-weight:bold;text-decoration:none;cursor:pointer}
.primary{background:#1f6feb;color:white}.light{background:white;border:1px solid #e5e7eb;color:#172033}
.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
label{display:block;color:#6b7280;font-size:12px;margin-bottom:5px}
input{width:100%;padding:11px;border:1px solid #d5dae2;border-radius:9px;font-size:14px}
.notice{background:#e9f8ef;border:1px solid #9bd6ad;color:#176b36;padding:11px;border-radius:10px;margin-bottom:14px}
.info{background:#eef4ff;border:1px solid #c7d7ff;color:#294e9b;padding:11px;border-radius:10px;margin-bottom:14px}
.doctor{border-top:1px solid #eef1f5;padding:16px 0}.doctor:first-child{border-top:0}
.doctor-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}
.name{font-weight:800}.status{font-size:12px;padding:5px 8px;border-radius:999px;background:#eef3fa}
.specialty{font-size:16px;font-weight:700;margin-top:6px}.edit-grid{display:grid;grid-template-columns:1.2fr 1fr 1fr auto;gap:8px;align-items:end}
.small{font-size:12px;color:#6b7280}
@media(max-width:760px){.grid,.edit-grid{grid-template-columns:1fr}.topbar{align-items:flex-start;flex-direction:column}.edit-grid .btn{width:100%}}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brand">
      <h1>Doctors Directory</h1>
      <div class="sub">Doctors, specialties and availability for ACT Doctor Call.</div>
    </div>
    <div class="actions">
      <a class="btn light" href="/doctor-call">Doctor Call</a>
      <a class="btn light" href="/modules">Apps</a>
    </div>
  </div>

  {% if message %}<div class="notice">{{ message }}</div>{% endif %}
  {% if not can_manage %}
    <div class="info">Insurance view: you can see doctors and specialties. Only Admin can add, edit, activate or disable doctor accounts.</div>
  {% endif %}

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
    <h2 style="margin-top:0">Doctors</h2>
    <div class="small" style="margin-bottom:8px">Each doctor receives only cases assigned to their username.</div>

    {% for d in doctors %}
    <div class="doctor">
      <div class="doctor-head">
        <div>
          <div class="name">{{ d['display_name'] }}</div>
          <div class="small">@{{ d['username'] }}</div>
          <div class="specialty">{{ d['specialty'] }}</div>
        </div>
        <div class="status">{{ 'Active' if d['active']==1 else 'Disabled' }}</div>
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
      <form method="post" action="/doctor-call/admin/doctors/{{ d['username'] }}/toggle" style="margin-top:8px">
        <button class="btn light">{{ 'Disable Doctor' if d['active']==1 else 'Activate Doctor' }}</button>
      </form>
      {% endif %}
    </div>
    {% else %}
      <div>No doctors.</div>
    {% endfor %}
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
    },
    "doctor": {
        "password": "1234",
        "role": "doctor",
        "display": "Demo Doctor"
    }
}


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
            ACT Integrated v3.1
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
        Auto refresh every 30 seconds
    </div>

</div>

</body>

</html>
"""


# =========================
# APP LANDING
# =========================

def landing_url():
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

    if USE_POSTGRES:
        conn.execute("""
            INSERT INTO doctor_profiles (username, specialty, active)
            VALUES (?, ?, 1)
            ON CONFLICT (username) DO NOTHING
        """, ("doctor", "Cardiology"))
    else:
        conn.execute("""
            INSERT OR IGNORE INTO doctor_profiles (username, specialty, active)
            VALUES (?, ?, 1)
        """, ("doctor", "Cardiology"))

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
            "pediatric", "paediatric", "child", "infant", "newborn",
            "neonate", "neonatal", "baby", "years old child",
            "months old", "birth weight"
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


def keyword_in_text(text, keyword):
    keyword = normalize_text(keyword)
    if not keyword:
        return False

    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def specialty_rule_score(text, rule):
    score = 0
    matches = []

    for keyword in rule["keywords"]:
        if keyword_in_text(text, keyword):
            words = len(keyword.split())
            if words >= 3:
                weight = 4
            elif words == 2:
                weight = 3
            elif len(keyword) <= 4:
                weight = 2
            else:
                weight = 2

            score += weight
            matches.append(keyword)

    return score, matches


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

        # Allow labels such as "Cardiology Consultant" without treating
        # "Neurosurgery" as "General Surgery" or other substring collisions.
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
                "for automatic routing."
            )
        }

    active_doctors = list(doctors)
    if not active_doctors:
        return {
            "ok": False,
            "reason": "No active doctors are configured."
        }

    scored = []

    # Score only specialty families that actually have an active doctor
    # in the current Doctors Directory.
    for specialty, rule in SMART_REFERRAL_RULES.items():
        candidates = [
            d for d in active_doctors
            if doctor_matches_specialty(d["specialty"], specialty)
        ]
        if not candidates:
            continue

        score, matches = specialty_rule_score(text, rule)
        if score > 0:
            scored.append({
                "score": score,
                "specialty": specialty,
                "matches": matches,
                "doctors": candidates
            })

    # Also recognize an exact specialty name written in the report,
    # including custom specialties entered by Admin.
    for d in active_doctors:
        actual_specialty = normalize_text(d["specialty"])
        if len(actual_specialty) >= 4 and keyword_in_text(text, actual_specialty):
            existing = next(
                (
                    item for item in scored
                    if any(
                        normalize_text(cd["specialty"]) == actual_specialty
                        for cd in item["doctors"]
                    )
                ),
                None
            )
            if existing:
                existing["score"] += 5
                existing["matches"].append(d["specialty"])
            else:
                same_specialty = [
                    x for x in active_doctors
                    if normalize_text(x["specialty"]) == actual_specialty
                ]
                scored.append({
                    "score": 5,
                    "specialty": d["specialty"],
                    "matches": [d["specialty"]],
                    "doctors": same_specialty
                })

    scored.sort(
        key=lambda item: (
            item["score"],
            len(item["matches"])
        ),
        reverse=True
    )

    if not scored:
        return {
            "ok": False,
            "reason": (
                "The report was read, but no matching specialty from the "
                "active Doctors Directory was found."
            )
        }

    best = scored[0]
    second_score = scored[1]["score"] if len(scored) > 1 else 0

    # Specific multi-word/abbreviation evidence is enough to route.
    # Only stop when two specialties are genuinely tied at the top.
    if second_score == best["score"] and best["score"] < 6:
        top_names = ", ".join(
            item["specialty"] for item in scored[:3]
            if item["score"] == best["score"]
        )
        return {
            "ok": False,
            "reason": (
                "Routing is still ambiguous between: "
                + top_names
                + ". Please use Manual Referral for this case."
            )
        }

    selected = select_least_busy_doctor(best["doctors"])
    if not selected:
        return {
            "ok": False,
            "reason": (
                f"Detected {best['specialty']}, but no active doctor "
                "is available in that specialty."
            )
        }

    return {
        "ok": True,
        "specialty": selected["specialty"],
        "canonical_specialty": best["specialty"],
        "doctor_username": selected["username"],
        "doctor_display": selected["display_name"],
        "pending_count": selected["pending_count"],
        "matches": best["matches"][:8],
        "score": best["score"]
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
    return render_template_string(
        DOCTOR_CALL_INSURANCE_HTML,
        doctors=doctors,
        specialties=specialties,
        cases=rows,
        message=request.args.get("message"),
        role=session.get("role")
    )


@app.route("/doctor-call/smart-referral", methods=["POST"])
@login_required
def doctor_call_smart_referral():
    if session.get("role") not in ("insurance", "admin"):
        return redirect(landing_url())

    wants_json = (
        request.headers.get("X-Requested-With", "").lower() == "fetch"
    )

    def fail(message, fallback=False, status=400):
        if wants_json:
            return jsonify({
                "ok": False,
                "fallback": bool(fallback),
                "reason": message if fallback else None,
                "error": None if fallback else message
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
                routing["reason"]
                + " Choose the specialty and doctor manually below.",
                fallback=True
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
    else:
        matched_terms = ", ".join(
            routing["matches"]
        ) or "clinical pattern"
        route_label = "Smart Referral"

    device_note = (
        f" Push delivered to {push_result['sent']} of "
        f"{push_result['registered']} registered device(s)."
        if push_result["registered"] > 0
        else " Doctor has no registered notification device yet."
    )

    message = (
        f"🤖 {route_label}: {case_no} → {routing['specialty']} → "
        f"{routing['doctor_display']}. "
        f"Matched: {matched_terms}. "
        f"Merged {merged['file_count']} PDF(s), "
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

    return render_template_string(
        DOCTOR_CALL_DOCTOR_HTML,
        cases=cases,
        display_name=session.get("display", session["username"]),
        doctor_specialty=(profile["specialty"] if profile else "Doctor"),
        vapid_public_key=ensure_vapid_keys(),
        latest_case_id=(latest_row["latest_id"] if latest_row else 0)
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

    return render_template_string(
        DOCTOR_CALL_ADMIN_HTML,
        doctors=doctors,
        message=request.args.get("message"),
        can_manage=(role == "admin")
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

MANIFEST_JSON_V31 = '{\n  "name": "ACT Operations",\n  "short_name": "ACT",\n  "start_url": "/modules",\n  "display": "standalone",\n  "background_color": "#f4f7fb",\n  "theme_color": "#1f6feb",\n  "description": "ACT BedFlow and Doctor Call integrated workflow"\n}'

# =========================
# WEB PUSH / PWA
# =========================

@app.route("/sw.js")
def service_worker():
    response = Response(SW_JS_V31, mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/manifest.json")
def manifest_json():
    return Response(MANIFEST_JSON_V31, mimetype="application/manifest+json")


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
