from datetime import datetime, timezone

from app.auth.models import ROLE_EMPLOYEE, User
from app.extensions import db
from app.retrieval.chunking import split_paragraphs
from app.retrieval.models import EmbeddingProfile
from app.retrieval.policies import AGREEMENT_CLAUSES
from app.retrieval.store import RetrievalUnavailable, has_clause, query_documents, upsert_documents

TOP_PASSAGES = 5
FIELD_LIMIT = 500


class ProfileRejected(Exception):
    """The profile cannot be indexed."""


def save_employee_profile(
    *,
    store_path,
    employee_id,
    years_experience,
    skills,
    shift_types="",
    preferences="",
    limitations="",
    manager_notes="",
):
    employee = _employee(employee_id)
    text = _profile_text(
        role_name=employee.professional_role,
        years_experience=_years(years_experience),
        skills=_required_text(skills, "Skills"),
        shift_types=_optional_text(shift_types, "Shift types"),
        preferences=_optional_text(preferences, "Preferences"),
        limitations=_optional_text(limitations, "Limitations"),
        manager_notes=_optional_text(manager_notes, "Manager notes"),
    )
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        raise ProfileRejected("The profile has no text to index.")
    _replace_employee_vectors(store_path, employee.user_id, paragraphs)
    _store_profile_text(employee.user_id, text)
    return {"employee_id": employee.user_id, "paragraphs": len(paragraphs)}


def match_candidates(store_path, query_text, *, limit=TOP_PASSAGES):
    ensure_agreement_indexed(store_path)
    rows = query_documents(
        store_path,
        query_text,
        limit=max(limit * 4, limit),
        where={"document_type": "employee_profile"},
    )
    best = {}
    for row in rows:
        employee_id = row["employee_id"]
        if not employee_id:
            continue
        current = best.get(employee_id)
        if current is None or _closer(row, current):
            best[employee_id] = row
    ranked = sorted(best.values(), key=_distance)
    return [_public_match(row) for row in ranked[:limit]]


def passages_for_employee(store_path, employee_id, query_text, *, limit=TOP_PASSAGES):
    if not employee_id:
        raise ProfileRejected("An employee is required.")
    ensure_agreement_indexed(store_path)
    rows = query_documents(
        store_path,
        query_text,
        limit=limit,
        where={
            "$or": [
                {"employee_id": employee_id},
                {"document_type": "policy"},
            ]
        },
    )
    visible = []
    for row in rows:
        if row["document_type"] == "policy" or row["employee_id"] == employee_id:
            visible.append(_public_passage(row))
        if len(visible) >= limit:
            break
    return visible


def ensure_agreement_indexed(store_path, *, force=False):
    if not force and has_clause(store_path, AGREEMENT_CLAUSES[0]["clause_id"]):
        return 0
    stored = 0
    for clause in AGREEMENT_CLAUSES:
        paragraphs = split_paragraphs(clause["text"])
        ids = ["policy:{0}:{1}".format(clause["clause_id"], index) for index in range(len(paragraphs))]
        metadatas = [
            {
                "document_type": "policy",
                "employee_id": "",
                "clause_id": clause["clause_id"],
            }
            for _ in paragraphs
        ]
        upsert_documents(
            store_path,
            ids=ids,
            documents=paragraphs,
            metadatas=metadatas,
            delete_where={"clause_id": clause["clause_id"]},
        )
        stored += len(paragraphs)
    return stored


def describe_shift(*, required_role="", note="", start_time="", end_time="", shift_date=""):
    parts = []
    role = (required_role or "").strip()
    if role:
        parts.append("{0} role".format(role))
    day_part = _day_part(start_time)
    if day_part:
        parts.append("{0} shift".format(day_part))
    if (note or "").strip():
        parts.append(note.strip())
    if shift_date:
        parts.append(str(shift_date))
    if (start_time or "").strip() and (end_time or "").strip():
        parts.append("{0}-{1}".format(start_time.strip(), end_time.strip()))
    text = ", ".join(parts)
    if not text:
        raise ProfileRejected("A shift role or note is required for semantic search.")
    return text


def _replace_employee_vectors(store_path, employee_id, paragraphs):
    ids = ["profile:{0}:{1}".format(employee_id, index) for index in range(len(paragraphs))]
    metadatas = [
        {
            "document_type": "employee_profile",
            "employee_id": employee_id,
            "clause_id": "",
        }
        for _ in paragraphs
    ]
    upsert_documents(
        store_path,
        ids=ids,
        documents=paragraphs,
        metadatas=metadatas,
        delete_where={"employee_id": employee_id},
    )


def _store_profile_text(employee_id, text):
    EmbeddingProfile.__table__.create(db.engine, checkfirst=True)
    row = db.session.get(EmbeddingProfile, employee_id)
    if row is None:
        row = EmbeddingProfile(employee_id=employee_id)
        db.session.add(row)
    row.profile_text = text
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()


def _employee(employee_id):
    if not (employee_id or "").strip():
        raise ProfileRejected("An employee is required.")
    employee = db.session.get(User, employee_id.strip())
    if employee is None or employee.role != ROLE_EMPLOYEE or not employee.is_active:
        raise ProfileRejected("The employee does not exist.")
    if not (employee.professional_role or "").strip():
        raise ProfileRejected("The employee has no professional role.")
    return employee


def _profile_text(*, role_name, years_experience, skills, shift_types, preferences, limitations, manager_notes):
    lines = [
        "Role: {0}".format(role_name),
        "Experience: {0} years".format(years_experience),
        "Skills: {0}".format(skills),
    ]
    if shift_types:
        lines.append("Shift types: {0}".format(shift_types))
    if preferences:
        lines.append("Preferences: {0}".format(preferences))
    if limitations:
        lines.append("Limitations: {0}".format(limitations))
    if manager_notes:
        lines.append("Manager notes: {0}".format(manager_notes))
    return "\n".join(lines)


def _years(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileRejected("Experience must be a whole number of years.")
    if value < 0:
        raise ProfileRejected("Experience must be a whole number of years.")
    return value


def _required_text(value, label):
    text = _optional_text(value, label)
    if not text:
        raise ProfileRejected("{0} are required.".format(label))
    return text


def _optional_text(value, label):
    text = " ".join(str(value or "").split())
    if len(text) > FIELD_LIMIT:
        raise ProfileRejected("{0} must be at most {1} characters.".format(label, FIELD_LIMIT))
    return text


def _day_part(start_time):
    raw = (start_time or "").strip()
    if not raw or ":" not in raw:
        return ""
    try:
        hour = int(raw.split(":", 1)[0])
    except ValueError:
        return ""
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _closer(candidate, current):
    return _distance(candidate) < _distance(current)


def _distance(row):
    if row["distance"] is None:
        return 1.0
    return row["distance"]


def _public_match(row):
    user = db.session.get(User, row["employee_id"])
    return {
        "employee_id": row["employee_id"],
        "full_name": user.full_name if user else "",
        "professional_role": user.professional_role if user else "",
        "passage": row["passage"],
        "score": _score(row["distance"]),
    }


def _public_passage(row):
    payload = {
        "document_type": row["document_type"],
        "passage": row["passage"],
        "score": _score(row["distance"]),
    }
    if row["document_type"] == "policy":
        payload["clause_id"] = row["clause_id"]
    else:
        payload["employee_id"] = row["employee_id"]
    return payload


def _score(distance):
    if distance is None:
        return 0.0
    return round(max(0.0, 1.0 - float(distance)), 4)
