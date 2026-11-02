# api/ledger_routes.py
"""AI 记账 — LLM 解析交易记录/成交截图/交易短信 → 结构化交易草稿。

本路由只负责"解析": 把用户随手贴的一段文本和几张截图交给 LLM, 换回结构化的
交易列表, 供前端预览、改错后走**既有**入账端点落账 —— POST /api/portfolio(股票/期货/期权
建仓)、POST /api/portfolio/{code}/actions(加仓/卖出/平仓)、POST /api/assets(基金/加密/
理财/现金/机器人)全都是现成的, 这里不碰任何存储。
"""
from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import llm_client

router = APIRouter(prefix="/api/ledger", tags=["ai-ledger"])

# 视觉 + 文本都要会; 用户在设置里配的 LLM_MODEL_MAP 别名可以把它换成任意兼容模型
PARSE_MODEL = "claude-sonnet-5"
MAX_IMAGES = 5
# 单张解码后上限。截图 base64 再膨胀 1/3, JSON body 太大前端 fetch 和 LLM 都难受,
# 前端已把长边压到 1600px, 这里只拦真正的异常大图。
MAX_IMAGE_BYTES = 6 * 1024 * 1024
_ALLOWED_MEDIA = {"image/png", "image/jpeg", "image/webp", "image/gif"}

HINT_LABEL = {
    "A": "股票(A股/港股/美股)", "F": "基金", "W": "理财", "M": "现金",
    "C": "加密货币", "R": "量化机器人", "T": "期货", "O": "期权",
}
_TYPE_KEYS = set(HINT_LABEL)

PARSE_SYSTEM = """你是个人理财应用的记账助手。用户会给你交易信息, 形式是: 手打的交易记录、\
券商/交易所/银行的成交截图(图片)、或银行/券商发来的交易短信原文。你的任务是把它们提取成\
结构化的交易数据, 供用户核对后写入账本。

支持八类资产, type 取值: A股票 / F基金 / W理财 / M现金 / C加密货币 / R量化机器人 / T期货 / O期权。
各类关键字段:
- 股票A: market=A(6位数字代码)/HK(5位代码)/US(字母Ticker); side=BUY买入/SELL卖出
- 基金F: fund_kind=etf(场内ETF/LOF, 市价成交)/open(场外开放式, 净值申购); 场内代码5/6位, 场外6位
- 理财W/现金M: name产品名, platform平台/银行, amount金额(投入本金或余额), annual_yield_pct年化%(有才填)
- 加密C: code币对如BTC-USDT, price单价USDT, shares币数
- 机器人R: name策略名, platform平台, amount投入本金, current_value当前资产(有才填)
- 期货T/期权O: code品种代码(如CU), month合约月份YYMM, direction=LONG多/SHORT空, \
side=OPEN开仓/CLOSE平仓, shares手数, multiplier每手乘数(有才填)

规则:
- 只提取真实发生的交易, 不猜测不编造; 截图里的行情、自选股、广告等无关信息忽略。
- 短信里"今天/昨日"等相对日期按【今天】换算; 没有年份默认最近的过去; 输出 YYYY-MM-DD。
- 金额/价格/数量输出数字而不是字符串; 看不清的字段宁缺勿错(留 null), 不要凑数。
- 一张截图可能有多笔成交 → 拆成多条; 文本和截图可能是同一批交易, 按代码+方向+数量+时间去重, 别重复计。
- 手续费 fee 看得到就填, 看不到留 null。currency: A股=CNY, 港股=HKD, 美股=USD, 加密=USDT, 其余=CNY。
- 每条给 confidence(0~1, 你对这条的把握)和 evidence(原文里支持这条的最短片段)。
- 输入里没有任何可提取的交易时, 返回空数组并在 notes 里说明原因。

只输出一个 JSON 对象, 不要输出其它内容:
{"transactions": [
  {"type": "A|F|W|M|C|R|T|O",
   "market": "A|HK|US",          // 仅股票
   "fund_kind": "etf|open",      // 仅基金
   "side": "BUY|SELL|OPEN|CLOSE",
   "code": "", "name": "",
   "price": null, "shares": null,   // 单价与数量(基金=净值与份额, 加密=USDT价与币数, 期货=开仓价与手数)
   "amount": null,                  // 成交金额/投入本金/余额(有就填)
   "fee": null, "currency": "CNY",
   "month": null, "direction": null, "multiplier": null,   // 仅期货/期权
   "annual_yield_pct": null, "current_value": null,        // 仅理财/现金/机器人
   "broker": "",                    // 券商/平台/银行, 原文能看出来才填
   "trade_date": "YYYY-MM-DD", "trade_time": "HH:MM",
   "confidence": 0.9, "evidence": ""}
 ], "notes": "给用户的一句解析说明, 可为空"}"""


class ParseIn(BaseModel):
    text: str = ""
    images: list[str] = []          # data URL(data:image/...;base64,xxx)或裸 base64
    asset_type: Optional[str] = None   # 品种提示: A/F/W/M/C/R/T/O, 帮 LLM 缩小判断范围


def _decode_image(raw: str) -> tuple[str, str]:
    """data URL / 裸 base64 → (media_type, base64数据)。不认识的格式直接拒。"""
    s = (raw or "").strip()
    m = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", s, re.S)
    if m:
        media, b64 = m.group(1).lower(), m.group(2)
    else:
        # 裸 base64: 前端一般传 data URL, 这里兜底防手写调用方
        media, b64 = "image/png", s
    if media not in _ALLOWED_MEDIA:
        raise HTTPException(400, f"不支持的图片格式: {media}(仅 png/jpeg/webp/gif)")
    b64 = re.sub(r"\s+", "", b64)
    try:
        import base64
        decoded = base64.b64decode(b64, validate=False)
    except Exception:
        raise HTTPException(400, "图片 base64 解码失败")
    if len(decoded) > MAX_IMAGE_BYTES:
        raise HTTPException(400, f"单张图片超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB, 请裁剪后再试")
    if not decoded:
        raise HTTPException(400, "图片内容为空")
    return media, b64


def _f(v) -> Optional[float]:
    """LLM 数字字段清洗: 字符串数字/None/inf 全收编成 float 或 None。"""
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _s(v) -> str:
    return str(v or "").strip()


def _norm_date(v) -> Optional[str]:
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", _s(v))
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _norm_time(v) -> Optional[str]:
    m = re.search(r"(\d{1,2}):(\d{2})", _s(v))
    if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _norm_code(tk: str, market: str, code: str) -> str:
    c = re.sub(r"\s+", "", _s(code))
    if not c:
        return ""
    if tk == "A":
        if market == "HK":
            digits = re.sub(r"\D", "", c)
            return f"HK.{int(digits):05d}" if digits else c.upper()
        if market == "US":
            return "US." + re.sub(r"^US\.?", "", c, flags=re.I).upper()
        m = re.search(r"(\d{6})", c)     # sh600362 / 600362.SH / SZ000001 → 裸 6 位
        return m.group(1) if m else c.upper()
    if tk == "F":
        m = re.search(r"(\d{6})", c)
        return m.group(1) if m else c.upper()
    return c.upper()                      # 加密币对/自定义标识原样(大写)


def _normalize_tx(tx: dict) -> Optional[dict]:
    """LLM 单条输出 → 归一化交易。识别不了类型(没法入账)的返回 None 丢弃。"""
    tk = _s(tx.get("type")).upper()[:1]
    if tk not in _TYPE_KEYS:
        # 类型缺省/写歪时按字段猜: 有合约月份/方向 → 期货, 有基金细分 → 基金, 有市场 → 股票
        if tx.get("month") or tx.get("direction"):
            tk = "T"
        elif tx.get("fund_kind"):
            tk = "F"
        elif tx.get("market"):
            tk = "A"
        else:
            return None
    market = _s(tx.get("market")).upper()
    if market not in ("A", "HK", "US"):
        market = "A"

    side = _s(tx.get("side")).upper()
    raw_side = _s(tx.get("side"))
    if side not in ("BUY", "SELL", "OPEN", "CLOSE"):
        if "平" in raw_side:
            side = "CLOSE"
        elif "卖" in raw_side or "赎" in raw_side:
            side = "SELL"
        else:
            side = "BUY"
    if tk in ("T", "O"):                  # 期货/期权的买卖改叫开平
        side = "CLOSE" if side in ("SELL", "CLOSE") else "OPEN"

    out = {
        "type": tk,
        "market": market if tk == "A" else "",
        "fund_kind": ("etf" if _s(tx.get("fund_kind")).lower() == "etf" else "open") if tk == "F" else "",
        "side": side,
        "direction": "SHORT" if _s(tx.get("direction")).upper() == "SHORT" else "LONG",
        "code": _norm_code(tk, market, tx.get("code")),
        "name": _s(tx.get("name")),
        "price": _f(tx.get("price")),
        "shares": _f(tx.get("shares")),
        "amount": _f(tx.get("amount")),
        "fee": _f(tx.get("fee")),
        "currency": _s(tx.get("currency")).upper() or ("USDT" if tk == "C" else "CNY"),
        "month": re.sub(r"\D", "", _s(tx.get("month")))[:4],
        "multiplier": _f(tx.get("multiplier")),
        "annual_yield_pct": _f(tx.get("annual_yield_pct")),
        "current_value": _f(tx.get("current_value")),
        "start_date": _norm_date(tx.get("start_date")),
        "broker": _s(tx.get("broker")),
        "platform": _s(tx.get("platform")),
        "trade_date": _norm_date(tx.get("trade_date")),
        "trade_time": _norm_time(tx.get("trade_time")),
        "note": _s(tx.get("note"))[:200],
        "confidence": _f(tx.get("confidence")),
        "evidence": _s(tx.get("evidence"))[:200],
    }
    # 至少得有个标的或名字, 否则这条没法入账
    if not out["code"] and not out["name"]:
        return None
    return out


def _extract_json(raw: str) -> dict:
    """LLM 返回 → JSON dict。剥代码围栏、截取首尾大括号; 救不回来就报 502。"""
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(json)?\s*", "", txt)
        if txt.endswith("```"):
            txt = txt[:-3].strip()
    lo, hi = txt.find("{"), txt.rfind("}")
    if lo >= 0 and hi > lo:
        txt = txt[lo:hi + 1]
    try:
        data = json.loads(txt)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    raise HTTPException(502, "AI 返回的内容不是有效 JSON, 请重试或换一段更清晰的输入")


@router.post("/parse")
async def ledger_parse(data: ParseIn):
    """文本 + 截图 → LLM → 结构化交易列表。不入账, 由前端预览确认后走既有端点落账。"""
    text = (data.text or "").strip()
    images = data.images or []
    if not text and not images:
        raise HTTPException(400, "请输入交易记录或附上截图")
    if len(text) > 20000:
        raise HTTPException(400, "文本过长(>2万字), 请分批解析")

    content: list[dict] = []
    for img in images[:MAX_IMAGES]:
        media, b64 = _decode_image(img)
        content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})
    if len(images) > MAX_IMAGES:
        raise HTTPException(400, f"一次最多 {MAX_IMAGES} 张截图")

    today = date.today()
    weekday = "周" + "一二三四五六日"[today.weekday()]
    lines = [f"【今天】{today.isoformat()} ({weekday})"]
    if data.asset_type:
        lines.append(f"【目标资产】用户提示主要与「{HINT_LABEL.get(data.asset_type, data.asset_type)}」相关, 以实际内容为准")
    lines.append("【输入文本】")
    lines.append(text or "(无文本, 只看截图)")
    if content:
        lines.append(f"\n另附 {len(content)} 张截图, 注意与文本去重。")
    content.append({"type": "text", "text": "\n".join(lines)})

    try:
        resp = await asyncio.to_thread(
            llm_client.call_claude_messages,
            [{"role": "user", "content": content}],
            PARSE_SYSTEM, PARSE_MODEL, 4096,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"LLM 调用失败: {e}")

    raw = "".join(b.get("text", "") for b in (resp.get("content") or []) if b.get("type") == "text")
    if not raw.strip():
        raise HTTPException(502, "AI 没有返回内容, 请重试")

    parsed = _extract_json(raw)
    txs, dropped = [], 0
    for tx in (parsed.get("transactions") or []):
        if not isinstance(tx, dict):
            continue
        norm = _normalize_tx(tx)
        if norm:
            txs.append(norm)
        else:
            dropped += 1
    result = {
        "transactions": txs,
        "notes": _s(parsed.get("notes")),
        "dropped": dropped,
        "model": PARSE_MODEL,
    }
    if not txs:
        result["notes"] = result["notes"] or "没有识别到可入账的交易"
    return result
