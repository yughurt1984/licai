import requests
import json

def test_twelvedata_etfs():
    url = "https://api.twelvedata.com/funds"
    params = {
        "apikey": "2c3a9ed97e55403f9aafc00a0c22aad0",
        "isin": "KYG875721634",
        #"symbol": "00000.SZ",


    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        result = resp.json()

        print(f"HTTP状态码: {resp.status_code}")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    except requests.exceptions.RequestException as err:
        print(f"网络请求异常: {err}")
        if 'resp' in locals():
            print(f"原始响应文本: {resp.text}")


if __name__ == "__main__":
    test_twelvedata_etfs()
