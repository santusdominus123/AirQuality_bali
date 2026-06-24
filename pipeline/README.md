# Data pipeline

Generates the JSON the dashboard loads, from the real air-quality dataset.

## Regenerate the dashboard data

```bash
python3 pipeline/generate_data.py
```

Reads `pipeline/source/` and writes `data/*.json`. No third-party packages —
Python standard library only.

## Source files (`pipeline/source/`)

- `Data_Final.csv` — hourly air quality + weather per regency (the "Final_Data").
- `koordinat.json` — daerah → kecamatan → coordinate points.

## Outputs (`data/`)

| file                    | feeds                                   |
|-------------------------|-----------------------------------------|
| `current.json`          | map markers + dashboard cards (latest snapshot per regency) |
| `trend.json`            | Tren AQI chart (last 24h)               |
| `prediction.json`       | Prediksi AQI chart (next 24h)           |
| `pollutant_series.json` | Info Polutan: Tren & Prediksi Polutan   |
| `history.json`          | Data Histori table + CSV                |
| `accuracy.json`         | Akurasi Model page                      |

## Notes

- **Coordinates → kecamatan → daerah**: each regency's marker sits at the mean
  of all its coordinate points (`koordinat.json`). Snapshot/history values come
  straight from `Data_Final.csv` (already aggregated per regency).
- **Prediction**: the trained `.joblib` models + `metadata.json` were not
  available locally (only a 1 GB `co_best_model.joblib`, missing the other 7 and
  the feature list). So `prediction.json` / forecast series use the real
  historical **diurnal pattern** (per hour-of-day average over the last 30 days)
  as a transparent stand-in. To use the actual models, run `input_model.ipynb`
  with the full `saved_best_models/` folder and export its `prediction_result`
  to `data/prediction.json` in the same shape.
- **Accuracy**: `accuracy.json` holds the real MAE/RMSE/R² per pollutant taken
  from `train.ipynb`'s evaluation (best model per pollutant by RMSE).
