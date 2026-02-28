import urllib.request
import json

try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/status') as response:
        data = json.loads(response.read().decode())
        print("TIMERS DATA:")
        print(json.dumps(data.get("timers"), indent=2))
except Exception as e:
    print("Error:", e)
