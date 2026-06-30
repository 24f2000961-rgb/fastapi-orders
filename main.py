import time
import uuid
from collections import defaultdict, deque
from fastapi import FastAPI, Request, Header, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional

TOTAL_ORDERS = 43
RATE_LIMIT = 17
WINDOW_SECONDS = 10

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# in-memory state
idempotency_store = {}  # key -> order dict
client_buckets = defaultdict(deque)  # client_id -> deque of timestamps

# fixed catalog of orders 1..T
ORDERS = [{"id": i, "item": f"item-{i}", "amount": i * 10} for i in range(1, TOTAL_ORDERS + 1)]


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path == "/orders":
        client_id = request.headers.get("x-client-id")
        if client_id:
            now = time.time()
            bucket = client_buckets[client_id]
            # drop timestamps outside the window
            while bucket and now - bucket[0] > WINDOW_SECONDS:
                bucket.popleft()
            if len(bucket) >= RATE_LIMIT:
                retry_after = max(1, int(WINDOW_SECONDS - (now - bucket[0])))
                return JSONResponse(
                    status_code=429,
                    content={"error": "rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
    response = await call_next(request)
    return response


@app.post("/orders")
async def create_order(request: Request, idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    if idempotency_key and idempotency_key in idempotency_store:
        existing = idempotency_store[idempotency_key]
        return JSONResponse(status_code=201, content=existing)

    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    order = {
        "id": str(uuid.uuid4()),
        "item": body.get("item", "unspecified"),
        "amount": body.get("amount", 0),
    }

    if idempotency_key:
        idempotency_store[idempotency_key] = order

    return JSONResponse(status_code=201, content=order)


@app.get("/orders")
async def list_orders(limit: int = Query(10), cursor: Optional[str] = Query(default=None)):
    start = 0
    if cursor:
        try:
            start = int(cursor)
        except ValueError:
            start = 0

    end = min(start + limit, TOTAL_ORDERS)
    items = ORDERS[start:end]

    next_cursor = str(end) if end < TOTAL_ORDERS else None

    return {"items": items, "next_cursor": next_cursor}
