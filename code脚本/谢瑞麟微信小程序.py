#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
谢瑞麟微信小程序签到脚本（适配器版）
功能：
  1. 统一微信协议适配器获取code（支持YYB-Go等全协议）
  2. code 登录换取 token + openid
  3. 查询用户信息与签到状态，未签到自动执行签到
  4. 青龙面板原生推送通知
环境变量：
  tslj                账号标识（对应YYB-Go的ref/id），多账号用 # 或换行分隔
  soy_codeurl_data    微信授权服务地址（YYB-Go填完整地址）
  soy_codetoken_data  微信授权token（YYB-Go留空）
  LY_NOTIFY           填true开启青龙推送
依赖：
  pip install requests
------------------------------------------------------------
更新日志:
2026/07/07  V2.0    Python重构，适配统一适配器，新增青龙原生推送
"""

import os
import json
import time
import random
import traceback
import sys
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode

import requests

APP_NAME = "谢瑞麟小程序签到"
APPID = "wx439d0e0cc6742818"
API_BASE = "https://tslmember-crm.tslj.com.cn"
LOGIN_URL = f"{API_BASE}/api/auth/login"
USER_INFO_URL = f"{API_BASE}/api/user/index"
SIGN_URL = f"{API_BASE}/api/userSignIn/signIn"

MULTI_ACCOUNT_SPLIT = ["\n", "#"]
NOTIFY = os.getenv("LY_NOTIFY", "").lower() == "true"
REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf254173b) XWEB/19027"
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


def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"


def mask_phone(phone: str) -> str:
    phone = str(phone or "")
    if len(phone) >= 11:
        return f"{phone[:3]}****{phone[7:]}"
    return phone or "未知"


def log_title() -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 💎 谢瑞麟小程序签到                           ║")
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
        tslj = os.getenv("tslj", "")
        if not tslj:
            print("❌ [环境] 未配置 tslj 环境变量")
            return []
        split_char = None
        for sep in MULTI_ACCOUNT_SPLIT:
            if sep in tslj:
                split_char = sep
                break
        if not split_char:
            raw_list = [tslj]
        else:
            raw_list = tslj.split(split_char)

        account_list = []
        for item in raw_list:
            item = item.strip()
            if item:
                account_list.append(item)
        return account_list
    except Exception as e:
        print(f"❌ [环境] 读取账号列表失败: {e}")
        return []


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


# ===================== 接口请求封装 =====================
def login_by_code(code: str) -> Tuple[str, str]:
    """使用code登录，返回(token, openid)"""
    print("🔐 [登录] 使用code换取token")
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "content-type": "application/json",
        "user-agent": USER_AGENT,
    }
    payload = {"code": code}
    response = requests.post(
        LOGIN_URL,
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()

    if str(result.get("code")) == "0":
        user_info = result.get("data", {}).get("user_info", {})
        token = user_info.get("token", "")
        openid = user_info.get("openid", "")
        if token and openid:
            print(f"✅ [登录] 获取token成功")
            return token, openid
    raise Exception(result.get("msg", "登录失败"))


def get_user_info(token: str, openid: str) -> Dict[str, Any]:
    """获取用户信息、积分、签到状态"""
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "authorization": f"Bearer {token}",
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": USER_AGENT,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
    }
    payload = {"openid": openid}
    response = requests.post(
        USER_INFO_URL,
        data=urlencode(payload),
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()

    if str(result.get("code")) == "0":
        data = result.get("data", {})
        is_sign = False
        task_list = data.get("task_list", [])
        for task in task_list:
            if task.get("name") == "每日签到":
                is_sign = bool(task.get("status", False))
                break
        return {
            "mobile": data.get("mobile", "-"),
            "integral": data.get("integral", 0),
            "is_sign": is_sign,
        }
    raise Exception(result.get("msg", "获取用户信息失败"))


def do_sign(token: str, openid: str) -> Tuple[int, int]:
    """执行签到，返回(获得积分, 连续签到天数)"""
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "authorization": f"Bearer {token}",
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": USER_AGENT,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
    }
    payload = {"openid": openid}
    response = requests.post(
        SIGN_URL,
        data=urlencode(payload),
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()

    if str(result.get("code")) == "0":
        data = result.get("data", {})
        integral = int(data.get("integral", 0))
        total_days = int(data.get("total_days", 0))
        return integral, total_days
    raise Exception(result.get("msg", "签到失败"))


# ===================== 单账号执行 =====================
def run_account(index: int, total: int, wx_id: str, adapter: WechatCodeAdapter) -> Dict[str, Any]:
    account_name = mask(wx_id)
    result = {
        "account": account_name,
        "success": False,
        "mobile": "-",
        "integral": "-",
        "signMsg": "-",
        "continuousDays": 0,
        "error": "",
    }

    log_account_header(index, total, account_name)

    # 随机延迟5-30秒模拟人工操作
    delay = random.randint(5, 30)
    print(f"⏳ [延迟] 随机等待 {delay}s")
    time.sleep(delay)

    try:
        # 获取code
        code = adapter.get_code(wx_id)
        if not code:
            result["error"] = "获取code失败"
            return result

        # 登录
        token, openid = login_by_code(code)
    except Exception as e:
        result["error"] = f"登录失败: {e}"
        print(f"❌ [登录] {result['error']}")
        return result

    try:
        # 获取用户信息
        user_info = get_user_info(token, openid)
        result["mobile"] = mask_phone(user_info["mobile"])
        result["integral"] = str(user_info["integral"])
        print(f"👤 [用户] 手机号: {mask_phone(user_info['mobile'])}")
        print(f"💰 [积分] 当前积分: {user_info['integral']}")

        if user_info["is_sign"]:
            result["signMsg"] = "今日已签到"
            print("✅ [签到] 今日已签到")
        else:
            print("📝 [签到] 今日未签到，开始签到...")
            earned, days = do_sign(token, openid)
            result["signMsg"] = f"签到成功，获得{earned}积分，连续{days}天"
            result["continuousDays"] = days
            print(f"✅ [签到] 签到成功，获得{earned}积分，连续签到{days}天")

        result["success"] = True
        return result

    except Exception as e:
        result["error"] = str(e)
        print(f"❌ [执行] 失败: {e}")
        return result


# ===================== 通知汇总 =====================
def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    content = f"""💎 谢瑞麟小程序签到结果
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
📱 手机号：{res["mobile"]}
💰 当前积分：{res["integral"]}
📝 签到：{res["signMsg"]}
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
                "mobile": "-",
                "integral": "-",
                "signMsg": "-",
                "continuousDays": 0,
                "error": traceback.format_exc().strip(),
            })

        if index < total:
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            time.sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 谢瑞麟签到任务执行完成                      ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    if NOTIFY:
        send_qinglong_notify("💎 谢瑞麟小程序签到完成", build_notify(results))


if __name__ == "__main__":
    main()