# Predictive model (scikit-learn)

Code: `backend/app/ml/` (`features.py`, `train.py`, `synthetic.py`); served by `backend/app/advice.py`
(`GET /devices/{id}/advice`) and stored by the scheduled prediction job.

Models: Random Forest and Gradient Boosting, as in the report. Target: will soil moisture fall below the
dry threshold within the horizon. Features: sensor trends from the per-minute summary rows plus the
rain-forecast flag. Time-ordered train/test split (no shuffling).

## Status

* **The code is written and unit-tested for plumbing. No model has been trained on real data, and no
  accuracy figure exists.**
* `train.py` refuses to train on fewer than 2880 rows (2 days) or fewer than 30 examples of a class, so a
  meaningless model cannot be produced by accident.
* `synthetic.py` makes FAKE data so the code can be exercised. A model trained on it is flagged
  `synthetic`, is ignored by the API (unless `ML_ALLOW_SYNTHETIC`), and its numbers must never go in the report.
* Until a model trained on the project's own logged data is installed (`ML_MODEL_PATH`), the app shows
  "no trained model active" and irrigation uses the local rule (skip if forecast rain probability >= threshold).

## To get a real model

1. Run the firmware with `EXPERIMENT_MODE = false` for several days; summaries accumulate in PostgreSQL.
2. Export: `cd backend && python ../tools/export_summaries_csv.py --device <id> --out summaries.csv`
   then train: `python -m app.ml.train summaries.csv --dry-pct 30 --horizon-min 360 --out model.joblib`
   (default horizon 360 min = 6 h; the report says 6-24 h). Point `ML_MODEL_PATH` at the result.
3. Report only the held-out numbers it prints, with the data size and period.
