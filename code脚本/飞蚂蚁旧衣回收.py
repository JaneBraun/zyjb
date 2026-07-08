#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞蚂蚁旧衣回收签到脚本（适配器版）
功能：
  1. 统一微信协议适配器获取code（支持YYB-Go等全协议）
  2. code 登录换取 token
  3. 每日签到 + 3 次步数兑换
  4. 品赞代理支持，失败自动直连兜底
  5. 青龙面板原生推送通知
环境变量：
  soy_wxid_data       账号标识（对应YYB-Go的ref/id），多账号用 & 或换行分隔
  soy_codeurl_data    微信授权服务地址（YYB-Go填完整地址）
  soy_codetoken_data  微信授权token（YYB-Go留空）
  LY_NOTIFY           填true开启青龙推送
  PROXY_API           品赞代理提取API，可选
  PROXY_TYPE          http / socks5，默认 http
  ENABLE_PER_ACCOUNT_PROXY  每个账号独立获取代理，默认true
依赖：
  pip install requests
  socks5代理需：pip install requests[socks]
------------------------------------------------------------
更新日志:
2026/07/07  V2.0    Python重构，适配统一适配器，新增青龙原生推送
"""

import os
import sys
import json
import time
import random
import traceback
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote, urlencode

import requests

APP_NAME = "飞蚂蚁旧衣回收"
APPID = "wx501990400906c9ff"
PLATFORM_KEY = "F2EE24892FBF66F0AFF8C0EB532A9394"
APP_VERSION = "V2.00.01"
API_BASE = "https://openapp.fmy90.com"

MULTI_ACCOUNT_SPLIT = ["\n", "&"]
NOTIFY = os.getenv("LY_NOTIFY", "").lower() == "true"
REQUEST_TIMEOUT = 20

PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
ENABLE_PER_ACCOUNT_PROXY = os.getenv("ENABLE_PER_ACCOUNT_PROXY", "true").lower() == "true"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True

USER_AGENT_LIST = [
    "Mozilla/5.0 (Linux; Android 14; 2512BPNDAC Build/UKQ1.230917.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.153 Mobile Safari/537.36 XWEB/1460043 MMWEBSDK/20251006 MiniProgramEnv/android",
    "Mozilla/5.0 (Linux; Android 13; Redmi K60 Build/TKQ1.221114.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.6723.102 Mobile Safari/537.36 XWEB/1300003 MMWEBSDK/20250901 MiniProgramEnv/android",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541885) XWEB/19463",
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


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"


def get_ua() -> str:
    return random.choice(USER_AGENT_LIST)


def log_title() -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🧺 飞蚂蚁旧衣回收签到                         ║")
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
    if PROXY_TYPE == "socks5":
        proxy_url = f"socks5://{auth}{host}:{port}"
    else:
        proxy_url = f"http://{auth}{host}:{port}"
    print(f"🛠️ [代理] 生成 {PROXY_TYPE.upper()} 代理 {host}:{port}")
    return {"http": proxy_url, "https": proxy_url}


def validate_proxy(proxies: Dict[str, str] | None) -> Tuple[bool, str]:
    if not proxies:
        return False, ""
    try:
        res = direct_session().get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
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
            time.sleep(2)
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
            return direct_session().request(method, url, proxies=proxies, **kwargs)
        except Exception as exc:
            print(f"⚠️ [代理] {account_name} 代理请求失败：{exc}")
            if not ENABLE_DIRECT_FALLBACK:
                raise
            print("🔁 [兜底] 切换直连重试")
    return direct_session().request(method, url, **kwargs)


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


# ===================== 业务接口封装 =====================
def login_by_code(code: str, ua: str, proxies: Dict[str, str] | None, account_name: str) -> str | None:
    """使用code登录换取token"""
    print("🔐 [登录] 使用code换取token")
    headers = {
        "Host": "openapp.fmy90.com",
        "Connection": "keep-alive",
        "device-version": "Windows 10 x64",
        "User-Agent": ua,
        "xweb_xhr": "1",
        "Content-Type": "application/x-www-form-urlencoded",
        "device-model": "microsoft",
        "Accept": "*/*",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://servicewechat.com/{APPID}/506/page-frame.html",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    payload = {
        "code": code,
        "platformKey": PLATFORM_KEY,
        "version": APP_VERSION,
        "vital": "",
        "partner_platform_key": "",
    }
    try:
        res = request_with_proxy(
            "POST",
            f"{API_BASE}/auth/wx/login",
            headers=headers,
            data=urlencode(payload),
            proxies=proxies,
            account_name=account_name,
        )
        data = res.json()
        if data.get("code") == 200:
            # 多路径提取token，兼容不同响应结构
            data_obj = data.get("data", {})
            token = (
                data_obj.get("userInfo", {}).get("token")
                or data_obj.get("token")
                or data.get("token")
                or data_obj.get("access_token")
            )
            if token:
                print("✅ [登录] 登录成功，获取到有效token")
                return token
        print(f"❌ [登录] 失败：{data.get('message', '未知错误')}")
        return None
    except Exception as e:
        print(f"❌ [登录] 请求异常：{e}")
        return None


def do_sign(token: str, ua: str, proxies: Dict[str, str] | None, account_name: str) -> Tuple[bool, str]:
    """执行签到"""
    headers = {
        "Host": "openapp.fmy90.com",
        "Connection": "keep-alive",
        "device-version": "Windows 10 x64",
        "User-Agent": ua,
        "xweb_xhr": "1",
        "Content-Type": "application/json",
        "device-model": "microsoft",
        "Accept": "*/*",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://servicewechat.com/{APPID}/506/page-frame.html",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "authorization": f"Bearer {token}",
    }
    payload = {
        "version": APP_VERSION,
        "platformKey": PLATFORM_KEY,
        "mini_scene": 1089,
        "partner_ext_infos": "",
    }
    try:
        res = request_with_proxy(
            "POST",
            f"{API_BASE}/sign/new/do",
            headers=headers,
            json=payload,
            proxies=proxies,
            account_name=account_name,
        )
        data = res.json()
        if data.get("code") == 200:
            msg = data.get("message", "签到成功")
            return True, msg
        return False, data.get("message", "签到失败")
    except Exception as e:
        return False, f"请求异常：{e}"


def do_step_exchange(token: str, ua: str, proxies: Dict[str, str] | None, account_name: str, steps: int) -> Tuple[bool, str]:
    """步数兑换"""
    headers = {
        "Host": "openapp.fmy90.com",
        "Connection": "keep-alive",
        "device-version": "Windows 10 x64",
        "User-Agent": ua,
        "xweb_xhr": "1",
        "Content-Type": "application/json",
        "device-model": "microsoft",
        "Accept": "*/*",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://servicewechat.com/{APPID}/506/page-frame.html",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "authorization": f"Bearer {token}",
    }
    payload = {
        "steps": steps,
        "version": APP_VERSION,
        "platformKey": PLATFORM_KEY,
        "mini_scene": 1089,
        "partner_ext_infos": "",
    }
    try:
        res = request_with_proxy(
            "POST",
            f"{API_BASE}/step/exchange",
            headers=headers,
            json=payload,
            proxies=proxies,
            account_name=account_name,
        )
        data = res.json()
        if data.get("code") == 200:
            msg = data.get("message", "兑换成功")
            return True, msg
        return False, data.get("message", "兑换失败")
    except Exception as e:
        return False, f"请求异常：{e}"


# ===================== 单账号执行 =====================
def run_account(index: int, total: int, wx_id: str, adapter: WechatCodeAdapter, global_proxies: Dict | None = None) -> Dict[str, Any]:
    account_name = mask(wx_id)
    result = {
        "account": account_name,
        "success": False,
        "proxyStatus": "未使用代理",
        "proxyIp": "-",
        "signMsg": "-",
        "exchangeMsgs": [],
        "error": "",
    }

    log_account_header(index, total, account_name)
    ua = get_ua()
    proxies = global_proxies
    proxy_ip = "-"

    if ENABLE_PER_ACCOUNT_PROXY:
        proxies, proxy_ip = get_valid_proxy(account_name)
        result["proxyStatus"] = "使用专属代理" if proxies else "使用直连"
        result["proxyIp"] = proxy_ip
        time.sleep(PROXY_FETCH_INTERVAL)

    try:
        start_delay = random.randint(2, 6)
        print(f"⏳ [延迟] 启动延迟 {start_delay}s")
        time.sleep(start_delay)

        # 获取code（强制直连，不走代理）
        code = adapter.get_code(wx_id)
        if not code:
            result["error"] = "获取code失败"
            return result

        # 登录
        token = login_by_code(code, ua, proxies, account_name)
        if not token:
            result["error"] = "登录失败，未获取到有效token"
            return result

        time.sleep(random.randint(3, 8))

        # 签到
        sign_ok, sign_msg = do_sign(token, ua, proxies, account_name)
        result["signMsg"] = sign_msg
        if sign_ok:
            print(f"✅ [签到] {sign_msg}")
        else:
            print(f"❌ [签到] {sign_msg}")

        time.sleep(random.randint(2, 5))

        # 3次步数兑换
        for i in range(3):
            steps = random.randint(5000, 8000)
            print(f"🚶 [步数] 第{i+1}次兑换，步数：{steps}")
            ex_ok, ex_msg = do_step_exchange(token, ua, proxies, account_name, steps)
            result["exchangeMsgs"].append(f"第{i+1}次：{ex_msg}")
            if ex_ok:
                print(f"✅ [步数] {ex_msg}")
            else:
                print(f"❌ [步数] {ex_msg}")
            if i < 2:
                time.sleep(random.randint(3, 5))

        result["success"] = True
        return result

    except Exception as e:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行异常：{e}")
        return result


# ===================== 通知汇总 =====================
def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    content = f"""🧺 飞蚂蚁旧衣回收签到结果
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
📝 签到：{res["signMsg"]}
🚶 步数兑换：
"""
        for msg in res["exchangeMsgs"]:
            content += f"  - {msg}\n"
        content += f"{icon} 结果：{'成功' if res['success'] else '失败'}\n"
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

    # 全局代理（非单账号代理模式时使用）
    global_proxies = None
    if not ENABLE_PER_ACCOUNT_PROXY:
        global_proxies, _ = get_valid_proxy("全局共用")

    results: List[Dict[str, Any]] = []
    for index, wx_id in enumerate(account_list, 1):
        try:
            result = run_account(index, total, wx_id, adapter, global_proxies)
            results.append(result)
        except Exception as exc:
            print(f"❌ [主程序] 账号 {index} 执行异常: {exc}")
            results.append({
                "account": mask(wx_id),
                "success": False,
                "proxyStatus": "-",
                "proxyIp": "-",
                "signMsg": "-",
                "exchangeMsgs": [],
                "error": traceback.format_exc().strip(),
            })

        if index < total:
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            time.sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 飞蚂蚁旧衣回收任务执行完成                  ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    if NOTIFY:
        send_qinglong_notify("🧺 飞蚂蚁旧衣回收签到完成", build_notify(results))


if __name__ == "__main__":
    main()