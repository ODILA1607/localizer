from curl_cffi import requests

s = requests.Session(impersonate="chrome131")
for url in [
    "https://www.zimmo.be/",
    "https://www.immoweb.be/",
    "https://www.immoscoop.be/",
    "https://www.immovlan.be/",
]:
    try:
        r = s.get(url, timeout=15)
        print(f"{url}: {r.status_code} ({len(r.text)} bytes)")
    except Exception as e:
        print(f"{url}: ERROR {e}")
