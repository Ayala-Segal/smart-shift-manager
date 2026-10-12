from datetime import date
from functools import wraps

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from app.auth.models import ROLE_EMPLOYEE, ROLE_MANAGER
from app.auth.service import SIGN_IN_FAILED, RegistrationError, authenticate, register_user
from app.commands.decide_request import decide_request
from app.commands.define_shift import CommandRejected, define_shift
from app.commands.submit_requests import request_assignment, request_swap, submit_availability
from app.event_store import ASSIGNMENT_APPROVED, SWAP_APPROVED
from app.extensions import db
from app.notifications.dispatch import dispatch_notifications
from app.projections import WAITING_FOR_AGENT, project_shift_board
from app.queries.board import board_filters_from_request, get_shift_board, recommendations_for, requests_for_shift, shift_detail_payload
from app.queries.dashboard import (
    constraints_for,
    employee_dashboard,
    manager_dashboard_summary,
    my_approved_shifts,
    other_employees,
    pending_decisions,
)
from app.read_models import ShiftBoardRow
from app.screens import screens_bp


def screen_role(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("screens.sign_in"))
            if current_user.role not in roles:
                return render_template("forbidden.html"), 403
            return view(*args, **kwargs)

        return wrapped

    return decorator


@screens_bp.get("/sign-in")
def sign_in():
    if current_user.is_authenticated:
        return redirect(_home())
    return render_template("sign_in.html", error="", email="")


@screens_bp.post("/sign-in")
def sign_in_submit():
    email = request.form.get("email") or ""
    user = authenticate(email, request.form.get("password"))
    if user is None:
        return render_template("sign_in.html", error=SIGN_IN_FAILED, email=email), 401
    login_user(user)
    return redirect(_home())


@screens_bp.get("/register")
def register():
    if current_user.is_authenticated:
        return redirect(_home())
    return render_template("register.html", errors={}, form=_register_form())


@screens_bp.post("/register")
def register_submit():
    form = _register_form()
    try:
        register_user(
            full_name=form["full_name"],
            email=form["email"],
            password=request.form.get("password") or "",
            role=form["role"],
            professional_role=form["professional_role"],
        )
    except RegistrationError as error:
        return render_template("register.html", errors=error.errors, form=form), 400
    flash("Account created. Sign in to continue.")
    return redirect(url_for("screens.sign_in"))


@screens_bp.post("/sign-out")
def sign_out():
    logout_user()
    return redirect(url_for("screens.sign_in"))


@screens_bp.get("/manager")
@screen_role(ROLE_MANAGER)
def manager_home():
    start, end, error = _optional_range()
    summary = manager_dashboard_summary(start=start, end=end) if error is None else None
    decisions = []
    if summary is not None:
        decisions = pending_decisions(
            start=date.fromisoformat(summary["start"]),
            end=date.fromisoformat(summary["end"]),
        )
    return render_template(
        "manager_dashboard.html",
        summary=summary,
        decisions=decisions,
        error=error or "",
        failed_id="",
        field_error="",
    ), (400 if error else 200)


@screens_bp.get("/board")
@screen_role(ROLE_MANAGER, ROLE_EMPLOYEE)
def board():
    return _board_page(search=False)


@screens_bp.get("/search")
@screen_role(ROLE_EMPLOYEE)
def search():
    return _board_page(search=True)


@screens_bp.get("/shifts/new")
@screen_role(ROLE_MANAGER)
def new_shift():
    return render_template("define_shift.html", errors={}, form={}, error="")


@screens_bp.post("/shifts/new")
@screen_role(ROLE_MANAGER)
def create_shift():
    form = {
        "shift_date": request.form.get("shift_date") or "",
        "start_time": _clock(request.form.get("start_time")),
        "end_time": _clock(request.form.get("end_time")),
        "required_role": request.form.get("required_role") or "",
        "required_headcount": request.form.get("required_headcount") or "",
        "note": request.form.get("note") or "",
        "crosses_midnight": request.form.get("crosses_midnight") == "yes",
    }
    try:
        headcount = int(form["required_headcount"])
    except ValueError:
        return render_template(
            "define_shift.html",
            errors={"required_headcount": "The required headcount must be at least 1."},
            form=form,
            error="",
        ), 400
    try:
        event = define_shift(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            shift_date=form["shift_date"],
            start_time=form["start_time"],
            end_time=form["end_time"],
            required_role=form["required_role"],
            required_headcount=headcount,
            note=form["note"],
            crosses_midnight=form["crosses_midnight"],
        )
    except CommandRejected as error:
        message = str(error)
        return render_template(
            "define_shift.html",
            errors={_field_for(message): message} if _field_for(message) else {},
            form=form,
            error="" if _field_for(message) else message,
        ), 400
    project_shift_board()
    flash("Shift {0} is open.".format(event.aggregate_id))
    return redirect(url_for("screens.shift_card", shift_id=event.aggregate_id))


@screens_bp.get("/shifts/<shift_id>")
@screen_role(ROLE_MANAGER, ROLE_EMPLOYEE)
def shift_card(shift_id):
    row = db.session.get(ShiftBoardRow, shift_id)
    if row is None:
        return render_template("forbidden.html", message="The shift does not exist."), 404
    detail = shift_detail_payload(
        row,
        requests_for_shift(shift_id),
        current_user,
        recommendations_for(shift_id),
    )
    return render_template("shift_card.html", detail=detail, error="")


@screens_bp.post("/shifts/<shift_id>/request")
@screen_role(ROLE_EMPLOYEE)
def request_shift(shift_id):
    note = request.form.get("note") or ""
    try:
        event = request_assignment(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            shift_id=shift_id,
            note=note,
        )
    except CommandRejected as error:
        row = db.session.get(ShiftBoardRow, shift_id)
        if row is None:
            return render_template("forbidden.html", message="The shift does not exist."), 404
        detail = shift_detail_payload(
            row,
            requests_for_shift(shift_id),
            current_user,
            recommendations_for(shift_id),
        )
        return render_template("shift_card.html", detail=detail, error=str(error)), 400
    project_shift_board()
    flash("Request {0} is {1}.".format(event.aggregate_id, WAITING_FOR_AGENT))
    return redirect(url_for("screens.shift_card", shift_id=shift_id))


@screens_bp.get("/decisions")
@screen_role(ROLE_MANAGER)
def decisions():
    return render_template(
        "decisions.html",
        decisions=pending_decisions(),
        error="",
        failed_id="",
        field_error="",
    )


@screens_bp.post("/decisions/<request_id>")
@screen_role(ROLE_MANAGER)
def decide(request_id):
    action = request.form.get("action") or ""
    note = request.form.get("note") or ""
    try:
        event = decide_request(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            request_id=request_id,
            action=action,
            note=note,
        )
    except CommandRejected as error:
        message = str(error)
        return render_template(
            "decisions.html",
            decisions=pending_decisions(),
            error="" if "note" in message.lower() else message,
            failed_id=request_id,
            field_error=message if "note" in message.lower() else "",
        ), 400
    project_shift_board()
    dispatch_notifications()
    status = "Approved" if event.event_type in {ASSIGNMENT_APPROVED, SWAP_APPROVED} else "Rejected"
    flash("Request {0} is {1}.".format(event.aggregate_id, status))
    return redirect(url_for("screens.decisions"))


@screens_bp.get("/me")
@screen_role(ROLE_EMPLOYEE)
def my_home():
    return render_template("employee_dashboard.html", summary=employee_dashboard(current_user.user_id))


@screens_bp.get("/constraints")
@screen_role(ROLE_EMPLOYEE)
def constraints():
    return render_template(
        "constraints.html",
        constraints=constraints_for(current_user.user_id),
        errors={},
        form={},
        error="",
    )


@screens_bp.post("/constraints")
@screen_role(ROLE_EMPLOYEE)
def add_constraint():
    form = {
        "date": request.form.get("date") or "",
        "start_date": request.form.get("start_date") or "",
        "end_date": request.form.get("end_date") or "",
        "constraint_type": request.form.get("constraint_type") or "",
        "note": request.form.get("note") or "",
    }
    try:
        event = submit_availability(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            constraint_type=form["constraint_type"],
            note=form["note"],
            shift_date=form["date"] or None,
            start_date=form["start_date"] or None,
            end_date=form["end_date"] or None,
        )
    except CommandRejected as error:
        message = str(error)
        return render_template(
            "constraints.html",
            constraints=constraints_for(current_user.user_id),
            errors={_constraint_field(message): message} if _constraint_field(message) else {},
            form=form,
            error="" if _constraint_field(message) else message,
        ), 400
    project_shift_board()
    flash("Constraint {0} is stored.".format(event.aggregate_id))
    return redirect(url_for("screens.constraints"))


@screens_bp.get("/swaps")
@screen_role(ROLE_EMPLOYEE)
def swaps():
    return render_template(
        "swap.html",
        shifts=my_approved_shifts(current_user.user_id),
        employees=other_employees(current_user.user_id),
        error="",
        form={},
    )


@screens_bp.post("/swaps")
@screen_role(ROLE_EMPLOYEE)
def request_swap_screen():
    form = {
        "shift_id": request.form.get("shift_id") or "",
        "replacement_employee_id": request.form.get("replacement_employee_id") or "",
        "note": request.form.get("note") or "",
    }
    try:
        event = request_swap(
            actor_id=current_user.user_id,
            actor_role=current_user.role,
            shift_id=form["shift_id"],
            replacement_employee_id=form["replacement_employee_id"],
            note=form["note"],
        )
    except CommandRejected as error:
        return render_template(
            "swap.html",
            shifts=my_approved_shifts(current_user.user_id),
            employees=other_employees(current_user.user_id),
            error=str(error),
            form=form,
        ), 400
    project_shift_board()
    flash("Request {0} is {1}.".format(event.aggregate_id, WAITING_FOR_AGENT))
    return redirect(url_for("screens.my_home"))


def _board_page(search):
    args = request.args.to_dict(flat=True)
    if search:
        args["open_only"] = "1"
    try:
        filters = board_filters_from_request(args, current_user)
        rows = get_shift_board(**filters)
    except ValueError as error:
        return render_template(
            "board.html",
            shifts=[],
            message="",
            error=str(error),
            search=search,
            filters=args,
        ), 400
    message = "" if rows else "No shifts in the selected range"
    return render_template(
        "board.html",
        shifts=rows,
        message=message,
        error="",
        search=search,
        filters=args,
    )


def _optional_range():
    try:
        start = _posted_date("start")
        end = _posted_date("end")
    except ValueError as error:
        return None, None, str(error)
    return start, end, None


def _posted_date(name):
    value = (request.args.get(name) or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Dates must use YYYY-MM-DD.") from error


def _home():
    if current_user.role == ROLE_MANAGER:
        return url_for("screens.manager_home")
    return url_for("screens.my_home")


def _register_form():
    return {
        "full_name": request.form.get("full_name") or "",
        "email": request.form.get("email") or "",
        "role": request.form.get("role") or "employee",
        "professional_role": request.form.get("professional_role") or "",
    }


def _clock(value):
    text = (value or "").strip()
    return text[:5] if len(text) >= 5 else text


def _field_for(message):
    text = message.lower()
    if "date" in text:
        return "shift_date"
    if "time" in text or "midnight" in text:
        return "start_time"
    if "headcount" in text:
        return "required_headcount"
    if "role" in text:
        return "required_role"
    if "note" in text:
        return "note"
    return None


def _constraint_field(message):
    text = message.lower()
    if "type" in text:
        return "constraint_type"
    if "note" in text:
        return "note"
    if "date" in text:
        return "date"
    return None
