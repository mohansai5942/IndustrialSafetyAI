"""Hybrid SIF NLP engine.

The analyzer combines a small local Naive-Bayes text classifier trained from a
bundled safety-report corpus with transparent domain rules. This makes the
analyzer responsive to the actual report text instead of behaving like a static
keyword demo. The bundled corpus is a prototype corpus, not confidential OIL data.
"""
import math, re
from dataclasses import dataclass
from collections import Counter, defaultdict

@dataclass
class SIFResult:
    is_sif: bool
    score: int
    level: str
    category: str
    precursors: list[str]
    evidence: list[str]
    recommendations: list[str]
    unsafe_acts: list[str]
    unsafe_conditions: list[str]
    near_miss_indicators: list[str]
    model_confidence: float = 0.0
    model_label: str = "No precursor"

# Curated, compact training corpus for the offline prototype. A production/OIL
# deployment should retrain this classifier on validated historical reports.
TRAINING = [
    ("PPE failure", "worker entered without helmet"),
    ("PPE failure", "worker did not wear required protective equipment"),
    ("PPE failure", "employee entered area without safety cap"),
    ("PPE failure", "required PPE was missing during work"),
    ("Confined space", "worker entered confined space without gas testing"),
    ("Confined space", "tank entry occurred without atmospheric monitoring"),
    ("Confined space", "manhole entry without permit and rescue plan"),
    ("Energy isolation", "maintenance started before lockout tagout was verified"),
    ("Energy isolation", "equipment remained energized during maintenance"),
    ("Energy isolation", "isolation was not confirmed before work"),
    ("Working at height", "worker was at height without harness"),
    ("Working at height", "open edge had no fall protection"),
    ("Working at height", "scaffold work without guardrail"),
    ("Line of fire", "worker stood below suspended load"),
    ("Line of fire", "caught between moving equipment and structure"),
    ("Line of fire", "lifting operation exposed worker to struck by hazard"),
    ("Loss of containment", "hydrocarbon leak was observed"),
    ("Loss of containment", "gas release occurred from pipeline"),
    ("Loss of containment", "spill and loss of containment detected"),
    ("Fire / explosion", "fire and flame were observed near equipment"),
    ("Fire / explosion", "flammable vapor and ignition source were present"),
    ("Fire / explosion", "hot work created sparks near flammable material"),
    ("Dropped object", "tool fell from elevated platform"),
    ("Dropped object", "overhead load dropped near worker"),
    ("Vehicle", "forklift reversed near pedestrian"),
    ("Vehicle", "vehicle struck a worker in loading area"),
    ("Fall", "worker fell from platform"),
    ("Fall", "person collapsed and was found on the floor"),
    ("Near miss", "near miss occurred with no injury"),
    ("Near miss", "suspended load moved unexpectedly but no one was hurt"),
    ("Near miss", "incident was narrowly avoided"),
    ("Fatality", "person died after the incident"),
    ("Fatality", "worker was killed in an accident"),
    ("Fatality", "fatality occurred at the worksite"),
    ("Serious injury", "worker suffered serious injury"),
    ("Serious injury", "employee sustained severe injury and was hospitalized"),
    ("Serious injury", "life threatening injury required emergency treatment"),
    ("Normal", "routine inspection completed with no unsafe condition"),
    ("Normal", "normal housekeeping inspection completed"),
    ("Normal", "worker followed required safety controls"),
]

def _tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())

class _NB:
    def __init__(self, rows):
        self.labels = sorted(set(y for y, _ in rows))
        self.doc_count = Counter(y for y, _ in rows)
        self.word_count = defaultdict(Counter)
        self.total_words = Counter()
        vocab = set()
        for y, x in rows:
            toks = _tokens(x); vocab.update(toks)
            self.word_count[y].update(toks); self.total_words[y] += len(toks)
        self.vocab = vocab
        self.n = len(rows)

    def predict(self, text):
        toks = _tokens(text)
        if not toks: return "Normal", 0.0
        scores = {}
        V = len(self.vocab)
        for y in self.labels:
            logp = math.log((self.doc_count[y] + 1) / (self.n + len(self.labels)))
            denom = self.total_words[y] + V
            for t in toks:
                logp += math.log((self.word_count[y][t] + 1) / denom)
            scores[y] = logp
        ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best, second = ordered[0], ordered[1] if len(ordered) > 1 else (None, -999)
        # Stable softmax-like confidence from the top-two margin.
        margin = max(-50.0, min(50.0, best[1] - second[1]))
        conf = 1.0 / (1.0 + math.exp(-margin))
        return best[0], round(conf * 100, 1)

MODEL = _NB(TRAINING)

OUTCOME_PATTERNS = [
    ("Fatality / death", [r"\bperson died\b", r"\bdied\b", r"\bfatality\b", r"\bfatal\b", r"\bdeath\b", r"\bdead\b", r"\bkilled\b"], 100,
     "Activate emergency response, preserve the scene and initiate formal incident investigation."),
    ("Serious injury", [r"\bserious injur(?:y|ies)\b", r"\bsevere injur(?:y|ies)\b", r"\bmajor injur(?:y|ies)\b", r"\bcritical injur(?:y|ies)\b", r"\blife[- ]threatening\b", r"\bhospitali[sz]ed\b", r"\bpermanent disability\b"], 88,
     "Provide emergency medical response, secure the area and investigate the causal controls."),
]
PATTERNS = [
    ("Energy isolation / LOTO", [r"lockout", r"lock out", r"tagout", r"tag out", r"\bloto\b", r"isolation", r"isolat(?:ed|ion|ing)", r"energized", r"live equipment"], 30, "Verify zero-energy state and lockout/tagout before work."),
    ("Confined space", [r"confined space", r"manhole", r"vessel entry", r"tank entry", r"entered the tank"], 30, "Require permit, atmospheric testing, attendant and rescue readiness."),
    ("Working at height", [r"working at height", r"work at height", r"scaffold", r"unprotected edge", r"fall protection", r"without harness", r"no harness", r"height work", r"roof work"], 28, "Verify edge protection, certified access and fall-arrest equipment."),
    ("Line of fire", [r"line of fire", r"struck by", r"caught between", r"suspended load", r"lifting operation", r"pinch point"], 28, "Establish exclusion zones and keep personnel out of the line of fire."),
    ("Loss of containment", [r"leak", r"leakage", r"spill", r"release", r"loss of containment", r"gas release", r"hydrocarbon release"], 32, "Stop the source where safe, isolate the system and verify containment."),
    ("Fire / explosion potential", [r"\bfire\b(?!\s+extinguisher)", r"flame", r"ignition", r"explosion", r"hot work", r"spark", r"flammable"], 32, "Control ignition sources and verify gas testing / hot-work controls."),
    ("Dropped object", [r"dropped object", r"falling object", r"object fell", r"dropped tool", r"overhead load"], 24, "Barricade the drop zone and secure tools/materials against falling."),
    ("Vehicle / mobile equipment", [r"vehicle", r"forklift", r"mobile equipment", r"reversing", r"hit by vehicle", r"vehicle-pedestrian"], 22, "Separate pedestrians and vehicles and verify spotter/reversing controls."),
    ("PPE / critical control failure", [r"no helmet", r"without helmet", r"without a helmet", r"helmet not worn", r"helmet missing", r"without ppe", r"missing ppe", r"no ppe", r"no gloves", r"without respirator", r"without safety harness", r"without safety cap", r"no cap", r"without cap"], 22, "Stop the task until required PPE and critical controls are verified."),
]
NEGATION = re.compile(r"\b(no|not|never|without|failed to|did not|didn't|wasn't|weren't|missing|lack(?:ed|ing)?|unverified)\b", re.I)
UNSAFE_ACT_PATTERNS = [
    (r"without helmet|no helmet|helmet not worn|without ppe|no ppe|missing ppe|without safety cap|no cap|without cap", "Required PPE / cap not used"),
    (r"entered .*confined space|entered .*tank|entered .*vessel", "Entered controlled space without confirmed controls"),
    (r"bypassed|defeated|overrode|ignored|failed to follow", "Safety procedure/control not followed"),
    (r"worked on .*energized|worked on .*live|without isolation", "Work performed without confirmed isolation"),
]
UNSAFE_CONDITION_PATTERNS = [
    (r"leak|spill|release|gas release", "Loss-of-containment condition"),
    (r"unprotected edge|no guardrail|open edge|scaffold", "Fall exposure / inadequate edge protection"),
    (r"\bfire\b(?!\s+extinguisher)|flame|spark|flammable|explosion", "Fire or ignition hazard"),
    (r"suspended load|dropped object|falling object", "Dropped-object / line-of-fire hazard"),
    (r"restricted zone|unauthorized area", "Restricted-area exposure"),
]
NEAR_MISS_PATTERNS = [r"near[- ]miss", r"could have", r"almost", r"narrowly avoided", r"no injury", r"no one was hurt", r"potentially serious"]

def _unique(items): return list(dict.fromkeys(x for x in items if x))

def analyze_report(text: str) -> SIFResult:
    text = re.sub(r"\s+", " ", (text or "").strip()); low = text.lower()
    ml_label, ml_conf = MODEL.predict(text)
    hits, recs, evidence = [], [], []
    score = 0

    # Consequence language always takes precedence over the generic classifier.
    for category, patterns, weight, rec in OUTCOME_PATTERNS:
        if any(re.search(p, low) for p in patterns):
            hits.append(category); recs.append(rec); score = max(score, weight)

    for category, patterns, weight, rec in PATTERNS:
        if any(re.search(p, low) for p in patterns):
            boost = 8 if NEGATION.search(low) else 0
            score += min(38, weight + boost); hits.append(category); recs.append(rec)

    near_miss = any(re.search(p, low) for p in NEAR_MISS_PATTERNS)
    if near_miss: score += 8

    # Do not let the lightweight text classifier invent a serious event when
    # the report contains no explicit SIF precursor or consequence language.
    # The transparent rules above are the source of the operational SIF score.
    if not hits:
        score = 0

    score = min(100, int(score))
    level = "CRITICAL" if score >= 80 else "HIGH" if score >= 55 else "MEDIUM" if score >= 30 else "LOW"
    is_sif = bool(hits) and score >= 30
    category = hits[0] if hits else "Normal condition"

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    for sentence in sentences:
        sl = sentence.lower()
        if (any(re.search(p, sl) for _, ps, _, _ in OUTCOME_PATTERNS for p in ps) or
            any(re.search(p, sl) for _, ps, _, _ in PATTERNS for p in ps) or
            (ml_label.lower() in sl and ml_label != "Normal")):
            evidence.append(sentence)
    if not evidence and text and hits: evidence.append(text[:320])

    unsafe_acts = [label for pattern, label in UNSAFE_ACT_PATTERNS if re.search(pattern, low)]
    unsafe_conditions = [label for pattern, label in UNSAFE_CONDITION_PATTERNS if re.search(pattern, low)]
    near_miss_indicators = ["Near-miss / potential serious consequence language"] if near_miss else []

    return SIFResult(
        is_sif=is_sif, score=score, level=level, category=category,
        precursors=_unique(hits), evidence=_unique(evidence)[:3], recommendations=_unique(recs),
        unsafe_acts=_unique(unsafe_acts), unsafe_conditions=_unique(unsafe_conditions),
        near_miss_indicators=_unique(near_miss_indicators), model_confidence=ml_conf, model_label=ml_label
    )
