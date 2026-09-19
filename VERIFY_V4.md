# V4 verification checklist

## Alarm
- Alert panel is hidden during normal operation.
- Alarm sound occurs only when `fire == true` AND `risk > 95`.
- Acknowledgement is available only while that emergency condition is active.
- If fire clears or risk falls to 95 or below, the alarm and alert panel disappear.

## Cap detection
- The previous head-region fallback is removed; hair/dark head regions cannot become `CAP OK`.
- A dedicated hardhat/hat model is used when it can be downloaded.
- Only `hat`/`cap` detections count as `CAP OK`.
- If the model is unavailable or no cap is detected, the UI says `CAP NOT VERIFIED`.

## Fall detection
- Temporal tracking combines posture, sudden vertical displacement and persistence.
- A single noisy frame does not immediately trigger a fall.
- A sustained horizontal/low posture or sudden drop can trigger a fall.

## SIF NLP
- Runtime analyzer combines a bundled Naive-Bayes classifier with domain rules.
- `person died` -> CRITICAL / 100.
- Helmet/cap failure, confined space, LOTO, fire, dropped object and near-miss examples are classified.
- The page starts in READY state without a fake 0/100 result.

## Incident records
- Every distinct non-normal safety event is persisted.
- Every persisted event receives a PDF.
- Repeated identical frames are de-duplicated to avoid record spam.
