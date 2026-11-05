
from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple
import numpy as np
import joblib
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score
from xgboost import XGBClassifier

from feature_extractor import FEATURE_NAMES, FEATURE_BOUNDS

MODEL_PATH = Path(__file__).parent / "viability_xgb.joblib"
METADATA_PATH = Path(__file__).parent / "model_metadata.json"

CLASS_MAP = {
    0: "SUITABLE",
    1: "CAUTION",
    2: "RECONSIDER",
}
REV_CLASS_MAP = {v: k for k, v in CLASS_MAP.items()}


def generate_empirical_training_dataset(
    n_samples: int = 10000,
    random_seed: int = 42,
) -> Tuple[np.ndarray, np.ndarray]:
    rng = np.random.RandomState(random_seed)

    dscr = rng.gamma(shape=4.0, scale=0.35, size=n_samples)

    subsidy_coverage_ratio = rng.choice([0.0, 0.15, 0.25, 0.35], size=n_samples, p=[0.25, 0.20, 0.35, 0.20])
    subsidy_coverage_ratio += rng.uniform(-0.02, 0.02, size=n_samples)

    loan_to_income_ratio = rng.gamma(shape=2.5, scale=0.55, size=n_samples)

    log_projected_population = rng.normal(loc=3.8, scale=0.5, size=n_samples)

    msme_density_per_10k = rng.exponential(scale=12.0, size=n_samples)

    infrastructure_score = rng.normal(loc=6.5, scale=2.0, size=n_samples)

    cpi_inflation_pct = rng.normal(loc=5.2, scale=2.0, size=n_samples)

    working_capital_months_buffer = rng.gamma(shape=2.5, scale=1.0, size=n_samples)

    competition_intensity = rng.beta(a=2.0, b=4.0, size=n_samples)

    weather_risk_score = rng.beta(a=1.5, b=4.5, size=n_samples)

    X_raw = np.column_stack([
        dscr,
        subsidy_coverage_ratio,
        loan_to_income_ratio,
        log_projected_population,
        msme_density_per_10k,
        infrastructure_score,
        cpi_inflation_pct,
        working_capital_months_buffer,
        competition_intensity,
        weather_risk_score,
    ])

    for i, name in enumerate(FEATURE_NAMES):
        low, high = FEATURE_BOUNDS[name]
        X_raw[:, i] = np.clip(X_raw[:, i], low, high)

    labels = np.zeros(n_samples, dtype=int)

    for idx in range(n_samples):
        x = X_raw[idx]
        f_dscr = x[0]
        f_sub = x[1]
        f_lti = x[2]
        f_pop = x[3]
        f_msme = x[4]
        f_infra = x[5]
        f_cpi = x[6]
        f_wc = x[7]
        f_comp = x[8]
        f_weather = x[9]

        risk_penalty = 0.0

        if f_infra < 4.0:
            risk_penalty += (4.0 - f_infra) * 0.4
        if f_cpi > 7.5:
            risk_penalty += (f_cpi - 7.5) * 0.3
        if f_comp > 0.60:
            risk_penalty += (f_comp - 0.60) * 2.5
        if f_weather > 0.50:
            risk_penalty += (f_weather - 0.50) * 2.0
        if f_wc < 0.60:
            risk_penalty += (0.60 - f_wc) * 0.6
        if f_lti > 3.0:
            risk_penalty += (f_lti - 3.0) * 0.5

        bonus = (f_sub * 0.8) + (max(f_pop - 3.5, 0) * 0.2) + (max(f_wc - 2.0, 0) * 0.1)

        composite_score = (f_dscr * 1.5) + bonus - risk_penalty

        if f_dscr < 0.95 or (f_dscr < 1.10 and f_lti > 3.0) or (f_dscr < 1.15 and risk_penalty > 1.8) or composite_score < 1.30:
            labels[idx] = 2
        elif f_dscr >= 1.33 and composite_score >= 1.95 and f_lti <= 3.2 and risk_penalty < 1.5:
            labels[idx] = 0
        else:
            labels[idx] = 1

    return X_raw, labels


def train_and_evaluate_model():
    print("=" * 80)
    print("UDYAM SAATHI — PHASE 3 XGBOOST VIABILITY MODEL TRAINING")
    print("=" * 80)

    X, y = generate_empirical_training_dataset(n_samples=10000, random_seed=42)
    print(f"Dataset generated: {X.shape[0]} samples, {X.shape[1]} features")
    for cls_idx, cls_name in CLASS_MAP.items():
        count = int(np.sum(y == cls_idx))
        pct = (count / len(y)) * 100
        print(f"  Class {cls_idx} ({cls_name:10s}): {count:5d} ({pct:5.1f}%)")

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    clf = XGBClassifier(
        n_estimators=160,
        max_depth=4,
        learning_rate=0.08,
        subsample=0.85,
        colsample_bytree=0.85,
        min_child_weight=2,
        objective="multi:softprob",
        num_class=3,
        random_state=42,
        eval_metric="mlogloss",
    )

    cv_scores = cross_val_score(clf, X, y, cv=cv, scoring="accuracy")
    print("\n5-Fold Stratified Cross-Validation Accuracy:")
    for fold, score in enumerate(cv_scores, 1):
        print(f"  Fold {fold}: {score * 100:.2f}%")
    print(f"  Mean CV Accuracy: {cv_scores.mean() * 100:.2f}% (+/- {cv_scores.std() * 100:.2f}%)")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, stratify=y, random_state=42
    )

    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)
    test_acc = accuracy_score(y_test, y_pred)
    macro_f1 = f1_score(y_test, y_pred, average="macro")

    print("\nHold-Out Test Set Performance:")
    print(f"  Accuracy: {test_acc * 100:.2f}%")
    print(f"  Macro F1: {macro_f1 * 100:.2f}%")
    print("\nDetailed Classification Report:")
    print(classification_report(y_test, y_pred, target_names=[CLASS_MAP[i] for i in range(3)], digits=4))

    booster = clf.get_booster()
    importance_gain = booster.get_score(importance_type="gain")
    importance_weight = booster.get_score(importance_type="weight")

    feature_gain_list = []
    for i, name in enumerate(FEATURE_NAMES):
        f_key = f"f{i}"
        gain = float(importance_gain.get(f_key, 0.0))
        weight = float(importance_weight.get(f_key, 0.0))
        feature_gain_list.append({
            "feature_index": i,
            "feature_name": name,
            "gain": gain,
            "weight": weight,
        })

    feature_gain_list = sorted(feature_gain_list, key=lambda x: x["gain"], reverse=True)

    print("\nFeature Gain Importance Ranking:")
    for rank, item in enumerate(feature_gain_list, 1):
        print(f"  Rank {rank:2d}: {item['feature_name']:30s} (Gain: {item['gain']:8.2f}, Splits: {int(item['weight']):4d})")

    joblib.dump(clf, MODEL_PATH)
    print(f"\n[OK] Model artifact serialized to {MODEL_PATH}")

    metadata = {
        "model_type": "XGBClassifier",
        "library_versions": {
            "xgboost": "2.1.1",
            "scikit_learn": "1.5.1",
            "joblib": "1.5.3",
            "numpy": "1.26.4",
        },
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "n_samples": int(X.shape[0]),
        "feature_count": int(X.shape[1]),
        "feature_names": FEATURE_NAMES,
        "class_mapping": CLASS_MAP,
        "cross_validation": {
            "folds": 5,
            "mean_accuracy": float(round(cv_scores.mean(), 4)),
            "std_accuracy": float(round(cv_scores.std(), 4)),
            "fold_scores": [float(round(s, 4)) for s in cv_scores],
        },
        "test_metrics": {
            "test_accuracy": float(round(test_acc, 4)),
            "macro_f1": float(round(macro_f1, 4)),
            "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
        },
        "hyperparameters": {
            "n_estimators": 160,
            "max_depth": 4,
            "learning_rate": 0.08,
            "subsample": 0.85,
            "colsample_bytree": 0.85,
            "min_child_weight": 2,
        },
        "feature_importances_ranked": feature_gain_list,
    }

    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    print(f"[OK] Model metadata serialized to {METADATA_PATH}")

    return clf, metadata


if __name__ == "__main__":
    train_and_evaluate_model()
