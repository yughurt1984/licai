import akshare as ak
import pandas as pd

def get_bond_latest_kline(symbol: str):
    """
    获取债券最新一条K线（最近一笔成交记录，不一定是今日）
    :param symbol: 债券代码，例 sz111086
    :return: 单行DataFrame，无数据返回空
    """
    try:
        df = ak.bond_zh_hs_daily(symbol=symbol)
        if df.empty:
            print("没有查到行情数据")
            return pd.DataFrame()
        
        # 转为日期并倒序，取最新一行
        df["date"] = pd.to_datetime(df["date"])
        df_sorted = df.sort_values("date", ascending=False).reset_index(drop=True)
        latest = df_sorted.iloc[[0]]
        return latest

    except Exception as e:
        print(f"获取失败：{e}")
        return pd.DataFrame()


if __name__ == "__main__":
    sym = "sz111086"
    latest_df = get_bond_latest_kline(sym)
    if not latest_df.empty:
        print(f"【{sym}】最新一条K线：")
        print(latest_df)
