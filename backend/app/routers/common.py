"""Helpers shared by the routers: dates, CSV/XLSX export, identifier masking (SEC-001..003)."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from ..models import User, utc_now
from ..security import can_see_identifiers


def parse_day(s: str | None, default: date | None = None) -> date:
    if not s:
        if default is None:
            raise HTTPException(400, "Date required (YYYY-MM-DD)")
        return default
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        raise HTTPException(400, f"Not a date: {s}")


def period(d0: str | None, d1: str | None, days: int = 30) -> tuple[date, date]:
    end = parse_day(d1, utc_now().date() - timedelta(days=1))
    start = parse_day(d0, end - timedelta(days=days - 1))
    if start > end:
        raise HTTPException(400, "Start date is after end date")
    return start, end


class Masker:
    """Replaces identifiers for users who may not see them; operators become 'Operator A, B…' (stable per response)."""

    def __init__(self, user: User):
        self.full = can_see_identifiers(user)
        self.ops: dict[str, str] = {}

    def op(self, code: str | None) -> str:
        if self.full or not code:
            return code or ""
        if code not in self.ops:
            n = len(self.ops)
            self.ops[code] = "Operator " + (chr(65 + n) if n < 26 else f"{n + 1}")
        return self.ops[code]

    def ident(self, v: str | None) -> str:
        return (v or "") if self.full else "—"


def export(rows: list[dict], fmt: str, name: str):
    """CSV or XLSX download of a list of dicts (FR-CORE-033)."""
    cols = list(rows[0].keys()) if rows else ["empty"]
    if fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) if not isinstance(r.get(c), (dict, list)) else str(r.get(c)) for c in cols])
        b = io.BytesIO()
        wb.save(b)
        return Response(b.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
