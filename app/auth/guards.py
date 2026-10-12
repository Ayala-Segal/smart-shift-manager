from functools import wraps

from flask import jsonify
from flask_login import current_user


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({"error": "Sign-in required."}), 401
            if current_user.role not in roles:
                return jsonify({"error": "You cannot open this page."}), 403
            return view(*args, **kwargs)

        return wrapped

    return decorator


def session_user_id():
    if not current_user.is_authenticated:
        return None
    return current_user.user_id
