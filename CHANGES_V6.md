# Industrial Safety AI — V6 changes

- CCTV incidents are persisted only when operational risk is HIGH or CRITICAL (71–100).
- CCTV evidence images are captured only for HIGH/CRITICAL incidents.
- Operational severity follows the risk score; normal/medium events are not presented as critical.
- Alarm acknowledgement window is 15 seconds.
- Alarm sound repeats continuously while an alert is active and stops immediately on acknowledgement.
- If not acknowledged within 15 seconds, the incident is marked ESCALATED and the UI shows escalation to the next responsible person / area in-charge.
- After acknowledgement, the safety alert panel disappears and does not reappear until the risk condition clears and a new event occurs.
- Removed the SIF SIGNALS KPI from CCTV and the SIF-screened KPI from the main dashboard.
- Manual analysis now uses “No precursor identified” instead of “No signal”.
