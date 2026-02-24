#!/usr/bin/env python3
"""Launch the registry dashboard server."""
import os
import uvicorn

if __name__ == "__main__":
    is_production = os.environ.get("ENV") == "production"
    uvicorn.run(
        "agent-05-platform-api.src.app:app",
        host="0.0.0.0" if is_production else "127.0.0.1",
        port=int(os.environ.get("PORT", 3004 if is_production else 8000)),
        reload=not is_production,
    )
