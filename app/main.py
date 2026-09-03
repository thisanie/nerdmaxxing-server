from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.core.database import engine
from app.models.base import Base


import app.models


Base.metadata.create_all(
        bind= engine)




app = FastAPI(title = "NerdMaxxing API")




@app.get("/")
def health_check():
    return {"status" : "ok"}



app.include_router(auth_router)




