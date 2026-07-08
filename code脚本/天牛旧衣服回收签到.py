#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天牛旧衣服回收签到脚本（适配器版）
功能：
  1. 统一微信协议适配器获取code（支持YYB-Go等全协议）
  2. code 登录换取 token，支持本地缓存自动续期
  3. 每日签到领取环保币
  4. 青龙面板原生推送通知
环境变量：
  tnjy                账号标识（对应YYB-Go的ref/id），多账号用 & 或换行分隔
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
import re
import json
import time
import traceback
import sys
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode

import requests

APP_NAME = "天牛旧衣服回收"
APPID = "wx887c2f947bffa76e"
PAGE_VERSION = "6"
API_BASE = "https://tianniunew.fzjingzhou.com"
GUEST_TOKEN = "wek2020123456788wek"

MULTI_ACCOUNT_SPLIT = ["\n", "&"]
NOTIFY = os.getenv("LY_NOTIFY", "").lower() == "true"
REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) MicroMessenger/3.9.12 MiniProgramEnv/Windows "
    "WindowsWechat/WMPF"
)

TOKEN_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tnjy_token_cache.json")

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


def log_title() -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ ♻️ 天牛旧衣服回收签到                         ║")
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
        tnjy = os.getenv("tnjy", "")
        if not tnjy:
            print("❌ [环境] 未配置 tnjy 环境变量")
            return []
        split_char = None
        for sep in MULTI_ACCOUNT_SPLIT:
            if sep in tnjy:
                split_char = sep
                break
        if not split_char:
            raw_list = [tnjy]
        else:
            raw_list = tnjy.split(split_char)

        account_list = []
        for item in raw_list:
            item = item.strip()
            if item:
                account_list.append(item)
        return account_list
    except Exception as e:
        print(f"❌ [环境] 读取账号列表失败: {e}")
        return []


# ===================== Token 缓存 =====================
def read_token_cache() -> Dict[str, Any]:
    try:
        if not os.path.exists(TOKEN_CACHE_FILE):
            return {}
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def write_token_cache(cache: Dict[str, Any]) -> None:
    try:
        with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ [缓存] 写入token缓存失败: {e}")


def is_token_error(message: str) -> bool:
    pattern = r"token|登录|验证失败|9999|401|403|expire|过期|失效"
    return bool(re.search(pattern, str(message or ""), re.IGNORECASE))


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
def request(api_path: str, data: Dict[str, Any] = None, token: str = None, noauth: bool = False) -> Dict[str, Any]:
    use_token = GUEST_TOKEN if noauth or not token else token
    payload = dict(data or {})
    payload["token"] = use_token

    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "platform": "MP-WEIXIN",
        "User-Agent": USER_AGENT,
        "Referer": f"https://servicewechat.com/{APPID}/{PAGE_VERSION}/page-frame.html",
    }

    response = requests.post(
        f"{API_BASE}{api_path}",
        data=urlencode(payload),
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    res_data = response.json()

    if int(res_data.get("code", 0)) != 1000:
        msg = res_data.get("msg") or f"接口错误: {res_data.get('code', 'unknown')}"
        raise Exception(msg)

    return res_data


def login_by_code(code: str) -> Dict[str, Any]:
    print("🔐 [登录] 使用code换取token")
    res = request(
        "/api/login/getWxMiniProgramSessionKey",
        data={"code": code, "gdtVid": ""},
        noauth=True,
    )
    data = res.get("data") or {}
    session = {
        "token": data.get("token") or res.get("token") or "",
        "userInfo": data.get("personInfo") or {},
        "newOrder": data.get("newOrder"),
    }
    if not session["token"]:
        raise Exception("登录接口未返回token")
    print("✅ [登录] 登录成功")
    return session


def check_token(token: str) -> Tuple[bool, Dict[str, Any]]:
    try:
        res = request("/api/Person/index", token=token)
        user_info = res.get("data") or {}
        return True, user_info
    except Exception:
        return False, {}


def do_sign(token: str) -> Tuple[bool, str]:
    try:
        res = request("/api/Person/sign", token=token)
        beans = res.get("data")
        if beans is not None:
            msg = f"签到成功，获得{beans}环保币"
        else:
            msg = "签到成功"
        return True, msg
    except Exception as e:
        msg = str(e)
        if "已签到" in msg or "今日已" in msg or "重复" in msg or "已经签到" in msg:
            return True, "今日已签到"
        return False, msg


# ===================== 单账号执行 =====================
def run_account(index: int, total: int, wx_id: str, adapter: WechatCodeAdapter) -> Dict[str, Any]:
    account_name = mask(wx_id)
    result = {
        "account": account_name,
        "success": False,
        "token": "-",
        "signMsg": "-",
        "beans": "-",
        "error": "",
    }

    log_account_header(index, total, account_name)
    session = {}
    cache = read_token_cache()

    # 尝试使用缓存token
    if wx_id in cache:
        cached = cache[wx_id]
        session = cached
        print(f"ℹ️ [缓存] 使用缓存token")
        token_valid, user_info = check_token(session.get("token", ""))
        if token_valid:
            session["userInfo"] = user_info
            print("✅ [缓存] token有效")
        else:
            print("⚠️ [缓存] token失效，重新登录")
            del cache[wx_id]
            write_token_cache(cache)
            session = {}

    # 无有效token则重新登录
    if not session.get("token"):
        try:
            code = adapter.get_code(wx_id)
            if not code:
                result["error"] = "获取code失败"
                return result
            session = login_by_code(code)
        except Exception as e:
            result["error"] = f"登录失败: {e}"
            print(f"❌ [登录] {result['error']}")
            return result

    result["token"] = mask(session["token"])

    try:
        # 执行签到
        sign_ok, sign_msg = do_sign(session["token"])
        result["signMsg"] = sign_msg

        if sign_ok:
            print(f"✅ [签到] {sign_msg}")
            result["success"] = True
        else:
            print(f"❌ [签到] {sign_msg}")
            if is_token_error(sign_msg):
                cache = read_token_cache()
                if wx_id in cache:
                    del cache[wx_id]
                    write_token_cache(cache)
                    print("⚠️ [缓存] 已清除失效token缓存")
            return result

        # 更新缓存
        cache = read_token_cache()
        cache[wx_id] = {
            "token": session["token"],
            "userInfo": session.get("userInfo", {}),
            "newOrder": session.get("newOrder"),
            "updatedAt": datetime.now().isoformat(),
        }
        write_token_cache(cache)

        return result

    except Exception as e:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行异常: {e}")
        return result


# ===================== 通知汇总 =====================
def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    content = f"""♻️ 天牛旧衣服回收签到结果
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
                "token": "-",
                "signMsg": "-",
                "beans": "-",
                "error": traceback.format_exc().strip(),
            })

        if index < total:
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            time.sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 天牛旧衣服回收任务执行完成                  ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    if NOTIFY:
        send_qinglong_notify("♻️ 天牛旧衣服回收签到完成", build_notify(results))


if __name__ == "__main__":
    main()