"""futu REST 源: 签名构造(Ed25519/RSA-SHA256)、响应解析 → QuoteRow、限流冷却、未配置兜底。"""
import asyncio
import base64
import json
from datetime import datetime

from services import futu_quote as F


# ── 签名 ──────────────────────────────────────────────────────────────────
def _ed25519_keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode("ascii")
    return key, pem


def _rsa_keypair():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode("ascii")
    return key, pem


def test_build_auth_headers_ed25519():
    key, pem = _ed25519_keypair()
    body = b'{"code_list":["HK.00700"]}'
    headers = F.build_auth_headers("POST", "/api/v1.0/quote/snapshot", "", body,
                                   "appkey-123", pem, "ed25519")
    assert headers["X-Api-Key"] == "appkey-123"
    assert headers["X-Timestamp"].isdigit()
    assert headers["X-Nonce"]
    sig = base64.b64decode(headers["Authorization"])
    # 用公钥独立复算签名原文, 验证签名成立
    raw = "\n".join([headers["X-Timestamp"], "POST", "/api/v1.0/quote/snapshot", "",
                     __import__("hashlib").sha256(body).hexdigest()])
    key.public_key().verify(sig, raw.encode("utf-8"))      # 不抛 = 验签通过


def test_build_auth_headers_rsa():
    import hashlib
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key, pem = _rsa_keypair()
    headers = F.build_auth_headers("GET", "/api/v1.0/quote/trading-days", "market=HK",
                                   b"", "appkey-rsa", pem, "rsa")
    sig = base64.b64decode(headers["Authorization"])
    raw = "\n".join([headers["X-Timestamp"], "GET", "/api/v1.0/quote/trading-days",
                     "market=HK", ""])     # 无 body → body_part 空串
    key.public_key().verify(sig, raw.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())


def test_quote_time_bj_golden():
    assert F._quote_time_bj(0) == ""
    from datetime import timezone
    # 2026-11-05 02:00:00 UTC = 北京 10:00
    ms = int(datetime(2026, 11, 5, 2, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert F._quote_time_bj(ms) == "2026-11-05 10:00:00"


# ── 响应解析(打桩 HTTP) ───────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def json(self):
        return self._payload


def _patch_snapshot(monkeypatch, payload, calls):
    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append((url, json.loads(data or b"{}"), headers))
        return _FakeResp(payload)
    monkeypatch.setattr(F, "_session", type("S", (), {"post": staticmethod(fake_post),
                                                      "get": staticmethod(lambda *a, **k: None)}))


_SNAP_OK = {
    "ret_code": 0,
    "data": {"snapshot_list": [{
        "code": "HK.00700", "name": "TENCENT", "sc_name": "腾讯控股", "tc_name": "騰訊控股",
        "last_price": 500.0, "prev_close_price": 490.0, "open_price": 492.0,
        "high_price": 505.0, "low_price": 491.0, "volume": 12345678, "turnover": 6.1e9,
        "update_time": 1772986800000, "data_date": "2026-11-05",
    }]},
}


def test_std_and_futu_code_conversions():
    """futu 码只在本模块内存在: 标准码「代码.市场」↔ futu 码「市场.代码」。"""
    assert F._std_code("HK.00700") == "00700.HK"
    assert F._std_code("HK.700") == "00700.HK"        # futu 有时不带前导零
    assert F._std_code("US.SNDK") == "SNDK.US"
    assert F._to_futu_code("00700.HK") == "HK.00700"
    assert F._to_futu_code("SNDK.US") == "US.SNDK"
    assert F._to_futu_code("600406.SH") == "600406.SH"   # 非 HK/US 不动


def test_snapshot_sync_parses_snapshot_list(monkeypatch):
    key, pem = _ed25519_keypair()
    calls = []
    _patch_snapshot(monkeypatch, _SNAP_OK, calls)
    cfg = {"host": "https://webapi.futunn.com", "app_key": "k", "private_key": pem, "algo": "ed25519"}
    # 响应码(HK.00700)被转回标准码(00700.HK) —— futu 码绝不外泄
    out = F._snapshot_sync(["00700.HK"], cfg, {"00700.HK": "HK.00700"})
    assert out and out[0]["code"] == "00700.HK" and out[0]["price"] == 500.0
    assert out[0]["currency"] == "HKD" and out[0]["name"] == "腾讯控股"
    # 请求体 code_list 是 futu 码(交易所.代码)
    assert calls[0][1]["code_list"] == ["HK.00700"]
    # 鉴权头四件套都在
    assert {"X-Api-Key", "X-Timestamp", "X-Nonce", "Authorization"} <= set(calls[0][2])
    assert calls[0][0].endswith("/api/v1.0/quote/snapshot")


def test_futu_symbol_map_from_symbol_dict(temp_db):
    """口径: symbol_dict 的 exchange+ticker 拼接 futu 码; 查不到/段位不可信走段交换兜底。"""
    from tests.conftest import insert_rows
    asyncio.run(insert_rows("symbol_dict", [
        {"code": "00700.HK", "name": "腾讯控股", "ticker": "00700", "sub_assets_type": "hk_comm_stock", "exchange": "HK"},
        {"code": "SNDK.US", "name": "闪迪", "ticker": "SNDK", "sub_assets_type": "us_comm_stock", "exchange": "US"},
        {"code": "02828.HK", "name": "盈富基金", "ticker": "02828", "sub_assets_type": "hk_etf_fund", "exchange": ""},  # 段位缺失
    ]))
    from services.futu_quote import _futu_symbol_map

    m = asyncio.run(_futu_symbol_map(["00700.HK", "SNDK.US", "02828.HK", "09999.HK"]))
    assert m["00700.HK"] == "HK.00700"                # exchange+ticker 拼接
    assert m["SNDK.US"] == "US.SNDK"
    assert m["02828.HK"] == "HK.02828"                # exchange 为空 → 段交换兜底
    assert m["09999.HK"] == "HK.09999"                # 底稿没有 → 段交换兜底


def test_snapshot_sync_batches_over_400(monkeypatch):
    key, pem = _ed25519_keypair()
    calls = []
    _patch_snapshot(monkeypatch, {"ret_code": 0, "data": {"snapshot_list": []}}, calls)
    cfg = {"host": "https://x", "app_key": "k", "private_key": pem, "algo": "ed25519"}
    F._snapshot_sync([f"HK.{i:05d}" for i in range(900)], cfg)
    assert len(calls) == 3 and len(calls[0][1]["code_list"]) == 400


def test_snapshot_sync_unconfigured_and_ratelimited(monkeypatch):
    F.invalidate_ctx()
    monkeypatch.setattr(F, "_session", type("S", (), {"post": staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应发请求")))}))
    # 未配置 → None + last_error, 不发请求
    assert F._snapshot_sync(["HK.00700"], {"host": "x", "app_key": "", "private_key": "", "algo": "ed25519"}) is None
    assert "未配置" in F.last_error()
    # 限流 → 进冷却
    calls = []
    _patch_snapshot(monkeypatch, {"ret_code": -12011, "ret_msg": "rate limited"}, calls)
    cfg = {"host": "x", "app_key": "k",
           "private_key": _ed25519_keypair()[1], "algo": "ed25519"}
    assert F._snapshot_sync(["HK.00700"], cfg) is None
    assert calls and "-12011" in F.last_error()
    calls.clear()
    assert F._snapshot_sync(["HK.00700"], cfg) is None        # 冷却期内直接空返回
    assert not calls
    F.invalidate_ctx()                                         # 清冷却


def test_source_adapter_never_raises(monkeypatch):
    """协议契约: 未配置/失败表现为空列表, 绝不抛。"""
    import asyncio

    async def fake_cfg():
        return {"host": "x", "app_key": "", "private_key": "", "algo": "ed25519"}
    monkeypatch.setattr(F, "futu_config", fake_cfg)
    assert asyncio.run(F.SOURCE.snapshot(["HK.00700"])) == []
