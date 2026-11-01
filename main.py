import os
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client

SUPABASE_URL = "https://znophlwtwpntupvurbtz.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inpub3BobHd0d3BudHVwdnVyYnR6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTMzODUzNzAsImV4cCI6MjEwODk2MTM3MH0.chTlj53KGcZIGdIrM7l62bsySdpDvPcPeA9ACChTnqk")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="Smart Irrigation Decision Engine", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TelemetryPayload(BaseModel):
    moisture_pct: float = Field(..., ge=0, le=100)
    raw_adc: int = Field(..., ge=0, le=4095)
    temperature_c: float
    pump_state: bool
    trigger_type: str = Field(default="PERIODIC")

class DecisionResponse(BaseModel):
    status: str
    pump_command: bool
    water_duration_sec: int
    ai_decision: str
    ai_confidence: float
    reason: str
    trigger_type: str

def evaluate_irrigation_need(moisture: float, temp: float, pump_active: bool) -> tuple[bool, int, str, float, str]:
    if temp <= -100.0 or temp > 65.0:
        return False, 0, "STANDBY", 0.99, "Sensor fault detected."
    if pump_active:
        if moisture >= 65.0:
            return False, 0, "STANDBY", 0.95, "Moisture target achieved."
        return True, 0, "RUNNING", 0.95, "Pump is actively irrigating."
    if moisture >= 65.0:
        return False, 0, "STANDBY", 0.98, "Soil moisture optimal."
    if moisture < 25.0:
        return True, 0, "IRRIGATE", 0.95, "Critical moisture deficit."
    if 25.0 <= moisture < 40.0 and temp >= 28.0:
        return True, 0, "IRRIGATE", 0.88, "High evaporation heat demand."
    return False, 0, "STANDBY", 0.92, "Hydration within limits."

@app.get("/")
def health_check():
    return {"status": "online", "service": "Smart Irrigation Decision Engine"}

@app.post("/api/telemetry", response_model=DecisionResponse)
def process_telemetry(data: TelemetryPayload):
    try:
        if data.trigger_type in ["MANUAL_ON", "MANUAL_OFF"]:
            pump_cmd = data.pump_state
            decision = data.trigger_type
            confidence = 1.0
            duration = 0
            reason = f"Manual user override: Pump set to {'ON' if data.pump_state else 'OFF'}."
        else:
            pump_cmd, duration, decision, confidence, reason = evaluate_irrigation_need(
                moisture=data.moisture_pct,
                temp=data.temperature_c,
                pump_active=data.pump_state
            )

        db_payload = {
            "moisture_pct": data.moisture_pct,
            "raw_adc": data.raw_adc,
            "temperature_c": data.temperature_c,
            "pump_state": data.pump_state,
            "ai_decision": decision,
            "ai_confidence": confidence,
            "water_duration_sec": duration,
            "trigger_type": data.trigger_type
        }
        supabase.table("sensor_telemetry").insert(db_payload).execute()

        return DecisionResponse(
            status="SUCCESS",
            pump_command=pump_cmd,
            water_duration_sec=duration,
            ai_decision=decision,
            ai_confidence=confidence,
            reason=reason,
            trigger_type=data.trigger_type
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inference/DB Error: {str(e)}")
