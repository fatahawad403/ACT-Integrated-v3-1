"""Persistent per-service access switches and non-identifying usage aggregates.

Uses the application's existing database adapter (PostgreSQL or local SQLite).
No case data, usernames, IPs or device IDs enter the usage counters. The separate
access audit records only the administrator who changes an access setting.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
import hmac
import secrets
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import Response, g, jsonify, redirect, render_template_string, request, session, url_for

EVENTS = ("visit", "launch", "install")
KSA = ZoneInfo("Asia/Riyadh")


class ControlsUnavailable(RuntimeError):
    pass


class StaleAccessState(RuntimeError):
    pass


class ControlStore:
    def __init__(self, get_db, services):
        self.get_db = get_db
        self.services = dict(services)

    @contextmanager
    def connection(self):
        conn = self.get_db()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS act_service_access (
                service_key TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
                version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS act_service_access_audit (
                service_key TEXT NOT NULL, version INTEGER NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
                changed_at TEXT NOT NULL, changed_by TEXT NOT NULL,
                PRIMARY KEY (service_key, version)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS act_service_usage_daily (
                service_key TEXT NOT NULL, day TEXT NOT NULL,
                event_name TEXT NOT NULL CHECK (event_name IN ('visit','launch','install')),
                event_count BIGINT NOT NULL CHECK (event_count >= 0),
                PRIMARY KEY (service_key, day, event_name)
            )""")
            for key in self.services:
                conn.execute("""INSERT INTO act_service_access
                    (service_key,enabled,version,updated_at,updated_by)
                    VALUES (?,1,0,?,?) ON CONFLICT (service_key) DO NOTHING""",
                    (key, datetime.now(timezone.utc).isoformat(), "initial-open-mode"))
            conn.commit()

    def actor_role(self):
        if not session.get("username"):
            return None
        if not hasattr(g, "act_actor_role"):
            with self.connection() as conn:
                row = conn.execute("SELECT role FROM users WHERE username=?",
                                   (session["username"],)).fetchone()
            g.act_actor_role = row["role"] if row else None
        return g.act_actor_role

    def state(self, service):
        if service not in self.services:
            raise ValueError("Unknown service")
        try:
            with self.connection() as conn:
                row = conn.execute("""SELECT enabled,version,updated_at,updated_by
                    FROM act_service_access WHERE service_key=?""", (service,)).fetchone()
            if row is None:
                raise ControlsUnavailable("Access setting is missing")
            return {**dict(row), "enabled": bool(row["enabled"])}
        except Exception as exc:
            raise ControlsUnavailable("Access settings are temporarily unavailable") from exc

    def set_state(self, service, enabled, version, actor):
        if service not in self.services or type(enabled) is not bool or type(version) is not int or version < 0:
            raise ValueError("Invalid access setting")
        changed_at = datetime.now(timezone.utc).isoformat()
        try:
            with self.connection() as conn:
                cursor = conn.execute("""UPDATE act_service_access
                    SET enabled=?,version=version+1,updated_at=?,updated_by=?
                    WHERE service_key=? AND version=?""",
                    (int(enabled), changed_at, actor, service, version))
                if cursor.rowcount != 1:
                    raise StaleAccessState("Access changed in another tab. Reload and try again.")
                conn.execute("""INSERT INTO act_service_access_audit
                    (service_key,version,enabled,changed_at,changed_by) VALUES (?,?,?,?,?)""",
                    (service, version + 1, int(enabled), changed_at, actor))
                conn.commit()
        except StaleAccessState:
            raise
        except Exception as exc:
            raise ControlsUnavailable("The change could not be saved") from exc
        return self.state(service)

    def record(self, service, event):
        if service not in self.services or event not in EVENTS:
            raise ValueError("Unknown usage event")
        day = datetime.now(KSA).date().isoformat()
        with self.connection() as conn:
            conn.execute("""INSERT INTO act_service_usage_daily
                (service_key,day,event_name,event_count) VALUES (?,?,?,1)
                ON CONFLICT (service_key,day,event_name)
                DO UPDATE SET event_count=act_service_usage_daily.event_count+1""", (service, day, event))
            conn.commit()

    def summary(self, service):
        today = datetime.now(KSA).date()
        first = today - timedelta(days=13)
        with self.connection() as conn:
            totals = conn.execute("""SELECT event_name,SUM(event_count) AS total
                FROM act_service_usage_daily WHERE service_key=? GROUP BY event_name""", (service,)).fetchall()
            rows = conn.execute("""SELECT day,event_name,event_count FROM act_service_usage_daily
                WHERE service_key=? AND day>=? AND day<=? ORDER BY day""",
                (service, first.isoformat(), today.isoformat())).fetchall()
        all_time = {event: 0 for event in EVENTS}
        for row in totals:
            if row["event_name"] in EVENTS:
                all_time[row["event_name"]] = int(row["total"])
        days = {(first + timedelta(days=i)).isoformat(): {event: 0 for event in EVENTS} for i in range(14)}
        for row in rows:
            if row["day"] in days and row["event_name"] in EVENTS:
                days[row["day"]][row["event_name"]] = int(row["event_count"])
        return {"all_time": all_time, "today": days[today.isoformat()],
                "daily": [{"day": day, **counts} for day, counts in days.items()], "timezone": "Asia/Riyadh"}


PANEL_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>ACT Usage &amp; Installations</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6fb;color:#243551;font:15px/1.5 system-ui,Arial,sans-serif}
main{max-width:1180px;margin:auto;padding:28px}header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:22px;border:1px solid #dfe5f3;border-radius:18px;background:linear-gradient(110deg,#eaf2ff,#f0edfb)}
h1{font-size:25px;letter-spacing:-.5px;line-height:1.25;margin:0 0 7px}header p{margin:0;color:#64738b}.back{color:#345a88;white-space:nowrap;text-decoration:none;font-weight:650}
.apps{display:grid;grid-template-columns:repeat({{ [services|length,2]|min }},minmax(0,1fr));gap:20px;margin-top:22px}.app{min-width:0;padding:22px;background:white;border:1px solid #e0e6ef;border-radius:16px}
.app-heading{display:flex;align-items:center;justify-content:space-between;gap:10px}h2{margin:0;font-size:21px}.status{padding:5px 11px;border-radius:20px;font-size:12px;font-weight:700;background:#e5f4ec;color:#28614b}.status.paused{background:#fff0db;color:#825b24}
.access-copy{color:#64738b;min-height:45px;font-size:14px;margin:13px 0}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:20px 0}.metric{min-width:0;border-radius:12px;background:#f2f6fb;padding:13px 10px}.metric span{display:block;font-size:12px;color:#60718a}.metric strong{display:block;font-size:28px;line-height:1.4;font-variant-numeric:tabular-nums}.metric small{color:#60718a;font-size:11px}
button{min-height:44px;border:0;padding:11px 17px;border-radius:10px;background:#245d9b;color:white;font:600 14px system-ui;cursor:pointer}.pause{background:#7d542c}.update-note{display:block;color:#6a778b;font-size:12px;margin-top:9px;overflow-wrap:anywhere}
.notice{margin-top:18px;padding:13px 16px;border-radius:10px;background:#e5f4ec;color:#28614b}.notice.error{background:#fff0df;color:#81582e}
details{border-top:1px solid #e7ebf2;margin-top:20px;padding-top:13px}summary{cursor:pointer;color:#536881;font-size:14px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px;margin-top:11px}th,td{padding:8px 5px;text-align:right;border-bottom:1px solid #edf0f6}th:first-child,td:first-child{text-align:left;white-space:nowrap}th{color:#65748c;font-weight:600}.notes{margin-top:20px;font-size:12px;color:#69788e;line-height:1.65}.credit{margin:25px 0 0;text-align:center;color:#718197;font-size:12px}
@media(max-width:760px){main{padding:16px}header{display:block;padding:19px}h1{font-size:22px}.back{display:inline-block;margin-top:12px}.apps{grid-template-columns:1fr}.app{padding:18px}.metric{padding:12px 8px}.metric strong{font-size:25px}.metric span{font-size:11px}}
</style></head><body><main><header><div><h1>ACT Usage &amp; Installations</h1><p>Usage counters and user access, controlled by the administrator.</p></div><a class="back" href="{{ back_url }}">Back to app →</a></header>
{% if message %}<div class="notice{% if error %} error{% endif %}" role="status">{{ message }}</div>{% endif %}
<section class="apps">{% for key,label in services.items() %}{% set state=states[key] %}{% set usage=metrics[key] %}
<article class="app" data-service="{{ key }}"><div class="app-heading"><h2>{{ label }}</h2><span class="status{% if not state.enabled %} paused{% endif %}">{{ 'Open for users' if state.enabled else 'Paused for users' }}</span></div>
<p class="access-copy">{{ 'Users can open and use this app.' if state.enabled else 'User pages, APIs and actions are blocked. Administrators can still sign in and manage the app.' }}</p>
<form method="post" action="{{ url_for('act_change_access',service=key) }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="version" value="{{ state.version }}"><input type="hidden" name="enabled" value="{{ '0' if state.enabled else '1' }}"><button class="{{ 'pause' if state.enabled else '' }}" type="submit">{{ 'Pause user access' if state.enabled else 'Reopen for users' }}</button></form>
<span class="update-note">Updated {{ state.updated_display }} Saudi time · {{ state.updated_by }}</span>
<div class="metrics">{% for event,label in [('visit','Visits'),('launch','App opens'),('install','Installs')] %}<div class="metric"><span>{{ label }}</span><strong>{{ usage.all_time[event] }}</strong><small>Today: {{ usage.today[event] }}</small></div>{% endfor %}</div>
<details><summary>Last 14 days · Saudi time</summary><div class="table-wrap"><table><thead><tr><th>Date</th><th>Visits</th><th>App opens</th><th>Installs</th></tr></thead><tbody>{% for row in usage.daily|reverse %}<tr><td>{{ row.day }}</td><td>{{ row.visit }}</td><td>{{ row.launch }}</td><td>{{ row.install }}</td></tr>{% endfor %}</tbody></table></div></details></article>{% endfor %}</section>
<p class="notes">Visits and installed-app opens count once per browser tab per Saudi day. Installs are browser-confirmed PWA installations; app-store downloads and unique device totals are not available. Counters start with this update. No patient details, usernames, IPs or device IDs are stored in these counters.</p>
<footer class="credit">Powered by Dr. Abdulfatah Suliman · Insurance Department</footer></main></body></html>"""

PAUSED_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>{{ label }} · Temporarily unavailable</title>
<style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#f3f6fb;color:#253952;font:15px/1.6 system-ui,Arial,sans-serif}main{max-width:460px;background:white;border:1px solid #e0e6f0;border-radius:20px;padding:30px;text-align:center}h1{font-size:24px;line-height:1.3;margin:0 0 16px}p{color:#62748c}a{display:inline-block;margin:8px;padding:10px 14px;border-radius:9px;background:#edf3fb;color:#28558b;text-decoration:none}small{display:block;margin-top:20px;color:#7a879b}</style></head><body><main><h1>{{ label }}</h1><p>{{ message }}</p><a href="{{ back_url }}">Back to apps</a><a href="/login">Staff / administrator sign in</a><small>Powered by Dr. Abdulfatah Suliman</small></main>
<script>const destination={{ back_url|tojson }},safeReload={{ safe_reload|tojson }};setInterval(async()=>{try{const r=await fetch('/act/status?app={{ service }}',{cache:'no-store'});if(r.ok&&(await r.json()).enabled){if(safeReload)location.reload();else location.replace(destination);}}catch(_){}},20000);</script></body></html>"""

USAGE_JS = r"""(() => {
 const tag=document.currentScript,app=tag&&tag.dataset.app;
 if(!app)return;
 const day=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Riyadh',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const send=(event,once=false)=>{
   const key='actUsage:'+app+':'+event+':'+day;
   try{if(once&&sessionStorage.getItem(key))return;}catch(_){}
   fetch('/act/usage/event',{method:'POST',credentials:'same-origin',keepalive:true,
     headers:{'Content-Type':'application/json'},body:JSON.stringify({app,event})})
     .then(r=>{if(r.ok&&once){try{sessionStorage.setItem(key,'1');}catch(_){}}}).catch(()=>{});
 };
 if(tag.dataset.paused!=='1'){
   send('visit',true);
   if(matchMedia('(display-mode: standalone)').matches||navigator.standalone===true)send('launch',true);
   addEventListener('appinstalled',()=>send('install'));
 }
 if(tag.dataset.admin!=='1')setInterval(async()=>{
   try{const r=await fetch('/act/status?app='+encodeURIComponent(app),{cache:'no-store',credentials:'same-origin'});
     if(r.ok&&!(await r.json()).enabled)location.reload();
     else if(r.status===503)location.reload();
   }catch(_){}
 },20000);
})();"""


def setup_controls(app, get_db, *, services, service_for_request, tracked_pages,
                   public_endpoints=(), back_url="/"):
    store = ControlStore(get_db, services)
    # Additive only. Existing users, beds, cases, PDFs and push keys are untouched.
    store.initialize()
    app.extensions["act_controls"] = store
    public_endpoints = set(public_endpoints)
    tracked_pages = set(tracked_pages)

    def administrator():
        try:
            return store.actor_role() == "admin"
        except Exception:
            return False

    def same_origin():
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        origin = request.headers.get("Origin")
        return not origin or urlsplit(origin).netloc == request.host

    def admin_only(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("username"):
                return redirect(url_for("login"))
            if not administrator():
                return Response("Administrator access required", status=403)
            return fn(*args, **kwargs)
        return wrapper

    def unavailable(service, *, storage_error=False):
        message = ("This app is temporarily unavailable. Please try again shortly." if storage_error
                   else "This app has been temporarily paused by the administrator.")
        if "/api/" in request.path or request.accept_mimetypes.best == "application/json":
            response = jsonify({"error": "access_unavailable" if storage_error else "app_paused", "message": message})
            response.status_code = 503
        else:
            response = Response(render_template_string(PAUSED_HTML, label=services[service], service=service,
                message=message, back_url=back_url, safe_reload=request.method == "GET"), status=503, mimetype="text/html")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Retry-After"] = "20"
        return response

    @app.before_request
    def act_access_gate():
        service = service_for_request()
        if service not in services or request.endpoint in public_endpoints:
            return None
        if administrator():
            return None
        try:
            if not store.state(service)["enabled"]:
                return unavailable(service)
        except ControlsUnavailable:
            app.logger.warning("ACT access setting unavailable; user access blocked")
            return unavailable(service, storage_error=True)
        return None

    @app.after_request
    def act_usage_script(response):
        service = service_for_request()
        if service in services or request.endpoint in {"act_control_panel", "act_change_access", "act_admin_usage", "act_status"}:
            response.headers["Cache-Control"] = "no-store"
        if (service in services and request.endpoint in tracked_pages and response.status_code == 200
                and response.mimetype == "text/html" and not response.direct_passthrough):
            html = response.get_data(as_text=True)
            script = ('<script src="/act/usage.js?v=20261025-1" data-app="' + service +
                      '" data-admin="' + ("1" if administrator() else "0") + '" defer></script>')
            if "</body>" in html:
                response.set_data(html.replace("</body>", script + "</body>", 1))
        return response

    @app.get("/admin/controls")
    @admin_only
    def act_control_panel():
        token = session.setdefault("act_controls_csrf", secrets.token_urlsafe(32))
        try:
            states = {key: store.state(key) for key in services}
            for state in states.values():
                state["updated_display"] = datetime.fromisoformat(state["updated_at"]).astimezone(KSA).strftime("%d %b %Y %H:%M")
            metrics = {key: store.summary(key) for key in services}
        except Exception:
            app.logger.exception("ACT control panel database unavailable")
            return Response("Settings and counters are temporarily unavailable. No access change was confirmed.", status=503,
                            headers={"Cache-Control": "no-store"})
        return render_template_string(PANEL_HTML, services=services, states=states, metrics=metrics,
            csrf_token=token, back_url=back_url, message="Access setting saved." if request.args.get("saved") == "1" else "", error=False)

    @app.post("/admin/controls/<service>/access")
    @admin_only
    def act_change_access(service):
        supplied = request.form.get("csrf_token", "")
        token = session.get("act_controls_csrf", "")
        if not same_origin() or not token or not hmac.compare_digest(token, supplied):
            return Response("Reload the control panel before changing access.", status=403)
        if service not in services or request.form.get("enabled") not in {"0", "1"}:
            return Response("Invalid access setting", status=400)
        try:
            version = int(request.form.get("version", ""))
            store.set_state(service, request.form["enabled"] == "1", version, session["username"])
        except ValueError:
            return Response("Invalid access version", status=400)
        except StaleAccessState as exc:
            return Response(str(exc), status=409)
        except ControlsUnavailable:
            app.logger.exception("ACT access change could not be saved")
            return Response("Access change could not be saved. Reload the panel and try again.", status=503)
        return redirect(url_for("act_control_panel", saved="1"), code=303)

    @app.get("/api/admin/usage")
    @admin_only
    def act_admin_usage():
        try:
            return jsonify({key: {"access": store.state(key), "usage": store.summary(key)} for key in services})
        except Exception:
            return jsonify({"error": "metrics_unavailable"}), 503

    @app.get("/act/status")
    def act_status():
        service = request.args.get("app", "")
        if service not in services:
            return jsonify({"error": "unknown_app"}), 404
        try:
            return jsonify({"app": service, "enabled": store.state(service)["enabled"]})
        except ControlsUnavailable:
            return jsonify({"error": "access_unavailable"}), 503

    @app.post("/act/usage/event")
    def act_usage_event():
        if not same_origin():
            return jsonify({"error": "origin_not_allowed"}), 403
        if request.content_length is not None and request.content_length > 256:
            return jsonify({"error": "invalid_event"}), 400
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {"app", "event"}:
            return jsonify({"error": "invalid_event"}), 400
        if not isinstance(data["app"], str) or not isinstance(data["event"], str) or data["app"] not in services or data["event"] not in EVENTS:
            return jsonify({"error": "invalid_event"}), 400
        try:
            if not store.state(data["app"])["enabled"] and data["event"] != "install" and not administrator():
                return jsonify({"error": "app_paused"}), 503
            store.record(data["app"], data["event"])
        except Exception:
            app.logger.warning("ACT usage counter unavailable")
            return jsonify({"error": "metrics_unavailable"}), 503
        return jsonify({"ok": True})

    @app.get("/act/usage.js")
    def act_usage_javascript():
        return Response(USAGE_JS, mimetype="application/javascript", headers={"Cache-Control": "no-cache"})

    return store
