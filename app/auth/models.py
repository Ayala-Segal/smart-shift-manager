from flask_login import UserMixin

from app.extensions import db, login_manager

ROLE_MANAGER = "manager"
ROLE_EMPLOYEE = "employee"
ROLES = frozenset({ROLE_MANAGER, ROLE_EMPLOYEE})


class User(UserMixin, db.Model):
    __tablename__ = "users"

    user_id = db.Column(db.String(64), primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False)
    professional_role = db.Column(db.String(64), nullable=False, default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    def get_id(self):
        return self.user_id

    def to_public_dict(self):
        return {
            "user_id": self.user_id,
            "full_name": self.full_name,
            "email": self.email,
            "role": self.role,
            "professional_role": self.professional_role,
            "is_active": self.is_active,
            "redirect_to": dashboard_path(self.role),
        }


def dashboard_path(role):
    if role == ROLE_MANAGER:
        return "/queries/manager-dashboard"
    return "/queries/my-dashboard"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, user_id)
