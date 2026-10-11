# HR Attendance API - Final Submission

## How to run
1. Create a `.env` file with `MONGO_URI` and `MONGO_DB` variables.
2. Install dependencies: `pip install -r requirements.txt`
3. Run the app: `uvicorn app.main:app --port 8000`

## Notes
All endpoints, business rules (R1-R10), and MongoDB aggregation pipelines have been successfully implemented. Database indexes are created automatically on startup.