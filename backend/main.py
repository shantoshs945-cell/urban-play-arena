from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv
from typing import Optional
from datetime import datetime, timedelta, timezone
import os
import smtplib
from email.mime.text import MIMEText

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")        # urbanplayarena@gmail.com
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")  # 16-char app password
OWNER_EMAIL = os.getenv("OWNER_EMAIL", GMAIL_ADDRESS)  # where owner notifications go

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
    email: Optional[str] = None
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

def send_email(to_email: str, subject: str, body: str):
    """Send an email via Gmail SMTP. Fails silently (logs only) so it never breaks booking flow."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD or not to_email:
        print(f"[email skipped] missing config or recipient: {to_email}")
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = to_email
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [to_email], msg.as_string())
        print(f"[email sent] to {to_email}: {subject}")
    except Exception as e:
        print(f"[email failed] to {to_email}: {e}")

ALL_SLOTS = [
    "06:00 AM – 07:00 AM","07:00 AM – 08:00 AM","08:00 AM – 09:00 AM",
    "09:00 AM – 10:00 AM","10:00 AM – 11:00 AM","11:00 AM – 12:00 PM",
    "12:00 PM – 01:00 PM","01:00 PM – 02:00 PM","02:00 PM – 03:00 PM",
    "03:00 PM – 04:00 PM","04:00 PM – 05:00 PM","05:00 PM – 06:00 PM",
    "06:00 PM – 07:00 PM","07:00 PM – 08:00 PM","08:00 PM – 09:00 PM",
    "09:00 PM – 10:00 PM","10:00 PM – 11:00 PM"
]

PENDING_EXPIRY_MINUTES = 60  # auto-cancel pending bookings older than this

def expire_old_pending():
    """Auto-cancel any pending booking older than PENDING_EXPIRY_MINUTES. Called on every /slots check."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=PENDING_EXPIRY_MINUTES)).isoformat()
        res = supabase.table("bookings")\
            .select("id, created_at")\
            .eq("status", "pending")\
            .lt("created_at", cutoff)\
            .execute()
        for b in res.data:
            supabase.table("bookings").update({"status": "cancelled"}).eq("id", b["id"]).execute()
    except Exception as e:
        print(f"[expire check failed] {e}")

# ── ROUTES ──

@app.get("/")
def root():
    return {"status": "Urban Play Arena API is running"}

# Get booked slots for a specific date (pending + active both block the slot)
@app.get("/slots")
def get_slots(date: str):
    expire_old_pending()  # cleanup expired holds on every slot check

    res = supabase.table("bookings")\
        .select("slot, duration, status")\
        .eq("date", date)\
        .in_("status", ["active", "pending"])\
        .execute()

    booked = set()
    for b in res.data:
        idx = ALL_SLOTS.index(b["slot"]) if b["slot"] in ALL_SLOTS else -1
        if idx >= 0:
            for i in range(b.get("duration", 1)):
                if idx + i < len(ALL_SLOTS):
                    booked.add(ALL_SLOTS[idx + i])

    return {"date": date, "booked_slots": list(booked)}

# Customer creates a booking (starts as PENDING until admin confirms payment)
@app.post("/bookings")
def create_booking(booking: BookingCreate):
    expire_old_pending()

    existing = supabase.table("bookings")\
        .select("id")\
        .eq("date", booking.date)\
        .eq("slot", booking.slot)\
        .in_("status", ["active", "pending"])\
        .execute()

    if existing.data:
        raise HTTPException(status_code=409, detail="Slot already booked")

    if booking.duration == 2:
        idx = ALL_SLOTS.index(booking.slot) if booking.slot in ALL_SLOTS else -1
        if idx >= 0 and idx + 1 < len(ALL_SLOTS):
            next_slot = ALL_SLOTS[idx + 1]
            existing2 = supabase.table("bookings")\
                .select("id")\
                .eq("date", booking.date)\
                .eq("slot", next_slot)\
                .in_("status", ["active", "pending"])\
                .execute()
            if existing2.data:
                raise HTTPException(status_code=409, detail="Next slot already booked")

    data = booking.dict()
    data["status"] = "pending"
    res = supabase.table("bookings").insert(data).execute()
    saved = res.data[0]

    # Notify owner by email — new booking awaiting payment verification
    send_email(
        OWNER_EMAIL,
        f"🏏 New Booking Request — {booking.name}",
        f"""A new slot booking is awaiting your confirmation.

Customer: {booking.name}
Phone: {booking.phone}
Date: {booking.date}
Slot: {booking.slot} ({booking.duration}hr)
Players: {booking.players}
Amount: ₹{booking.amount} ({booking.amount_type})

Please verify the payment in your PhonePe and confirm or reject this booking in the admin dashboard within {PENDING_EXPIRY_MINUTES} minutes, or it will auto-expire.

— Urban Play Arena System"""
    )

    return {"success": True, "booking": saved}

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

# Customer checks their booking status by phone number
@app.get("/bookings/check")
def check_booking_status(phone: str):
    res = supabase.table("bookings")\
        .select("*")\
        .eq("phone", phone)\
        .order("created_at", desc=True)\
        .limit(10)\
        .execute()

    return {"bookings": res.data}

# ── ADMIN ROUTES ──

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

@app.post("/admin/bookings")
def admin_create_booking(
    booking: BookingCreate,
    x_admin_token: Optional[str] = Header(None)
):
    check_admin(x_admin_token)

    existing = supabase.table("bookings")\
        .select("id")\
        .eq("date", booking.date)\
        .eq("slot", booking.slot)\
        .in_("status", ["active", "pending"])\
        .execute()

    if existing.data:
        raise HTTPException(status_code=409, detail="Slot already booked")

    data = booking.dict()
    data["type"] = "offline"
    data["status"] = "active"
    res = supabase.table("bookings").insert(data).execute()
    return {"success": True, "booking": res.data[0]}

# Admin updates booking status (confirm pending -> active, reject -> cancelled, etc.)
@app.patch("/admin/bookings/{booking_id}")
def admin_update_booking(
    booking_id: int,
    update: BookingUpdate,
    x_admin_token: Optional[str] = Header(None)
):
    check_admin(x_admin_token)

    # fetch booking first so we know who to email
    existing = supabase.table("bookings").select("*").eq("id", booking_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Booking not found")
    booking = existing.data[0]

    res = supabase.table("bookings")\
        .update({"status": update.status})\
        .eq("id", booking_id)\
        .execute()

    if not res.data:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Email customer about confirm/reject decisions
    if booking.get("email"):
        if update.status == "active":
            send_email(
                booking["email"],
                "✅ Your Urban Play Arena Booking is Confirmed!",
                f"""Hi {booking['name']},

Great news! Your booking has been confirmed.

Date: {booking['date']}
Slot: {booking['slot']} ({booking.get('duration',1)}hr)
Amount Paid: ₹{booking.get('amount',0)}

See you on the pitch! 🏏

— Urban Play Arena"""
            )
        elif update.status == "cancelled":
            send_email(
                booking["email"],
                "❌ Urban Play Arena Booking — Not Confirmed",
                f"""Hi {booking['name']},

Unfortunately we could not confirm your booking for:

Date: {booking['date']}
Slot: {booking['slot']}

This may be because payment was not received or verified in time. If you did pay, please contact us directly at +91 70135 34047.

— Urban Play Arena"""
            )

    return {"success": True, "booking": res.data[0]}

@app.post("/admin/login")
def admin_login(body: dict):
    password = body.get("password")
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password")
    return {"success": True, "token": ADMIN_PASSWORD}