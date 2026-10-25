"""Run with python -m unittest discover -s tests -v; all data stays temporary."""
import importlib.util
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {"DATABASE_URL": "", "ACT_LOCAL_DB_PATH": str(Path(cls.temp.name) / "test.db"),
            "ACT_VAPID_RUNTIME_KEY": str(Path(cls.temp.name) / "test-vapid.pem")})
        cls.env.start()
        spec = importlib.util.spec_from_file_location("act_test_application", ROOT / "app.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.app = cls.module.app
        cls.app.config.update(TESTING=True, SECRET_KEY="temporary-controls-test-key")
        cls.store = cls.app.extensions["act_controls"]
        from werkzeug.security import generate_password_hash
        cls.test_hash = generate_password_hash("test-only-unused-password")

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.temp.cleanup()

    def setUp(self):
        conn = self.module.get_db()
        conn.execute("DELETE FROM act_service_access_audit")
        conn.execute("DELETE FROM act_service_usage_daily")
        conn.execute("UPDATE act_service_access SET enabled=1,version=0,updated_by='test'")
        for user, role in [("qa_admin", "admin"), ("qa_insurance", "insurance"), ("qa_icu", "icu"), ("qa_doctor", "doctor")]:
            conn.execute("""INSERT INTO users (username,password_hash,role,display_name,updated_at)
                VALUES (?,?,?,?,?) ON CONFLICT (username) DO UPDATE SET role=excluded.role""",
                (user, self.test_hash, role, "QA user", "2026-01-01"))
        if "doctor_call" in self.store.services:
            conn.execute("""INSERT INTO doctor_profiles (username,specialty,active) VALUES ('qa_doctor','Internal Medicine',1)
                ON CONFLICT (username) DO NOTHING""")
        conn.commit()
        conn.close()
        self.admin = self.client("qa_admin", "admin")
        self.insurance = self.client("qa_insurance", "insurance")
        self.icu = self.client("qa_icu", "icu")
        self.doctor = self.client("qa_doctor", "doctor")
        self.guest = self.app.test_client()
        panel = self.admin.get("/admin/controls")
        self.assertEqual(panel.status_code, 200)
        self.token = re.search(r'name="csrf_token" value="([^"]+)"', panel.text)[1]

    def client(self, username, role):
        client = self.app.test_client()
        with client.session_transaction() as data:
            data.update(username=username, role=role, display="QA user")
        return client

    def change(self, service="bedflow", enabled=False, version=None, **kwargs):
        if version is None:
            version = self.store.state(service)["version"]
        return self.admin.post(f"/admin/controls/{service}/access", data={
            "csrf_token": self.token, "enabled": "1" if enabled else "0", "version": str(version)}, **kwargs)

    def query(self, sql, params=()):
        conn = self.module.get_db()
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def test_only_current_administrators_can_read_or_change_controls(self):
        self.assertEqual(self.guest.get("/admin/controls").status_code, 302)
        for user in (self.insurance, self.icu, self.doctor):
            self.assertEqual(user.get("/admin/controls").status_code, 403)
            self.assertEqual(user.get("/api/admin/usage").status_code, 403)
            self.assertEqual(user.post("/admin/controls/bedflow/access", data={"enabled":"0","version":"0","csrf_token":self.token}).status_code, 403)
        conn = self.module.get_db()
        conn.execute("UPDATE users SET role='insurance' WHERE username='qa_admin'")
        conn.commit(); conn.close()
        self.assertEqual(self.admin.post("/admin/controls/bedflow/access", data={"enabled":"0","version":"0","csrf_token":self.token}).status_code, 403)
        self.assertTrue(self.store.state("bedflow")["enabled"])

    def test_csrf_origin_and_post_only(self):
        self.assertEqual(self.admin.get("/admin/controls/bedflow/access").status_code, 405)
        self.assertEqual(self.admin.post("/admin/controls/bedflow/access", data={"enabled":"0","version":"0"}).status_code, 403)
        self.assertEqual(self.change(headers={"Origin":"https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.change(headers={"Sec-Fetch-Site":"cross-site"}).status_code, 403)
        self.assertTrue(self.store.state("bedflow")["enabled"])
        self.assertEqual(self.query("SELECT * FROM act_service_access_audit"), [])

    def test_pause_blocks_existing_sessions_actions_and_keeps_admin_login(self):
        before = self.query("SELECT id,status,last_updated FROM beds ORDER BY id")
        self.assertEqual(self.change().status_code, 303)
        for user in (self.insurance, self.icu):
            page = user.get("/")
            self.assertEqual(page.status_code, 503)
            self.assertIn("temporarily paused", page.text)
            self.assertEqual(page.headers["Cache-Control"], "no-store")
            self.assertEqual(user.post("/update/1", data={"status":"Occupied"}).status_code, 503)
            blocked_post = user.post("/confirm")
            self.assertEqual(blocked_post.status_code, 503)
            self.assertIn("safeReload=false", blocked_post.text)
            self.assertNotIn("&#34;", blocked_post.text)
            self.assertEqual(user.get("/login").status_code, 302)
        self.assertEqual(self.admin.get("/").status_code, 200)
        self.assertEqual(self.admin.get("/admin/controls").status_code, 200)
        self.assertEqual(self.guest.get("/login").status_code, 200)
        self.assertEqual(before, self.query("SELECT id,status,last_updated FROM beds ORDER BY id"))
        self.assertEqual(self.guest.get("/act/status?app=bedflow").json, {"app":"bedflow","enabled":False})
        self.assertEqual(self.change(enabled=True).status_code, 303)
        self.assertEqual(self.insurance.get("/").status_code, 200)

    def test_stale_form_cannot_overwrite_a_new_setting(self):
        self.assertEqual(self.change(version=0).status_code, 303)
        self.assertEqual(self.change(enabled=True,version=0).status_code, 409)
        self.assertFalse(self.store.state("bedflow")["enabled"])
        rows = self.query("SELECT * FROM act_service_access_audit")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["changed_by"], "qa_admin")
        self.assertEqual(self.admin.post("/admin/controls/bedflow/access", data={"csrf_token":self.token,"enabled":"no","version":"1"}).status_code,400)
        self.assertEqual(self.admin.post("/admin/controls/bedflow/access", data={"csrf_token":self.token,"enabled":"1","version":"invalid"}).status_code,400)

    def test_closed_state_survives_initialization_and_new_store(self):
        from act_controls import ControlStore
        self.change()
        second = ControlStore(self.module.get_db, self.store.services)
        second.initialize()
        self.assertFalse(second.state("bedflow")["enabled"])
        self.assertEqual(second.state("bedflow")["version"], 1)

    def test_db_outage_does_not_restore_open_access(self):
        from act_controls import ControlsUnavailable
        self.assertEqual(self.insurance.get("/").status_code, 200)
        with patch.object(self.store, "get_db", side_effect=RuntimeError("test database unavailable")):
            self.assertEqual(self.insurance.get("/").status_code, 503)
            self.assertEqual(self.guest.get("/act/status?app=bedflow").status_code, 503)
            self.assertEqual(self.guest.get("/login").status_code, 200)
            with self.assertRaises(ControlsUnavailable):
                self.store.state("bedflow")

    def test_metrics_are_aggregate_and_use_saudi_date(self):
        for event in ("visit","launch","install"):
            self.assertEqual(self.guest.post("/act/usage/event",json={"app":"bedflow","event":event}).status_code, 200)
        data = self.admin.get("/api/admin/usage").json["bedflow"]["usage"]
        self.assertEqual(data["all_time"], {"visit":1,"launch":1,"install":1})
        self.assertEqual(data["today"], data["all_time"])
        self.assertEqual(len(data["daily"]),14)
        rows = self.query("SELECT * FROM act_service_usage_daily")
        self.assertEqual(set(rows[0]), {"service_key","day","event_name","event_count"})
        self.assertEqual(rows[0]["day"], datetime.now(ZoneInfo("Asia/Riyadh")).date().isoformat())
        self.assertNotIn("updated_by", self.guest.get("/act/status?app=bedflow").json)

    def test_metrics_reject_extra_sensitive_fields_malformed_types_and_origins(self):
        for data in [{"app":"bedflow","event":"visit","patient":"not-real"},
                     {"app":[],"event":"visit"},{"app":"bedflow","event":{}},
                     {"app":"missing","event":"visit"},{"app":"bedflow","event":"arbitrary"},["bedflow","visit"]]:
            self.assertEqual(self.guest.post("/act/usage/event",json=data).status_code,400)
        self.assertEqual(self.guest.post("/act/usage/event",json={"app":"bedflow","event":"visit"},headers={"Origin":"https://unrelated.example"}).status_code,403)
        self.assertEqual(self.query("SELECT * FROM act_service_usage_daily"),[])
        self.assertEqual(self.guest.get("/act/status?app=missing").status_code,404)

    def test_atomic_counter_increments(self):
        def add(_):
            for __ in range(8):
                self.store.record("bedflow","visit")
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(add,range(5)))
        self.assertEqual(self.store.summary("bedflow")["all_time"]["visit"],40)

    def test_closed_app_stops_usage_but_records_real_install_events(self):
        self.change()
        self.assertEqual(self.guest.post("/act/usage/event",json={"app":"bedflow","event":"visit"}).status_code,503)
        self.assertEqual(self.guest.post("/act/usage/event",json={"app":"bedflow","event":"install"}).status_code,200)
        self.assertEqual(self.store.summary("bedflow")["all_time"],{"visit":0,"launch":0,"install":1})

    def test_correct_service_scripts_and_admin_navigation(self):
        page = self.admin.get("/")
        self.assertIn('href="/admin/controls"', self.admin.get("/modules" if "doctor_call" in self.store.services else "/").text)
        self.assertIn('data-app="bedflow"',page.text)
        self.assertIn('data-admin="1"',page.text)
        self.assertIn('data-admin="0"',self.insurance.get("/").text)
        script = self.guest.get("/act/usage.js").text
        self.assertIn("appinstalled",script)
        self.assertIn("Asia/Riyadh",script)
        self.assertNotIn("beforeinstallprompt",script,"Install-button clicks are not counted as successful installations")
        path = "/bedflow/install" if "doctor_call" in self.store.services else "/install"
        self.assertIn('data-app="bedflow"',self.guest.get(path).text)

    def test_doctor_call_and_bedflow_pause_independently(self):
        if "doctor_call" not in self.store.services:
            self.skipTest("Standalone BedFlow has one service")
        self.assertEqual(self.change(service="doctor_call").status_code,303)
        self.assertEqual(self.insurance.get("/").status_code,200)
        self.assertEqual(self.insurance.get("/doctor-call").status_code,503)
        self.assertEqual(self.doctor.get("/doctor-call/doctor").status_code,503)
        blocked = self.doctor.get("/doctor-call/api/doctor/latest")
        self.assertEqual(blocked.status_code,503)
        self.assertEqual(blocked.json["error"],"app_paused")
        self.assertEqual(self.doctor.post("/doctor-call/api/push/subscribe",json={}).status_code,503)
        self.assertEqual(self.doctor.get("/doctor-call/case/987/pdf").status_code,503)
        self.assertEqual(self.doctor.get("/doctor-call/uploads/not-real.pdf").status_code,503)
        self.assertEqual(self.admin.get("/doctor-call").status_code,200)
        self.assertEqual(self.insurance.get("/modules").status_code,200)
        self.assertEqual(self.guest.get("/doctor-call/install").status_code,200)
        self.assertEqual(self.change(service="doctor_call",enabled=True).status_code,303)
        self.assertIn('data-app="doctor_call"',self.doctor.get("/doctor-call/doctor").text)
        self.assertEqual(self.change(service="bedflow").status_code,303)
        self.assertEqual(self.insurance.get("/doctor-call").status_code,200)
        self.assertEqual(self.insurance.get("/").status_code,503)
        self.assertIn("push",self.guest.get("/sw.js").text)


if __name__ == "__main__":
    unittest.main()
