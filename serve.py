#!/usr/bin/env python3
"""Launch the registry dashboard server."""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "agent-05-platform-api.src.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", 3004)),
        reload=os.environ.get("ENV") != "production",
    )
