from datetime import date, datetime, timedelta

WEEKLY_LIMIT = 42
REST_HOURS = 8


def parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def parse_minutes(value):
    raw = str(value or "")
    try:
        parsed = datetime.strptime(raw, "%H:%M")
    except (TypeError, ValueError):
        return None
    return parsed.hour * 60 + parsed.minute


def duration_hours(start_time, end_time):
    start = parse_minutes(start_time)
    end = parse_minutes(end_time)
    if start is None or end is None or start == end:
        raise ValueError("invalid time")
    if end < start:
        end += 24 * 60
    return round((end - start) / 60, 2)


def week_start_sunday(day):
    days_since_sunday = (day.weekday() + 1) % 7
    return day - timedelta(days=days_since_sunday)


def interval_minutes(shift_date, start_time, end_time, reference):
    start = parse_minutes(start_time)
    end = parse_minutes(end_time)
    if start is None or end is None:
        return None
    offset = (shift_date - reference).days * 24 * 60
    start += offset
    end += offset
    if end <= start:
        end += 24 * 60
    return start, end
