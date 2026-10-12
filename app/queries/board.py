from app.extensions import db
from app.read_models import ShiftBoardRow


def get_shift_board():
    return (
        db.session.query(ShiftBoardRow)
        .order_by(ShiftBoardRow.shift_date, ShiftBoardRow.start_time, ShiftBoardRow.shift_id)
        .all()
    )


def shift_board_payload(rows):
    return {
        "shifts": [
            {
                "shift_id": row.shift_id,
                "shift_date": row.shift_date.isoformat(),
                "weekday": row.weekday,
                "start_time": row.start_time,
                "end_time": row.end_time,
                "required_role": row.required_role,
                "assigned_count": row.assigned_count,
                "required_headcount": row.required_headcount,
                "status": row.status,
                "waiting_candidates": row.waiting_candidates,
                "note": row.note,
            }
            for row in rows
        ]
    }
