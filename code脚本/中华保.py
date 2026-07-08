#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
中华保自动任务脚本（适配器版）
功能：
  1. 统一微信协议适配器获取code（支持YYB-Go等全协议）
  2. code换token（带签名校验）
  3. 每日签到
  4. 任务提交 + 奖励领取
  5. 积分前后查询
  6. 青龙面板原生推送通知
  7. 品赞代理，失败直连兜底
环境变量：
  soy_wxid_data       微信id，多账号换行/@分隔
  soy_codeurl_data    微信授权服务地址（YYB-Go填完整地址）
  soy_codetoken_data  微信授权token（YYB-Go留空）
  LY_NOTIFY           填true开启青龙推送
  PROXY_API           品赞代理提取API，可选
  PROXY_TYPE          http / socks5，默认http
依赖：
  pip install requests pycryptodome
  socks5代理需：pip install requests[socks]
------------------------------------------------------------
更新日志:
2026/07/07  V2.0    重构为统一适配器版，适配YYB-Go，仅保留青龙原生推送
"""

import os
import json
import hmac
import hashlib
import base64
import uuid
import time
import random
import traceback
import sys
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import requests


APP_NAME = "中华保自动任务小程序"
APP_ID = "wx16ad5860375f084d"
SECRET_KEY = "a0febe42a67811eba09f0242ac110003"
SIGN_KEY = "adf1d8eaa67811eba09f0242ac110003"
DEFAULT_PATH = "pages/home/home#pages/home/home#1256"

MULTI_ACCOUNT_SPLIT = ["\n", "@"]
NOTIFY = os.getenv("LY_NOTIFY", "").lower() == "true"

PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

BASE_URL = "https://sfa.cic.cn"

USER_AGENT = (
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


def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"


def mask_name(name: str) -> str:
    if not name or len(name) == 0:
        return "未实名用户"
    if len(name) == 1:
        return name
    return name[0] + "*" * (len(name) - 1)


def json_preview(data: Any, limit: int = 800) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)[:limit]
    except Exception:
        return str(data)[:limit]


def log_title() -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🛡️ 中华保自动任务脚本                           ║")
    print(f"║ 🕒 启动时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")


def log_account_header(index: int, total: int, wxid_mask: str) -> None:
    print()
    print("┌" + "─" * 50 + "┐")
    print(f"│ 🧩 账号 {index} / {total:<37}│")
    print(f"│ 🆔 标识 {wxid_mask:<40}│")
    print("└" + "─" * 50 + "┘")


def check_env() -> List[str]:
    """读取青龙环境变量中的微信账号列表"""
    try:
        soy_wxid_data = "1"
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


def direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


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


def get_token_from_code(account_name: str, code: str, proxies: Dict[str, str] | None) -> Tuple[str | None, Dict[str, Any] | None, str | None]:
    """使用微信code获取token"""
    try:
        print(f"🔐 [登录] 使用code获取token")

        url = f"{BASE_URL}/miniprogram/api/user/v2/getOpenId?code={code}"
        path = f"/miniprogram/api/user/v2/getOpenId?code={code}"
        nonce = generate_nonce()
        timestamp = generate_timestamp()
        signature = generate_signature(path, "", nonce, timestamp, "")

        headers = {
            "Host": "sfa.cic.cn",
            "Connection": "keep-alive",
            "appId": APP_ID,
            "timestamp": timestamp,
            "signature": signature,
            "secretKey": SECRET_KEY,
            "nonce": nonce,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Referer": f"https://servicewechat.com/{APP_ID}/594/page-frame.html",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        response = request_with_proxy(
            "GET",
            url,
            headers=headers,
            proxies=proxies,
            account_name=account_name,
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:800]}

        if data.get("code") == "200":
            payload = data.get("data", {})
            token = payload.get("token")
            user_name = payload.get("idName")

            if token:
                print(f"✅ [登录] token获取成功: {mask(token)}")
                print(f"👤 [登录] 用户: {user_name}")
                return token, data, user_name

        print(f"❌ [登录] 获取token失败: {json_preview(data)}")
        return None, data, None

    except Exception as exc:
        print(f"❌ [登录] 请求异常: {exc}")
        return None, None, None


def generate_nonce() -> str:
    return str(uuid.uuid4())


def generate_timestamp() -> str:
    return str(int(time.time() * 1000))


def generate_signature(path, body, nonce, timestamp, token):
    sign_string = f"{path}{body}{nonce}{timestamp}{token}"

    hmac_obj = hmac.new(
        SIGN_KEY.encode('utf-8'),
        sign_string.encode('utf-8'),
        hashlib.sha256
    )

    signature = base64.b64encode(hmac_obj.digest()).decode('utf-8')
    return signature


def build_headers(path, token, body="", method="GET"):
    nonce = generate_nonce()
    timestamp = generate_timestamp()
    signature = generate_signature(path, body, nonce, timestamp, token)

    headers = {
        "Host": "sfa.cic.cn",
        "Connection": "keep-alive",
        "appId": APP_ID,
        "timestamp": timestamp,
        "signature": signature,
        "secretKey": SECRET_KEY,
        "nonce": nonce,
        "path": DEFAULT_PATH,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "token": token
    }

    if method == "POST" and body:
        headers["Content-Length"] = str(len(body))

    return headers


def make_request(method, path, token, payload=None, custom_path=None, proxies=None, account_name=""):
    url = f"{BASE_URL}{path}"

    body = ""
    if method == "POST" and payload:
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)

    headers = build_headers(path, token, body, method)

    if custom_path:
        headers["path"] = custom_path

    try:
        if method == "GET":
            response = request_with_proxy("GET", url, headers=headers, proxies=proxies, account_name=account_name)
        else:
            response = request_with_proxy("POST", url, headers=headers, data=body.encode('utf-8'), proxies=proxies, account_name=account_name)

        result = response.json()

        if result.get("code") == "200":
            return True, result.get("data")
        else:
            return False, None

    except Exception as e:
        print(f"❌ 请求异常: {str(e)}")
        return False, None


def get_user_name(token, proxies=None, account_name=""):
    url = f"{BASE_URL}/miniprogram/api/integral/v2/queryIntegralCardWindows?areaCode=510107"
    headers = build_headers("/miniprogram/api/integral/v2/queryIntegralCardWindows?areaCode=510107", token, "", "GET")

    try:
        response = request_with_proxy("GET", url, headers=headers, proxies=proxies, account_name=account_name)
        result = response.json()

        if result.get("code") != "200":
            error_msg = result.get("msg", "Token错误")
            print(f"❌ Token失效: {error_msg}")
            return None

        data = result.get("data")
        if data and isinstance(data, list) and len(data) > 0:
            return data[0].get("name", "未实名用户")
        return "未实名用户"

    except Exception as e:
        print(f"❌ 获取用户名失败: {str(e)}")
        return None


def check_sign_status(token, proxies=None, account_name=""):
    success, data = make_request("GET", "/miniprogram/api/integral/v2/getSignInfo", token, proxies=proxies, account_name=account_name)
    if success and data:
        today_sign = data.get("todaySign", 0)
        initial_integral = data.get("totalIntegral", 0)
        return today_sign == 1, initial_integral
    return False, 0


def do_sign(token, proxies=None, account_name=""):
    today = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "description": "签到",
        "integralDate": today,
        "type": 0
    }

    custom_path = "pages/home/home#pages/shoppingMall/huaBaoPark/huaBaoHome/index#1256"
    success, _ = make_request("POST", "/miniprogram/api/integral/v2/sign", token, payload, custom_path, proxies, account_name)

    if success:
        print(f"✅ {today}签到成功")
        return True
    return False


def get_task_list(token, proxies=None, account_name=""):
    custom_path = "pages/home/home#pages/shoppingMall/huaBaoPark/huaBaoHome/index#1256"
    success, data = make_request("GET", "/miniprogram/api/huabaopark/v6/getHomePage", token, custom_path=custom_path, proxies=proxies, account_name=account_name)

    if success and data:
        return data
    return None


def submit_task(token, task_type, point_strategy_id, task_name, proxies=None, account_name=""):
    payload = {
        "integralTaskTypeCd": task_type,
        "pointStrategyId": point_strategy_id
    }

    if task_type == 11:
        payload["answerPassed"] = True

    custom_path = "pages/home/home#pages/shoppingMall/huaBaoPark/dailyQuiz/index#1256"
    success, _ = make_request("POST", "/miniprogram/api/huabaopark/v6/completedTask", token, payload, custom_path, proxies, account_name)

    return success


def receive_task_reward(token, task_id, task_name, proxies=None, account_name=""):
    payload = {"id": task_id}
    custom_path = "pages/home/home#pages/shoppingMall/huaBaoPark/huaBaoHome/index#1256"
    success, _ = make_request("POST", "/miniprogram/api/huabaopark/v6/receiveTaskIntegral", token, payload, custom_path, proxies, account_name)

    return success


def get_final_integral(token, name, initial_integral=0, proxies=None, account_name=""):
    success, data = make_request("GET", "/miniprogram/api/integral/v2/getSignInfo", token, proxies=proxies, account_name=account_name)
    if success and data:
        total_integral = data.get("totalIntegral", 0)
        earned_integral = total_integral - initial_integral
        print(f"🎉 【{name}】今日新增{earned_integral}积分，当前总积分{total_integral}")
        return total_integral
    return 0


def run_account(index: int, total: int, wx_id: str, adapter: WechatCodeAdapter) -> Dict[str, Any]:
    account_name = mask(wx_id)
    result = {
        "wxid": account_name,
        "success": False,
        "proxyStatus": "未使用代理",
        "proxyIp": "-",
        "token": "-",
        "userName": "-",
        "signMsg": "-",
        "taskMsg": "-",
        "rewardMsg": "-",
        "integral": "-",
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

    # 通过统一适配器获取code，自动适配YYB-Go
    code = adapter.get_code(wx_id)
    if not code:
        result["error"] = "获取 code 失败"
        return result

    token, raw_login, user_name = get_token_from_code(account_name, code, proxies)
    if not token:
        result["error"] = f"获取token失败: {json_preview(raw_login)}"
        return result

    result["token"] = mask(token)

    try:
        # 1. 获取用户昵称
        name = get_user_name(token, proxies, account_name)
        if not name:
            result["error"] = "Token失效或获取用户信息失败"
            return result

        masked_name = mask_name(name)
        result["userName"] = masked_name
        print(f"👤 当前账号: 【{masked_name}】")

        # 2. 检查签到状态并获取初始积分
        is_signed, initial_integral = check_sign_status(token, proxies, account_name)
        if is_signed:
            result["signMsg"] = f"今日已签到"
            print(f"✅ 【{masked_name}】今日已签到")
        else:
            print(f"⏰ 【{masked_name}】今日未签到")
            if do_sign(token, proxies, account_name):
                result["signMsg"] = f"签到成功"
            else:
                result["signMsg"] = "签到失败"

        sleep(2)

        # 3. 获取任务列表
        task_data = get_task_list(token, proxies, account_name)
        if not task_data:
            print("❌ 获取任务列表失败")
            result["error"] = "获取任务列表失败"
            return result

        # 4. 提交未完成的任务
        tasks_to_submit = []

        for i in range(1, 4):
            task_key = f"showTask{i}"
            task = task_data.get(task_key)
            if task and task.get("status") == 0:
                if task.get("taskType") == 4 and task.get("pointStrategyId") == 9:
                    print(f"🖕 跳过任务【{task.get('taskName')}】（无法自动完成）")
                    continue
                tasks_to_submit.append({
                    "taskType": task.get("taskType"),
                    "pointStrategyId": task.get("pointStrategyId"),
                    "taskName": task.get("taskName")
                })

        show_task_list = task_data.get("showTaskList", [])
        for task in show_task_list:
            if task.get("status") == 0:
                if task.get("taskType") == 4 and task.get("pointStrategyId") == 9:
                    continue
                tasks_to_submit.append({
                    "taskType": task.get("taskType"),
                    "pointStrategyId": task.get("pointStrategyId"),
                    "taskName": task.get("taskName")
                })

        task_results = []
        if tasks_to_submit:
            print(f"📝 发现{len(tasks_to_submit)}个待提交任务")
            print("⏳ 正在提交任务中，请稍后...")
            for task in tasks_to_submit:
                success = submit_task(token, task["taskType"], task["pointStrategyId"], task["taskName"], proxies, account_name)
                if success:
                    task_results.append(f"{task['taskName']}完成")
                else:
                    task_results.append(f"{task['taskName']}失败")
                sleep(1)
        else:
            print("✅ 没有待提交的任务")

        result["taskMsg"] = "、".join(task_results) if task_results else "无待提交任务"

        # 5. 再次获取任务列表,领取奖励
        wait_time = random.uniform(3, 5)
        sleep(wait_time)
        task_data = get_task_list(token, proxies, account_name)

        if task_data:
            tasks_to_receive = []

            for i in range(1, 4):
                task_key = f"showTask{i}"
                task = task_data.get(task_key)
                if task and task.get("status") == 2:
                    tasks_to_receive.append({
                        "id": task.get("id"),
                        "taskName": task.get("taskName")
                    })

            show_task_list = task_data.get("showTaskList", [])
            for task in show_task_list:
                if task.get("status") == 2:
                    tasks_to_receive.append({
                        "id": task.get("id"),
                        "taskName": task.get("taskName")
                    })

            # 领取奖励
            reward_results = []
            if tasks_to_receive:
                print(f"🎁 发现{len(tasks_to_receive)}个待领取奖励")
                print("⏳ 正在领取任务奖励，请稍后...")
                for task in tasks_to_receive:
                    success = receive_task_reward(token, task["id"], task["taskName"], proxies, account_name)
                    if success:
                        reward_results.append(f"{task['taskName']}领取成功")
                    else:
                        reward_results.append(f"{task['taskName']}领取失败")
                    sleep(1)
            else:
                print("✅ 没有待领取的奖励")

            result["rewardMsg"] = "、".join(reward_results) if reward_results else "无待领取奖励"

        # 6. 获取最终积分
        final_integral = get_final_integral(token, masked_name, initial_integral, proxies, account_name)
        result["integral"] = str(final_integral)

        result["success"] = True
        return result

    except Exception as exc:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行失败: {exc}")
        return result


def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    content = f"""🛡️ 中华保任务结果

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
🌐 代理：{res["proxyStatus"]}
📡 出口IP：{res["proxyIp"]}
🔐 Token：{res["token"]}
👤 用户：{res["userName"]}
📝 签到：{res["signMsg"]}
📋 任务：{res["taskMsg"]}
🎁 奖励：{res["rewardMsg"]}
💰 积分：{res["integral"]} 积分
{icon} 结果：{"成功" if res["success"] else "失败"}
"""

        if not res["success"]:
            content += f"❌ 原因：{res['error']}\n"

        content += "━━━━━━━━━━━━━━━━━━━━\n"

    return content


def main() -> None:
    log_title()

    # 初始化统一微信协议适配器
    adapter = WechatCodeAdapter(APP_ID)
    wxid_list = check_env()
    total = len(wxid_list)

    if total == 0:
        print("❌ 没有可用的账号，任务退出")
        return

    results: List[Dict[str, Any]] = []

    for index, wx_id in enumerate(wxid_list, 1):
        try:
            result = run_account(index, total, wx_id, adapter)
            results.append(result)
        except Exception as exc:
            print(f"❌ [主程序] 账号 {index} 执行异常: {exc}")
            results.append({
                "wxid": mask(wx_id),
                "success": False,
                "proxyStatus": "-",
                "proxyIp": "-",
                "token": "-",
                "userName": "-",
                "signMsg": "-",
                "taskMsg": "-",
                "rewardMsg": "-",
                "integral": "-",
                "error": traceback.format_exc().strip(),
            })

        if index < total:
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 中华保任务执行完成                            ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    if NOTIFY:
        send_qinglong_notify("🛡️ 中华保任务完成", build_notify(results))


if __name__ == "__main__":
    main()