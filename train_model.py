"""
train_model.py - Train the BioFarm crop disease classifier.

A pure-Python Multinomial Naive Bayes text classifier over the curated
symptom -> disease dataset in data/crop_diseases.json. No third-party ML
libraries are required, so it trains on any Python 3.x (including 3.14) and
the resulting model runs unchanged on Render's free tier.

Run:  python train_model.py
Output: models/crop_nb_model.json  (the trained model artifact)
"""

import os
import re
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, 'data', 'crop_diseases.json')
MODEL_DIR = os.path.join(BASE_DIR, 'models')
MODEL_PATH = os.path.join(MODEL_DIR, 'crop_nb_model.json')

SEED = 42
ALPHA = 1.0  # Laplace smoothing

STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'of', 'on', 'in', 'to', 'with', 'that',
    'this', 'is', 'are', 'be', 'as', 'at', 'by', 'from', 'into', 'it', 'its',
    'then', 'than', 'over', 'up', 'out', 'off', 'for', 'so', 'but', 'they',
    'them', 'their', 'has', 'have', 'had', 'was', 'were', 'not', 'no',
}

TOKEN_RE = re.compile(r'[a-z]+')


def tokenize(text):
    """Lowercase, keep alphabetic tokens of length >= 2, drop stopwords."""
    tokens = TOKEN_RE.findall((text or '').lower())
    return [t for t in tokens if len(t) >= 2 and t not in STOPWORDS]


def load_samples():
    """Return (samples, treatments, disease_names, crop_to_labels).

    Label is "crop::disease" so per-crop 'Healthy' and diseases that recur
    across crops (e.g. Anthracnose) stay distinct with their own treatment.
    """
    with open(DATA_PATH, encoding='utf-8') as fh:
        records = json.load(fh)

    samples = []                 # list of (tokens, label)
    treatments = {}              # label -> treatment text
    disease_names = {}           # label -> human disease name
    crop_to_labels = defaultdict(list)

    for rec in records:
        crop = rec['crop']
        disease = rec['disease']
        label = f"{crop}::{disease}"
        treatments[label] = rec['treatment']
        disease_names[label] = disease
        if label not in crop_to_labels[crop]:
            crop_to_labels[crop].append(label)
        for symptom in rec['symptoms']:
            samples.append((tokenize(symptom), label))

    return samples, treatments, disease_names, dict(crop_to_labels)


def train(samples):
    """Train Multinomial NB. Returns the serialisable model parameters."""
    classes = sorted({label for _, label in samples})
    vocab = sorted({tok for tokens, _ in samples for tok in tokens})
    vocab_size = len(vocab)

    class_doc_count = defaultdict(int)
    class_token_total = defaultdict(int)
    token_count = {c: defaultdict(int) for c in classes}

    for tokens, label in samples:
        class_doc_count[label] += 1
        for tok in tokens:
            token_count[label][tok] += 1
            class_token_total[label] += 1

    total_docs = len(samples)
    log_prior = {c: math.log(class_doc_count[c] / total_docs) for c in classes}

    # Precompute log-likelihoods for the observed vocabulary, plus a per-class
    # fallback for out-of-vocabulary tokens at prediction time.
    log_likelihood = {}
    default_ll = {}
    for c in classes:
        denom = class_token_total[c] + ALPHA * vocab_size
        log_likelihood[c] = {
            tok: math.log((token_count[c][tok] + ALPHA) / denom) for tok in vocab
        }
        default_ll[c] = math.log(ALPHA / denom)

    return {
        'classes': classes,
        'log_prior': log_prior,
        'log_likelihood': log_likelihood,
        'default_ll': default_ll,
        'vocab_size': vocab_size,
    }


def score(model, tokens, candidates):
    """Length-normalised log-posterior per candidate label. Candidates not in
    the trained model are skipped (a class can fall entirely into the test
    split, so its label would be absent from a train-only model)."""
    n = len(tokens) + 1  # +1 avoids division by zero and softens short inputs
    scores = {}
    for c in candidates:
        if c not in model['log_prior']:
            continue
        s = model['log_prior'][c]
        ll = model['log_likelihood'][c]
        default = model['default_ll'][c]
        for tok in tokens:
            s += ll.get(tok, default)
        scores[c] = s / n
    return scores


def predict_label(model, tokens, candidates):
    scores = score(model, tokens, candidates)
    if not scores:
        return None
    return max(scores, key=scores.get)


def candidates_for(crop, crop_to_labels, all_labels):
    """Labels considered for a crop: the crop's own diseases plus the shared
    'general' problems (powdery mildew, aphids, nutrient deficiency). Falls
    back to every label when the crop is unknown."""
    labels = list(crop_to_labels.get(crop, []))
    for lbl in crop_to_labels.get('general', []):
        if lbl not in labels:
            labels.append(lbl)
    return labels or all_labels


def evaluate(model, test, crop_to_labels):
    """Report global accuracy (all classes) and crop-scoped accuracy."""
    all_labels = model['classes']
    global_ok = scoped_ok = 0
    for tokens, label in test:
        crop = label.split('::', 1)[0]
        if predict_label(model, tokens, all_labels) == label:
            global_ok += 1
        candidates = candidates_for(crop, crop_to_labels, all_labels)
        if predict_label(model, tokens, candidates) == label:
            scoped_ok += 1
    n = len(test) or 1
    return global_ok / n, scoped_ok / n


def main():
    random.seed(SEED)
    samples, treatments, disease_names, crop_to_labels = load_samples()
    random.shuffle(samples)

    split = int(len(samples) * 0.8)
    train_set, test_set = samples[:split], samples[split:]

    model = train(train_set)
    global_acc, scoped_acc = evaluate(model, test_set, crop_to_labels)

    # Retrain on ALL data for the shipped artifact (more coverage in production).
    final = train(samples)
    final.update({
        'treatments': treatments,
        'disease_names': disease_names,
        'crop_to_labels': crop_to_labels,
        'stopwords': sorted(STOPWORDS),
        'alpha': ALPHA,
        'trained_at': datetime.now(timezone.utc).isoformat(),
        'metrics': {
            'n_samples': len(samples),
            'n_train': len(train_set),
            'n_test': len(test_set),
            'n_classes': len(final['classes']),
            'global_accuracy': round(global_acc, 3),
            'scoped_accuracy': round(scoped_acc, 3),
        },
    })

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(MODEL_PATH, 'w', encoding='utf-8') as fh:
        json.dump(final, fh)

    print('Training complete.')
    print(f'  samples         : {len(samples)}  (train {len(train_set)} / test {len(test_set)})')
    print(f'  classes         : {len(final["classes"])}')
    print(f'  global accuracy : {global_acc:.1%}  (guessing among all diseases)')
    print(f'  scoped accuracy : {scoped_acc:.1%}  (candidates limited to the chosen crop)')
    print(f'  model saved     : {MODEL_PATH}')


if __name__ == '__main__':
    main()
