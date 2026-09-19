# SIF NLP Analyzer — V4

The SIF analyzer is a hybrid offline prototype. It combines a bundled Naive-Bayes text classifier with transparent domain rules for SIF precursor extraction.

## Runtime behaviour
- Accepts arbitrary safety-report text.
- Classifies the report using the local text model.
- Extracts SIF precursor categories and evidence sentences.
- Detects unsafe acts, unsafe conditions and near-miss indicators.
- Produces a 0–100 decision-support score and recommendations.
- Fatality/death language is treated as critical consequence evidence.

## Important limitation
The bundled training corpus is a small prototype corpus created for demonstration. It is not confidential OIL historical data and is not a substitute for validated OIL training data. For a production system, retrain, calibrate and validate the model on approved historical reports with domain experts.
