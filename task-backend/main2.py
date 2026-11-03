# main2.py (Deprecated: please use main.py directly)
# This module proxies all endpoints and app lifespan to main.py
from main import app, lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
