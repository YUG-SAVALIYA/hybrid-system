import requests

url = "http://127.0.0.1:8001/api/v1/discovery/runs/run-c36b8201f92c46c39ca23281325ba56d/execute"
payload = {
    "resume": True,
    "force_restart": False
}

try:
    response = requests.post(url, json=payload, timeout=30)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
