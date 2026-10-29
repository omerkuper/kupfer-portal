"""The app object uvicorn serves (python main.py, or: uvicorn portal.asgi:app --port 8080)."""
from dotenv import load_dotenv

load_dotenv()   # before create_app reads the sign-in settings from the environment

from portal.app import create_app  # noqa: E402

app = create_app()
