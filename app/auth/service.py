from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.auth.models import ROLES, ROLE_EMPLOYEE, User
from app.extensions import db

SIGN_IN_FAILED = "Sign-in failed."


class RegistrationError(Exception):
    def __init__(self, errors):
        self.errors = errors


def ensure_accounts_table():
    User.__table__.create(db.engine, checkfirst=True)


def register_user(full_name, email, password, role, professional_role):
    ensure_accounts_table()
    errors = _registration_errors(full_name, email, password, role, professional_role)
    if errors:
        raise RegistrationError(errors)
    user = User(
        user_id="user-" + uuid4().hex,
        full_name=full_name.strip(),
        email=email.strip().lower(),
        password_hash=generate_password_hash(password),
        role=role,
        professional_role=(professional_role or "").strip(),
        is_active=True,
    )
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise RegistrationError({"email": "An account with this email already exists."})
    return user


def authenticate(email, password):
    ensure_accounts_table()
    normalized = (email or "").strip().lower()
    user = User.query.filter_by(email=normalized).one_or_none()
    if user is None or not user.is_active:
        return None
    if not check_password_hash(user.password_hash, password or ""):
        return None
    return user


def _registration_errors(full_name, email, password, role, professional_role):
    errors = {}
    name = (full_name or "").strip()
    if not name:
        errors["full_name"] = "Enter your full name."
    elif len(name) > 120:
        errors["full_name"] = "Full name must be 120 characters or fewer."
    normalized_email = (email or "").strip().lower()
    if "@" not in normalized_email or "." not in normalized_email.split("@")[-1]:
        errors["email"] = "Enter a valid email address."
    elif len(normalized_email) > 255:
        errors["email"] = "Email must be 255 characters or fewer."
    if not password or len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."
    if role not in ROLES:
        errors["role"] = "Role must be manager or employee."
    profession = (professional_role or "").strip()
    if role == ROLE_EMPLOYEE and not profession:
        errors["professional_role"] = "Enter the professional role for this employee."
    elif len(profession) > 64:
        errors["professional_role"] = "Professional role must be 64 characters or fewer."
    return errors
