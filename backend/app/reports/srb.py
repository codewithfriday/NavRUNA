"""Monthly Safety Review Board report (FR-SPI-022/023, FR-CORE-040).

Only public (aggregated) data appears: no callsigns, registrations or operator names (SEC-001..003).
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,  # noqa: E402
                                Spacer, Table, TableStyle)
from sqlalchemy.orm import Session  # noqa: E402

from .. import config  # noqa: E402
from ..models import (Alert, EwsCell, EwsCoverage, GaApproach, GaCoverage, JobRun, Report, SpiObservation, utc_now,  # noqa: E402
                      now)
from ..modules.spi.engine import LEVEL_NAMES, approved, period_end, target_status  # noqa: E402

INK, MUTED, ACCENT = "#1b2a3a", "#5d6b7a", "#0f6e8c"
STATE_COLORS = {0: "#2e7d4f", 1: "#c58b00", 2: "#d0632a", 3: "#b3261e"}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#8a96a3", "xtick.color": MUTED, "ytick.color": MUTED})


def _png(fig):
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    b.seek(0)
    return b


def _trend_chart(obs, spi):
    fig, ax = plt.subplots(figsize=(3.4, 1.7))
    xs = [o.period_start for o in obs]
    ys = [o.value for o in obs]
    ax.plot(xs, ys, color=ACCENT, lw=1.6, marker="o", ms=2.5)
    base = [o.baseline for o in obs]
    if any(b is not None for b in base):
        ax.plot(xs, base, color="#9aa5b1", lw=1, ls="--")
    last = obs[-1]
    for k, col in (("l1", "#c58b00"), ("l2", "#d0632a"), ("l3", "#b3261e")):
        if (last.levels or {}).get(k) is not None:
            ax.axhline(last.levels[k], color=col, lw=0.8, ls=":")
    if spi.target is not None:
        ax.axhline(spi.target, color="#2e7d4f", lw=0.9)
    for o in obs:
        if o.alert_state:
            ax.plot([o.period_start], [o.value], "o", color=STATE_COLORS[o.alert_state], ms=5)
    ax.set_title(f"{spi.code} {spi.name}", fontsize=8, loc="left", color=INK)
    ax.tick_params(labelsize=6.5)
    fig.autofmt_xdate(rotation=0)
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b\n%y"))
    ax.grid(axis="y", color="#e4e8ec", lw=0.6)
    return _png(fig)


def build_srb(db: Session, month: str, user: str) -> Report:
    y, m = (int(v) for v in month.split("-"))
    m0 = date(y, m, 1)
    m1 = period_end(m0, "M")
    path = config.data_path("reports", f"SRB_{month}_{utc_now():%Y%m%d%H%M%S}.pdf")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.4, leading=13.2, textColor=colors.HexColor(INK))
    small = ParagraphStyle("s", parent=body, fontSize=7.8, leading=10, textColor=colors.HexColor(MUTED))
    h1 = ParagraphStyle("h1", parent=body, fontName="Helvetica-Bold", fontSize=14, leading=18, spaceBefore=8, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=3)
    title = ParagraphStyle("t", parent=body, fontName="Helvetica-Bold", fontSize=22, leading=26)
    cell = ParagraphStyle("c", parent=body, fontSize=7.8, leading=9.6)
    cellb = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold")
    W = A4[0] - 36 * mm
    P = lambda t, st=body: Paragraph(str(t), st)

    def table(rows, widths, num=()):
        data = [[P(c, cellb) for c in rows[0]]] + [[P(c, cell) for c in r] for r in rows[1:]]
        t = Table(data, colWidths=widths, repeatRows=1)
        st = [("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(INK)), ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#d9dee4")),
              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
        st += [("ALIGN", (c, 0), (c, -1), "RIGHT") for c in num]
        t.setStyle(TableStyle(st))
        return t

    spis = approved(db)
    story = [P("NavRUNA", ParagraphStyle("k", parent=small, fontSize=10)), Spacer(1, 2 * mm),
             P(f"Safety Review Board report<br/>{m0:%B %Y}", title), Spacer(1, 4 * mm),
             P(f"Safety performance indicators from ADS-B analysis, {m0:%d %B} to {m1:%d %B %Y}. Draft prepared {now():%d %b %Y %H:%M} UTC.", small),
             Spacer(1, 3 * mm), P(config.DISCLAIMER, small), Spacer(1, 6 * mm)]
    # 1 summary
    rows = [["SPI", "Value", "Unit", "Alert state", "Target", "Owner"]]
    n_alert = 0
    summary = []
    for s in spis:
        o = db.query(SpiObservation).filter_by(spi_code=s.code, period="M", period_start=m0, breakdown_key="").first()
        ts = target_status(db, s)
        val = f"{o.value:.2f}" if o and o.value is not None else "–"
        state = LEVEL_NAMES[o.alert_state] if o else "no data"
        n_alert += int(bool(o and o.alert_state))
        tgt = "–" if s.target is None else f"{s.target:g} ({ts['status']})"
        rows.append([f"{s.code} {s.name}", val, s.unit, f"<font color='{STATE_COLORS.get(o.alert_state, '#5d6b7a') if o else '#5d6b7a'}'>{state}</font>",
                     tgt, s.owner_unit])
        summary.append({"code": s.code, "value": o.value if o else None, "state": o.alert_state if o else None})
    n_ga = db.query(GaApproach).filter(GaApproach.day >= m0, GaApproach.day <= m1, GaApproach.outcome == "go_around").count()
    n_app = db.query(GaApproach).filter(GaApproach.day >= m0, GaApproach.day <= m1, GaApproach.outcome.in_(["landed", "go_around"])).count()
    lead = (f"In {m0:%B %Y}, {n_alert} of {len(spis)} SPIs were above an alert level. NavRUNA analysed {n_app:,} approaches at the "
            f"monitored aerodromes and detected {n_ga} go-arounds"
            + (f" ({1000 * n_ga / n_app:.1f} per 1,000 approaches)." if n_app else "."))
    story += [P("1  Summary", h1), P(lead), Spacer(1, 2 * mm),
              table(rows, [W * 0.36, W * 0.1, W * 0.16, W * 0.14, W * 0.12, W * 0.12], num=(1,))]
    # 2 trends
    story += [PageBreak(), P("2  Twelve-month trends", h1),
              P("Monthly values (blue), baseline (grey dashed), alert levels (dotted) and target (green). Coloured points are months above an alert level.", small)]
    imgs = []
    for s in spis:
        obs = (db.query(SpiObservation).filter(SpiObservation.spi_code == s.code, SpiObservation.period == "M",
                                               SpiObservation.breakdown_key == "", SpiObservation.period_start <= m0,
                                               SpiObservation.period_start > m0 - timedelta(days=370),
                                               SpiObservation.value.isnot(None)).order_by(SpiObservation.period_start).all())
        if len(obs) >= 2:
            im = Image(_trend_chart(obs, s))
            im.drawWidth, im.drawHeight = W / 2 - 4, (W / 2 - 4) * im.imageHeight / im.imageWidth
            imgs.append(im)
    grid = [imgs[i:i + 2] + [""] * (2 - len(imgs[i:i + 2])) for i in range(0, len(imgs), 2)]
    if grid:
        t = Table(grid, colWidths=[W / 2] * 2)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)
    else:
        story.append(P("Not enough monthly history yet for trend charts."))
    # 3 alerts
    alerts = db.query(Alert).filter(Alert.first_seen >= datetime(m0.year, m0.month, m0.day),
                                    Alert.first_seen < datetime(m1.year, m1.month, m1.day) + timedelta(days=1)).order_by(Alert.first_seen).all()
    rows = [["Raised", "Source", "Level", "What", "Status and action"]]
    for a in alerts:
        rows.append([f"{a.first_seen:%d %b}", a.source, str(a.level), a.public_message or a.title,
                     f"{a.status}" + (f": {a.ack_comment}" if a.ack_comment else "")])
    story += [CondPageBreak(60 * mm), P("3  Alerts raised and actions", h1),
              table(rows, [W * 0.1, W * 0.1, W * 0.07, W * 0.45, W * 0.28]) if alerts else P("No alerts were raised this month.")]
    # 4 module highlights (aggregates only)
    story += [CondPageBreak(60 * mm), P("4  Highlights by module", h1), P("Go-arounds", h2)]
    rows = [["Aerodrome", "Approaches", "Go-arounds", "Per 1,000", "Below 500 ft", "With thunderstorm"]]
    by = {}
    for a in db.query(GaApproach).filter(GaApproach.day >= m0, GaApproach.day <= m1, GaApproach.outcome.in_(["landed", "go_around"])):
        e = by.setdefault(a.icao, [0, 0, 0, 0])
        e[0] += 1
        if a.outcome == "go_around":
            e[1] += 1
            e[2] += int(a.ga_band == "low")
            e[3] += int(a.ts)
    for icao, (n, g, low, ts) in sorted(by.items()):
        rows.append([icao, f"{n:,}", str(g), f"{1000 * g / n:.2f}" if n else "–", str(low), str(ts)])
    story.append(table(rows, [W * 0.18, W * 0.16, W * 0.16, W * 0.16, W * 0.16, W * 0.18], num=(1, 2, 3, 4, 5)) if by else P("No approaches stored for this month."))
    from ..modules.tdm.core import hazard_candidates
    from ..params import current
    Pv, _ = current(db)
    hz = hazard_candidates(db, m0, m1, Pv)[:10]
    story.append(P("Route deviation hazard candidates", h2))
    if hz:
        rows = [["Route", "Critical point", "Flights", "Share flagged", "Pattern"]]
        rows += [[h["route"], h["point"], str(h["flights"]), f"{100 * h['share']:.1f} %", h["pattern"]] for h in hz]
        story.append(table(rows, [W * 0.15, W * 0.22, W * 0.12, W * 0.14, W * 0.37], num=(2, 3)))
    else:
        story.append(P("No critical point exceeded the hazard-candidate threshold."))
    n_ews = db.query(EwsCell).filter(EwsCell.day >= m0, EwsCell.day <= m1, EwsCell.alert.is_(True)).count()
    n_real = db.query(EwsCell).filter(EwsCell.day >= m0, EwsCell.day <= m1, EwsCell.alert.is_(True), EwsCell.label == "real").count()
    story.append(P("Early warning", h2))
    story.append(P(f"{n_ews} unusual cells were flagged; {n_real} were judged a real safety concern after review."))
    # 5 data completeness
    inc = db.query(GaCoverage).filter(GaCoverage.day >= m0, GaCoverage.day <= m1, GaCoverage.incomplete.is_(True)).count()
    covan = db.query(EwsCoverage).filter(EwsCoverage.day >= m0, EwsCoverage.day <= m1, EwsCoverage.anomaly.is_(True)).count()
    failed = db.query(JobRun).filter(JobRun.created_at >= datetime(m0.year, m0.month, m0.day),
                                     JobRun.created_at < datetime(m1.year, m1.month, m1.day) + timedelta(days=1),
                                     JobRun.status == "error").count()
    story += [CondPageBreak(50 * mm), P("5  Data completeness", h1),
              P(f"{inc} runway-days had incomplete low-level ADS-B coverage and were left out of go-around rates. "
                f"{covan} aerodrome-days had unusual coverage and were not scored by early warning. {failed} processing jobs failed "
                "(each is retried twice before an alert).")]
    # sign-off
    sig = Table([[P("Prepared by", cellb), P("Reviewed by", cellb), P("Released by (SRB)", cellb)],
                 [P(f"{user}<br/><br/>Date:", cell), P("<br/><br/>Date:", cell), P("<br/><br/>Date:", cell)]],
                colWidths=[W / 3] * 3, rowHeights=[None, 26 * mm])
    sig.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa5b1")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [CondPageBreak(60 * mm), P("Sign-off", h1), sig]

    def deco(c, d):
        c.saveState()
        c.setFont("Helvetica", 7.5)
        c.setFillColor(colors.HexColor(MUTED))
        c.drawString(18 * mm, 10 * mm, f"NavRUNA · SRB report {month} · for safety management only")
        c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"page {d.page}")
        c.restoreState()
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=17 * mm,
                      title=f"NavRUNA SRB report {month}", author="AirNav Indonesia").build(story, onFirstPage=deco, onLaterPages=deco)
    old = db.query(Report).filter_by(kind="srb", period=month, status="draft").all()
    for o in old:
        db.delete(o)
    rep = Report(kind="srb", period=month, title=f"SRB report {m0:%B %Y}", status="draft", path=str(path),
                 summary={"spis": summary, "approaches": n_app, "go_arounds": n_ga, "alerts": len(alerts)},
                 created_by=user, data_version=f"spi:{','.join(f'{s.code}v{s.version}' for s in spis)}")
    db.add(rep)
    db.commit()
    return rep
