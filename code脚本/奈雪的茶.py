#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
奈雪点单签到脚本（适配器版）
说明：
  通过统一微信协议适配器获取Code，调用奈雪登录接口换 token 后执行签到。
环境变量：
  soy_wxid_data       微信id，多账号换行/@分隔
  soy_codeurl_data    微信授权服务地址（YYB-Go填完整地址）
  soy_codetoken_data  微信授权token（YYB-Go留空）
  LY_NOTIFY           填true开启青龙推送
  PROXY_API           品赞代理提取链接，可选
  PROXY_TYPE          代理类型：http / socks5，默认 http
依赖：
  pip install requests
  socks5代理需：pip install requests[socks]
------------------------------------------------------------
更新日志:
2026/07/07  V2.0    重构为统一适配器版，适配YYB-Go，仅保留青龙原生推送
"""
import base64
import hashlib
import hmac
import json
import os
import random
import time
import sys
import traceback
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import requests

# ===================== 配置项 =====================
APPID = "wxab7430e6e8b9a4ab"

MULTI_ACCOUNT_SPLIT = ["\n", "@"]
NOTIFY = os.getenv("LY_NOTIFY", "").lower() == "true"

PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
ENABLE_PER_ACCOUNT_PROXY = True
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True

OPEN_ID = "QL6ZOftGzbziPlZwfiXM"
SIGN_SECRET = "sArMTldQ9tqU19XIRDMWz7BO5WaeBnrezA"
LOGIN_URL = "https://tm-api.pin-dao.cn/passport/authenticate/wxapp/verify/grc"

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541923) XWEB/19823",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781 NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/19725",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781 NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/19613",
]

# ========== 导入微信协议适配器 ==========
if "miniapp" not in os.path.abspath(__file__):
    wechat_adapter_path = "wechatCodeAdapter.py"
else:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../utils')))
    wechat_adapter_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../utils/wechatCodeAdapter.py'))

if not os.path.exists(wechat_adapter_path):
    try:
        url = "https://raw.githubusercontent.com/LinYuanovo/AutoTaskScripts/refs/heads/main/utils/wechatCodeAdapter.py"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        with open(wechat_adapter_path, "w", encoding="utf-8") as f:
            f.write(response.text)
    except Exception as e:
        print(f"下载微信协议适配器失败，请手动放置 wechatCodeAdapter.py：{e}")
        exit(1)
from wechatCodeAdapter import WechatCodeAdapter  # type: ignore

# ===================== 工具函数 =====================
def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def sleep(seconds: float) -> None:
    time.sleep(seconds)

def rand_sleep(min_s: int = 2, max_s: int = 5) -> None:
    sleep(random.randint(min_s, max_s))

def get_ua() -> str:
    return random.choice(UA_LIST)

def random_int_string(length: int) -> str:
    return "".join(random.choice("123456789") for _ in range(length))

def hmac_sha1_base64(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")

def build_request_data(extra_params: dict | None = None) -> dict:
    nonce = random_int_string(6)
    timestamp = int(time.time())
    url_path = f"nonce={nonce}&openId={OPEN_ID}&timestamp={timestamp}"
    signature = hmac_sha1_base64(SIGN_SECRET, url_path)
    common = {
        "platform": "wxapp",
        "version": "6.0.42",
        "imei": "",
        "osn": "microsoft",
        "sv": "Windows 10 x64",
        "lat": "",
        "lng": "",
        "lang": "zh_CN",
        "currency": "CNY",
        "timeZone": "",
        "nonce": int(nonce),
        "openId": OPEN_ID,
        "timestamp": timestamp,
        "signature": signature,
    }
    params = {
        "businessType": 1,
        "brand": 26000252,
        "tenantId": 1,
        "channel": 2,
        "stallType": None,
        "storeId": "",
        "storeType": "",
        "cityId": "",
    }
    if extra_params:
        params.update(extra_params)
    return {
        "common": common,
        "params": params,
    }

def china_date_parts() -> tuple[int, int, int]:
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.year, now.month, now.day

def mask_phone(phone: str) -> str:
    phone = str(phone or "")
    if len(phone) >= 11:
        return f"{phone[:3]}****{phone[7:]}"
    return phone or "未知"

def mask(value) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"

def log_title() -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🍵 奈雪点单签到脚本                             ║")
    print(f"║ 🕒 启动时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

def log_account_header(index: int, total: int, wxid_mask: str) -> None:
    print()
    print("┌" + "─" * 50 + "┐")
    print(f"│ 🧩 账号 {index} / {total:<37}│")
    print(f"│ 🆔 标识 {wxid_mask:<40}│")
    print("└" + "─" * 50 + "┘")

def check_env() -> list[str]:
    """读取青龙环境变量中的微信账号列表"""
    try:
        soy_wxid_data = os.getenv("soy_wxid_data", "")
        if not soy_wxid_data:
            print("❌ [环境] 未配置 soy_wxid_data 环境变量")
            return []
        split_char = None
        for sep in MULTI_ACCOUNT_SPLIT:
            if sep in soy_wxid_data:
                split_char = sep
                break
        if not split_char:
            raw_list = [soy_wxid_data]
        else:
            raw_list = soy_wxid_data.split(split_char)

        wxid_list = []
        for item in raw_list:
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                wxid = item.split("=", 1)[1].strip()
            else:
                wxid = item
            if wxid:
                wxid_list.append(wxid)
        return wxid_list
    except Exception as e:
        print(f"❌ [环境] 读取账号列表失败: {e}")
        return []

# ===================== 品赞代理 =====================
def parse_proxy_response(text) -> dict | None:
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        proxy_obj = None
        if isinstance(data.get("data"), list) and data["data"]:
            proxy_obj = data["data"][0]
        elif isinstance(data.get("data"), dict):
            proxy_obj = data["data"]
        elif data.get("ip") and data.get("port"):
            proxy_obj = data
        elif isinstance(data.get("result"), dict):
            proxy_obj = data["result"]
        if proxy_obj:
            host = proxy_obj.get("ip") or proxy_obj.get("host")
            port = proxy_obj.get("port")
            if host and port:
                return {
                    "host": str(host),
                    "port": int(port),
                    "username": proxy_obj.get("user") or proxy_obj.get("username") or "",
                    "password": proxy_obj.get("pass") or proxy_obj.get("password") or "",
                }
    except Exception:
        pass
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            return {
                "host": parts[0],
                "port": int(parts[1]),
                "username": parts[2] if len(parts) > 2 else "",
                "password": parts[3] if len(parts) > 3 else "",
            }
    return None

def build_proxy_dict(proxy_info: dict | None) -> dict | None:
    if not proxy_info:
        return None
    host = proxy_info["host"]
    port = proxy_info["port"]
    username = proxy_info.get("username", "")
    password = proxy_info.get("password", "")
    auth = ""
    if username and password:
        auth = f"{quote(username)}:{quote(password)}@"
    if PROXY_TYPE == "socks5":
        proxy_url = f"socks5://{auth}{host}:{port}"
    else:
        proxy_url = f"http://{auth}{host}:{port}"
    print(f"🛠️ [代理] 生成 {PROXY_TYPE.upper()} 代理 {host}:{port}")
    return {
        "http": proxy_url,
        "https": proxy_url,
    }

def validate_proxy(proxies: dict | None) -> tuple[bool, str]:
    if not proxies:
        return False, ""
    try:
        res = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
        if res.status_code == 200:
            try:
                ip = res.json().get("origin", "未知")
            except Exception:
                ip = "未知"
            print(f"✅ [代理] 验证通过，出口IP：{ip}")
            return True, ip
    except Exception as exc:
        print(f"⚠️ [代理] 验证失败：{exc}")
    return False, ""

def get_valid_proxy(account_name: str) -> tuple[dict | None, str]:
    if not PROXY_API:
        print(f"⚠️ [代理] {account_name} 未配置 PROXY_API，使用直连")
        return None, ""
    print(f"🌐 [代理] {account_name} 正在获取品赞代理...")
    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            res = requests.get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(res.text)
            if not proxy_info:
                print(f"⚠️ [代理] 第 {index} 次代理解析失败")
                continue
            print(f"✅ [代理] 提取到代理：{proxy_info['host']}:{proxy_info['port']}")
            proxies = build_proxy_dict(proxy_info)
            ok, ip = validate_proxy(proxies)
            if ok:
                return proxies, ip
            print(f"⚠️ [代理] 第 {index} 次代理不可用")
        except Exception as exc:
            print(f"⚠️ [代理] 第 {index} 次获取代理异常：{exc}")
        if index < PROXY_RETRY_TIMES:
            sleep(2)
    print(f"⚠️ [代理] 获取代理失败，使用直连")
    return None, ""

# ===================== 青龙推送 =====================
def send_qinglong_notify(title: str, content: str) -> None:
    """青龙面板原生推送通知"""
    try:
        import notify
        notify.send(title, content)
        print("✅ [青龙推送] 通知发送成功")
    except ImportError:
        if not os.path.exists("notify.py"):
            try:
                url = "https://raw.githubusercontent.com/whyour/qinglong/refs/heads/develop/sample/notify.py"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                with open("notify.py", "w", encoding="utf-8") as f:
                    f.write(response.text)
            except Exception as e:
                print(f"⚠️ [青龙推送] 加载notify模块失败: {e}")
                return
        if os.path.exists("notify.py"):
            import notify
            notify.send(title, content)
            print("✅ [青龙推送] 通知发送成功")
    except Exception as e:
        print(f"❌ [青龙推送] 发送失败: {e}")

# ===================== 请求封装 =====================
def request_with_proxy(method: str, url: str, *, proxies: dict | None = None, account_name: str = "", **kwargs):
    kwargs.setdefault("timeout", 30)
    if proxies:
        try:
            return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception as exc:
            print(f"⚠️ [代理] {account_name} 代理请求失败：{exc}")
            if not ENABLE_DIRECT_FALLBACK:
                raise
            print(f"🔁 [兜底] 切换直连重试")
    return requests.request(method, url, **kwargs)

def extract_token(data) -> str | None:
    if not isinstance(data, dict):
        return None
    candidates = [
        data.get("token"),
        data.get("accessToken"),
        data.get("access_token"),
        data.get("authToken"),
        data.get("memberToken"),
    ]
    inner = data.get("data")
    if isinstance(inner, dict):
        candidates.extend([
            inner.get("token"),
            inner.get("accessToken"),
            inner.get("access_token"),
            inner.get("authToken"),
            inner.get("memberToken"),
            inner.get("access_token_value"),
        ])
        token_info = inner.get("tokenInfo")
        if isinstance(token_info, dict):
            candidates.extend([
                token_info.get("token"),
                token_info.get("accessToken"),
                token_info.get("access_token"),
            ])
        user_token = inner.get("userToken")
        if isinstance(user_token, dict):
            candidates.extend([
                user_token.get("token"),
                user_token.get("accessToken"),
                user_token.get("access_token"),
            ])
    for item in candidates:
        if item and item != "null":
            return str(item)
    return None

def login_by_code(code: str, ua: str, proxies: dict | None, account_name: str) -> tuple[str | None, dict | None]:
    headers = {
        "Host": "tm-api.pin-dao.cn",
        "Connection": "keep-alive",
        "Authorization": "Bearer null",
        "User-Agent": ua,
        "xweb_xhr": "1",
        "storeId": "",
        "Content-Type": "application/json",
        "iv": random_int_string(16),
        "Accept": "*/*",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://servicewechat.com/{APPID}/819/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    body = build_request_data({
        "appId": APPID,
        "dAId": "",
        "type": 3,
        "wxappCode": code,
        "regChannelCode": "|1027",
    })
    try:
        res = request_with_proxy(
            "POST",
            LOGIN_URL,
            headers=headers,
            data=json.dumps(body, separators=(",", ":"), ensure_ascii=False),
            proxies=proxies,
            account_name=account_name,
        )
        try:
            data = res.json()
        except Exception:
            data = {"raw": res.text[:500]}
        token = extract_token(data)
        if token:
            print(f"✅ [登录] 登录成功，已获取 token")
            return token, data
        print(f"❌ [登录] 登录成功但未识别 token 字段：{json.dumps(data, ensure_ascii=False)[:800]}")
        return None, data
    except Exception as exc:
        print(f"❌ [登录] 登录异常：{exc}")
        return None, None

def call_api(url: str, token: str, ua: str, proxies: dict | None, account_name: str, body: dict | None = None) -> dict:
    headers = {
        "User-Agent": ua,
        "Authorization": f"Bearer {token}",
        "Referer": "https://tm-web.pin-dao.cn/",
        "Origin": "https://tm-web.pin-dao.cn",
        "Content-Type": "application/json",
    }
    payload = build_request_data(body or {})
    try:
        res = request_with_proxy(
            "POST",
            url,
            headers=headers,
            data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            proxies=proxies,
            account_name=account_name,
        )
        return res.json()
    except Exception as exc:
        return {
            "code": -1,
            "message": str(exc),
        }

# ===================== 业务逻辑 =====================
def run_account(index: int, total: int, wx_id: str, adapter: WechatCodeAdapter) -> dict:
    account_name = mask(wx_id)
    result = {
        "wxid": account_name,
        "success": False,
        "proxy_status": "未使用代理",
        "proxy_ip": "-",
        "login_msg": "",
        "sign_msg": "",
        "coin": "-",
        "error": "",
    }

    log_account_header(index, total, account_name)
    ua = get_ua()
    proxies = None
    proxy_ip = "-"
    if ENABLE_PER_ACCOUNT_PROXY:
        proxies, proxy_ip = get_valid_proxy(account_name)
        result["proxy_status"] = "使用专属代理" if proxies else "使用直连"
        result["proxy_ip"] = proxy_ip
        sleep(PROXY_FETCH_INTERVAL)

    try:
        delay = random.randint(2, 6)
        print(f"⏳ [延迟] 启动延迟 {delay}s")
        sleep(delay)

        # 通过统一适配器获取code，自动适配YYB-Go
        code = adapter.get_code(wx_id)
        if not code:
            result["error"] = "获取 code 失败"
            return result

        token, login_raw = login_by_code(code, ua, proxies, account_name)
        if not token:
            result["error"] = "登录失败或未识别 token 字段"
            return result
        result["login_msg"] = "登录成功"

        rand_sleep(2, 5)
        userinfo = call_api(
            "https://tm-web.pin-dao.cn/user/base-userinfo",
            token,
            ua,
            proxies,
            account_name,
            {},
        )
        if userinfo.get("code") != 0:
            result["error"] = f"查询用户信息失败：{userinfo.get('message') or '未知错误'}"
            return result
        phone = userinfo.get("data", {}).get("phone", "")
        print(f"👤 [用户] 登录账号：{mask_phone(phone)}")

        year, month, day = china_date_parts()
        sign_date = f"{year}-{month:02d}-01"
        today = f"{year}-{month:02d}-{day:02d}"
        sign_records = call_api(
            "https://tm-web.pin-dao.cn/user/sign/records",
            token,
            ua,
            proxies,
            account_name,
            {
                "signDate": sign_date,
                "startDate": today,
            },
        )
        if sign_records.get("code") != 0:
            result["sign_msg"] = f"查询签到失败：{sign_records.get('message') or '未知错误'}"
            print(f"⚠️ [签到] {result['sign_msg']}")
        else:
            status = bool(sign_records.get("data", {}).get("status"))
            count = sign_records.get("data", {}).get("signCount", "-")
            print(f"📅 [签到] 今天{'已' if status else '未'}签到，已签到 {count} 天")
            if status:
                result["sign_msg"] = f"今日已签到，累计 {count} 天"
            else:
                sign_save = call_api(
                    "https://tm-web.pin-dao.cn/user/sign/save",
                    token,
                    ua,
                    proxies,
                    account_name,
                    {
                        "signDate": today,
                    },
                )
                if sign_save.get("code") == 0 and sign_save.get("data", {}).get("flag"):
                    result["sign_msg"] = "签到成功"
                    print(f"✅ [签到] 签到成功")
                else:
                    result["sign_msg"] = f"签到失败：{sign_save.get('message') or '未知错误'}"
                    print(f"❌ [签到] {result['sign_msg']}")

        rand_sleep(2, 5)
        account = call_api(
            "https://tm-web.pin-dao.cn/user/account/user-account",
            token,
            ua,
            proxies,
            account_name,
            {},
        )
        if account.get("code") == 0:
            result["coin"] = account.get("data", {}).get("coin", "-")
            print(f"💰 [奈雪币] 当前奈雪币：{result['coin']}")
        else:
            print(f"⚠️ [奈雪币] 查询失败：{account.get('message') or '未知错误'}")

        result["success"] = True
        return result

    except Exception as exc:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行异常：{exc}")
        return result

def build_notify(results: list[dict]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    content = f"""🍵 奈雪点单签到任务结果
━━━━━━━━━━━━━━━━━━━━
🏁 总结：{success_count} 成功 / {fail_count} 失败
🕒 时间：{now_text()}
━━━━━━━━━━━━━━━━━━━━
"""
    for idx, res in enumerate(results, 1):
        icon = "✅" if res["success"] else "❌"
        content += f"""
🧩 账号 {idx}
🆔 微信ID：{res["wxid"]}
🌐 代理：{res["proxy_status"]}
📡 出口IP：{res["proxy_ip"]}
📝 登录：{res["login_msg"]}
✅ 签到：{res["sign_msg"]}
💰 奈雪币：{res["coin"]}
{icon} 结果：{"成功" if res["success"] else "失败"}
"""
        if not res["success"]:
            content += f"❌ 原因：{res['error']}\n"
        content += "━━━━━━━━━━━━━━━━━━━━\n"
    return content

def main() -> None:
    log_title()

    # 初始化统一微信协议适配器
    adapter = WechatCodeAdapter(APPID)
    wxid_list = check_env()
    total = len(wxid_list)

    if total == 0:
        print("❌ 没有可用的账号，任务退出")
        return

    results = []
    for index, wx_id in enumerate(wxid_list, 1):
        try:
            res = run_account(index, total, wx_id, adapter)
            results.append(res)
        except Exception as exc:
            print(f"❌ [主程序] 账号 {index} 执行异常: {exc}")
            results.append({
                "wxid": mask(wx_id),
                "success": False,
                "proxy_status": "-",
                "proxy_ip": "-",
                "login_msg": "-",
                "sign_msg": "-",
                "coin": "-",
                "error": traceback.format_exc().strip(),
            })

        if index < total:
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 奈雪点单任务执行完成                        ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    if NOTIFY:
        send_qinglong_notify("🍵 奈雪点单签到任务完成", build_notify(results))

if __name__ == "__main__":
    main()