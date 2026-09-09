"""
crop_ai.py - Inference for the BioFarm crop disease classifier.

Loads the trained Multinomial Naive Bayes model produced by train_model.py and
predicts a disease + treatment + confidence from a symptom description. Pure
Python, no third-party ML libraries.

An OPTIONAL image path uses a hosted vision model, but only when the
CROP_VISION_API_KEY environment variable is set. With no key (the default) the
app runs entirely offline on the trained text model - which keeps it working on
Render's free tier at zero cost.
"""

import os
import re
import json
import math
import base64

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, 'models', 'crop_nb_model.json')
TOKEN_RE = re.compile(r'[a-z]+')

_model = None


def _load():
    """Lazily load and cache the trained model (empty dict if missing)."""
    global _model
    if _model is None:
        try:
            with open(MODEL_PATH, encoding='utf-8') as fh:
                _model = json.load(fh)
        except (FileNotFoundError, ValueError):
            _model = {}
    return _model


def is_ready():
    """True when a trained model artifact is available."""
    return bool(_load().get('classes'))


def tokenize(text):
    stop = set(_load().get('stopwords', []))
    tokens = TOKEN_RE.findall((text or '').lower())
    return [t for t in tokens if len(t) >= 2 and t not in stop]


def _candidates(crop_type):
    """Crop's own diseases + shared 'general' problems; all labels if unknown.
    Mirrors candidates_for() in train_model.py so inference matches training."""
    model = _load()
    crop_to_labels = model.get('crop_to_labels', {})
    labels = list(crop_to_labels.get(crop_type, []))
    for lbl in crop_to_labels.get('general', []):
        if lbl not in labels:
            labels.append(lbl)
    return labels or model.get('classes', [])


def _softmax_top(scores):
    if not scores:
        return 0.0
    mx = max(scores.values())
    exps = {k: math.exp(v - mx) for k, v in scores.items()}
    total = sum(exps.values()) or 1.0
    return max(exps.values()) / total


def predict(symptoms, crop_type=None):
    """Return {disease, treatment, confidence, label} from symptom text."""
    model = _load()
    if not model.get('classes'):
        return {
            'disease': 'Model unavailable',
            'treatment': 'The detection model has not been trained yet. '
                         'Run "python train_model.py" to build it.',
            'confidence': 0.0,
            'label': None,
        }

    tokens = tokenize(symptoms)
    candidates = _candidates(crop_type)
    n = len(tokens) + 1  # length normalisation (matches training)

    scores = {}
    for c in candidates:
        if c not in model['log_prior']:
            continue
        s = model['log_prior'][c]
        likelihood = model['log_likelihood'][c]
        default = model['default_ll'][c]
        for tok in tokens:
            s += likelihood.get(tok, default)
        scores[c] = s / n

    if not scores:
        return {'disease': 'Inconclusive',
                'treatment': 'Not enough information to diagnose. Please add more '
                             'detail about the symptoms.',
                'confidence': 0.0, 'label': None}

    best = max(scores, key=scores.get)
    return {
        'disease': model['disease_names'].get(best, best.split('::', 1)[-1]),
        'treatment': model['treatments'].get(best, ''),
        'confidence': round(_softmax_top(scores), 4),
        'label': best,
    }


def vision_enabled():
    return bool(os.environ.get('CROP_VISION_API_KEY'))


def predict_from_image(image_path, crop_type=None):
    """Optional upgrade: diagnose from a plant photo via a hosted vision model.

    Returns the same dict shape as predict(), or None when vision is disabled
    or anything goes wrong - so callers can cleanly fall back to text.
    """
    if not vision_enabled():
        return None
    try:
        import requests  # already a project dependency

        with open(image_path, 'rb') as fh:
            b64 = base64.standard_b64encode(fh.read()).decode('ascii')
        ext = os.path.splitext(image_path)[1].lower().lstrip('.')
        media_type = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext or "png"}'

        model_name = os.environ.get('CROP_VISION_MODEL', 'claude-sonnet-5')
        crop_hint = f" The crop is {crop_type}." if crop_type else ''
        prompt = (
            'You are a plant pathologist. Identify the most likely disease in '
            'this crop photo.' + crop_hint + ' Respond ONLY with compact JSON: '
            '{"disease": str, "treatment": str, "confidence": number between 0 and 1}.'
        )
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': os.environ['CROP_VISION_API_KEY'],
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': model_name,
                'max_tokens': 512,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'image', 'source': {
                            'type': 'base64', 'media_type': media_type, 'data': b64}},
                        {'type': 'text', 'text': prompt},
                    ],
                }],
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()['content'][0]['text']
        match = re.search(r'\{.*\}', text, re.DOTALL)
        data = json.loads(match.group(0) if match else text)
        return {
            'disease': str(data.get('disease', 'Unknown')),
            'treatment': str(data.get('treatment', '')),
            'confidence': round(float(data.get('confidence', 0.0)), 4),
            'label': 'vision',
        }
    except Exception:
        # Any failure (no network, bad key, unexpected response) -> text fallback.
        return None
