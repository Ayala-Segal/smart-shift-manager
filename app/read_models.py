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
