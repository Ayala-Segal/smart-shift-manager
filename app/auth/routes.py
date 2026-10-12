from flask import jsonify, request
from flask_login import current_user, login_user, logout_user

from app.auth import auth_bp
from app.auth.service import SIGN_IN_FAILED, RegistrationError, authenticate, register_user


@auth_bp.get("/status")
def status():
    return {"blueprint": "auth", "ready": True}


@auth_bp.post("/register")
def register():
    body = request.get_json(silent=True) or {}
    try:
        user = register_user(
            full_name=body.get("full_name"),
            email=body.get("email"),
            password=body.get("password"),
            role=body.get("role"),
            professional_role=body.get("professional_role"),
        )
    except RegistrationError as error:
        status_code = 409 if "already exists" in error.errors.get("email", "") else 400
        return jsonify({"errors": error.errors}), status_code
    return jsonify(user.to_public_dict()), 201


@auth_bp.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    user = authenticate(body.get("email"), body.get("password"))
    if user is None:
        return jsonify({"error": SIGN_IN_FAILED}), 401
    login_user(user)
    return jsonify(user.to_public_dict())


@auth_bp.post("/logout")
def logout():
    logout_user()
    return jsonify({"status": "signed out"})


@auth_bp.get("/me")
def me():
    if not current_user.is_authenticated:
        return jsonify({"error": "Sign-in required."}), 401
    return jsonify(current_user.to_public_dict())
