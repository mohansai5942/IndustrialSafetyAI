# SIH Finals V3 verification

## Start

Windows: `run_windows.bat` or `python app.py`

## NLP smoke tests

- `person died` -> CRITICAL, SIF detected
- `worker entered without helmet` -> MEDIUM, PPE precursor
- `near miss: suspended load almost struck worker` -> MEDIUM, line-of-fire precursor
- `routine toolbox meeting` -> LOW, no precursor

## Incident recording

Every distinct non-normal safety event is recorded, not only critical events. A persistent event is de-duplicated; a new event combination or the same event after the cooldown is recorded again. A PDF is generated immediately for every saved incident.

## Alarm

Critical alarms latch until `Reset All`. Acknowledge changes the incident status but does not hide the alarm.
