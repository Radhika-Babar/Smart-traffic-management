"""Model training, evaluation and live forecasting.

Three candidates per target on a chronological hold-out: persistence (naive
baseline), linear regression and a random forest. All reported metrics are
measured on the hold-out; nothing is assumed.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sqlalchemy import select, update

from app.algorithms.congestion import CongestionInput, CongestionThresholds, classify
from app.core.clock import utcnow
from app.core.config import DATA_DIR
from app.core.database import Database
from app.ml.dataset import TARGETS, add_features, load_bins, training_table
from app.models import ModelEvaluation, TrafficPrediction

logger = logging.getLogger(__name__)

MODEL_PATH = DATA_DIR / "models" / "traffic_models.joblib"
TEST_SHARE = 0.2
MIN_ROWS = 500
BACKTEST_POINTS = 144


def predicted_level(density: float, wait: float, thresholds: CongestionThresholds) -> str:
    return classify(CongestionInput(density * 0.3, density, wait, None), thresholds)


class PredictionService:
    def __init__(self, database: Database, model_path: Path = MODEL_PATH) -> None:
        self.database = database
        self.model_path = model_path
        self.bundle: dict | None = None
        self.training = False
        self.last_error: str | None = None
        self.thresholds = CongestionThresholds()
        self._lock = threading.Lock()
        self.last_forecast_bin: datetime | None = None

    def load(self) -> bool:
        if self.model_path.exists():
            try:
                self.bundle = joblib.load(self.model_path)
                return True
            except Exception as error:
                logger.warning("Could not load saved models: %s", error)
        return False

    def train_in_background(self) -> bool:
        if self.training:
            return False
        threading.Thread(target=self.train, name="ml-train", daemon=True).start()
        return True

    def train(self) -> dict | None:
        with self._lock:
            self.training = True
            self.last_error = None
            try:
                return self._train()
            except Exception as error:
                self.last_error = str(error)
                logger.exception("Model training failed")
                return None
            finally:
                self.training = False

    def _train(self) -> dict | None:
        with self.database.session() as session:
            frame = load_bins(session)
        if frame.empty:
            self.last_error = "No traffic history available yet"
            return None
        table, features = training_table(frame)
        if len(table) < MIN_ROWS:
            self.last_error = f"Need {MIN_ROWS} training rows, have {len(table)}"
            return None
        split = table["bucket"].quantile(1 - TEST_SHARE)
        train, test = table[table["bucket"] < split], table[table["bucket"] >= split]
        run_id = uuid.uuid4().hex[:10]
        models: dict = {}
        evaluations: list[dict] = []
        backtest_predictions: dict[str, np.ndarray] = {}

        for target in TARGETS:
            y_train, y_test = train[f"y_{target}"], test[f"y_{target}"]
            candidates: dict = {
                "Persistence": None,
                "LinearRegression": LinearRegression(),
                "RandomForest": RandomForestRegressor(
                    n_estimators=60, max_depth=12, min_samples_leaf=4, n_jobs=2, random_state=42,
                ),
            }
            scored = []
            for name, model in candidates.items():
                if model is None:
                    predicted = test[f"{target}_now"].to_numpy()
                else:
                    model.fit(train[features], y_train)
                    predicted = model.predict(test[features])
                rmse = float(np.sqrt(mean_squared_error(y_test, predicted)))
                importance = None
                if hasattr(model, "feature_importances_"):
                    ranked = sorted(zip(features, model.feature_importances_, strict=True), key=lambda p: -p[1])[:10]
                    importance = {k: round(float(v), 4) for k, v in ranked}
                scored.append({
                    "name": name, "model": model, "predicted": predicted, "rmse": rmse,
                    "mae": float(mean_absolute_error(y_test, predicted)),
                    "r2": float(r2_score(y_test, predicted)), "importance": importance,
                })
            learned = [s for s in scored if s["model"] is not None]
            best = min(learned, key=lambda s: s["rmse"])
            models[target] = {"name": best["name"], "model": best["model"]}
            backtest_predictions[target] = best["predicted"]
            for item in scored:
                evaluations.append({
                    "run_id": run_id, "model_name": item["name"], "target": target,
                    "mae": round(item["mae"], 3), "rmse": round(item["rmse"], 3), "r2": round(item["r2"], 4),
                    "train_rows": len(train), "test_rows": len(test), "feature_importance": item["importance"],
                    "selected": item is best,
                })

        sample = test.assign(**{f"p_{t}": backtest_predictions[t] for t in TARGETS})
        backtest = {}
        for intersection_id, part in sample.groupby("intersection_id"):
            tail = part.sort_values("bucket").tail(BACKTEST_POINTS)
            backtest[intersection_id] = [
                {
                    "t": row.bucket.isoformat() + "Z",
                    **{f"actual_{t}": round(float(getattr(row, f"y_{t}")), 2) for t in TARGETS},
                    **{f"predicted_{t}": round(float(getattr(row, f"p_{t}")), 2) for t in TARGETS},
                }
                for row in tail.itertuples()
            ]

        bundle = {
            "run_id": run_id, "trained_at": utcnow().isoformat() + "Z", "features": features, "models": models,
            "backtest": backtest, "train_rows": len(train), "test_rows": len(test),
            "split_at": split.isoformat() + "Z",
        }
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(bundle, self.model_path)
        self.bundle = bundle
        with self.database.session() as session, session.begin():
            session.add_all(ModelEvaluation(**row) for row in evaluations)
        logger.info("Trained models (run %s) on %s rows", run_id, len(train))
        return bundle

    def forecast(self, sim_now: datetime) -> int:
        """Forecast the next five-minute bin for each intersection, then back-fill actuals."""
        if self.bundle is None:
            return 0
        current = sim_now.replace(second=0, microsecond=0)
        current -= timedelta(minutes=current.minute % 5)
        if self.last_forecast_bin == current:
            return 0
        self.last_forecast_bin = current
        with self.database.session() as session:
            frame = load_bins(session, current - timedelta(hours=3))
        if frame.empty:
            return 0
        self._record_actuals(frame)
        table, features = add_features(frame)
        latest = table.sort_values("bucket").groupby("intersection_id").tail(1).dropna(subset=features)
        if latest.empty:
            return 0
        target_at = current + timedelta(minutes=5)
        with self.database.session() as session:
            existing = set(session.scalars(
                select(TrafficPrediction.intersection_id).where(TrafficPrediction.target_at == target_at)
            ))
        latest = latest[~latest["intersection_id"].isin(existing)]
        if latest.empty:
            return 0
        outputs = {t: self.bundle["models"][t]["model"].predict(latest[features]) for t in TARGETS}
        rows = []
        for index, row in enumerate(latest.itertuples()):
            volume = max(0.0, float(outputs["volume"][index]))
            wait = max(0.0, float(outputs["wait"][index]))
            density = float(np.clip(outputs["density"][index], 0, 100))
            rows.append(TrafficPrediction(
                intersection_id=row.intersection_id, generated_at=sim_now,
                target_at=target_at,
                model_name=self.bundle["models"]["volume"]["name"],
                predicted_volume=round(volume, 1), predicted_wait_s=round(wait, 1),
                predicted_density=round(density, 1),
                predicted_level=predicted_level(density, wait, self.thresholds),
            ))
        with self.database.session() as session, session.begin():
            session.add_all(rows)
        return len(rows)

    def _record_actuals(self, frame: pd.DataFrame) -> None:
        with self.database.session() as session, session.begin():
            pending = session.scalars(
                select(TrafficPrediction).where(TrafficPrediction.actual_volume.is_(None))
            ).all()
            if not pending:
                return
            lookup = {(r.intersection_id, r.bucket.to_pydatetime()): r for r in frame.itertuples()}
            for prediction in pending:
                actual = lookup.get((prediction.intersection_id, prediction.target_at))
                if actual is not None:
                    session.execute(update(TrafficPrediction).where(TrafficPrediction.id == prediction.id).values(
                        actual_volume=float(actual.volume), actual_wait_s=float(actual.wait),
                        actual_density=float(actual.density),
                    ))
