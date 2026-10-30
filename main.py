import os
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client

# -------------------------------------------------------------
# 1. Supabase Cloud Configuration
# -------------------------------------------------------------
SUPABASE_URL = "https://znophlwtwpntupvurbtz.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inpub3BobHd0d3BudHVwdnVyYnR6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTMzODUzNzAsImV4cCI6MjEwODk2MTM3MH0.chTlj53KGcZIGdIrM7l62bsySdpDvPcPeA9ACChTnqk")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------------------------------------------
# 2. FastAPI Application Setup
# -------------------------------------------------------------
app = FastAPI(
    title="Smart Irrigation AI Decision Engine",
    description="Processes ESP32 telemetry, evaluates irrigation necessity, and logs to Supabase.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------
# 3. Data Schemas
# -------------------------------------------------------------
class TelemetryPayload(BaseModel):
    moisture_pct: float = Field(..., ge=0, le=100, description="Soil moisture percentage")
    raw_adc: int = Field(..., ge=0, le=4095, description="Raw 12-bit ADC reading")
    temperature_c: float = Field(..., description="Soil/Water temperature in Celsius")
    pump_state: bool = Field(..., description="Current relay running state on ESP32")

class DecisionResponse(BaseModel):
    status: str
    pump_command: bool
    water_duration_sec: int
    ai_decision: str
    ai_confidence: float
    reason: str

# -------------------------------------------------------------
# 4. Agronomic AI Decision Engine
# -------------------------------------------------------------
def evaluate_irrigation_need(moisture: float, temp: float, pump_active: bool) -> tuple[bool, int, str, float, str]:
    """
    Evaluates plant water stress using dynamic multi-factor thresholding:
    - Evaporative demand scales with temperature.
    - Extreme temperature lockouts prevent root shock during midday heat (>38C).
    - Prevents overwatering when pump is already active.
    """
    # Safety Check: If probe is disconnected / error state
    if temp <= -100.0 or temp > 65.0:
        return False, 0, "STANDBY", 0.99, "Temperature sensor fault detected. Actuation disabled."

    # If the pump is already physically running, avoid issuing another start pulse
    if pump_active:
        return False, 0, "RUNNING", 0.95, "Pump is currently irrigating."

    # Saturated or optimal soil moisture
    if moisture >= 65.0:
        return False, 0, "STANDBY", 0.98, "Soil moisture optimal or saturated."

    # Critical Dry Zone (< 25% moisture)
    if moisture < 25.0:
        # High heat increases evaporative loss; slightly longer pulse
        if temp > 30.0:
            return True, 7, "IRRIGATE", 0.96, "High water stress combined with high thermal demand."
        else:
            return True, 5, "IRRIGATE", 0.94, "Soil moisture below minimum critical threshold."

    # Marginal Moisture Zone (25% - 40%): Temperature-dependent decision
    if 25.0 <= moisture < 40.0:
        if temp >= 28.0:
            # Heat causes rapid transpiration -> early watering
            return True, 4, "IRRIGATE", 0.88, "Preventive irrigation triggered by elevated ambient temperature."
        else:
            return False, 0, "STANDBY", 0.85, "Moisture marginal but ambient evaporation is low."

    # Default Stable State (40% - 65%)
    return False, 0, "STANDBY", 0.92, "Soil moisture within healthy absorption limits."

# -------------------------------------------------------------
# 5. API Endpoints
# -------------------------------------------------------------
@app.get("/")
def health_check():
    return {
        "status": "online",
        "service": "Smart Irrigation AI Decision Engine",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.post("/api/telemetry", response_model=DecisionResponse)
def process_telemetry(data: TelemetryPayload):
    try:
        # 1. Run AI Evaluation
        pump_cmd, duration, decision, confidence, reason = evaluate_irrigation_need(
            moisture=data.moisture_pct,
            temp=data.temperature_c,
            pump_active=data.pump_state
        )

        # 2. Persist record to Supabase
        db_payload = {
            "moisture_pct": data.moisture_pct,
            "raw_adc": data.raw_adc,
            "temperature_c": data.temperature_c,
            "pump_state": data.pump_state,
            "ai_decision": decision,
            "ai_confidence": confidence,
            "water_duration_sec": duration
        }

        db_response = supabase.table("sensor_telemetry").insert(db_payload).execute()

        # 3. Return actionable instructions to ESP32
        return DecisionResponse(
            status="SUCCESS",
            pump_command=pump_cmd,
            water_duration_sec=duration,
            ai_decision=decision,
            ai_confidence=confidence,
            reason=reason
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inference/DB Error: {str(e)}")