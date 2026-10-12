import os
import unittest

from app import create_app
from app.auth.models import User
from app.extensions import db


class AuthTests(unittest.TestCase):
    def setUp(self):
        self._saved_connection = os.environ.pop("SOMEE_SQLSERVER_CONNECTION_STRING", None)
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
        if self._saved_connection is not None:
            os.environ["SOMEE_SQLSERVER_CONNECTION_STRING"] = self._saved_connection

    def test_register_and_login_routes_each_role_to_its_dashboard(self):
        manager = self.client.post(
            "/auth/register",
            json={
                "full_name": "Noa Bar",
                "email": "Noa@Example.com",
                "password": "password1",
                "role": "manager",
                "professional_role": "shift lead",
            },
        )
        self.assertEqual(manager.status_code, 201)
        self.assertEqual(manager.get_json()["redirect_to"], "/queries/manager-dashboard")
        self.assertNotIn("password_hash", manager.get_json())

        employee = self.client.post(
            "/auth/register",
            json={
                "full_name": "Ayala Levi",
                "email": "ayala@example.com",
                "password": "password1",
                "role": "employee",
                "professional_role": "cashier",
            },
        )
        self.assertEqual(employee.status_code, 201)
        self.assertEqual(employee.get_json()["redirect_to"], "/queries/my-dashboard")

        signed_in = self.client.post(
            "/auth/login",
            json={"email": "noa@example.com", "password": "password1"},
        )
        self.assertEqual(signed_in.status_code, 200)
        self.assertEqual(signed_in.get_json()["role"], "manager")
        dashboard = self.client.get("/queries/manager-dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.get_json()["user_id"], signed_in.get_json()["user_id"])
        self.assertEqual(self.client.get("/queries/my-dashboard").status_code, 403)

    def test_wrong_credentials_and_inactive_account_share_one_message(self):
        self.client.post(
            "/auth/register",
            json={
                "full_name": "Ayala Levi",
                "email": "ayala@example.com",
                "password": "password1",
                "role": "employee",
                "professional_role": "cashier",
            },
        )
        wrong = self.client.post(
            "/auth/login",
            json={"email": "ayala@example.com", "password": "wrong-password"},
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.get_json(), {"error": "Sign-in failed."})

        with self.app.app_context():
            user = User.query.filter_by(email="ayala@example.com").one()
            user.is_active = False
            db.session.commit()
        inactive = self.client.post(
            "/auth/login",
            json={"email": "ayala@example.com", "password": "password1"},
        )
        self.assertEqual(inactive.get_json(), wrong.get_json())

    def test_employee_cannot_open_the_manager_dashboard(self):
        self.client.post(
            "/auth/register",
            json={
                "full_name": "Ayala Levi",
                "email": "ayala@example.com",
                "password": "password1",
                "role": "employee",
                "professional_role": "cashier",
            },
        )
        self.client.post(
            "/auth/login",
            json={"email": "ayala@example.com", "password": "password1"},
        )
        denied = self.client.get("/queries/manager-dashboard")
        self.assertEqual(denied.status_code, 403)
        me = self.client.get("/auth/me")
        self.assertEqual(me.get_json()["user_id"], self.client.get("/queries/my-dashboard").get_json()["user_id"])
        self.client.post("/auth/logout")
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_registration_reports_field_errors_and_duplicate_email(self):
        missing = self.client.post(
            "/auth/register",
            json={"full_name": "", "email": "not-an-email", "password": "short", "role": "guest"},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("email", missing.get_json()["errors"])
        self.assertIn("role", missing.get_json()["errors"])

        payload = {
            "full_name": "Ayala Levi",
            "email": "ayala@example.com",
            "password": "password1",
            "role": "employee",
            "professional_role": "cashier",
        }
        self.assertEqual(self.client.post("/auth/register", json=payload).status_code, 201)
        duplicate = self.client.post("/auth/register", json=payload)
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("email", duplicate.get_json()["errors"])
