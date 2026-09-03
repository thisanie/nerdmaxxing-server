from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.api.challenges import router as challenges_router
from app.api.participation import router as participation_router
from app.api.evidence import router as evidence_router
from app.api.skills import router as skills_router
from app.core.database import engine, ensure_local_schema
from app.models.base import Base
from app.core.config import settings
from app.core.dependencies import limiter


import app.models


Base.metadata.create_all(
        bind= engine)
ensure_local_schema()




app = FastAPI(title = "NerdMaxxing API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

if settings.force_https:
    app.add_middleware(HTTPSRedirectMiddleware)


@app.middleware("http")
async def add_security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response




@app.get("/")
def health_check():
    return {"status" : "ok"}



app.include_router(auth_router)
app.include_router(users_router)
app.include_router(challenges_router)
app.include_router(participation_router)
app.include_router(evidence_router)
app.include_router(skills_router)




