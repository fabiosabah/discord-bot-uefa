from datetime import datetime, timezone, timedelta

SECONDS = 1
MINUTE = 60 * SECONDS
HOUR = 60 * MINUTE
DAY = 24 * HOUR
MONTH = 30 * DAY
YEAR = 365 * DAY
BRAZIL_TZ = timezone(timedelta(hours=-3))


def relative_time(iso_string: str):
    dt = datetime.fromisoformat(iso_string)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    diff = now - dt

    seconds = int(diff.total_seconds())

    if seconds < MINUTE:
        return "agora mesmo"

    elif seconds < HOUR:
        minutes = seconds // MINUTE
        return f"há {minutes} minuto{'s' if minutes > 1 else ''}"

    elif seconds < DAY:
        hours = seconds // HOUR
        return f"há {hours} hora{'s' if hours > 1 else ''}"

    elif seconds < MONTH:
        days = seconds // DAY
        return f"há {days} dia{'s' if days > 1 else ''}"

    elif seconds < YEAR:
        months = seconds // MONTH
        return f"há {months} mês{'es' if months > 1 else ''}"

    else:
        years = seconds // YEAR
        return f"há {years} ano{'s' if years > 1 else ''}"
  

  
def format_brazil_time(iso_string: str | None):
    if not iso_string:
        return None
    
    dt = datetime.fromisoformat(iso_string)


    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    dt_br = dt.astimezone(BRAZIL_TZ)