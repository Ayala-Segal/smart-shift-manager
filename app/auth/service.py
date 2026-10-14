from uuid import uuid4

from sqlalchemy import inspect, text
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
    _ensure_account_column(
        "locality",
        "ALTER TABLE users ADD COLUMN locality VARCHAR(80) NOT NULL DEFAULT 'Israel'",
        "ALTER TABLE users ADD locality VARCHAR(80) NOT NULL CONSTRAINT DF_users_locality DEFAULT 'Israel'",
    )
    _ensure_account_column(
        "notify_email",
        "ALTER TABLE users ADD COLUMN notify_email BOOLEAN NOT NULL DEFAULT 1",
        "ALTER TABLE users ADD notify_email BIT NOT NULL CONSTRAINT DF_users_notify_email DEFAULT 1",
    )


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


def update_profile(user, *, full_name, professional_role, locality, notify_email, current_password, new_password):
    """Update the signed-in account. Email stays the sign-in name."""
    ensure_accounts_table()
    errors = {}
    name = (full_name or "").strip()
    if not name:
        errors["full_name"] = "Enter your full name."
    elif len(name) > 120:
        errors["full_name"] = "Full name must be 120 characters or fewer."
    profession = (professional_role or "").strip()
    if user.role == ROLE_EMPLOYEE and not profession:
        errors["professional_role"] = "Enter the professional role for this employee."
    elif len(profession) > 64:
        errors["professional_role"] = "Professional role must be 64 characters or fewer."
    place = (locality or "").strip() or "Israel"
    if len(place) > 80:
        errors["locality"] = "Locality must be 80 characters or fewer."
    replacement = new_password or ""
    if replacement or (current_password or ""):
        if not check_password_hash(user.password_hash, current_password or ""):
            errors["current_password"] = "The current password does not match."
        elif len(replacement) < 8:
            errors["new_password"] = "Password must be at least 8 characters."
    if errors:
        raise RegistrationError(errors)
    user.full_name = name
    user.professional_role = profession
    user.locality = place
    user.notify_email = bool(notify_email)
    if replacement:
        user.password_hash = generate_password_hash(replacement)
    db.session.commit()
    return user


def _ensure_account_column(name, sqlite_sql, sqlserver_sql):
    columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
    if name in columns:
        return
    statement = sqlite_sql if db.engine.dialect.name == "sqlite" else sqlserver_sql
    db.session.execute(text(statement))
    db.session.commit()


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
