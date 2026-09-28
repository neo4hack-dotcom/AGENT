"""Business-day arithmetic for the calendars a CIB desk lives by.

"The last business day of May", "T+2 from Thursday", "was 1 May a trading day?" — questions
a small model answers from memory, and gets wrong exactly where it matters: Easter moves,
Good Friday is a TARGET2 holiday but not a French bank holiday, 29 May 2026 is the month-end
and 31 May is a Sunday. Computed here, from rules, with no data file to keep up to date.

Calendars: TARGET2 (euro settlement, also Euronext Paris trading days), UK (England &
Wales bank holidays), US (NYSE trading days), WEEKDAYS (Saturdays and Sundays only).
Holidays a source publishes can be added per call.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

CALENDARS = ("TARGET2", "UK", "US", "WEEKDAYS")
ALIASES = {"TARGET": "TARGET2", "EUR": "TARGET2", "EURO": "TARGET2", "FR": "TARGET2", "PARIS": "TARGET2",
           "EURONEXT": "TARGET2", "ECB": "TARGET2", "GB": "UK", "LONDON": "UK", "LSE": "UK", "GBP": "UK",
           "NYSE": "US", "NY": "US", "USD": "US", "NEW YORK": "US", "NONE": "WEEKDAYS", "WEEKENDS": "WEEKDAYS"}


def calendar_name(name: str) -> str:
    key = (name or "TARGET2").strip().upper()
    key = ALIASES.get(key, key)
    if key not in CALENDARS:
        raise ValueError(f"Unknown calendar '{name}'. Use one of {', '.join(CALENDARS)}.")
    return key


def easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = (h + ell - 7 * m + 114) % 31 + 1
    return dt.date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The n-th `weekday` (0 = Monday) of a month; n = -1 for the last."""
    if n > 0:
        first = dt.date(year, month, 1)
        return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed_us(day: dt.date) -> dt.date:
    if day.weekday() == 5:
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + dt.timedelta(days=1)
    return day


@lru_cache(maxsize=256)
def holidays(calendar: str, year: int) -> dict[dt.date, str]:
    """Named holidays of a calendar in a year (weekdays only matter; weekends are closed anyway)."""
    cal = calendar_name(calendar)
    out: dict[dt.date, str] = {}
    if cal == "WEEKDAYS":
        return out
    e = easter(year)
    if cal == "TARGET2":
        out[dt.date(year, 1, 1)] = "New Year's Day"
        out[e - dt.timedelta(days=2)] = "Good Friday"
        out[e + dt.timedelta(days=1)] = "Easter Monday"
        out[dt.date(year, 5, 1)] = "Labour Day"
        out[dt.date(year, 12, 25)] = "Christmas Day"
        out[dt.date(year, 12, 26)] = "Boxing Day (St Stephen's)"
    elif cal == "UK":
        new_year = dt.date(year, 1, 1)
        out[new_year + dt.timedelta(days={5: 2, 6: 1}.get(new_year.weekday(), 0))] = "New Year's Day"
        out[e - dt.timedelta(days=2)] = "Good Friday"
        out[e + dt.timedelta(days=1)] = "Easter Monday"
        out[_nth_weekday(year, 5, 0, 1)] = "Early May bank holiday"
        out[_nth_weekday(year, 5, 0, -1)] = "Spring bank holiday"
        out[_nth_weekday(year, 8, 0, -1)] = "Summer bank holiday"
        christmas, boxing = dt.date(year, 12, 25), dt.date(year, 12, 26)
        if christmas.weekday() == 5:          # Sat: Mon and Tue
            christmas, boxing = dt.date(year, 12, 27), dt.date(year, 12, 28)
        elif christmas.weekday() == 6:        # Sun: Tue for Christmas, Mon for Boxing Day
            christmas, boxing = dt.date(year, 12, 27), dt.date(year, 12, 26)
        elif boxing.weekday() == 5:           # Fri Christmas, Sat Boxing: Mon
            boxing = dt.date(year, 12, 28)
        out[christmas] = "Christmas Day"
        out[boxing] = "Boxing Day"
    elif cal == "US":
        new_year = dt.date(year, 1, 1)
        if new_year.weekday() != 5:           # NYSE does not observe a Saturday New Year on Friday
            out[_observed_us(new_year)] = "New Year's Day"
        out[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
        out[_nth_weekday(year, 2, 0, 3)] = "Washington's Birthday"
        out[e - dt.timedelta(days=2)] = "Good Friday"
        out[_nth_weekday(year, 5, 0, -1)] = "Memorial Day"
        if year >= 2022:
            out[_observed_us(dt.date(year, 6, 19))] = "Juneteenth"
        out[_observed_us(dt.date(year, 7, 4))] = "Independence Day"
        out[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
        out[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
        out[_observed_us(dt.date(year, 12, 25))] = "Christmas Day"
    return out


class Calendar:
    def __init__(self, name: str = "TARGET2", extra: list[str] | None = None) -> None:
        self.name = calendar_name(name)
        self.extra = {parse(d): "listed holiday" for d in (extra or [])}

    def holiday_name(self, day: dt.date) -> str:
        return self.extra.get(day) or holidays(self.name, day.year).get(day, "")

    def is_business_day(self, day: dt.date) -> bool:
        return day.weekday() < 5 and not self.holiday_name(day)

    def roll(self, day: dt.date, direction: int) -> dt.date:
        while not self.is_business_day(day):
            day += dt.timedelta(days=direction)
        return day

    def add(self, day: dt.date, n: int) -> dt.date:
        step = 1 if n >= 0 else -1
        remaining = abs(n)
        while remaining:
            day += dt.timedelta(days=step)
            if self.is_business_day(day):
                remaining -= 1
        return day

    def between(self, start: dt.date, end: dt.date) -> int:
        """Business days after `start` up to and including `end` (the usual day count)."""
        lo, hi, sign = (start, end, 1) if start <= end else (end, start, -1)
        count, day = 0, lo
        while day < hi:
            day += dt.timedelta(days=1)
            count += self.is_business_day(day)
        return sign * count

    def month_end(self, year: int, month: int) -> dt.date:
        last = (dt.date(year, month + 1, 1) if month < 12 else dt.date(year + 1, 1, 1)) - dt.timedelta(days=1)
        return self.roll(last, -1)


def parse(value: str) -> dt.date:
    text = str(value).strip()[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{value}' is not a date — use YYYY-MM-DD.")


def describe(cal: Calendar, day: dt.date) -> str:
    why = cal.holiday_name(day) or ("weekend" if day.weekday() >= 5 else "")
    return (f"{day.isoformat()} ({day.strftime('%A')}): "
            + ("business day" if not why else f"not a business day — {why}"))


_MONTHS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
           "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
           "décembre": 12, "decembre": 12, "january": 1, "february": 2, "march": 3, "april": 4,
           "may": 5, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
           "november": 11, "december": 12}


def dates_in(text: str) -> list[tuple[str, dt.date, str]]:
    """The dates a question names: (as written, date, "day" | "month_end")."""
    import re
    lowered = (text or "").lower()
    months = "|".join(sorted(_MONTHS, key=len, reverse=True))
    found: list[tuple[str, dt.date, str]] = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", lowered):
        try:
            found.append((m.group(0), dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), "day"))
        except ValueError:
            pass
    for m in re.finditer(rf"\b(\d{{1,2}})(?:er|st|nd|rd|th)? ({months}) (\d{{4}})\b", lowered):
        try:
            found.append((m.group(0), dt.date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))), "day"))
        except ValueError:
            pass
    for m in re.finditer(rf"\b({months}) (\d{{1,2}})(?:st|nd|rd|th)?,? (\d{{4}})\b", lowered):
        try:
            found.append((m.group(0), dt.date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2))), "day"))
        except ValueError:
            pass
    for m in re.finditer(rf"\b(?:fin|end of|à fin|a fin)(?: de)? ({months}) (\d{{4}})\b", lowered):
        month, year = _MONTHS[m.group(1)], int(m.group(2))
        found.append((m.group(0), dt.date(year, month, 1), "month_end"))
    seen, out = set(), []
    for item in found:
        if (item[1], item[2]) not in seen:
            seen.add((item[1], item[2]))
            out.append(item)
    return out[:8]


def facts_for(text: str, calendar: str = "TARGET2") -> list[str]:
    """One computed line per date the question names — weekday, business day or not, the
    business days around it — so that no model has to know what day 1 May 2026 was."""
    cal = Calendar(calendar)
    lines = []
    for written, day, kind in dates_in(text):
        if kind == "month_end":
            end = cal.month_end(day.year, day.month)
            lines.append(f"\"{written}\": last business day of the month ({cal.name}) = "
                         f"{end.isoformat()} ({end.strftime('%A')}).")
            continue
        why = cal.holiday_name(day) or ("weekend" if day.weekday() >= 5 else "")
        if why:
            before, after = cal.roll(day, -1), cal.roll(day, 1)
            lines.append(f"\"{written}\" = {day.isoformat()}, a {day.strftime('%A')}: NOT a business day "
                         f"({cal.name}: {why}) — no close, no fixing that day. Previous business day "
                         f"{before.isoformat()} ({before.strftime('%A')}), next {after.isoformat()} "
                         f"({after.strftime('%A')}).")
        else:
            lines.append(f"\"{written}\" = {day.isoformat()}, a {day.strftime('%A')}: a business day ({cal.name}).")
    return lines
