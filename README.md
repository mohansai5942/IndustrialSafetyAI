# IndustrialSafetyAI — SIH Finals Upgraded Prototype

A Windows-first student decision-support prototype for Oil India Limited's SIF-precursor problem statement.

## What this version demonstrates

1. 🪖 Helmet/PPE verification signal
2. 💨 Aerosol / smoke-like visual event
3. 🔥 Fire-like visual event
4. 🚧 Restricted-zone entry
5. 🧍 Possible fall
6. 🏭 Gas, temperature, pressure and equipment-condition simulation
7. 📊 0–100 operational risk score
8. 🚨 Latched critical alarm + acknowledgement/verification flow
9. 📋 Automatic incident record + camera evidence
10. 📄 Enhanced incident PDF with Unsafe Acts, Unsafe Conditions, Near-Miss Indicators and SIF Precursors
11. 🧠 SIF NLP Analyzer for unsafe-act / unsafe-condition / near-miss reports
12. 📈 Incident analytics

## Important UI fixes in this build

### Alarm / acknowledgement
The critical alert is **latched**. A one-frame detector drop will not make the alarm disappear. The alert stays visible until the operator acknowledges it and the risk returns below the critical threshold. After acknowledgement, the panel clearly shows `ACKNOWLEDGED / VERIFY` instead of vanishing.

### Safe demo controls
The PPE, aerosol, fire-like and fall demo controls now update the state immediately and remain active until toggled off or Reset All is used. This makes the SIH presentation deterministic without creating a real hazard.

### SIF NLP startup
The SIF page starts in a clear `READY` state instead of showing a misleading `0/100` result before a report is analyzed. It also contains working example reports for helmet/PPE failure, confined space and near-miss scenarios.

### PDF format
Generated PDFs follow the supplied Enhanced Incident Report layout: title, incident summary table, Incident Description / Report, Unsafe Acts, Unsafe Conditions, Near-Miss Indicators, SIF Precursors, SIF Precursor Classification, evidence/reasoning, recommended controls and evidence image when available.

## NLP engine
`SIF_NLP_README.md` describes the explainable hybrid NLP layer. It uses a small bundled Naive-Bayes text classifier plus transparent domain rules, so the report text is actually classified at runtime. The bundled training corpus is a prototype corpus and is not confidential OIL data; a production deployment should retrain and validate the model on approved historical OIL reports.

## Camera detection notes

- YOLO11n detects people.
- Restricted-zone detection uses the configured polygon.
- Possible-fall detection uses person bounding-box geometry.
- Aerosol/mist and fire-like events use temporal visual heuristics.
- Helmet verification uses a lightweight helmet model when available, otherwise a conservative head-region fallback.
- The four Safe Demo Controls provide deterministic presentation signals and should be used for the SIH demo.

These visual detectors are **student prototype components, not certified safety instruments**. Real deployment requires validated models, calibrated sensors, fail-safe design and safety certification.

## Windows setup

### Recommended
If `run_windows.bat` is available, double-click it. It creates `.venv`, installs dependencies and starts the server.

Then open:

`http://127.0.0.1:5000`

### Manual

```bat
py -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

If a dependency does not yet support the Python version installed on the machine, use a supported Python 3.11/3.12 environment for the project.

## Camera permissions

If the dashboard says camera unavailable:

- Windows Settings → Privacy & security → Camera
- Enable Camera access
- Enable Let desktop apps access your camera
- Close other applications using the webcam
- Restart the application

## Recommended SIH demo flow

1. Start with Normal state.
2. Show the live camera and dashboard.
3. Demonstrate Restricted Zone with a real person in the orange zone.
4. Toggle 🪖 Helmet / PPE and show the event immediately.
5. Toggle 💨 Smoke / Aerosol and show the event immediately.
6. Toggle 🔥 Fire-like and show the event immediately. Do not bring a real flame near electronics.
7. Toggle 🧍 Fall to simulate a fall signal safely.
8. Use the Critical process preset to push the combined risk over the alarm threshold.
9. Show the **latched** critical alert and acknowledgement timer.
10. Click ACKNOWLEDGE / VERIFY. The alert remains visible as acknowledged while the hazard is still active.
11. Reset the hazard and show the system returning to normal.
12. Open the SIF NLP Analyzer and analyze an example report.
13. Open the incident PDF and show the SIF sections and evidence.
14. Finish with incident analytics.

## Project structure

```text
IndustrialSafetyAI/
├── app.py
├── camera_ai.py
├── risk_engine.py
├── sif_engine.py
├── nlp_routes.py
├── incident_manager.py
├── requirements.txt
├── run_windows.bat
├── run_linux.sh
├── README.md
├── DEMO_SCRIPT.md
├── SIF_NLP_README.md
├── templates/
│   ├── dashboard.html
│   └── nlp.html
├── models/
├── evidence/       # created automatically
├── reports/        # created automatically
└── safety.db       # created automatically
```

## No email escalation
Email escalation has been removed from this version. The response flow is dashboard alarm → acknowledgement/verification → incident record → report.

## Safety disclaimer

This is a **student demonstration / decision-support prototype**. Do not connect it directly to emergency shutdown systems, industrial actuators, evacuation systems, medical services, or other life-safety controls. Real deployment requires validated sensors/models, calibration, fail-safe design, testing and appropriate safety certification.

## National Demo — Unified Two-Path Safety Workflow

The prototype follows two connected inputs:

1. **Manual path:** UA / UC / Near-Miss report → NLP/SIF analysis → SIF precursor + risk + recommendation → common safety dashboard.
2. **CCTV path:** CCTV feed → AI Vision → unsafe event → Risk Engine → incident description → NLP/SIF analysis of the stored text → SIF precursor + risk + recommendation → common safety dashboard.

CCTV image frames are not sent directly to the NLP engine. The vision module first detects an event and the incident manager creates a textual incident description; NLP then analyzes that text. This keeps the architecture aligned with the OIL safety-report problem while demonstrating an optional real-time input path.


## Operations interface layout
The prototype now uses three operational interfaces:
1. `/` — Safety Analysis Dashboard: the landing console with two clear paths.
2. `/cctv` — CCTV Operations: live vision, process conditions, alarms and incident records.
3. `/manual-analysis` — Batch Safety Report Review: analyze up to 50 UA/UC/Near-Miss reports together, with recurring SIF precursor categories.
