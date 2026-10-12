import gc
import os
import shutil
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from app import create_app
from app.auth.models import User
from app.commands.define_shift import define_shift
from app.extensions import db
from app.projections import project_shift_board
from app.retrieval.models import EmbeddingProfile
from app.retrieval.store import RetrievalUnavailable


class SemanticSearchTests(unittest.TestCase):
    def setUp(self):
        self._saved_connection = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
        self.store_dir = tempfile.mkdtemp(prefix="chroma-test-")
        os.environ["VECTOR_STORE_PATH"] = self.store_dir
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self._close_chroma()
        shutil.rmtree(self.store_dir, ignore_errors=True)
        os.environ.pop("VECTOR_STORE_PATH", None)
        if self._saved_connection is not None:
            os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = self._saved_connection

    def test_manager_matches_a_shift_to_the_closer_profile(self):
        self._register("Noa Bar", "noa@example.com", "manager", "shift lead")
        cashier_id = self._register("Ayala Levi", "ayala@example.com", "employee", "cashier")["user_id"]
        lead_id = self._register("Yael Cohen", "yael@example.com", "employee", "shift lead")["user_id"]
        with self.app.app_context():
            event = define_shift(
                actor_id="manager-seed",
                actor_role="manager",
                shift_date="2026-10-19",
                start_time="17:00",
                end_time="23:00",
                required_role="cashier",
                required_headcount=1,
                note="busy evening register",
                today=date(2026, 10, 12),
            )
            project_shift_board()
            shift_id = event.aggregate_id
        self._login("noa@example.com")
        self._index(
            cashier_id,
            skills="cash handling and the register",
            shift_types="evening",
            preferences="prefers a busy evening shift",
            limitations="no overnight shifts",
        )
        self._index(
            lead_id,
            skills="team supervision",
            shift_types="morning",
            preferences="prefers opening the store",
        )
        matched = self.client.post("/queries/semantic-match", json={"shift_id": shift_id})
        self.assertEqual(matched.status_code, 200, matched.get_json())
        matches = matched.get_json()["matches"]
        self.assertEqual(matches[0]["employee_id"], cashier_id)
        self.assertIn("cashier", matched.get_json()["query"])
        self.assertGreater(matches[0]["score"], matches[1]["score"])

        context = self.client.get(
            "/queries/semantic-context",
            query_string={"employee_id": cashier_id, "q": "weekly hours limit of 42"},
        )
        self.assertEqual(context.status_code, 200, context.get_json())
        passages = context.get_json()["passages"]
        self.assertLessEqual(len(passages), 5)
        self.assertTrue(any(item.get("clause_id") == "weekly-cap" for item in passages))
        self.assertFalse(any(item.get("employee_id") == lead_id for item in passages))

        with self.app.app_context():
            stored = db.session.get(EmbeddingProfile, cashier_id)
            self.assertIn("Skills:", stored.profile_text)
            self.assertNotIn("embedding", User.__table__.columns.keys())

    def test_profile_update_replaces_the_old_vectors(self):
        self._register("Noa Bar", "noa@example.com", "manager", "shift lead")
        employee_id = self._register("Ayala Levi", "ayala@example.com", "employee", "cashier")["user_id"]
        self._login("noa@example.com")
        self._index(employee_id, skills="floral design only")
        self._index(employee_id, skills="cash handling only")
        matched = self.client.post(
            "/queries/semantic-match",
            json={"required_role": "cashier", "note": "floral design"},
        )
        passages = [
            item["passage"]
            for item in matched.get_json()["matches"]
            if item["employee_id"] == employee_id
        ]
        self.assertTrue(passages)
        self.assertFalse(any("floral" in passage for passage in passages))

    def test_employee_cannot_search_profiles_and_the_board_ignores_the_vector_store(self):
        self._register("Ayala Levi", "ayala@example.com", "employee", "cashier")
        self._login("ayala@example.com")
        denied = self.client.post("/queries/semantic-match", json={"required_role": "cashier"})
        self.assertEqual(denied.status_code, 403)
        self.client.post("/auth/logout")
        self.assertEqual(self.client.post("/commands/profiles", json={}).status_code, 401)

        self._register("Noa Bar", "noa@example.com", "manager", "shift lead")
        self._login("noa@example.com")
        with patch("app.queries.match_candidates", side_effect=RetrievalUnavailable("down")):
            failed = self.client.post("/queries/semantic-match", json={"required_role": "cashier"})
            board = self.client.get("/queries/board")
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(board.status_code, 200)
        self.assertEqual(board.get_json()["message"], "No shifts in the selected range")

    def _index(self, employee_id, **fields):
        payload = {"employee_id": employee_id, "years_experience": 4, "skills": "general"}
        payload.update(fields)
        response = self.client.post("/commands/profiles", json=payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response

    def _register(self, full_name, email, role, professional_role):
        response = self.client.post(
            "/auth/register",
            json={
                "full_name": full_name,
                "email": email,
                "password": "password1",
                "role": role,
                "professional_role": professional_role,
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def _login(self, email):
        response = self.client.post("/auth/login", json={"email": email, "password": "password1"})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response

    def _close_chroma(self):
        try:
            from chromadb.api.client import SharedSystemClient

            SharedSystemClient.clear_system_cache()
        except Exception:
            pass
        gc.collect()
