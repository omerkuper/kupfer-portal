"""The app object uvicorn serves (python main.py, or: uvicorn portal.asgi:app --port 8080)."""
from portal.app import create_app

app = create_app()
