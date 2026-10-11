# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
I created a unique index on employees(emp_code) to prevent duplicate users, and a compound unique index on attendance_logs(emp_code, date) to enforce uniqueness per day and eliminate race conditions on punch-in. I also indexed attendance_logs(date) to optimize range queries and the analytics endpoints (like the leaderboard).

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
Both requests attempt an atomic insert_one on the attendance_logs collection. Due to the compound unique index on (emp_code, date), the database accepts the first transaction. The second transaction triggers a DuplicateKeyError at the database level, which the FastAPI app catches and gracefully returns as a 409 Conflict.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
By using the MongoDB $setWindowFields pipeline stage with the $rank operator, ties correctly share the same numerical rank, and the subsequent rank number is skipped (e.g., 1, 2, 2, 4). Tied records are then deterministically sorted by emp_code. The $limit is applied after ranking, so a tie at the 10th spot simply populates the bottom of the requested limit.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
The pipeline initiates on the employees collection rather than attendance_logs. It matches employees by their joined_on date, then executes a $lookup to join their attendance records. Because $unwind is configured with preserveNullAndEmptyArrays: True, employees without logs remain in the aggregation pipeline to be accurately counted in the $addToSet headcount.

5. **One thing you would change** if this had to serve 100x the data.
I would implement asynchronous database drivers (like Motor) instead of blocking PyMongo to prevent thread starvation under high concurrency. I would also introduce materialized views or chron-triggered pre-aggregations for the analytics endpoints, as calculating rolling 7-day averages on the fly via $densify across millions of rows will degrade performance.
