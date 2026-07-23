import httpx
r = httpx.get("http://localhost:18000/v1/models",
              headers={"Authorization": "Bearer EMPTY"}, timeout=30)
r.raise_for_status()
for m in r.json()["data"]:
    print(m["id"])
