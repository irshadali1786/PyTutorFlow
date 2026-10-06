"""uvicorn entry point:  uvicorn app.api.main:app   (or:  python -m app serve)"""
from app.api.app import create_app

app = create_app()
