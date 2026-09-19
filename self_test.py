"""Quick offline project sanity test. Run: python self_test.py"""
from pathlib import Path
from sif_engine import analyze_report
from risk_engine import calculate_risk
from incident_manager import IncidentManager

base = Path(__file__).resolve().parent
r = analyze_report("Worker entered without helmet")
assert r.is_sif and any("PPE" in x for x in r.precursors)
r2 = analyze_report("Worker entered the confined space without atmospheric testing and isolation was not verified.")
assert r2.is_sif and r2.score >= 30
r3 = analyze_report("person died")
assert r3.is_sif and r3.score >= 95 and r3.level == "CRITICAL"
assert r2.is_sif and r2.score >= 30
rr = calculate_risk(restricted=0,aerosol=False,fire=False,fall=False,helmet_not_verified=0,workers=0,gas=10,temperature=40,pressure=5,equipment=0)
assert rr.score == 0 and rr.level == "LOW"
rr2 = calculate_risk(restricted=0,aerosol=False,fire=True,fall=False,helmet_not_verified=0,workers=1,gas=80,temperature=95,pressure=8.5,equipment=80)
assert rr2.score >= 95
m = IncidentManager(base)
id_ = m.save_incident({"incident_type":"Possible worker fall","workers":1,"risk":77,"severity":"HIGH","confidence":60.8,
                       "gas":10,"temperature":40,"pressure":5,"equipment":0,
                       "incident_description":"A possible worker fall was detected in the monitored area. Workers detected: 1."})
pdf = m.make_pdf(id_)
assert pdf and pdf.exists() and pdf.stat().st_size > 1000
# Keep the package clean after the test.
try: pdf.unlink()
except Exception: pass
try: (base / "safety.db").unlink()
except Exception: pass
print("SELF TEST PASSED: NLP, risk engine and PDF generation are working.")
