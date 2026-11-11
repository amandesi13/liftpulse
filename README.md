# LiftPulse

Turns ordinary lift rides into a continuous health check for the lift, using
the motion sensors of riders' smartphones. No hardware in the lift, no wiring,
no machine learning: plain signal processing and rule-based scoring.

> Work in progress: this README is completed in the last milestone.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
LIFTPULSE_ADMIN_TOKEN=demo uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

## Tests

```bash
pytest
```
