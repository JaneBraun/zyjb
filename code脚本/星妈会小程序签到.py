#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
星妈会小程序签到脚本（适配器版）

功能：
  1. 统一微信协议适配器获取code（支持YYB-Go等全协议）
  2. 使用 code 换取 token（优先），支持 TOKEN 环境变量兜底
  3. 每日签到
  4. 查询会员信息、积分余额、积分明细
  5. 青龙面板原生推送通知
  6. 品赞代理，业务请求优先代理，失败直连兜底

环境变量：
  soy_wxid_data       微信id，多账号换行/@分隔
  soy_codeurl_data    微信授权服务地址（YYB-Go填完整地址）
  soy_codetoken_data  微信授权token（YYB-Go留空）
  TOKEN               直接使用的 token（code 登录失败时兜底）
  LY_NOTIFY           填true开启青龙推送
  PROXY_API           品赞代理提取API，可选
  PROXY_TYPE          http / socks5，默认 http

依赖：
  pip install requests
  socks5 代理需：pip install requests[socks]
------------------------------------------------------------
更新日志:
2026/07/07  V2.0    重构为统一适配器版，适配YYB-Go，仅保留青龙原生推送
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

APP_NAME = "星妈会小程序"
APPID = "wxc83b55d61c7fc51d"

MULTI_ACCOUNT_SPLIT = ["\n", "@"]
NOTIFY = os.getenv("LY_NOTIFY", "").lower() == "true"

TOKEN = os.getenv("TOKEN", "")
PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()

PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

BASE_URL = "https://momclub.feihe.com"
CODE_TO_TOKEN_URL = f"{BASE_URL}/capis/social/ma"
CHECKIN_URL = f"{BASE_URL}/capis/c/activity/todo/checkIn"
TODO_LIST_URL = f"{BASE_URL}/capis/c/activity/todo/list"
MEMBER_INFO_URL = f"{BASE_URL}/capis/c/user/memberInfo"
SCORE_INDEX_URL = f"{BASE_URL}/capis/c/equity/score/index"
SCORE_MONTH_URL = f"{BASE_URL}/capis/c/equity/score/monthPage"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541938) XWEB/19823"
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
    print("║ ⭐ 星妈会小程序签到                            ║")
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
        soy_wxid_data = os.getenv("soy_wxid_data", "")
        if not soy_wxid_data:
            # 兼容旧版单TOKEN模式，无wxid时返回空，走TOKEN兜底
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


def common_headers(token: str | None = None) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "*/*",
        "xweb_xhr": "1",
        "locale": "zh_CN",
        "Referer": f"https://servicewechat.com/{APPID}/125/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if token:
        headers["Authorization"] = token
    return headers


def login_by_code(code: str, proxies: Dict[str, str] | None) -> Tuple[str | None, Dict[str, Any] | None]:
    try:
        print("🔐 [登录] 使用 code 换 token")
        response = request_with_proxy(
            "POST",
            CODE_TO_TOKEN_URL,
            headers=common_headers(),
            data=f'"{code}"',
            proxies=proxies,
            account_name="",
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:800]}

        if data.get("code") == "00000" and data.get("success"):
            token_info = data.get("data", {}).get("tokenInfo", {})
            token = token_info.get("accessToken")
            if token:
                print(f"✅ [登录] token 获取成功: {mask(token)}")
                return token, data

        print(f"❌ [登录] code 登录失败: {json_preview(data)}")
        return None, data
    except Exception as exc:
        print(f"❌ [登录] 请求异常: {exc}")
        return None, None


def api_get(account_name: str, url: str, token: str, proxies: Dict[str, str] | None, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    try:
        response = request_with_proxy(
            "GET",
            url,
            headers=common_headers(token),
            params=params,
            proxies=proxies,
            account_name=account_name,
        )
        return response.json()
    except Exception as exc:
        print(f"⚠️ [请求] GET 请求失败: {exc}")
        return {
            "code": -1,
            "msg": f"请求失败: {str(exc)}",
            "data": None
        }


def api_post(account_name: str, url: str, token: str, proxies: Dict[str, str] | None, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        response = request_with_proxy(
            "POST",
            url,
            headers=common_headers(token),
            json=payload,
            proxies=proxies,
            account_name=account_name,
        )
        return response.json()
    except Exception as exc:
        print(f"⚠️ [请求] POST 请求失败: {exc}")
        return {
            "code": -1,
            "msg": f"请求失败: {str(exc)}",
            "data": None
        }


def run_account(index: int, total: int, wx_id: str, adapter: WechatCodeAdapter) -> Dict[str, Any]:
    account_name = mask(wx_id) if wx_id else "TOKEN兜底账号"
    result = {
        "wxid": account_name,
        "success": False,
        "proxyStatus": "未使用代理",
        "proxyIp": "-",
        "token": "-",
        "loginMethod": "-",
        "memberId": "-",
        "memberName": "-",
        "points": "-",
        "gradeName": "-",
        "signMsg": "-",
        "checkInCount": 0,
        "recentScores": "-",
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

    token = None
    login_method = ""

    # 优先通过适配器获取code登录
    if wx_id:
        try:
            code = adapter.get_code(wx_id)
            if code:
                token, raw_login = login_by_code(code, proxies)
                if token:
                    login_method = "code 登录"
                else:
                    print("⚠️ [登录] code 登录失败，尝试使用 TOKEN 环境变量")
        except Exception as exc:
            print(f"⚠️ [登录] code 获取失败: {exc}")

    # code登录失败时使用环境变量TOKEN兜底
    if not token and TOKEN:
        token = TOKEN
        login_method = "环境变量 TOKEN"
        print(f"🔐 [登录] 使用环境变量 TOKEN: {mask(token)}")

    if not token:
        result["error"] = "无法获取 token（code 登录失败且未配置 TOKEN）"
        return result

    result["token"] = mask(token)
    result["loginMethod"] = login_method

    try:
        # 获取会员信息
        member_resp = api_get(account_name, MEMBER_INFO_URL, token, proxies)
        if member_resp and member_resp.get("code") == "00000":
            member_data = member_resp.get("data", {})
            member_id = member_data.get("memberId", "-")
            member_name = member_data.get("memberName", "-")
            points = member_data.get("points", 0)
            grade_name = member_data.get("gradeName", "-")
            result["memberId"] = member_id
            result["memberName"] = member_name
            result["points"] = str(points)
            result["gradeName"] = grade_name
            print(f"👤 [信息] 会员: {member_name} ({member_id})")
            print(f"💰 [积分] 当前积分: {points}")
            print(f"🏆 [等级] {grade_name}")
        else:
            error_msg = member_resp.get("msg", "未知错误") if member_resp else "请求失败"
            print(f"⚠️ [信息] 获取会员信息失败: {error_msg}")

        # 获取待办任务与签到
        mock_time = int(time.time() * 1000)
        todo_resp = api_get(account_name, TODO_LIST_URL, token, proxies, {"mockTime": mock_time})
        if todo_resp and todo_resp.get("code") == "000000":
            todo_data = todo_resp.get("data", {})
            check_in_todo = todo_data.get("checkInTodo", {})
            check_in_id = check_in_todo.get("id", 1111)
            join_record = check_in_todo.get("joinRecord", [])
            check_in_count = sum(1 for record in join_record if record.get("joined"))
            result["checkInCount"] = check_in_count
            print(f"📋 [签到] 本月已签到 {check_in_count} 天")

            if not any(record.get("today") for record in join_record):
                print("✅ [签到] 今日未签到，开始签到...")
                check_in_resp = api_post(account_name, CHECKIN_URL, token, proxies, {
                    "activityId": check_in_id,
                    "mockTime": mock_time
                })

                if not check_in_resp:
                    result["signMsg"] = "签到请求失败，响应为空"
                    print(f"❌ [签到] 签到请求失败，响应为空")
                else:
                    check_in_code = check_in_resp.get("code")
                    check_in_msg = check_in_resp.get("msg", "")
                    data = check_in_resp.get("data") or {}
                    credits = data.get("credits", 0)
                    result["signMsg"] = f"签到成功，获得 {credits} 积分" if check_in_code == "000000" else check_in_msg
                    if check_in_code == "000000":
                        print(f"✅ [签到] 签到成功，获得 {credits} 积分")
                    else:
                        print(f"❌ [签到] 签到失败: {check_in_msg}")
            else:
                result["signMsg"] = "今日已签到"
                print("⚠️ [签到] 今日已签到")
        else:
            error_msg = todo_resp.get("msg", "未知错误") if todo_resp else "请求失败"
            print(f"⚠️ [签到] 获取待办任务失败: {error_msg}")

        # 查询积分明细
        if result["memberId"] != "-":
            month = datetime.now().strftime("%Y-%m")

            print(f"📋 [积分] 正在查询签到积分明细...")
            checkin_score_resp = api_get(account_name, SCORE_INDEX_URL, token, proxies, {
                "memberId": result["memberId"],
                "month": month,
                "pageSize": 10,
                "scoreChangeType": "OBTAIN",
                "scoreSourceType": "INTERACTION_GET"
            })

            checkin_score_records = []
            if checkin_score_resp and checkin_score_resp.get("ok") and checkin_score_resp.get("success"):
                checkin_score_data = checkin_score_resp.get("data", [])
                if checkin_score_data:
                    for s in checkin_score_data:
                        source_text = s.get("sourceOfScoreChange", "-")
                        business_desc = s.get("businessDesc", "-")
                        score_offset = s.get("scoreOffset", 0)
                        valid_time = s.get("scoreValidTime", "")

                        if valid_time:
                            try:
                                time_str = valid_time.replace("-", "/").split()[0].replace("/", "-")[5:] + " " + valid_time.split()[1][:5]
                            except:
                                time_str = valid_time
                        else:
                            time_str = "-"

                        if "签到" in source_text or "签到" in business_desc:
                            checkin_score_records.append(f"{time_str} {business_desc or source_text} {'+' if score_offset > 0 else ''}{score_offset}")

            if checkin_score_records:
                result["recentScores"] = "\n".join(checkin_score_records[:5])
                print(f"📋 [积分] 最近签到记录:")
                for record in checkin_score_records[:5]:
                    print(f"    {record}")
            else:
                score_resp = api_get(account_name, SCORE_INDEX_URL, token, proxies, {
                    "memberId": result["memberId"],
                    "month": month,
                    "pageSize": 5,
                    "scoreChangeType": "ALL",
                    "scoreSourceType": ""
                })
                if score_resp and score_resp.get("ok") and score_resp.get("success"):
                    score_data = score_resp.get("data", {})
                    score_list = score_data.get("list", [])[:3]
                    if score_list:
                        score_records = []
                        for s in score_list:
                            memo = s.get("memo", "-")
                            score = s.get("score", 0)
                            valid_time = s.get("validTime", "")
                            if valid_time:
                                try:
                                    time_str = datetime.fromtimestamp(valid_time / 1000).strftime("%m-%d %H:%M")
                                except:
                                    time_str = str(valid_time)
                            else:
                                time_str = "-"
                            score_records.append(f"{time_str} {memo} {'+' if score > 0 else ''}{score}")
                        result["recentScores"] = "\n".join(score_records)
                        print(f"📋 [积分] 最近积分记录:")
                        for record in score_records:
                            print(f"    {record}")
                    else:
                        print(f"📋 [积分] 暂无积分记录")

        result["success"] = True
        return result
    except Exception as exc:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行失败: {exc}")
        return result


def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    content = f"""⭐ 星妈会签到任务结果

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
🔑 登录方式：{res["loginMethod"]}
👤 会员：{res["memberName"]}
🆔 会员ID：{res["memberId"]}
💰 积分：{res["points"]}
🏆 等级：{res["gradeName"]}
📝 签到：{res["signMsg"]}
📋 本月签到：{res["checkInCount"]} 天
📋 最近积分记录：
{res["recentScores"]}
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

    # 兼容旧模式：没有配置wxid但有TOKEN时，单账号运行
    if not wxid_list and TOKEN:
        print("ℹ️ 未配置 soy_wxid_data，使用 TOKEN 环境变量单账号运行")
        wxid_list = [""]

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
                "wxid": mask(wx_id) if wx_id else "TOKEN兜底",
                "success": False,
                "proxyStatus": "-",
                "proxyIp": "-",
                "token": "-",
                "loginMethod": "-",
                "memberId": "-",
                "memberName": "-",
                "points": "-",
                "gradeName": "-",
                "signMsg": "-",
                "checkInCount": 0,
                "recentScores": "-",
                "error": traceback.format_exc().strip(),
            })

        if index < total:
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 星妈会签到任务执行完成                        ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    if NOTIFY:
        send_qinglong_notify("⭐ 星妈会签到任务完成", build_notify(results))


if __name__ == "__main__":
    main()