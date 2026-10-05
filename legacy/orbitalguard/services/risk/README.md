# 🛡️ Risk Agent (Agent 3)

**Role**: Conjunction Risk Assessment & Hazard Scoring  
**Contract Output**: `shared.schemas.risk.RiskAssessment`  
**Contract Input**: `shared.schemas.conjunction.ConjunctionCandidate` from Tracking/Screening Service  
**Default Port**: `8001`  

---

## 1. Overview & Responsibilities

The Risk Agent is responsible for evaluating potential close approach conjunction candidates identified by the Screening Agent. It calculates:
- Time remaining until Time of Closest Approach (`time_to_tca_minutes`).
- Deterministic hazard score from $0.0$ to $100.0$ (`risk_score`).
- Explainable risk classification tier (`risk_level`: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`).
- Operator-friendly risk diagnostic notes.

---

## 2. Risk Scoring Formula

The deterministic multi-factor hazard scoring model combines three key dimensions:

1. **Separation Distance ($S_{\text{dist}}$, 55% weight)**:
   - $d \le 1.0\text{ km} \rightarrow 100.0$
   - $1.0\text{ km} < d \le 50.0\text{ km} \rightarrow 100 \times ((50 - d) / 49)^{1.4}$
   - $d > 50.0\text{ km} \rightarrow 0.0$
2. **Time-to-TCA Urgency ($S_{\text{time}}$, 30% weight)**:
   - $t \le 15\text{ min} \rightarrow 100.0$
   - $15 < t \le 90\text{ min} \rightarrow 100 - 80 \times ((t - 15) / 75)$
   - $t > 90\text{ min} \rightarrow 20.0$
3. **Relative Velocity Severity ($S_{\text{vel}}$, 15% weight)**:
   - Scaled from $2.0\text{ km/s}$ ($20.0$) to $14.0\text{ km/s}$ ($100.0$).

$$\text{risk\_score} = \text{clamp}(0.55 \cdot S_{\text{dist}} + 0.30 \cdot S_{\text{time}} + 0.15 \cdot S_{\text{vel}}, 0.0, 100.0)$$

---

## 3. API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service health status and metadata |
| `POST` | `/assess-risk` | Score a `ConjunctionCandidate` payload or by `?conjunction_id=` |
| `POST` | `/batch-assess` | Batch assess multiple conjunction candidates |

---

## 4. Running the Service & Tests

### Run Service
```bash
python -m uvicorn services.risk.app.main:app --host 0.0.0.0 --port 8001 --reload
```

### Run Tests
```bash
python -m pytest services/risk/tests/ -v
```
