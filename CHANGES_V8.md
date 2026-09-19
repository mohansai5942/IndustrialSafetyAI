# V8 changes

- Manual analysis now loads exactly 50 workplace reports.
- Added a 50th normal-condition sample.
- Removed ML-only false positives that were turning ordinary reports into Fatality/High SIF results.
- SIF column now shows `Normal condition` when no SIF precursor is identified; otherwise it shows a plain precursor level such as `HIGH SIF precursor` without a misleading numeric SIF score.
- Live fall detection is more conservative to avoid treating normal upright people as falls.
- Missing optional cap/hat model no longer flags every detected person as a PPE violation.
- CCTV incident records remain restricted to HIGH/CRITICAL operational risk.
- Evidence photographs are captured and displayed in PDFs only for CRITICAL incidents.
- CCTV operator table no longer displays the SIF numeric score.
