#!/usr/bin/env python3
"""Quick check of Ollama models."""
import requests

try:
    r = requests.get('http://localhost:11434/api/tags', timeout=5)
    data = r.json()
    print("Ollama models installed:")
    for model in data.get('models', []):
        print(f"  - {model['name']}")
except Exception as e:
    print(f"Error connecting to Ollama: {e}")
