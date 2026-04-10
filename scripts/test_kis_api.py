import requests
import json
from datetime import date

APP_KEY = "PSNp7ENZtZXemQvp9M1MxZy85px44sl9XAhi"
APP_SECRET = "lo3u+89gtvikTydl9yRo4OIZvUgMUYp0v4v8udozI4XJAuaQ4nSDy0Vmisq3A3AyKg6jN6g+hLmkofJw+5E7ZMHKbfAX9uOYU9Tn9V9s8DBv94M4dHTQE00u8o7rAYalVzveaNuUApw5xnUJPyOLqzWCJpddaRyELb9VUVlBeU3YHLz93u8="
BASE_URL = "https://openapi.koreainvestment.com:9443"

# 1. Get Token
url = f"{BASE_URL}/oauth2/tokenP"
headers = {"content-type": "application/json"}
data = {
    "grant_type": "client_credentials",
    "appkey": APP_KEY,
    "appsecret": APP_SECRET
}
res = requests.post(url, headers=headers, data=json.dumps(data))
print("Token Status:", res.status_code)
if res.status_code == 200:
    token = res.json()["access_token"]
    
    # 2. Test Investor Supply API (FHKST01010900) - 종목별 투자자
    test_url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010900",
        "custtype": "P"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930"
    }
    res2 = requests.get(test_url, headers=headers, params=params)
    print("API Status:", res2.status_code)
    print("API Response:", res2.text[:1000])
else:
    print(res.text)
