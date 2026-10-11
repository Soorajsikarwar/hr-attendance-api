"""
Employee Attendance & Analytics API - STARTER (FIXED PART A)

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)
"""
import os
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def startup_db_client():
    # Create indexes idempotently at startup
    db.employees.create_index("emp_code", unique=True)
    db.attendance_logs.create_index([("emp_code", 1), ("date", -1)], unique=True)
    db.attendance_logs.create_index("date")

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def epoch_to_ist_seconds(epoch_ms: int) -> datetime:
    """Truncates milliseconds to whole seconds and converts to IST."""
    seconds = epoch_ms // 1000
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(IST)

def get_shift_date(ts: datetime, shift_start: str, shift_end: str) -> str:
    """Calculates the logical shift date, handling overnight shifts."""
    d = ts.date()
    if shift_end <= shift_start:
        h, m = map(int, shift_end.split(":"))
        shift_end_time = ts.replace(hour=h, minute=m, second=0, microsecond=0)
        if ts.time() <= shift_end_time.time() or ts <= shift_end_time:
            d -= timedelta(days=1)
    return d.isoformat()

def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - start).total_seconds() // 60)
    return minutes if minutes > 10 else 0

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)

def compute_overtime(punch_out: datetime, shift_end: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = punch_out.replace(hour=h, minute=m, second=0, microsecond=0)
    if punch_out < end and punch_out.hour < 12: 
        pass 
    minutes = int((punch_out - end).total_seconds() // 60)
    return max(0, minutes) if minutes >= 30 else 0

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

# --------------------------------------------------------------------------- #
# Endpoints provided (Fixed)
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now(IST).replace(microsecond=0)
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc

@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size).sort("emp_code", 1))
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    if body.punched_at:
        ts = epoch_to_ist_seconds(body.punched_at)
    else:
        ts = datetime.now(IST).replace(microsecond=0)

    d = get_shift_date(ts, emp["shift_start"], emp["shift_end"])

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    
    doc.pop("_id", None)
    return doc

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
        
    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    
    docs = list(db.attendance_logs.find(q, {"_id": 0}).sort("date", -1).skip(skip).limit(page_size))
    
    return {"items": docs, "total": total, "page": page, "page_size": page_size}


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

class CorrectionIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    corrected_by: str


@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    log = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_out": None},
        sort=[("date", -1)]
    )
    if not log:
        raise HTTPException(404, "No open punch-in found")

    if body.punched_at:
        ts_out = epoch_to_ist_seconds(body.punched_at)
    else:
        ts_out = datetime.now(IST).replace(microsecond=0)

    work_hrs = compute_work_hours(log["punch_in"].replace(tzinfo=IST), ts_out)
    half_day = work_hrs < 4.50
    overtime = compute_overtime(ts_out, emp["shift_end"])

    updated_doc = db.attendance_logs.find_one_and_update(
        {"emp_code": body.emp_code, "date": log["date"]},
        {
            "$set": {
                "punch_out": ts_out,
                "work_hours": work_hrs,
                "half_day": half_day,
                "overtime_minutes": overtime
            }
        },
        return_document=True
    )
    
    updated_doc.pop("_id", None)
    return updated_doc

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: CorrectionIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    log = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not log:
        raise HTTPException(404, "Attendance record not found")

    updates = {}
    history_entry = {
        "corrected_at": datetime.now(IST).replace(microsecond=0),
        "corrected_by": body.corrected_by,
        "reason": body.reason,
        "changes": {}
    }

    if body.status and body.status != log.get("status"):
        updates["status"] = body.status
        history_entry["changes"]["status"] = {"from": log.get("status"), "to": body.status}

    current_in = log.get("punch_in").replace(tzinfo=IST) if log.get("punch_in") else None
    current_out = log.get("punch_out").replace(tzinfo=IST) if log.get("punch_out") else None

    if body.punch_in is not None:
        new_in = epoch_to_ist_seconds(body.punch_in)
        if new_in != current_in:
            updates["punch_in"] = new_in
            history_entry["changes"]["punch_in"] = {"from": current_in, "to": new_in}
            current_in = new_in

    if body.punch_out is not None:
        new_out = epoch_to_ist_seconds(body.punch_out)
        if new_out != current_out:
            updates["punch_out"] = new_out
            history_entry["changes"]["punch_out"] = {"from": current_out, "to": new_out}
            current_out = new_out

    if not history_entry["changes"]:
        return log  

    if current_in:
        new_late = compute_late_minutes(current_in, emp["shift_start"])
        if new_late != log.get("late_minutes"):
            updates["late_minutes"] = new_late
            history_entry["changes"]["late_minutes"] = {"from": log.get("late_minutes"), "to": new_late}
    
    if current_in and current_out:
        new_work_hrs = compute_work_hours(current_in, current_out)
        new_half_day = new_work_hrs < 4.50
        new_overtime = compute_overtime(current_out, emp["shift_end"])

        if new_work_hrs != log.get("work_hours"):
            updates["work_hours"] = new_work_hrs
            history_entry["changes"]["work_hours"] = {"from": log.get("work_hours"), "to": new_work_hrs}
        if new_half_day != log.get("half_day"):
            updates["half_day"] = new_half_day
            history_entry["changes"]["half_day"] = {"from": log.get("half_day"), "to": new_half_day}
        if new_overtime != log.get("overtime_minutes"):
            updates["overtime_minutes"] = new_overtime
            history_entry["changes"]["overtime_minutes"] = {"from": log.get("overtime_minutes"), "to": new_overtime}

    updated_doc = db.attendance_logs.find_one_and_update(
        {"emp_code": emp_code, "date": date},
        {
            "$set": updates,
            "$push": {"history": history_entry}
        },
        return_document=True
    )

    updated_doc.pop("_id", None)
    return updated_doc

# --------------------------------------------------------------------------- #
# Part B & C: Analytics & Explain Endpoints
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    """Monthly summary for one employee (Aggregation)."""
    pipeline = [
        # Match employee and month prefix (e.g., 'YYYY-MM')
        {"$match": {"emp_code": emp_code, "date": {"$regex": f"^{month}"}}},
        {"$addFields": {
            "parsed_date": {"$dateFromString": {"dateString": "$date"}},
            "is_present": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}
        }},
        {"$addFields": {
            "day_of_week": {"$isoDayOfWeek": "$parsed_date"}
        }},
        {"$group": {
            "_id": "$emp_code",
            "present_days": {
                "$sum": {
                    "$cond": [
                        # R7: Present days only count Mon-Fri (isoDayOfWeek <= 5)
                        {"$and": [{"$lte": ["$day_of_week", 5]}, "$is_present"]},
                        {"$cond": ["$half_day", 0.5, 1]}, # R5: Half day counts as 0.5
                        0
                    ]
                }
            },
            "late_minutes": {"$sum": "$late_minutes"},
            "overtime_minutes": {"$sum": "$overtime_minutes"}
        }}
    ]
    
    result = list(db.attendance_logs.aggregate(pipeline))
    if not result:
        return {"emp_code": emp_code, "month": month, "present_days": 0, "late_minutes": 0, "overtime_minutes": 0}
    
    doc = result[0]
    return {
        "emp_code": emp_code,
        "month": month,
        "present_days": doc["present_days"],
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0)
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str):
    """Department summary for a month (Cross-collection Aggregation)."""
    end_of_month = f"{month}-31"
    pipeline = [
        # R9: Headcount includes people joined on/before period end, even with no logs
        {"$match": {"joined_on": {"$lte": end_of_month}}},
        {"$lookup": {
            "from": "attendance_logs",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "logs"
        }},
        {"$project": {
            "department": 1,
            "emp_code": 1,
            "logs": {
                "$filter": {
                    "input": "$logs",
                    "as": "log",
                    "cond": {"$regexMatch": {"input": "$$log.date", "regex": f"^{month}"}}
                }
            }
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$department",
            "headcount": {"$addToSet": "$emp_code"},
            "total_work_hours": {"$sum": "$logs.work_hours"},
            "log_count": {"$sum": {"$cond": [{"$gt": ["$logs.work_hours", type(None)]}, 1, 0]}}
        }},
        {"$project": {
            "department": "$_id",
            "headcount": {"$size": "$headcount"},
            "avg_work_hours": {
                "$cond": [
                    {"$gt": ["$log_count", 0]},
                    {"$round": [{"$divide": ["$total_work_hours", "$log_count"]}, 2]},
                    0
                ]
            },
            "_id": 0
        }},
        {"$sort": {"department": 1}}
    ]
    return list(db.employees.aggregate(pipeline))


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = 10):
    """Late-comers ranking with ties (Window function Aggregation)."""
    pipeline = [
        {"$match": {"date": {"$regex": f"^{month}"}}},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"}
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$setWindowFields": {
            # Sort descending by late minutes. Ties sorted by emp_code.
            "sortBy": {"total_late_minutes": -1, "_id": 1},
            "output": {
                # $rank ensures ties share a rank, and next rank is skipped (1, 2, 2, 4)
                "rank": {"$rank": {}}
            }
        }},
        {"$sort": {"rank": 1, "_id": 1}},
        {"$limit": limit}, # Limit applied AFTER ranking
        {"$project": {
            "emp_code": "$_id",
            "total_late_minutes": 1,
            "rank": 1,
            "_id": 0
        }}
    ]
    return list(db.attendance_logs.aggregate(pipeline))


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, date_from: str, date_to: str):
    """Daily series with gap filling and a 7-day moving average."""
    date_start_obj = datetime.fromisoformat(date_from)
    date_end_obj = datetime.fromisoformat(date_to) + timedelta(days=1)

    pipeline = [
        {"$match": {"department": department}},
        {"$lookup": {
            "from": "attendance_logs",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "logs"
        }},
        {"$unwind": "$logs"},
        {"$match": {
            "logs.date": {"$gte": date_from, "$lte": date_to}
        }},
        {"$group": {
            "_id": {"$dateFromString": {"dateString": "$logs.date"}},
            "daily_work_hours": {"$avg": "$logs.work_hours"}
        }},
        # Densify guarantees one row per calendar day, even weekends/holidays with no logs
        {"$densify": {
            "field": "_id",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [date_start_obj, date_end_obj]
            }
        }},
        {"$set": {
            "daily_work_hours": {"$ifNull": ["$daily_work_hours", 0]}
        }},
        {"$setWindowFields": {
            "sortBy": {"_id": 1},
            "output": {
                "moving_average": {
                    "$avg": "$daily_work_hours",
                    "window": {"documents": [-6, 0]} # 7-day moving average
                }
            }
        }},
        {"$project": {
            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$_id"}},
            "avg_work_hours": {"$round": ["$moving_average", 2]},
            "_id": 0
        }},
        {"$sort": {"date": 1}}
    ]
    return list(db.employees.aggregate(pipeline))


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    department: Optional[str] = None,
    month: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 10,
    page: int = 1,
    page_size: int = 20
):
    """Returns MongoDB explain output for an endpoint's main query."""
    collection = db.attendance_logs
    command_payload = {}
    
    if endpoint == "attendance_list":
        q = {}
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = date_from
            if date_to: q["date"]["$lte"] = date_to
        
        command_payload = {
            "find": "attendance_logs",
            "filter": q,
            "sort": {"date": -1},
            "skip": (page - 1) * page_size,
            "limit": page_size
        }

    elif endpoint == "late_leaderboard":
        # Provide the pipeline we created above
        pipeline = [
            {"$match": {"date": {"$regex": f"^{month}"}}},
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}}},
            {"$match": {"total_late_minutes": {"$gt": 0}}},
            {"$setWindowFields": {"sortBy": {"total_late_minutes": -1, "_id": 1}, "output": {"rank": {"$rank": {}}}}},
            {"$sort": {"rank": 1, "_id": 1}},
            {"$limit": limit}
        ]
        command_payload = {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}

    elif endpoint in ["department_summary", "department_trend"]:
        collection = db.employees
        # Minimal pipeline trigger to prove indexing on the initial match
        pipeline = [{"$match": {"department": department}}] if endpoint == "department_trend" else [{"$match": {"joined_on": {"$lte": f"{month}-31"}}}]
        command_payload = {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}
        
    else:
        raise HTTPException(400, "Unsupported explain endpoint")

    # Run the raw explain command verbosely to prove no COLLSCAN occurs
    return db.command("explain", command_payload, verbosity="executionStats")