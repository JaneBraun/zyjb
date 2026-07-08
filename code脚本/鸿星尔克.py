#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸿星尔克签到脚本（适配器版）
功能：
  1. 统一微信协议适配器获取code（支持YYB-Go等全协议）
  2. code 登录换取 memberId（含完整MD5签名校验）
  3. 每日签到领取积分
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

import hashlib
import json
import os
import random
import time
import traceback
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlencode
from typing import Any, Dict, List, Tuple

import requests

ENTERPRISE_ID = "ff8080817d9fbda8017dc20674f47fb6"
APPID = "wxa1f1fa3785a47c7d"
SECRET = "damogic8888"

MULTI_ACCOUNT_SPLIT = ["\n", "&"]
NOTIFY = os.getenv("LY_NOTIFY", "").lower() == "true"

PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541923) XWEB/19823"
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


def china_timestamp() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"


def mask_member_id(member_id: str) -> str:
    if len(member_id) <= 6:
        return member_id
    return f"{member_id[:3]}****{member_id[-3:]}"


def log_title() -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🎽 鸿星尔克签到脚本                           ║")
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
    return {"http": proxy_url, "https": proxy_url}


def validate_proxy(proxies: Dict[str, str] | None) -> Tuple[bool, str]:
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


def get_valid_proxy(account_name: str) -> Tuple[Dict[str, str] | None, str]:
    if not PROXY_API:
        print(f"⚠️ [代理] {account_name} 未配置 PROXY_API，使用直连")
        return None, ""
    print(f"🌐 [代理] {account_name} 正在获取品赞代理...")
    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            res = direct_session().get(PROXY_API, timeout=15)
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
            print(f"⚠️ [代理] 第 {index} 次获取代理异常: {exc}")
        if index < PROXY_RETRY_TIMES:
            sleep(2)
    print("⚠️ [代理] 获取代理失败，使用直连")
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
            print(f"⚠️ [代理] {account_name} 代理请求失败：{exc}")
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


# ===================== 业务核心函数 =====================
def make_sign(timestamp: str, random_int: int, member_id: str = "-1") -> str:
    sign_raw = (
        f"timestamp={timestamp}transId={APPID}{timestamp}"
        f"secret={SECRET}random={random_int}memberId={member_id}"
    )
    return hashlib.md5(sign_raw.encode("utf-8")).hexdigest()


def build_system_info() -> str:
    return json.dumps(
        {
            "SDKVersion": "3.16.0",
            "batteryLevel": "0",
            "brand": "microsoft",
            "fontSizeSetting": "-1",
            "language": "zh_CN",
            "model": "microsoft",
            "pixelRatio": 1,
            "platform": "windows",
            "screenHeight": 780,
            "screenWidth": 414,
            "statusBarHeight": 20,
            "system": "Windows 10 x64",
            "version": "4.1.9.35",
            "windowHeight": 716,
            "windowWidth": 414,
            "benchmarkLevel": -1,
            "safeArea": {
                "bottom": 780,
                "height": 716,
                "left": 0,
                "right": 414,
                "top": 64,
                "width": 414,
            },
            "theme": "light",
            "host": {
                "appId": "",
                "env": "WeChat",
            },
            "enableDebug": "-1",
            "mode": "-1",
            "deviceOrientation": "-1",
            "bluetoothEnabled": "-1",
            "locationEnabled": True,
            "wifiEnabled": True,
            "albumAuthorized": True,
            "cameraAuthorized": True,
            "locationAuthorized": True,
            "microphoneAuthorized": True,
            "notificationAuthorized": True,
            "notificationAlertAuthorized": "-1",
            "notificationBadgeAuthorized": "-1",
            "notificationSoundAuthorized": "-1",
            "phoneCalendarAuthorized": "-1",
            "bluetoothAuthorized": "-1",
            "locationReducedAccuracy": "-1",
            "devicePixelRatio": 1,
            "renderer": "-1",
            "environment": "-1",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def extract_member_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    candidates = [
        data.get("memberId"),
        data.get("member_id"),
    ]
    response = data.get("response")
    if isinstance(response, dict):
        candidates.extend([
            response.get("memberId"),
            response.get("member_id"),
        ])
        member = response.get("member")
        if isinstance(member, dict):
            candidates.extend([
                member.get("memberId"),
                member.get("member_id"),
                member.get("id"),
            ])
        user = response.get("user")
        if isinstance(user, dict):
            candidates.extend([
                user.get("memberId"),
                user.get("member_id"),
                user.get("id"),
            ])
    inner = data.get("data")
    if isinstance(inner, dict):
        candidates.extend([
            inner.get("memberId"),
            inner.get("member_id"),
        ])
        member = inner.get("member")
        if isinstance(member, dict):
            candidates.extend([
                member.get("memberId"),
                member.get("member_id"),
                member.get("id"),
            ])
    for item in candidates:
        if item not in (None, "", "-1", -1):
            return str(item)
    return None


def login_by_code(account_name: str, code: str, proxies: Dict[str, str] | None) -> Tuple[str | None, Dict | None]:
    timestamp = china_timestamp()
    random_int = random.randint(1000000, 9999999)
    sign = make_sign(timestamp, random_int, "-1")
    url = "https://hope.demogic.com/gic-wx-app/on_login.json"
    headers = {
        "Host": "hope.demogic.com",
        "Connection": "keep-alive",
        "sign": "",
        "User-Agent": UA,
        "channelEntrance": "wx_app",
        "xweb_xhr": "1",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://servicewechat.com/{APPID}/89/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    body = {
        "systemInfo": build_system_info(),
        "jcode": code,
        "openid": "",
        "scene": "1027",
        "memberId": "-1",
        "cliqueId": "-1",
        "cliqueMemberId": "-1",
        "useClique": "0",
        "enterpriseId": "",
        "unionid": "",
        "wxOpenid": "",
        "random": str(random_int),
        "appid": APPID,
        "transId": f"{APPID}{timestamp}",
        "sign": sign,
        "timestamp": timestamp,
        "gicWxaVersion": "3.9.74",
        "launchOptions": json.dumps(
            {
                "path": "pages/authorize/authorize",
                "query": {},
                "scene": 1027,
                "referrerInfo": {},
                "apiCategory": "default",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    try:
        print("🔐 [登录] 使用 code 换 memberId")
        res = request_with_proxy(
            "POST",
            url,
            headers=headers,
            data=urlencode(body),
            proxies=proxies,
            account_name=account_name,
        )
        try:
            data = res.json()
        except Exception:
            data = {"raw": res.text[:800]}
        member_id = extract_member_id(data)
        if member_id:
            print(f"✅ [登录] 登录成功，memberId={mask_member_id(member_id)}")
            return member_id, data
        print(f"❌ [登录] 登录失败或未识别 memberId")
        return None, data
    except Exception as exc:
        print(f"❌ [登录] 登录异常：{exc}")
        return None, None


def sign_once(account_name: str, member_id: str, proxies: Dict[str, str] | None) -> Dict[str, Any]:
    timestamp = china_timestamp()
    random_int = random.randint(1000000, 9999999)
    trans_id = f"{APPID}{timestamp}"
    sign = make_sign(timestamp, random_int, member_id)
    url = "https://hope.demogic.com/gic-wx-app/member_sign.json"
    headers = {
        "xweb_xhr": "1",
        "channelEntrance": "wx_app",
        "User-Agent": UA,
        "sign": ENTERPRISE_ID,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Referer": f"https://servicewechat.com/{APPID}/89/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    body = {
        "memberId": member_id,
        "cliqueId": "-1",
        "cliqueMemberId": "-1",
        "useClique": "0",
        "enterpriseId": ENTERPRISE_ID,
        "random": str(random_int),
        "sign": sign,
        "timestamp": timestamp,
        "transId": trans_id,
        "gicWxaVersion": "3.9.74",
        "launchOptions": json.dumps(
            {
                "path": "pages/authorize/authorize",
                "query": {},
                "scene": 1256,
                "referrerInfo": {},
                "apiCategory": "default",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    response = request_with_proxy(
        "POST",
        url,
        headers=headers,
        data=urlencode(body),
        proxies=proxies,
        account_name=account_name,
    )
    if not response.ok:
        return {
            "success": False,
            "message": f"HTTP {response.status_code}: {response.text[:200]}",
            "points": "-",
        }
    try:
        data = response.json()
    except Exception:
        return {
            "success": False,
            "message": f"JSON解析失败: {response.text[:300]}",
            "points": "-",
        }
    errcode = data.get("errcode")
    if errcode == 0:
        result = data.get("response") or {}
        member_sign = result.get("memberSign") or {}
        integral = member_sign.get("integralCount", "未知")
        continuous = member_sign.get("continuousCount", "未知")
        points = result.get("points", "未知")
        return {
            "success": True,
            "message": f"签到成功，获得积分 {integral}，连续签到 {continuous} 天",
            "points": points,
        }
    errmsg = (
        data.get("errmsg")
        or data.get("msg")
        or data.get("message")
        or (data.get("response") or {}).get("errmsg")
        or (data.get("response") or {}).get("msg")
        or ""
    )
    if errcode == 900001:
        msg = f"签到失败(errcode=900001){'，errmsg=' + str(errmsg) if errmsg else ''}"
    else:
        msg = f"签到结果未知，errcode={errcode}{'，errmsg=' + str(errmsg) if errmsg else ''}"
    return {
        "success": False,
        "message": msg,
        "points": "-",
    }


# ===================== 单账号执行 =====================
def run_account(index: int, total: int, wx_id: str, adapter: WechatCodeAdapter) -> Dict[str, Any]:
    account_name = mask(wx_id)
    result = {
        "account": account_name,
        "success": False,
        "proxyStatus": "未使用代理",
        "proxyIp": "-",
        "memberId": "-",
        "signMsg": "-",
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

    member_id, raw = login_by_code(account_name, code, proxies)
    if not member_id:
        result["error"] = "登录失败或未识别 memberId"
        return result

    result["memberId"] = mask_member_id(member_id)
    sign_result = sign_once(account_name, member_id, proxies)

    result["success"] = bool(sign_result["success"])
    result["signMsg"] = sign_result["message"]
    result["points"] = sign_result["points"]

    if result["success"]:
        print(f"✅ [签到] {result['signMsg']}，积分余额 {result['points']}")
    else:
        print(f"❌ [签到] {result['signMsg']}")

    return result


# ===================== 通知汇总 =====================
def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    content = f"""⚠️ 鸿星尔克签到任务异常提醒
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
🆔 memberId：{res["memberId"]}
📝 签到：{res["signMsg"]}
💰 积分余额：{res["points"]}
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
                "memberId": "-",
                "signMsg": "-",
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
    print("║ 🏁 鸿星尔克任务执行完成                      ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    # 仅失败时推送通知
    if NOTIFY and fail_count > 0:
        send_qinglong_notify("⚠️ 鸿星尔克签到任务失败提醒", build_notify(results))


if __name__ == "__main__":
    main()