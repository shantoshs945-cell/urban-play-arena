from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv
from typing import Optional
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="Urban Play Arena API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── MODELS ──
class BookingCreate(BaseModel):
    name: str
    phone: str
    date: str
    slot: str
    duration: int = 1
    players: int = 6
    payment: str = "phonepay"
    amount: int = 0
    amount_type: str = "full"
    type: str = "online"

class BookingUpdate(BaseModel):
    status: str

# ── HELPER ──
def check_admin(token: Optional[str] = None):
    if token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

ALL_SLOTS = [
    "06:00 AM – 07:00 AM","07:00 AM – 08:00 AM","08:00 AM – 09:00 AM",
    "09:00 AM – 10:00 AM","10:00 AM – 11:00 AM","11:00 AM – 12:00 PM",
    "12:00 PM – 01:00 PM","01:00 PM – 02:00 PM","02:00 PM – 03:00 PM",
    "03:00 PM – 04:00 PM","04:00 PM – 05:00 PM","05:00 PM – 06:00 PM",
    "06:00 PM – 07:00 PM","07:00 PM – 08:00 PM","08:00 PM – 09:00 PM",
    "09:00 PM – 10:00 PM","10:00 PM – 11:00 PM"
]

# ── ROUTES ──

# Health check
@app.get("/")
def root():
    return {"status": "Urban Play Arena API is running"}

# Get booked slots for a specific date
@app.get("/slots")
def get_slots(date: str):
    res = supabase.table("bookings")\
        .select("slot, duration, status")\
        .eq("date", date)\
        .eq("status", "active")\
        .execute()

    booked = set()
    for b in res.data:
        idx = ALL_SLOTS.index(b["slot"]) if b["slot"] in ALL_SLOTS else -1
        if idx >= 0:
            for i in range(b.get("duration", 1)):
                if idx + i < len(ALL_SLOTS):
                    booked.add(ALL_SLOTS[idx + i])

    return {"date": date, "booked_slots": list(booked)}

# Customer creates a booking
@app.post("/bookings")
def create_booking(booking: BookingCreate):
    # Check if slot is already taken
    existing = supabase.table("bookings")\
        .select("id")\
        .eq("date", booking.date)\
        .eq("slot", booking.slot)\
        .eq("status", "active")\
        .execute()

    if existing.data:
        raise HTTPException(status_code=409, detail="Slot already booked")

    # For 2hr bookings check next slot too
    if booking.duration == 2:
        idx = ALL_SLOTS.index(booking.slot) if booking.slot in ALL_SLOTS else -1
        if idx >= 0 and idx + 1 < len(ALL_SLOTS):
            next_slot = ALL_SLOTS[idx + 1]
            existing2 = supabase.table("bookings")\
                .select("id")\
                .eq("date", booking.date)\
                .eq("slot", next_slot)\
                .eq("status", "active")\
                .execute()
            if existing2.data:
                raise HTTPException(status_code=409, detail="Next slot already booked")

    res = supabase.table("bookings").insert(booking.dict()).execute()
    return {"success": True, "booking": res.data[0]}

# Customer cancels their booking
@app.delete("/bookings/{booking_id}")
def cancel_booking(booking_id: int):
    res = supabase.table("bookings")\
        .update({"status": "cancelled"})\
        .eq("id", booking_id)\
        .execute()

    if not res.data:
        raise HTTPException(status_code=404, detail="Booking not found")

    return {"success": True, "message": "Booking cancelled"}

# ── ADMIN ROUTES ──

# Get all bookings (admin)
@app.get("/admin/bookings")
def admin_get_bookings(
    status: Optional[str] = None,
    date: Optional[str] = None,
    x_admin_token: Optional[str] = Header(None)
):
    check_admin(x_admin_token)
    query = supabase.table("bookings").select("*").order("created_at", desc=True)
    if status:
        query = query.eq("status", status)
    if date:
        query = query.eq("date", date)
    res = query.execute()
    return {"bookings": res.data}

# Admin adds offline booking
@app.post("/admin/bookings")
def admin_create_booking(
    booking: BookingCreate,
    x_admin_token: Optional[str] = Header(None)
):
    check_admin(x_admin_token)

    # Check conflict
    existing = supabase.table("bookings")\
        .select("id")\
        .eq("date", booking.date)\
        .eq("slot", booking.slot)\
        .eq("status", "active")\
        .execute()

    if existing.data:
        raise HTTPException(status_code=409, detail="Slot already booked")

    data = booking.dict()
    data["type"] = "offline"
    res = supabase.table("bookings").insert(data).execute()
    return {"success": True, "booking": res.data[0]}

# Admin updates booking status (cancel or restore)
@app.patch("/admin/bookings/{booking_id}")
def admin_update_booking(
    booking_id: int,
    update: BookingUpdate,
    x_admin_token: Optional[str] = Header(None)
):
    check_admin(x_admin_token)
    res = supabase.table("bookings")\
        .update({"status": update.status})\
        .eq("id", booking_id)\
        .execute()

    if not res.data:
        raise HTTPException(status_code=404, detail="Booking not found")

    return {"success": True, "booking": res.data[0]}

# Admin login check
@app.post("/admin/login")
def admin_login(body: dict):
    password = body.get("password")
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password")
    return {"success": True, "token": ADMIN_PASSWORD}