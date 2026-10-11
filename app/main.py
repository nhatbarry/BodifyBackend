from fastapi import FastAPI

from app.api.endpoints import (
    ai_coach,
    auth,
    body_metrics,
    equipment,
    exercises,
    foods,
    health,
    health_data,
    meals,
    muscle_groups,
    nutrition,
    profile,
    schedules,
    session_sets,
    workout_days,
    workout_exercise_sets,
    workout_exercises,
    workout_plans,
    progress,
    workout_sessions,
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

# Phase 2 - workout planning. workout_plans owns both /workout-plans and the
# nested /workout-plans/{id}/days routes, so it carries no router prefix.
app.include_router(workout_plans.router, prefix=API_V1_PREFIX)
app.include_router(workout_days.router, prefix=API_V1_PREFIX)
app.include_router(workout_exercises.router, prefix=API_V1_PREFIX)
app.include_router(workout_exercise_sets.router, prefix=API_V1_PREFIX)
app.include_router(schedules.router, prefix=API_V1_PREFIX)

# Phase 3 - what was actually performed. workout_sessions owns /workout-history
# too, and session_sets owns both /session-exercises/{id}/sets and
# /session-sets/{id}, so neither carries a router prefix.
app.include_router(workout_sessions.router, prefix=API_V1_PREFIX)
app.include_router(session_sets.router, prefix=API_V1_PREFIX)

# Phase 4: what the record adds up to.
app.include_router(progress.router, prefix=API_V1_PREFIX)

# Phase 6: the AI coach. The model is called from here, never from the app -
# the key lives in this server's environment and the conversation history is
# read from the database rather than taken from the request.
app.include_router(ai_coach.router, prefix=API_V1_PREFIX)

# Phase 7: the food catalog and the diary logged against it. meals owns
# both /meal-items and /meals, and nutrition owns /nutrition-targets and
# /nutrition/summary, so neither carries a router prefix.
app.include_router(foods.router, prefix=API_V1_PREFIX)
app.include_router(meals.router, prefix=API_V1_PREFIX)
app.include_router(health_data.router, prefix=API_V1_PREFIX)
app.include_router(nutrition.router, prefix=API_V1_PREFIX)


@app.get("/")
def read_root():
    return {"message": "Welcome to the Bodify API!"}
