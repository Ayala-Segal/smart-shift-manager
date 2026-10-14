from app.extensions import db


class ShiftBoardRow(db.Model):
    __tablename__ = "read_shift_board"

    shift_id = db.Column(db.String(64), primary_key=True)
    shift_date = db.Column(db.Date, nullable=False)
    weekday = db.Column(db.String(16), nullable=False)
    start_time = db.Column(db.String(5), nullable=False)
    end_time = db.Column(db.String(5), nullable=False)
    required_role = db.Column(db.String(64), nullable=False)
    assigned_count = db.Column(db.Integer, nullable=False)
    required_headcount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False)
    waiting_candidates = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(300), nullable=False)


class ProjectorCheckpoint(db.Model):
    __tablename__ = "projector_checkpoints"

    projector_name = db.Column(db.String(64), primary_key=True)
    last_event_id = db.Column(db.BigInteger, nullable=False)


class ShiftRequestRow(db.Model):
    __tablename__ = "read_shift_requests"

    request_id = db.Column(db.String(64), primary_key=True)
    shift_id = db.Column(db.String(64), nullable=False, index=True)
    request_type = db.Column(db.String(32), nullable=False)
    employee_id = db.Column(db.String(64), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    professional_role = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(64), nullable=False)
    note = db.Column(db.String(300), nullable=False, default="")
    decision_note = db.Column(db.String(300), nullable=False, default="")
    replacement_employee_id = db.Column(db.String(64), nullable=True)
    replacement_name = db.Column(db.String(120), nullable=True)


class EmployeeHoursRow(db.Model):
    __tablename__ = "read_employee_hours"

    employee_id = db.Column(db.String(64), primary_key=True)
    week_start = db.Column(db.Date, primary_key=True)
    approved_hours = db.Column(db.Float, nullable=False)


class ApprovedAssignmentRow(db.Model):
    __tablename__ = "read_approved_assignments"

    assignment_id = db.Column(db.String(64), primary_key=True)
    employee_id = db.Column(db.String(64), nullable=False, index=True)
    shift_id = db.Column(db.String(64), nullable=False, index=True)
    shift_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.String(5), nullable=False)
    end_time = db.Column(db.String(5), nullable=False)
    hours = db.Column(db.Float, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class RequestRecommendationRow(db.Model):
    __tablename__ = "read_request_recommendations"

    request_id = db.Column(db.String(64), primary_key=True)
    shift_id = db.Column(db.String(64), nullable=False, index=True)
    employee_id = db.Column(db.String(64), nullable=False)
    request_type = db.Column(db.String(32), nullable=False)
    decision = db.Column(db.String(16), nullable=False)
    rationale = db.Column(db.String(500), nullable=False)
    hours_warning = db.Column(db.Boolean, nullable=False)


class EvaluationLock(db.Model):
    __tablename__ = "evaluation_locks"

    request_id = db.Column(db.String(64), primary_key=True)
    run_id = db.Column(db.String(64), nullable=False)
    locked_at = db.Column(db.DateTime, nullable=False)


class AvailabilityConstraintRow(db.Model):
    __tablename__ = "read_availability_constraints"

    constraint_id = db.Column(db.String(64), primary_key=True)
    employee_id = db.Column(db.String(64), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    constraint_type = db.Column(db.String(32), nullable=False)
    note = db.Column(db.String(300), nullable=False, default="")
