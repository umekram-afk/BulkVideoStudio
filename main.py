"""Entry point: python main.py [--port 8765] [--no-browser]"""
import argparse
from app.server import serve

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Local Bulk Video Production Studio")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    serve(a.port, not a.no_browser)
