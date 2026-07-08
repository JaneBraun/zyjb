#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
杰士邦会员中心签到脚本（适配器版）
功能：
  1. 统一微信协议适配器获取code（支持YYB-Go等全协议）
  2. code登录换取 clientToken
  3. 旧签到接口优先，新接口兜底
  4. 免费抽奖活动
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
  pip install requests
  socks5代理需：pip install requests[socks]
------------------------------------------------------------
更新日志:
2026/07/07  V2.0    重构为统一适配器版，适配YYB-Go，仅保留青龙原生推送
"""
import json
import os
import random
import re
import time
import traceback
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

APP_NAME = "杰士邦会员中心小程序"
APPID = "wx5966681b4a895dee"

MULTI_ACCOUNT_SPLIT = ["\n", "@"]
NOTIFY = os.getenv("LY_NOTIFY", "").lower() == "true"

PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

BASE_URL = "https://api.vshop.hchiv.cn"
LOGIN_URL = f"{BASE_URL}/jfmb/cloud/member/wechatlogin/authLoginApplet"
CLIENT_INFO_URL = f"{BASE_URL}/jfmb/cloud/member/tblogin/getClientInfo"
CUSTOMER_PAGE_URL = f"{BASE_URL}/jfmb/cloud/common/common/get-customer-page"
ACTIVITY_LIST_URL = f"{BASE_URL}/jfmb/cloud/activity/activity/activityList"
OLD_SIGN_URL = f"{BASE_URL}/jfmb/api/play-default/sign/add-sign-new.do"
NEW_SIGN_URL = f"{BASE_URL}/jfmb/cloud/activity/sign/getSignPrize"
CURRENT_MONTH_SIGN_DAYS_URL = f"{BASE_URL}/jfmb/api/play-default/sign/current-month-signdays-new.do"
LOTTERY_FREE_TIMES_URL = f"{BASE_URL}/jfmb/cloud/activity/draw/child/receiveFreeTimes"
LOTTERY_DRAW_URL = f"{BASE_URL}/jfmb/cloud/activity/draw/startTurntable"

DEFAULT_NEW_SIGN_ACTIVITY_ID = "156947"
DEFAULT_SECURE_PLAT_ID = "1ac76025c66470fee5ad33313eb3e4e1608bffd4a60371ccabc6ed6e7928851e"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541938) XWEB/19823"
)
REFERER = f"https://servicewechat.com/{APPID}/112/page-frame.html"

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


def get_timestamp() -> int:
    return int(time.time() * 1000)


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


def to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def log_title() -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🛡️ 杰士邦会员中心签到脚本                     ║")
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


def parse_proxy_response(text: Any) -> Optional[Dict[str, Any]]:
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


def build_proxy_dict(proxy_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
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


def validate_proxy(proxies: Optional[Dict[str, str]]) -> Tuple[bool, str]:
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


def get_valid_proxy(account_name: str) -> Tuple[Optional[Dict[str, str]], str]:
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
    proxies: Optional[Dict[str, str]] = None,
    account_name: str = "",
    **kwargs,
) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    kwargs.setdefault("verify", False)
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


def common_headers(token: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Host": "api.vshop.hchiv.cn",
        "Connection": "keep-alive",
        "xweb_xhr": "1",
        "User-Agent": USER_AGENT,
        "appenv": "test",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Referer": REFERER,
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def hchiv_success(data: Dict[str, Any]) -> bool:
    if data.get("success") is True:
        return True
    inner = data.get("data")
    if isinstance(inner, dict):
        if inner.get("success") is True:
            return True
        if inner.get("code") == 200:
            return True
    return False


def hchiv_inner_data(data: Dict[str, Any]) -> Dict[str, Any]:
    inner = data.get("data")
    if isinstance(inner, dict):
        nested = inner.get("data")
        if isinstance(nested, dict):
            return nested
        return inner
    return {}


def api_post(
    account_name: str,
    url: str,
    token: Optional[str],
    proxies: Optional[Dict[str, str]],
    params: Dict[str, Any],
    payload: Dict[str, Any],
    cookies: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Any], requests.Response]:
    response = request_with_proxy(
        "POST",
        url,
        headers=common_headers(token),
        params=params,
        json=payload,
        cookies=cookies,
        proxies=proxies,
        account_name=account_name,
    )
    try:
        return response.json(), response
    except Exception:
        return {"raw": response.text[:800]}, response


def login_by_code(
    account_name: str,
    code: str,
    proxies: Optional[Dict[str, str]],
) -> Tuple[Optional[str], str, str, Dict[str, Any]]:
    print("🔐 [登录] 使用 code 换 clientToken")
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
        "wxInfo": code,
        "extend": json.dumps({
            "sourcePage": "/packageA/pages/integral-index/integral-index",
            "activityId": "",
            "sourceShopId": "",
            "guideNo": "",
            "way": "member",
            "linkType": "2001",
        }, ensure_ascii=False),
        "sessionIdForWxShop": "",
    }
    params = {
        "sideType": "3",
        "mob": "",
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
        "guideNo": "",
    }
    try:
        data, response = api_post(account_name, LOGIN_URL, None, proxies, params, payload)
        jsessionid = response.cookies.get("JSESSIONID", "")
        if hchiv_success(data):
            inner = hchiv_inner_data(data)
            token = inner.get("clientToken")
            if token:
                print(f"✅ [登录] clientToken 获取成功: {mask(token)}")
                return token, jsessionid, "", data
        if isinstance(data.get("data"), dict) and data["data"].get("code") == 1012:
            print("⚠️ [登录] code 已过期")
            return None, jsessionid, "CODE_EXPIRED", data
        print(f"❌ [登录] 登录失败: {json_preview(data)}")
        return None, jsessionid, "", data
    except Exception as exc:
        print(f"❌ [登录] 请求异常: {exc}")
        return None, "", "", {}


def get_client_info(
    account_name: str,
    token: str,
    proxies: Optional[Dict[str, str]],
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
    }
    params = {
        "sideType": "3",
        "mob": "",
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
    }
    try:
        data, _ = api_post(account_name, CLIENT_INFO_URL, token, proxies, params, payload)
        if hchiv_success(data):
            client_data = hchiv_inner_data(data)
            name = client_data.get("client_name") or "未知用户"
            integral = client_data.get("residualIntegral", 0)
            print(f"✅ [用户] {name} 当前积分: {integral}")
            return client_data, data
        inner = data.get("data")
        if isinstance(inner, dict) and inner.get("code") == 204:
            print("⚠️ [用户] 账号未授权，请手动登录小程序授权手机号")
            return None, data
        print(f"⚠️ [用户] 获取失败: {json_preview(data)}")
        return None, data
    except Exception as exc:
        print(f"⚠️ [用户] 获取异常: {exc}")
        return None, {}


def get_signin_activity_id(account_name: str, token: str, mob: str, proxies: Optional[Dict[str, str]]) -> Tuple[Optional[str], str]:
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
        "pageId": 102999,
        "pageType": 2,
    }
    params = {
        "sideType": "3",
        "mob": mob,
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
    }
    try:
        data, _ = api_post(account_name, CUSTOMER_PAGE_URL, token, proxies, params, payload)
        if data.get("success"):
            page_json_str = data.get("data", {}).get("result", {}).get("pageJson", "")
            if page_json_str:
                try:
                    page_json = json.loads(page_json_str)
                    for module in page_json.get("moduleList", []):
                        if module.get("type") == "iconNav":
                            link_list = module.get("detail", {}).get("linkList", [])
                            for link in link_list:
                                if link.get("text") == "签到":
                                    activity_id = str(link.get("id"))
                                    print(f"✅ [活动] 页面签到活动ID: {activity_id}")
                                    return activity_id, "页面签到"
                except Exception as exc:
                    print(f"⚠️ [活动] 解析页面签到ID失败: {exc}")
        print("⚠️ [活动] 页面签到活动ID未找到")
        return None, ""
    except Exception as exc:
        print(f"⚠️ [活动] 获取页面签到ID异常: {exc}")
        return None, ""


def get_activity_list_sign_id(account_name: str, token: str, mob: str, proxies: Optional[Dict[str, str]]) -> Tuple[Optional[str], str]:
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
        "pageNumber": 1,
        "pageSize": 20,
        "decoActStatus": ["1"],
    }
    params = {
        "sideType": "3",
        "mob": mob,
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
    }
    try:
        data, _ = api_post(account_name, ACTIVITY_LIST_URL, token, proxies, params, payload)
        if hchiv_success(data):
            inner = hchiv_inner_data(data)
            data_list = inner.get("dataList", [])
            if isinstance(data_list, list):
                for activity in data_list:
                    name = str(activity.get("name", ""))
                    if "签到" in name:
                        activity_id = str(activity.get("id"))
                        print(f"✅ [活动] 活动列表签到: {name} ID={activity_id}")
                        return activity_id, name
        print("⚠️ [活动] 活动列表未找到签到活动")
        return None, ""
    except Exception as exc:
        print(f"⚠️ [活动] 获取活动列表异常: {exc}")
        return None, ""


def old_daily_sign(
    account_name: str,
    token: str,
    mob: str,
    activity_id: str,
    proxies: Optional[Dict[str, str]],
) -> Tuple[bool, str, int, Dict[str, Any]]:
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
        "activityId": activity_id,
    }
    params = {
        "sideType": "3",
        "mob": mob,
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
    }
    try:
        data, _ = api_post(account_name, OLD_SIGN_URL, token, proxies, params, payload)
        if data.get("success"):
            sign_data = data.get("data") or {}
            integral = to_int(sign_data.get("integral", 0))
            alias = sign_data.get("integralAlias") or "积分"
            message = sign_data.get("message") or ""
            if "已签到" in message:
                return True, "旧接口：今日已签到", 0, data
            if integral > 0:
                return True, f"旧接口：签到成功 +{integral}{alias}", integral, data
            return True, "旧接口：签到成功", 0, data
        message = data.get("message") or data.get("errorMessage") or data.get("msg") or "签到失败"
        if "已签到" in str(message) or "重复" in str(message):
            return True, "旧接口：今日已签到", 0, data
        return False, f"旧接口失败：{message}", 0, data
    except Exception as exc:
        return False, f"旧接口异常：{exc}", 0, {}


def new_sign_prize(
    account_name: str,
    token: str,
    mob: str,
    activity_id: str,
    secure_plat_id: str,
    jsessionid: str,
    proxies: Optional[Dict[str, str]],
) -> Tuple[bool, str, int, Dict[str, Any]]:
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
        "activityId": str(activity_id),
    }
    params = {
        "sideType": "3",
        "mob": mob,
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
        "guideNo": "",
        "securePlatId": secure_plat_id,
    }
    cookies = {}
    if jsessionid:
        cookies["JSESSIONID"] = jsessionid
    try:
        data, _ = api_post(account_name, NEW_SIGN_URL, token, proxies, params, payload, cookies=cookies)
        if data.get("success"):
            response_list = data.get("data", {}).get("responseList", [])
            points = 0
            prize_name = ""
            if isinstance(response_list, list) and response_list:
                prize = response_list[0] or {}
                prize_name = prize.get("prizeName") or "签到奖励"
                points = to_int(prize.get("points") or prize.get("integral") or prize.get("score") or 0)
            if points > 0:
                return True, f"新接口：签到成功 {prize_name} +{points}积分", points, data
            if prize_name:
                return True, f"新接口：签到成功 {prize_name}", 0, data
            return True, "新接口：签到成功", 0, data
        message = data.get("message") or data.get("errorMessage") or data.get("msg") or "签到失败"
        if "已签到" in str(message) or "重复" in str(message):
            return True, "新接口：今日已签到", 0, data
        return False, f"新接口失败：{message}", 0, data
    except Exception as exc:
        return False, f"新接口异常：{exc}", 0, {}


def get_continuous_sign_days(account_name: str, token: str, mob: str, activity_id: str, proxies: Optional[Dict[str, str]]) -> Optional[int]:
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
        "activityId": activity_id,
        "time": datetime.now().strftime("%Y-%m"),
    }
    params = {
        "sideType": "3",
        "mob": mob,
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
    }
    try:
        data, _ = api_post(account_name, CURRENT_MONTH_SIGN_DAYS_URL, token, proxies, params, payload)
        if data.get("success"):
            return to_int(data.get("data", {}).get("continuousSignDay", 0))
    except Exception:
        pass
    return None


def get_lottery_activity_id(account_name: str, token: str, mob: str, proxies: Optional[Dict[str, str]]) -> Tuple[Optional[str], str]:
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
        "pageId": 111079,
        "pageType": 2,
    }
    params = {
        "sideType": "3",
        "mob": mob,
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
    }
    try:
        data, _ = api_post(account_name, CUSTOMER_PAGE_URL, token, proxies, params, payload)
        if data.get("success"):
            page_json_str = data.get("data", {}).get("result", {}).get("pageJson", "")
            if page_json_str:
                page_json_str = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", page_json_str)
                page_json = json.loads(page_json_str)
                for module in page_json.get("moduleList", []):
                    if module.get("type") == "imgAd":
                        link_list = module.get("detail", {}).get("linkList", [])
                        for link in link_list:
                            for zone in link.get("zones", []):
                                link_url = zone.get("linkUrl", "")
                                if "抽奖" in link_url:
                                    return str(zone.get("id")), link_url
    except Exception as exc:
        print(f"⚠️ [抽奖] 获取抽奖活动ID失败: {exc}")
    return None, ""


def get_lottery_times(account_name: str, token: str, mob: str, activity_id: str, proxies: Optional[Dict[str, str]]) -> int:
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
        "activityId": activity_id,
    }
    params = {
        "sideType": "3",
        "mob": mob,
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
    }
    try:
        data, _ = api_post(account_name, LOTTERY_FREE_TIMES_URL, token, proxies, params, payload)
        if data.get("success") and data.get("data", {}).get("success"):
            return to_int(data["data"]["data"].get("totalTimes", 0))
    except Exception:
        pass
    return 0


def lottery(account_name: str, token: str, mob: str, activity_id: str, proxies: Optional[Dict[str, str]]) -> str:
    ts = get_timestamp()
    payload = {
        "appId": APPID,
        "openId": True,
        "shopNick": "",
        "timestamp": ts,
        "interfaceSource": 0,
        "activityId": activity_id,
    }
    params = {
        "sideType": "3",
        "mob": mob,
        "appId": APPID,
        "shopNick": APPID,
        "timestamp": ts,
    }
    try:
        data, _ = api_post(account_name, LOTTERY_DRAW_URL, token, proxies, params, payload)
        if data.get("success") and data.get("data", {}).get("success"):
            prize_result = data["data"]["data"].get("prizeResult", {})
            prize_name = prize_result.get("prizeName", "未知奖品")
            return f"抽奖成功，获得{prize_name}"
        message = data.get("errorMessage") or data.get("message") or data.get("msg") or "抽奖失败"
        return f"抽奖失败：{message}"
    except Exception as exc:
        return f"抽奖异常：{exc}"


def run_sign_flow(
    account_name: str,
    token: str,
    mob: str,
    secure_plat_id: str,
    jsessionid: str,
    proxies: Optional[Dict[str, str]],
) -> Tuple[str, int, str]:
    messages = []
    total_integral = 0
    page_activity_id, page_activity_name = get_signin_activity_id(account_name, token, mob, proxies)
    list_activity_id, list_activity_name = get_activity_list_sign_id(account_name, token, mob, proxies)

    old_candidates = []
    for activity_id, name in [
        (page_activity_id, page_activity_name),
        (list_activity_id, list_activity_name),
    ]:
        if activity_id and activity_id not in [item[0] for item in old_candidates]:
            old_candidates.append((activity_id, name or "签到活动"))

    old_success = False
    for activity_id, name in old_candidates:
        ok, msg, integral, _ = old_daily_sign(account_name, token, mob, activity_id, proxies)
        messages.append(f"{name}({activity_id}) {msg}")
        print(f"{'✅' if ok else '⚠️'} [签到] {name}({activity_id}) {msg}")
        if ok:
            old_success = True
            total_integral += integral
            days = get_continuous_sign_days(account_name, token, mob, activity_id, proxies)
            if days is not None:
                messages.append(f"连续签到{days}天")
            break

    new_candidates = []
    for activity_id in [list_activity_id, page_activity_id, DEFAULT_NEW_SIGN_ACTIVITY_ID]:
        if activity_id and activity_id not in new_candidates:
            new_candidates.append(activity_id)

    new_success = False
    if not old_success:
        for activity_id in new_candidates:
            ok, msg, integral, _ = new_sign_prize(
                account_name,
                token,
                mob,
                activity_id,
                secure_plat_id,
                jsessionid,
                proxies,
            )
            messages.append(f"新签到({activity_id}) {msg}")
            print(f"{'✅' if ok else '⚠️'} [签到] 新签到({activity_id}) {msg}")
            if ok:
                new_success = True
                total_integral += integral
                break

    final_ok = old_success or new_success
    if not final_ok and not messages:
        messages.append("未找到可用签到活动")
    return "；".join(messages), total_integral, "成功" if final_ok else "失败"


def run_lottery_flow(
    account_name: str,
    token: str,
    mob: str,
    proxies: Optional[Dict[str, str]],
) -> str:
    lottery_id, lottery_name = get_lottery_activity_id(account_name, token, mob, proxies)
    if not lottery_id:
        return "未找到抽奖活动"
    times = get_lottery_times(account_name, token, mob, lottery_id, proxies)
    print(f"🎰 [抽奖] {lottery_name} 可抽奖 {times} 次")
    if times <= 0:
        return f"{lottery_name} 无免费抽奖次数"
    results = []
    for index in range(1, times + 1):
        wait_time = random.randint(2, 5)
        print(f"⏳ [抽奖] 第 {index} 次抽奖前等待 {wait_time}s")
        sleep(wait_time)
        result = lottery(account_name, token, mob, lottery_id, proxies)
        results.append(f"第{index}次{result}")
        print(f"🎰 [抽奖] {result}")
    return "；".join(results)


def run_account(index: int, total: int, wx_id: str, adapter: WechatCodeAdapter) -> Dict[str, Any]:
    account_name = mask(wx_id)
    result = {
        "wxid": account_name,
        "success": False,
        "proxyStatus": "未使用代理",
        "proxyIp": "-",
        "token": "-",
        "nickname": "-",
        "beforeIntegral": "0",
        "afterIntegral": "0",
        "earnedIntegral": "0",
        "signMsg": "-",
        "lotteryMsg": "-",
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

    token, jsessionid, login_status, raw_login = login_by_code(account_name, code, proxies)
    if not token:
        result["error"] = f"登录失败: {login_status or json_preview(raw_login)}"
        return result
    result["token"] = mask(token)

    try:
        client_info, raw_info = get_client_info(account_name, token, proxies)
        if not client_info:
            result["error"] = f"获取账号信息失败: {json_preview(raw_info)}"
            return result

        nickname = client_info.get("client_name") or "未知用户"
        mob = client_info.get("user_mob") or ""
        secure_plat_id = client_info.get("securePlatId") or DEFAULT_SECURE_PLAT_ID
        before_integral = to_int(client_info.get("residualIntegral", 0))

        result["nickname"] = nickname
        result["beforeIntegral"] = str(before_integral)

        if not mob:
            result["error"] = "未获取到手机号 user_mob"
            return result

        print(f"👤 [用户] {nickname}")
        print(f"💰 [积分] 签到前积分: {before_integral}")

        sign_msg, api_integral, sign_status = run_sign_flow(
            account_name,
            token,
            mob,
            secure_plat_id,
            jsessionid,
            proxies,
        )
        result["signMsg"] = sign_msg

        sleep(random.randint(1, 3))
        lottery_msg = run_lottery_flow(account_name, token, mob, proxies)
        result["lotteryMsg"] = lottery_msg

        sleep(random.randint(1, 2))
        final_info, _ = get_client_info(account_name, token, proxies)
        if final_info:
            after_integral = to_int(final_info.get("residualIntegral", 0))
        else:
            after_integral = before_integral

        earned_integral = after_integral - before_integral
        if earned_integral == 0 and api_integral > 0:
            earned_integral = api_integral

        result["afterIntegral"] = str(after_integral)
        result["earnedIntegral"] = str(earned_integral)

        print(f"💰 [积分] 签到后积分: {after_integral}")
        print(f"💰 [积分] 本次变化: {earned_integral}")

        result["success"] = sign_status == "成功"
        if not result["success"]:
            result["error"] = "签到流程未成功"
        return result

    except Exception as exc:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行失败: {exc}")
        return result


def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count
    total_earned = sum(to_int(item.get("earnedIntegral", 0)) for item in results if item.get("success"))

    content = f"""🛡️ 杰士邦会员中心签到任务结果
━━━━━━━━━━━━━━━━━━━━
🏁 总结：{success_count} 成功 / {fail_count} 失败
💰 总获得积分：{total_earned}
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
👤 昵称：{res["nickname"]}
📝 签到：{res["signMsg"]}
🎰 抽奖：{res["lotteryMsg"]}
💰 签到前：{res["beforeIntegral"]} 积分
💰 签到后：{res["afterIntegral"]} 积分
💰 获得：{res["earnedIntegral"]} 积分
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
                "nickname": "-",
                "beforeIntegral": "0",
                "afterIntegral": "0",
                "earnedIntegral": "0",
                "signMsg": "-",
                "lotteryMsg": "-",
                "error": traceback.format_exc().strip(),
            })

        if index < total:
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 杰士邦会员中心任务执行完成                ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    if NOTIFY:
        send_qinglong_notify("🛡️ 杰士邦会员中心签到任务完成", build_notify(results))


if __name__ == "__main__":
    main()