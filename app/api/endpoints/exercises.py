from fastapi import APIRouter

router = APIRouter(prefix="/exercises", tags=["exercises"])

@router.get("/")
async def get_exercises():
    return {"message": "List of exercises"}

@router.get("/")
async def get_exercise_by_id(exercise_id: int):
    return {"message": f"Details of exercise with ID {exercise_id}"}

@router.post("/")
async def create_exercise(exercise: dict):
    return {"message": "Exercise created", "exercise": exercise}    