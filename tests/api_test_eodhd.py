import requests
import json

def test_eodhd_id_mapping():
    base_url = "https://eodhd.com/api/id-mapping"
    params = {
        "filter[symbol]": "603111.SH",
        "page[limit]": 100,
        "page[offset]": 0,
        "api_token": "691168c7e8a240.71383926",
        "fmt": "json"
    }

    try:
        res = requests.get(base_url, params=params, timeout=20)
        res.raise_for_status()  # 抛出4xx/5xx异常
        result = res.json()

        print(f"HTTP 状态码: {res.status_code}")
        print("返回数据：")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    except requests.exceptions.RequestException as err:
        print(f"请求异常：{err}")
        if 'res' in locals():
            print(f"原始响应：{res.text}")

if __name__ == "__main__":
    test_eodhd_id_mapping()
