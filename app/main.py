from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(
    title="Bodify API",
    version="1.0.0",
)

@app.get("/")
def read_root():
    return {"message": "Welcome to the Bodify API!"}