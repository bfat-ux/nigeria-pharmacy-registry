#!/usr/bin/env python3
"""Launch the registry dashboard server."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "agent-05-platform-api.src.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
