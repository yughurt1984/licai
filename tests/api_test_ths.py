import requests
import pandas as pd

API_KEY = "替换成你的X-api-key"
url = "https://fuyao.aicubes.cn/api/futures/prices/daily"
params = {
    "thscode": "CU2701.SHF",
    # 毫秒时间戳，示例：2025-01-01 00:00:00
    "start": 1793894400000,
    "end": 1794236400000,
    "time_period": "day_1"
}
headers = {
    "X-api-key": "sk-fuyao-WF9R9II-2zjrHSCwyivTiRqfbGAjsl7O"
}

try:
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    print("原始返回：")
    print(data)

    # 如果返回是行情数组，转成DataFrame查看
    if isinstance(data, list):
        df = pd.DataFrame(data)
        print("\n==== 行情表格 ====")
        print(df)
except Exception as e:
    print("请求出错：", e)
    if 'resp' in locals():
        print("响应文本：", resp.text)
