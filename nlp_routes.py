from flask import jsonify, request
from sif_engine import analyze_report

def register_nlp_routes(app):
    @app.post('/api/nlp/analyze')
    def nlp_analyze():
        data = request.get_json(force=True) or {}
        text = data.get('text', '')
        if not text.strip():
            return jsonify(ok=False, error='Report text is required'), 400
        r = analyze_report(text)
        return jsonify(ok=True, report=text, is_sif=r.is_sif, score=r.score,
                       level=r.level, category=r.category, precursors=r.precursors,
                       evidence=r.evidence, recommendations=r.recommendations,
                       unsafe_acts=r.unsafe_acts, unsafe_conditions=r.unsafe_conditions,
                       near_miss_indicators=r.near_miss_indicators, model_confidence=r.model_confidence, model_label=r.model_label)
