import requests

url = "https://api.openfigi.com/v3/mapping"
headers = {
    "Content-Type": "application/json",
    "X-OPENFIGI-APIKEY": "55b40e64-b5cb-4c9a-8d00-16e26eaa3667"  # 替换为您的真实API KEY
}
payload = [
    {
        "idType": "TICKER",
        "idValue": "6186",
        "exchCode": "HK"     
    }
]

response = requests.post(url, json=payload, headers=headers)
print(response.json())  # 运行这行代码就会打印出和 curl 一模一样的返回结果
