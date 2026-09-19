# SIH Finals Demo Script

## 1. Opening
"This is Industrial Safety AI, a decision-support system for identifying unsafe conditions and SIF precursor signals. It combines real-time visual/process monitoring with an NLP engine for safety reports."

## 2. Normal state
Show:
- Camera ONLINE
- Risk near 0
- LOW severity
- No active demo states

## 3. Helmet / PPE
Click **🪖 Helmet / PPE**.
Say: "The system flags a critical-control/PPE failure signal. The demo state is deterministic so the result does not depend on camera conditions."

## 4. Aerosol
Click **💨 Smoke / Aerosol**.
Say: "A visible aerosol or smoke-like event is treated as a safety signal and contributes to the operational risk."

## 5. Fire-like
Click **🔥 Fire-like**.
Say: "For safety, we simulate the fire-like signal instead of using a real flame."

## 6. Fall
Click **🧍 Fall**.
Say: "The possible-fall detector can be demonstrated without anyone actually falling."

## 7. Critical response
Choose **Critical** process preset.
Show the red alert. The alert is latched.
Say: "Once the risk crosses the critical threshold, the alarm remains active until the operator acknowledges and the hazard returns to a safe state."

Click **ACKNOWLEDGE / VERIFY**.
Say: "Acknowledgement does not hide the incident. It records operator acknowledgement while keeping the hazard visible until the condition is cleared."

Click **Reset All**.

## 8. SIF NLP
Open **SIF NLP Analyzer**.
Use the Helmet example or paste:

`Worker entered the confined space without atmospheric testing and isolation was not verified. This could have resulted in serious injury.`

Show:
- SIF decision
- score
- precursor categories
- evidence
- unsafe acts/conditions
- recommended controls

## 9. Incident PDF
Open the generated PDF from the incident table.
Point to:
- Incident summary
- Unsafe Acts
- Unsafe Conditions
- Near-Miss Indicators
- SIF Precursors
- SIF Precursor Classification
- Evidence/reasoning
- Recommended Controls
- Evidence image

## 10. Closing
"The key idea is to move from simply detecting an event to explaining the potential SIF precursor and giving safety teams a structured basis for preventive action."
