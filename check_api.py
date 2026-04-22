#!/usr/bin/env python3
"""Quick check of Nexus API health."""
import requests

# Check healthz endpoint
try:
    r = requests.get('http://localhost:11435/healthz', timeout=5)
    print(f"Nexus API /healthz: {r.status_code} - {r.json()}")
except Exception as e:
    print(f"Nexus API not running: {e}")

# Check health endpoint
try:
    r = requests.get('http://localhost:11435/health', timeout=5)
    print(f"Nexus API /health: {r.status_code} - {r.json()}")
except Exception as e:
    print(f"Error on /health: {e}")
