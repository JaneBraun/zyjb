#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
袋鼠妈妈会员商场小程序签到脚本（适配器版）
功能：
  1. 统一微信协议适配器获取code（支持YYB-Go等全协议）
  2. code 登录换取 token
  3. 查询用户资产、当月签到记录、执行每日签到
  4. 品赞代理支持，失败自动直连兜底
  5. 青龙面板原生推送通知（仅失败/错误时推送）
环境变量：
  soy_wxid_data       账号标识（对应YYB-Go的ref/id），多账号用 & 或换行分隔
  soy_codeurl_data    微信授权服务地址（YYB-Go填完整地址）
  soy_codetoken_data  微信授权token（YYB-Go留空）
  LY_NOTIFY           填true开启失败推送通知
  PROXY_API           品赞代理提取API，可选
  PROXY_TYPE          http / socks5，默认 http
依赖：
  pip install requests
  socks5代理需：pip install requests[socks]
------------------------------------------------------------
更新日志:
2026/07/08  V2.0    适配统一适配器，改用青龙推送，仅失败时推送
"""

import json
import os
import random
import time
import traceback
import sys
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import requests

APP_NAME = "袋鼠妈妈会员商场小程序"
APPID = "wxb27b46293d405a20"
KDT_ID = "44587018"
CHECKIN_ID = "17019"

MULTI_ACCOUNT_SPLIT = ["\n", "&"]
NOTIFY = os.getenv("LY_NOTIFY", "").lower() == "true"

PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()

PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

BASE_URL = "https://h5.youzan.com"
LOGIN_URL = f"https://uic.youzan.com/passport/general/auth.json?kdt_id={KDT_ID}&app_id={APPID}"

SIGN_URL = f"{BASE_URL}/wscump/checkin/checkinV2.json"
SIGN_INFO_URL = f"{BASE_URL}/wscump/checkin/check-in-info.json"
ACTIVITY_INFO_URL = f"{BASE_URL}/wscump/checkin/get_activity_by_yzuid_v2.json"
MONTH_SIGN_INFO_URL = f"{BASE_URL}/wscump/checkin/find_checkin_info_by_month.json"
USER_LEVEL_URL = f"{BASE_URL}/retail/h5/user/levelInfo.json"
ASSET_INFO_URL = f"{BASE_URL}/retail/h5/showcase/getAssetInfo.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541923) XWEB/19899"
)

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


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"


def json_preview(data: Any, limit: int = 800) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)[:limit]
    except Exception:
        return str(data)[:limit]


def log_title() -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🦘 袋鼠妈妈会员商场签到脚本                  ║")
    print(f"║ 🕒 启动时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")


def log_account_header(index: int, total: int, account_mask: str) -> None:
    print()
    print("┌" + "─" * 50 + "┐")
    print(f"│ 🧩 账号 {index} / {total:<37}│")
    print(f"│ 🆔 标识 {account_mask:<40}│")
    print("└" + "─" * 50 + "┘")


def check_env() -> List[str]:
    """读取青龙环境变量中的账号列表"""
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

        account_list = []
        for item in raw_list:
            item = item.strip()
            if item:
                account_list.append(item)
        return account_list
    except Exception as e:
        print(f"❌ [环境] 读取账号列表失败: {e}")
        return []


def direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


# ===================== 品赞代理 =====================
def parse_proxy_response(text: Any) -> Dict[str, Any] | None:
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


def build_proxy_dict(proxy_info: Dict[str, Any] | None) -> Dict[str, str] | None:
    if not proxy_info:
        return None

    host = proxy_info["host"]
    port = proxy_info["port"]
    username = proxy_info.get("username", "")
    password = proxy_info.get("password", "")

    auth = ""
    if username and password:
        auth = f"{quote(username)}:{quote(password)}@"

    scheme = "socks5" if PROXY_TYPE == "socks5" else "http"
    proxy_url = f"{scheme}://{auth}{host}:{port}"

    print(f"🛠️ [代理] 生成 {scheme.upper()} 代理 {host}:{port}")

    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def validate_proxy(proxies: Dict[str, str] | None) -> Tuple[bool, str]:
    if not proxies:
        return False, ""

    try:
        response = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
        if response.status_code == 200:
            try:
                ip = response.json().get("origin", "未知")
            except Exception:
                ip = "未知"
            print(f"✅ [代理] 验证通过，出口 IP: {ip}")
            return True, ip
    except Exception as exc:
        print(f"⚠️ [代理] 验证失败: {exc}")

    return False, ""


def get_valid_proxy(account_name: str) -> Tuple[Dict[str, str] | None, str]:
    if not PROXY_API:
        print(f"⚠️ [代理] {account_name} 未配置 PROXY_API，使用直连")
        return None, ""

    print(f"🌐 [代理] {account_name} 正在获取品赞代理...")

    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            response = direct_session().get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(response.text)

            if not proxy_info:
                print(f"⚠️ [代理] 第 {index} 次代理解析失败")
                continue

            print(f"✅ [代理] 提取到 {proxy_info['host']}:{proxy_info['port']}")
            proxies = build_proxy_dict(proxy_info)

            ok, ip = validate_proxy(proxies)
            if ok:
                return proxies, ip

            print(f"⚠️ [代理] 第 {index} 次代理不可用")
        except Exception as exc:
            print(f"⚠️ [代理] 第 {index} 次获取代理异常: {exc}")

        if index < PROXY_RETRY_TIMES:
            sleep(2)

    print("⚠️ [代理] 获取失败，使用直连")
    return None, ""


def request_with_proxy(
    method: str,
    url: str,
    *,
    proxies: Dict[str, str] | None = None,
    account_name: str = "",
    **kwargs,
) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)

    if proxies:
        try:
            return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception as exc:
            print(f"⚠️ [代理] {account_name} 代理请求失败: {exc}")
            if not ENABLE_DIRECT_FALLBACK:
                raise
            print("🔁 [兜底] 切换直连重试")

    session = direct_session()
    return session.request(method, url, **kwargs)


# ===================== 青龙推送 =====================
def send_qinglong_notify(title: str, content: str) -> None:
    """青龙面板原生推送通知"""
    try:
        import notify
        notify.send(title, content)
        print("✅ [青龙推送] 失败通知发送成功")
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
            print("✅ [青龙推送] 失败通知发送成功")
    except Exception as e:
        print(f"❌ [青龙推送] 发送失败: {e}")


# ===================== 业务接口封装 =====================
def common_headers(token: str | None = None) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "*/*",
        "xweb_xhr": "1",
        "Referer": f"https://servicewechat.com/{APPID}/39/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if token:
        headers["Extra-Data"] = json.dumps({
            "is_weapp": 1,
            "sid": "",
            "version": "2.232.5.101",
            "client": "weapp",
            "bizEnv": "wsc",
            "uuid": f"ksf0JQIifXUPu1F1780153190717",
            "ftime": 1780153190714
        })
    return headers


def login_by_code(account_name: str, code: str, proxies: Dict[str, str] | None) -> Tuple[str | None, Dict[str, Any] | None, str]:
    """使用code登录，返回(token, 完整响应, session_id)"""
    try:
        print("🔐 [登录] 使用 code 换 token")
        payload = {
            "appId": APPID,
            "code": code,
            "platformName": "weapp",
            "signature": "windows",
            "clientBiz": "weapp_wsc",
            "inWsc": True,
            "kdtId": KDT_ID,
            "extraBizData": {
                "enterOptions": {
                    "extKdtId": int(KDT_ID),
                    "path": "pages/home/dashboard/index",
                    "query": {},
                    "scene": 1005,
                    "referrerInfo": {},
                    "hostExtraData": {},
                    "apiCategory": "default"
                },
                "guideBizDataMap": {
                    "from_params": ""
                },
                "sceneData": {}
            }
        }

        response = request_with_proxy(
            "POST",
            LOGIN_URL,
            headers=common_headers(),
            json=payload,
            proxies=proxies,
            account_name=account_name,
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:800]}

        token = data.get("data", {}).get("accessToken")
        session_id = data.get("data", {}).get("sessionId", "")
        if token and token != "null":
            print(f"✅ [登录] token 获取成功: {mask(token)}")
            return token, data, session_id

        print(f"❌ [登录] 未识别 token 字段: {json_preview(data)}")
        return None, data, ""
    except Exception as exc:
        print(f"❌ [登录] 请求异常: {exc}")
        return None, None, ""


def api_get(account_name: str, url: str, token: str, proxies: Dict[str, str] | None, params: Dict[str, Any] | None = None, session_id: str = "") -> Dict[str, Any]:
    if params is None:
        params = {}

    params_with_token = {**params, "app_id": APPID, "kdt_id": KDT_ID, "access_token": token}

    headers = common_headers(token)
    if session_id:
        headers["Extra-Data"] = json.dumps({
            "is_weapp": 1,
            "sid": session_id,
            "version": "2.232.5.101",
            "client": "weapp",
            "bizEnv": "wsc",
            "uuid": f"ksf0JQIifXUPu1F1780153190717",
            "ftime": 1780153190714
        })

    response = request_with_proxy(
        "GET",
        url,
        headers=headers,
        params=params_with_token,
        proxies=proxies,
        account_name=account_name,
    )
    try:
        return response.json()
    except Exception:
        return {
            "code": -1,
            "msg": f"JSON解析失败: {response.text[:300]}",
        }


# ===================== 单账号执行 =====================
def run_account(index: int, total: int, wx_id: str, adapter: WechatCodeAdapter) -> Dict[str, Any]:
    account_name = mask(wx_id)
    result = {
        "account": account_name,
        "success": False,
        "proxyStatus": "未使用代理",
        "proxyIp": "-",
        "token": "-",
        "nickname": "-",
        "userId": "-",
        "signMsg": "-",
        "signDays": "-",
        "points": "-",
        "error": "",
    }

    log_account_header(index, total, account_name)

    proxies, proxy_ip = get_valid_proxy(account_name)
    result["proxyStatus"] = "使用专属代理" if proxies else "使用直连"
    result["proxyIp"] = proxy_ip or "-"

    sleep(PROXY_FETCH_INTERVAL)

    delay = random.randint(2, 6)
    print(f"⏳ [延迟] 启动延迟 {delay}s")
    sleep(delay)

    code = adapter.get_code(wx_id)
    if not code:
        result["error"] = "获取 code 失败"
        return result

    token, raw_login, session_id = login_by_code(account_name, code, proxies)
    if not token:
        result["error"] = f"登录失败: {json_preview(raw_login)}"
        return result

    result["token"] = mask(token)

    login_data = raw_login.get("data", {})
    if login_data:
        nickname = login_data.get("nickname") or login_data.get("nickName") or "未知用户"
        user_id = login_data.get("userId") or login_data.get("buyerId") or "-"
        result["nickname"] = nickname
        result["userId"] = str(user_id)
        print(f"👤 [用户] 昵称: {nickname}, ID: {user_id}")

    try:
        # 查询资产信息
        asset_resp = api_get(account_name, ASSET_INFO_URL, token, proxies, {}, session_id)
        if asset_resp.get("code") == 0:
            asset_data = asset_resp.get("data", {})
            level_name = asset_data.get("memberInfo", {}).get("vipName") or "未知等级"
            points = asset_data.get("assetInfo", {}).get("currentPoints") or "0"
            balance = asset_data.get("assetInfo", {}).get("storedBalanceValue") or "0"
            vouchers = asset_data.get("assetInfo", {}).get("voucherNum") or "0"
            result["points"] = points
            print(f"⭐ [等级] {level_name}, 积分: {points}")
            print(f"💰 [资产] 余额: {balance}, 优惠券: {vouchers}")

        # 查询当月签到记录
        month_sign_resp = api_get(account_name, MONTH_SIGN_INFO_URL, token, proxies, {
            "checkin_id": CHECKIN_ID,
            "year": datetime.now().year,
            "month": datetime.now().month
        }, session_id)
        if month_sign_resp.get("code") == 0:
            sign_data = month_sign_resp.get("data", {})
            checkin_dates = sign_data.get("checkin_date") or []
            sign_days = len(checkin_dates)
            result["signDays"] = f"{sign_days} 天"
            print(f"📅 [签到] 当月签到: {sign_days} 天")

        # 执行签到
        sign_resp = api_get(account_name, SIGN_URL, token, proxies, {
            "checkinId": CHECKIN_ID
        }, session_id)
        if sign_resp.get("code") == 0:
            sign_data = sign_resp.get("data", {})
            success = sign_data.get("success", False)
            if success:
                reward_list = sign_data.get("list", [])
                if reward_list:
                    reward = reward_list[0]
                    reward_info = reward.get("infos", {})
                    reward_title = reward_info.get("title", "未知奖励")
                    result["signMsg"] = f"签到成功: 获得 {reward_title}"
                    print(f"✅ [签到] {result['signMsg']}")
                else:
                    result["signMsg"] = "签到成功，但未获得奖励"
                    print(f"✅ [签到] {result['signMsg']}")
            else:
                msg = sign_data.get("desc") or "签到失败"
                result["signMsg"] = f"签到失败: {msg}"
                print(f"⚠️ [签到] {result['signMsg']}")
        else:
            msg = sign_resp.get("msg") or sign_resp.get("message") or "签到失败"
            result["signMsg"] = f"签到失败: {msg}"
            print(f"⚠️ [签到] {result['signMsg']}")

        result["success"] = True
        return result

    except Exception as exc:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行失败: {exc}")
        return result


# ===================== 通知汇总 =====================
def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    content = f"""⚠️ 袋鼠妈妈签到任务异常提醒
━━━━━━━━━━━━━━━━━━━━
🏁 总结：{success_count} 成功 / {fail_count} 失败
🕒 时间：{now_text()}
━━━━━━━━━━━━━━━━━━━━
"""
    for idx, res in enumerate(results, 1):
        icon = "✅" if res["success"] else "❌"
        content += f"""
🧩 账号 {idx}
🆔 标识：{res["account"]}
🌐 代理：{res["proxyStatus"]}
📡 出口IP：{res["proxyIp"]}
👤 昵称：{res["nickname"]}
🆔 用户ID：{res["userId"]}
📝 签到：{res["signMsg"]}
📅 当月签到：{res["signDays"]}
💰 积分：{res["points"]}
{icon} 结果：{"成功" if res["success"] else "失败"}
"""
        if not res["success"]:
            content += f"❌ 原因：{res['error']}\n"
        content += "━━━━━━━━━━━━━━━━━━━━\n"
    return content


# ===================== 主函数 =====================
def main() -> None:
    log_title()

    # 初始化统一微信协议适配器
    adapter = WechatCodeAdapter(APPID)
    account_list = check_env()
    total = len(account_list)

    if total == 0:
        print("❌ 没有可用的账号，任务退出")
        return

    results: List[Dict[str, Any]] = []
    for index, wx_id in enumerate(account_list, 1):
        try:
            result = run_account(index, total, wx_id, adapter)
            results.append(result)
        except Exception as exc:
            print(f"❌ [主程序] 账号 {index} 执行异常: {exc}")
            results.append({
                "account": mask(wx_id),
                "success": False,
                "proxyStatus": "-",
                "proxyIp": "-",
                "token": "-",
                "nickname": "-",
                "userId": "-",
                "signMsg": "-",
                "signDays": "-",
                "points": "-",
                "error": traceback.format_exc().strip(),
            })

        if index < total:
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 袋鼠妈妈任务执行完成                      ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    # 仅失败时推送通知
    if NOTIFY and fail_count > 0:
        send_qinglong_notify("⚠️ 袋鼠妈妈签到任务失败提醒", build_notify(results))


if __name__ == "__main__":
    main()