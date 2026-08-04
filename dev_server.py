"""Dev server startup — sets env vars then launches uvicorn."""
import os
import sys

os.environ["BOUNTY_X402_ACTIVE"] = "1"
os.environ["X402_PAY_TO"] = os.environ.get("X402_PAY_TO", "0x01bB34b56390a692BaB236f6aDE604A634eE019D")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import app object directly so module-level code sees the env var
from app import app

import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8100, reload=False)
