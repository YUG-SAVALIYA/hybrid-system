import urllib.request
import json

url = "http://127.0.0.1:8001/api/v1/discovery/runs/run-c36b8201f92c46c39ca23281325ba56d/execute"
data = json.dumps({"resume": True, "force_restart": False}).encode("utf-8")
headers = {"Content-Type": "application/json"}
req = urllib.request.Request(url, data=data, headers=headers)
try:
    response = urllib.request.urlopen(req, timeout=60)
    print("Status:", response.status)
    print("Response:", response.read().decode("utf-8"))
except Exception as e:
    print("Error:", e)
