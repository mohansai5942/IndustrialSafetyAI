"""Incident persistence, evidence storage and SIH-style PDF reporting."""
from pathlib import Path
import sqlite3
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
from reportlab.lib.units import mm
from xml.sax.saxutils import escape
from sif_engine import analyze_report

class IncidentManager:
    def __init__(self, base: Path):
        self.base = Path(base)
        self.db_path = self.base / "safety.db"
        self.evidence_dir = self.base / "evidence"
        self.reports_dir = self.base / "reports"
        self.evidence_dir.mkdir(exist_ok=True)
        self.reports_dir.mkdir(exist_ok=True)
        self._init_db()
        self._backfill_reports()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self):
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS incidents(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, incident_type TEXT NOT NULL,
                zone TEXT NOT NULL, workers INTEGER NOT NULL, risk INTEGER NOT NULL,
                severity TEXT NOT NULL, confidence REAL NOT NULL,
                gas REAL, temperature REAL, pressure REAL, equipment REAL,
                evidence TEXT, status TEXT, response TEXT,
                incident_description TEXT, unsafe_acts TEXT, unsafe_conditions TEXT,
                near_miss_indicators TEXT, sif_precursors TEXT, sif_classification TEXT,
                sif_score INTEGER DEFAULT 0, sif_evidence TEXT, recommendations TEXT,
                source TEXT DEFAULT 'CCTV / AI Vision'
            )""")
            existing = {r[1] for r in con.execute("PRAGMA table_info(incidents)").fetchall()}
            additions = {
                "incident_description":"TEXT", "unsafe_acts":"TEXT", "unsafe_conditions":"TEXT",
                "near_miss_indicators":"TEXT", "sif_precursors":"TEXT", "sif_classification":"TEXT",
                "sif_score":"INTEGER DEFAULT 0", "sif_evidence":"TEXT", "recommendations":"TEXT",
                "source":"TEXT DEFAULT 'CCTV / AI Vision'"
            }
            for col, typ in additions.items():
                if col not in existing:
                    con.execute(f"ALTER TABLE incidents ADD COLUMN {col} {typ}")

    def _backfill_reports(self):
        """Ensure previously stored incidents also have downloadable PDFs."""
        try:
            with self._connect() as con:
                ids = [r[0] for r in con.execute("SELECT id FROM incidents ORDER BY id").fetchall()]
            for incident_id in ids:
                out = self.reports_dir / f"incident_report_{incident_id}.pdf"
                if not out.exists():
                    self.make_pdf(incident_id)
        except Exception:
            # Reporting must never prevent the safety dashboard from starting.
            pass

    def save_incident(self, data: dict, frame=None) -> int:
        evidence_name = ""
        # Evidence photographs are reserved for CRITICAL incidents only.
        if frame is not None and str(data.get("severity", "")).upper() == "CRITICAL":
            import cv2
            evidence_name = "incident_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".jpg"
            cv2.imwrite(str(self.evidence_dir / evidence_name), frame)

        text = data.get("incident_description") or data.get("report") or data.get("incident_type", "")
        sif = analyze_report(text)

        # CCTV operational risk and SIF precursor likelihood are different signals.
        # Do not turn every camera event into the same MEDIUM(32) SIF result.
        # For CCTV records, use a small transparent mapping based on the actual
        # detected event; manual reports continue to use the NLP engine unchanged.
        if str(data.get("source", "")).startswith("CCTV"):
            cctv_map = {
                "Fire-like visual": (85, "Potential SIF precursor", "Fire / explosion potential",
                                     "Verify ignition-source control, isolate the area where required and follow site emergency procedure."),
                "Possible worker fall": (70, "Potential SIF precursor", "Fall / dropped-person potential",
                                          "Verify fall protection, access control and scene condition before work resumes."),
                "Restricted-zone entry": (55, "Potential SIF precursor", "Restricted-area exposure",
                                            "Stop the exposure and verify access authorization, isolation and barricading."),
                "Gas hazard": (65, "Potential SIF precursor", "Loss-of-containment / gas exposure",
                                "Verify gas reading, isolate the source where safe and follow the site's gas-response procedure."),
                "Pressure abnormality": (50, "SIF precursor watch", "High-energy process condition",
                                           "Verify the pressure condition and keep personnel clear until the process is confirmed safe."),
                "Temperature hazard": (45, "SIF precursor watch", "High-energy process condition",
                                         "Verify the temperature condition and maintain safe separation from the affected equipment."),
                "Equipment anomaly": (35, "SIF precursor watch", "Critical equipment condition",
                                        "Inspect the equipment and verify safeguards before returning it to service."),
                "Aerosol / smoke-like event": (0, "No SIF precursor identified", "Visual anomaly",
                                                 "Verify the source of the visual signal; no SIF precursor is assigned from this signal alone."),
                "Helmet not verified": (0, "No SIF precursor identified", "PPE control observation",
                                         "Verify required PPE before the task continues."),
                "Cap not verified": (0, "No SIF precursor identified", "PPE control observation",
                                      "Verify required PPE before the task continues."),
            }
            score, classification, category, recommendation = cctv_map.get(
                data.get("incident_type"), (30, "SIF precursor watch", "Safety anomaly",
                                              "Verify the scene and applicable site controls before action."))
            sif.score = score
            sif.level = "CRITICAL" if score >= 80 else "HIGH" if score >= 55 else "MEDIUM" if score >= 30 else "LOW"
            sif.is_sif = score >= 55
            sif.category = category
            sif.precursors = [category] if sif.is_sif else []
            sif.evidence = [text]
            sif.recommendations = [recommendation]
            if category.startswith("PPE"):
                sif.unsafe_acts = ["Required PPE not verified"]
            elif category == "Restricted-area exposure":
                sif.unsafe_conditions = ["Restricted-area exposure"]
            elif category == "Fire / explosion potential":
                sif.unsafe_conditions = ["Fire or ignition hazard"]

        with self._connect() as con:
            cur = con.execute("""INSERT INTO incidents
                (timestamp,incident_type,zone,workers,risk,severity,confidence,
                 gas,temperature,pressure,equipment,evidence,status,response,
                 incident_description,unsafe_acts,unsafe_conditions,near_miss_indicators,
                 sif_precursors,sif_classification,sif_score,sif_evidence,recommendations,source)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                data["incident_type"], data.get("zone", "Restricted Zone A"), data.get("workers", 0),
                data["risk"], data["severity"], data.get("confidence", 0), data.get("gas", 0),
                data.get("temperature", 0), data.get("pressure", 0), data.get("equipment", 0),
                evidence_name, "OPEN", data.get("response", "Critical alarm active. Acknowledge and verify the scene."),
                text, " • ".join(sif.unsafe_acts) if sif.unsafe_acts else "None detected",
                " • ".join(sif.unsafe_conditions) if sif.unsafe_conditions else "None detected",
                " • ".join(sif.near_miss_indicators) if sif.near_miss_indicators else "None detected",
                " • ".join(dict.fromkeys(sif.precursors)) if sif.precursors else "None detected",
                sif.level if sif.is_sif else "LOW", sif.score,
                " • ".join(sif.evidence) if sif.evidence else "None detected",
                " • ".join(dict.fromkeys(sif.recommendations)) if sif.recommendations else "Human verification and corrective action required.",
                data.get("source", "CCTV / AI Vision")
            ))
            return cur.lastrowid

    def acknowledge(self, incident_id=None):
        with self._connect() as con:
            if incident_id:
                con.execute("UPDATE incidents SET status='ACKNOWLEDGED' WHERE id=?", (incident_id,))
            else:
                con.execute("UPDATE incidents SET status='ACKNOWLEDGED' WHERE status='OPEN'")

    def escalate(self, incident_id=None):
        """Mark the active high/critical incident as escalated after the acknowledgement window."""
        with self._connect() as con:
            if incident_id:
                con.execute("UPDATE incidents SET status='ESCALATED', response=? WHERE id=? AND status='OPEN'",
                            ("Acknowledgement not received within 15 seconds. Escalated to the next responsible person / area in-charge.", incident_id))
            else:
                con.execute("UPDATE incidents SET status='ESCALATED', response=? WHERE status='OPEN' AND risk >= 71",
                            ("Acknowledgement not received within 15 seconds. Escalated to the next responsible person / area in-charge.",))

    def list_incidents(self, limit=100):
        with self._connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def analytics(self):
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            critical = con.execute("SELECT COUNT(*) FROM incidents WHERE severity='CRITICAL' OR risk >= 88").fetchone()[0]
            sif_count = con.execute("SELECT COUNT(*) FROM incidents WHERE sif_score >= 30 OR sif_classification NOT IN ('LOW', '')").fetchone()[0]
            by_type = [dict(r) for r in con.execute("""SELECT incident_type AS type, COUNT(*) AS count,
                ROUND(AVG(risk),1) AS avg_risk, MAX(risk) AS max_risk
                FROM incidents GROUP BY incident_type ORDER BY count DESC""").fetchall()]
            by_source = [dict(r) for r in con.execute("""SELECT COALESCE(source, 'CCTV / AI Vision') AS source, COUNT(*) AS count
                FROM incidents GROUP BY COALESCE(source, 'CCTV / AI Vision') ORDER BY count DESC""").fetchall()]
        return {"total": total, "critical": critical, "sif_count": sif_count, "by_type": by_type, "by_source": by_source}

    def make_pdf(self, incident_id: int) -> Path | None:
        with self._connect() as con:
            r = con.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not r: return None
        out = self.reports_dir / f"incident_report_{incident_id}.pdf"
        styles = getSampleStyleSheet()
        title = ParagraphStyle("title", parent=styles["Title"], alignment=TA_CENTER, fontSize=18, leading=22, spaceAfter=8)
        h = ParagraphStyle("h", parent=styles["Heading2"], fontSize=13, leading=16, spaceBefore=9, spaceAfter=5)
        body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.2, leading=12)
        small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.HexColor("#555555"))
        doc = SimpleDocTemplate(str(out), pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=14*mm, bottomMargin=14*mm)
        story = [Paragraph("Industrial Safety AI — Enhanced Incident Report", title),
                 Paragraph("Decision-support prototype. Human verification is required.", body), Spacer(1, 7)]
        rows = [
            ("Incident ID", r["id"]), ("Source", r["source"] or "CCTV / AI Vision"), ("Timestamp", r["timestamp"]), ("Incident", r["incident_type"]),
            ("Zone", r["zone"]), ("Workers", r["workers"]), ("Operational Risk", f'{r["risk"]}/100'),
            ("Operational Severity", r["severity"]), ("SIF Score", f'{r["sif_score"] or 0}/100'), ("SIF Classification", r["sif_classification"] or "LOW"), ("Confidence", f'{r["confidence"]:.1f}%'),
            ("Status", r["status"]), ("Response", r["response"] or "Human verification required."),
        ]
        rows = [[Paragraph(escape(str(a)), body), Paragraph(escape(str(b)), body)] for a,b in rows]
        table = Table(rows, colWidths=[48*mm, 130*mm], repeatRows=0)
        table.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),colors.HexColor("#E8EDF2")),
            ("GRID",(0,0),(-1,-1),0.45,colors.HexColor("#8C959E")),("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
        story += [table, Paragraph("Incident Description / Report", h), Paragraph(escape(r["incident_description"] or "No description available."), body),
                  Paragraph("Unsafe Acts", h), Paragraph(escape(r["unsafe_acts"] or "None detected"), body),
                  Paragraph("Unsafe Conditions", h), Paragraph(escape(r["unsafe_conditions"] or "None detected"), body),
                  Paragraph("Near-Miss Indicators", h), Paragraph(escape(r["near_miss_indicators"] or "None detected"), body),
                  Paragraph("SIF Precursors", h), Paragraph(escape(r["sif_precursors"] or "None detected"), body),
                  Paragraph(f'SIF Precursor Classification: {r["sif_classification"] or "LOW"}', h),
                  Paragraph("Hybrid local ML + explainable domain-rule NLP prototype", small)]
        if r["sif_evidence"] and r["sif_evidence"] != "None detected":
            story += [Paragraph("SIF Evidence", h), Paragraph(escape(r["sif_evidence"]), body)]
        if r["recommendations"]:
            story += [Paragraph("Recommended Controls", h), Paragraph(escape(r["recommendations"]), body)]
        if r["evidence"] and str(r["severity"]).upper() == "CRITICAL":
            img_path = self.evidence_dir / r["evidence"]
            if img_path.exists():
                story += [Spacer(1, 8), Paragraph("Evidence Image", h), Image(str(img_path), width=145*mm, height=82*mm)]
            story += [Paragraph(f"Evidence image: evidence/{r['evidence']}", small)]
        else:
            story += [Paragraph("Evidence image: Not captured", small)]
        doc.build(story)
        return out
