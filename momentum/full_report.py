"""The whole "No Contact > 72 Hours" report, for viewing, searching and selecting.

It is kept in memory (never written to disk). With lead_scope "all" anyone in it can be selected; with "mine" only the
account owner's own leads can. Either way every lead is opened and checked in Momentum before anything is done.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MAX_PAGE = 200
NO_SOURCE = "(No source)"
NO_PERSON = "(No salesperson)"
REACH = {"text": "c", "call": "p", "email": "e"}
# Most specific first. A column that is empty for everyone is passed over.
SOURCE_NAMES = ("lead source", "prospect source", "source", "lead source name", "source name", "ad source", "marketing source", "lead provider", "provider")


def pick_source(columns, rows, wanted=""):
    """Index of the lead source column among the source-like columns, or None. A configured name is never replaced by a guess."""
    if wanted:
        return columns.index(wanted) if wanted in columns else None
    names = [" ".join(c.lower().split()) for c in columns]
    rank = lambda i: (SOURCE_NAMES.index(names[i]) if names[i] in SOURCE_NAMES else len(SOURCE_NAMES) if "source" in names[i] else len(SOURCE_NAMES) + 1)
    for i in sorted(range(len(columns)), key=rank):
        if any(str(row[7][i] or "").strip() for row in rows):
            return i
    return None


def parse(value):
    try:
        return datetime.strptime((value or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


class FullReport:
    def __init__(self, rows, account, timezone, meta=None, source_column=""):
        meta = meta or {}
        columns = meta.get("sourceColumns") or []
        index = pick_source(columns, rows, source_column) if columns else None
        self.source_column = None if index is None else columns[index]
        self.columns, self.momentum_total = sorted(meta.get("columns") or [], key=str.lower), meta.get("momentumTotal")
        # What the report can tell about reaching someone; a column it does not have is unknown, never "no".
        self.contacts = {k: True for k, v in (meta.get("contacts") or {}).items() if v and k in REACH}
        spelling = {}  # "KBB" and "kbb " are one source, shown as first seen
        self.account, self.zone = account, ZoneInfo(timezone)
        self.rows, people = len(rows), {}
        for number, row in enumerate(rows):
            pid, name, person, last, action, created, status = [str(v or "").strip() for v in row[:7]]
            source = " ".join(str(row[7][index] or "").split()) if index is not None else ""
            source = spelling.setdefault(source.lower(), source) or NO_SOURCE
            # A row without a customer number cannot be matched to another one, so it stands alone.
            key = pid or f"row-{number}"
            # One entry per person; the account's own row wins when several people share a customer.
            # Someone listed under several sources is found under each of them.
            sources = people[key]["sources"] if key in people else []
            if source not in sources and not (source == NO_SOURCE and sources):
                sources[:] = [s for s in sources if s != NO_SOURCE] + [source]
            if key in people and not (person == account and people[key]["person"] != account):
                continue
            people[key] = {"id": pid, "name": name, "person": person, "last": parse(last), "action": action, "created": parse(created), "status": status, "sources": sources, "reach": row[8] if len(row) > 8 else "",
                           "hay": " ".join((name, pid, person, status, action)).lower()}
        for p in people.values():
            if index is not None:
                p["hay"] += " " + " ".join(p["sources"]).lower()
        self.sources = sorted({s for p in people.values() for s in p["sources"]}, key=lambda s: (s == NO_SOURCE, s.lower())) if index is not None else []
        oldest = datetime.min
        self.people = sorted(people.values(), key=lambda p: p["created"] or oldest, reverse=True)
        self.salespeople = sorted({p["person"] for p in self.people if p["person"]}, key=str.lower)
        if any(not p["person"] for p in self.people):
            self.salespeople.append(NO_PERSON)
        self.statuses = sorted({p["status"] for p in self.people if p["status"]}, key=str.lower)

    def unreachable(self, channel):
        """Ids the report itself shows no cell, phone or email for (empty when the report has no such column)."""
        if channel not in self.contacts:
            return set()
        return {p["id"] for p in self.people if REACH[channel] not in p["reach"]}

    def search(self, query="", salesperson="", status="", min_days=0, sort="created", page=1, size=50, sources=(), ids_only=False, reach=""):
        for value in (query, salesperson, status, sort, reach):
            if not isinstance(value, str) or len(value) > 200:
                raise ValueError("Invalid search")
        for value in (min_days, page, size):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("Invalid search")
        if not isinstance(sources, (list, tuple)) or len(sources) > 5000 or any(not isinstance(s, str) for s in sources):
            raise ValueError("Invalid search")
        # A source that is gone after a new sync is dropped, and the dashboard is told which ones are still in use.
        chosen = set(sources) & set(self.sources)
        size = max(1, min(size, MAX_PAGE))
        now = datetime.now(self.zone).replace(tzinfo=None)
        words = query.lower().split()
        limit = now - timedelta(days=max(0, min(min_days, 36500)))
        found = [p for p in self.people
                 if (not salesperson or (p["person"] or NO_PERSON) == salesperson) and (not status or p["status"] == status)
                 and (min_days <= 0 or (p["last"] is not None and p["last"] <= limit))
                 and (reach not in self.contacts or REACH[reach] in p["reach"])
                 and all(w in p["hay"] for w in words)]
        # The count beside each source follows the other filters, so it is what ticking that source adds.
        counts = dict.fromkeys(self.sources, 0)
        if counts:
            for p in found:
                for s in p["sources"]:
                    counts[s] += 1
        if chosen:
            found = [p for p in found if not chosen.isdisjoint(p["sources"])]
        if ids_only:
            return [p["id"] for p in found]
        if sort == "name":
            found.sort(key=lambda p: p["name"].lower())
        elif sort == "last":  # longest without contact first, unknown dates at the end
            found.sort(key=lambda p: (p["last"] is None, p["last"] or now))
        pages = max(1, -(-len(found) // size))
        page = max(1, min(page, pages))

        def age(moment):
            return None if moment is None else round((now - moment).total_seconds() / 3600, 1)
        shown = [{"id": p["id"], "name": p["name"], "salesperson": p["person"], "status": p["status"], "action": p["action"], "source": ", ".join(p["sources"]) if self.source_column else None, "mine": p["person"] == self.account,
                  "last": p["last"].strftime("%Y-%m-%d") if p["last"] else None, "age_hours": age(p["last"]), "created_hours": age(p["created"])}
                 for p in found[(page - 1) * size:page * size]]
        return {"contacts": sorted(self.contacts), "rows": shown, "matches": len(found), "people": len(self.people), "report_rows": self.rows, "page": page, "pages": pages,
                "salespeople": self.salespeople, "statuses": self.statuses,
                "sources": [{"name": s, "count": counts[s]} for s in self.sources], "chosen": sorted(chosen), "source_column": self.source_column,
                "columns": self.columns if not self.source_column else [], "momentum_total": self.momentum_total}
