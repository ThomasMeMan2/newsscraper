#!/usr/bin/env python3
"""Web server entry point for the Belgian Startup News Aggregator."""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))


def main():
    parser = argparse.ArgumentParser(
        description="Run the web interface for the Belgian Startup News Aggregator"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development"
    )

    args = parser.parse_args()

    print(f"""
    ╔══════════════════════════════════════════════════════════════╗
    ║           Belgian Startup News Aggregator                    ║
    ║                    Web Interface                             ║
    ╚══════════════════════════════════════════════════════════════╝

    Starting server at http://{args.host}:{args.port}

    Open http://localhost:{args.port} in your browser

    Press Ctrl+C to stop
    """)

    import uvicorn
    uvicorn.run(
        "src.web.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
