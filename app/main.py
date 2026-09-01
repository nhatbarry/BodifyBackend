from fastapi import FastAPI

from app.api.endpoints import (
    auth,
    body_metrics,
    equipment,
    exercises,
    health,
    muscle_groups,
    profile,
)

API_V1_PREFIX = "/api/v1"

app = FastAPI(
    title="Bodify API",
    version="1.0.0",
)

app.include_router(health.router, prefix=API_V1_PREFIX)
app.include_router(auth.router, prefix=API_V1_PREFIX)
app.include_router(profile.router, prefix=API_V1_PREFIX)
app.include_router(body_metrics.router, prefix=API_V1_PREFIX)
app.include_router(exercises.router, prefix=API_V1_PREFIX)
app.include_router(muscle_groups.router, prefix=API_V1_PREFIX)
app.include_router(equipment.router, prefix=API_V1_PREFIX)


@app.get("/")
def read_root():
    return {"message": "Welcome to the Bodify API!"}
