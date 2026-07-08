"""
作者: 临渊
日期: 2025/7/22
name: 微信协议适配器
变量: soy_wxid_data (微信id) 多个账号用换行分割 
        soy_codetoken_data (微信授权token)
        soy_codeurl_data (微信授权url)
定时: 一天两次
cron: 10 11,12 * * *
------------------------------------------------------------
更新日志:
2025/7/22   V1.0    初始化
2025/7/27   V1.1    适配StarBot Pro
2026/07/07  V1.2    新增 YYB-Go 协议支持（type=6），完全向下兼容
"""
import requests
import os
import logging
import traceback
from datetime import datetime

class WechatCodeAdapter:
    def __init__(self, wx_appid):
        """
        初始化微信授权适配器
        """
        self.wx_code_url = os.getenv("soy_codeurl_data")
        self.wx_code_token = os.getenv("soy_codetoken_data")
        self.wx_appid = wx_appid  # 微信小程序id
        self.wx_protocol_type = 0  # 微信协议类型
        self.wx_accounts_list = []  # 微信账号列表
        self.log_msgs = []  # 日志收集
        self._init_protocol_type()
        self._init_all_accounts()
        self.setup_logging()

    def setup_logging(self):
        """
        配置日志系统
        """
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s\t- %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                logging.StreamHandler()
            ]
        )

    def log(self, msg, level="info"):
        if level == "info":
            logging.info(msg)
        elif level == "error":
            logging.error(msg)
        elif level == "warning":
            logging.warning(msg)
        self.log_msgs.append(msg)

    def get_protocol_type(self):
        """
        获取协议类型
        :return: 协议类型
        """
        if self.wx_code_url:
            end_url = self.wx_code_url.split("/")[-1]
            full_url = self.wx_code_url
        else:
            end_url = ""
            full_url = ""

        if end_url == "getMiniProgramCode":
            # 养鸡场
            return 1
        elif end_url == "code":
            # 牛子
            return 2
        elif end_url == "GetAllDevices":
            # WeChatPadPro
            return 3
        elif end_url == "GetAuthKey":
            # iwechat
            return 4
        elif end_url == "processor":
            # StarBot Pro
            return 5
        elif "/wxapp/getCode" in full_url:
            # YYB-Go 协议
            return 6
        else:
            # 其他不知道的协议
            return 0

    def _init_protocol_type(self):
        """
        初始化微信协议类型
        """
        self.wx_protocol_type = self.get_protocol_type()
        
    def dict_keys_to_lower(self, obj):
        """
        递归将字典的所有键名转为小写
        """
        if isinstance(obj, dict):
            return {k.lower(): self.dict_keys_to_lower(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.dict_keys_to_lower(i) for i in obj]
        else:
            return obj

    # ==================== 新增：YYB-Go 账号列表拉取 ====================
    def _fix_url_schema(self, url):
        """补全 URL 协议头，防止 No connection adapters 报错"""
        if not url:
            return url
        if not url.startswith(("http://", "https://")):
            return f"http://{url}"
        return url

    def get_yybgo_accounts(self):
        """
        YYB-Go 获取账号列表 /accounts
        :return: 账号列表数组
        """
        try:
            base_url = self.wx_code_url.replace("/wxapp/getCode", "")
            base_url = self._fix_url_schema(base_url)
            accounts_url = f"{base_url}/accounts"

            headers = {"Content-Type": "application/json"}
            response = requests.get(accounts_url, headers=headers, timeout=10)
            response.raise_for_status()
            res_json = response.json()

            if res_json.get("code") == 0 and isinstance(res_json.get("data"), list):
                return res_json["data"]
            else:
                self.log(f"[YYB-Go] 获取账号列表失败: {res_json.get('msg', '未知错误')}", level="error")
                return []
        except requests.RequestException as e:
            self.log(f"[YYB-Go] 获取账号列表网络错误: {str(e)}", level="error")
            return []
        except Exception as e:
            self.log(f"[YYB-Go] 获取账号列表异常: {str(e)}\n{traceback.format_exc()}", level="error")
            return []

    def _match_yybgo_ref(self, wx_id):
        """
        从账号列表中匹配对应 ref（id/uin/openid）
        :param wx_id: 传入的微信id
        :return: 匹配到的 ref，匹配失败返回 None
        """
        if not self.wx_accounts_list:
            return None
        target = str(wx_id)
        for acc in self.wx_accounts_list:
            acc_id = str(acc.get("id", ""))
            acc_uin = str(acc.get("uin", ""))
            acc_openid = str(acc.get("openid", ""))
            if target in (acc_id, acc_uin, acc_openid):
                return acc_id
        return None
        
    def get_code_1(self, wx_id):
        """
        养鸡场 获取code
        :param wx_id: 微信id
        :return: code
        """
        try:
            url = self.wx_code_url
            headers = {
                "Authorization": self.wx_code_token,
                "Content-Type": "application/json"
            }
            payload = {"wxid": wx_id, "appid": self.wx_appid}
            response = requests.post(url, headers=headers, json=payload, timeout=5)
            response.raise_for_status()
            # 将所有键名转为小写
            response_json = self.dict_keys_to_lower(response.json())
            if response_json['code'] == 200:
                return response_json['data']['code']
            else:
                self.log(f"[微信授权] 失败，错误信息: {response_json.get('msg', '未知错误')}", level="error")
                return False
        except requests.RequestException as e:
            self.log(f"[微信授权]发生网络错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return False
        except Exception as e:
            self.log(f"[微信授权]发生未知错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return False
    
    def get_code_2(self, wx_id):
        """
        牛子 获取code
        :param wx_id: 微信id
        :return: code
        """
        try:
            url = self.wx_code_url
            headers = {
                "Content-Type": "application/json"
            }
            payload = {"wxid": wx_id, "appid": self.wx_appid}
            response = requests.post(url, headers=headers, json=payload, timeout=5)
            response.raise_for_status()
            # 将所有键名转为小写
            response_json = self.dict_keys_to_lower(response.json())
            # 直接取授权code，不判断返回码code
            code_value = response_json.get('data', {}).get('code', '')
            if code_value:
                code = code_value
                return code
            else:
                self.log(f"[微信授权] 失败，错误信息: {response_json['message']}", level="error")
                return False
        except requests.RequestException as e:
            self.log(f"[微信授权]发生网络错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return False
        except Exception as e:
            self.log(f"[微信授权]发生未知错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return False

    def _init_all_accounts(self):
        """
        初始化微信账号列表
        """
        if self.wx_protocol_type == 3:
            self.wx_accounts_list = self.get_all_devices()
        elif self.wx_protocol_type == 4:
            self.wx_accounts_list = self.get_auth_keys()
        elif self.wx_protocol_type == 6:
            # YYB-Go 预加载账号列表
            self.wx_accounts_list = self.get_yybgo_accounts()
        
    def get_all_devices(self):
        """
        WeChatPadPro 获取账号授权码列表
        :return: 账号授权码列表
        """
        try:
            url = self.wx_code_url
            params = {
                "key": self.wx_code_token
            }
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            response_json = response.json()
            if response_json.get('Code') == 200:
                all_devices = response_json.get('Data', {}).get('devices', [])
                if all_devices:
                    return all_devices
                else:
                    self.log(f"[获取账号授权码列表] 返回信息: {response_json['Text']}", level="error")
                    return []
            else:
                self.log(f"[获取账号授权码列表] 失败，错误信息: {response_json['Text']}", level="error")
                return []
        except Exception as e:
            self.log(f"[获取账号授权码列表] 发生错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return []
        
    def get_target_key_by_wxid(self, all_keys, wx_id):
        """
        获取指定微信id的授权码
        :param all_keys: 所有账号授权码列表
        :param wx_id: 微信id
        :return: 指定微信id的授权码
        """
        for key in all_keys:
            _wx_id = key.get('deviceId') or key.get('wx_id')
            if _wx_id == wx_id:
                return key.get('authKey') or key.get('license')
        return None
    
    def get_code_3(self, wx_id):
        """
        WeChatPadPro 获取code
        :param wx_id: 微信id
        :return: code
        """
        try:
            all_devices = self.wx_accounts_list
            if not all_devices:
                self.log(f"[获取code] 账号列表为空，未能获取到", level="error")
                return False
            target_key = self.get_target_key_by_wxid(all_devices, wx_id)
            url = self.wx_code_url.split("/admin")[0] + "/applet/JsLogin"
            params = {
                "key": target_key
            }
            payload = {
                "AppId": self.wx_appid,
                "Data": "",
                "Opt": 1,
                "PackageName": "",
                "SdkName": ""
            }
            response = requests.post(url, params=params, json=payload, timeout=5)
            response.raise_for_status()
            response_json = response.json()
            if response_json.get('Code') == 200:
                return response_json.get('Data', {}).get('Code', '')
            else:
                self.log(f"[获取code] 失败，错误信息: {response_json['Text']}", level="error")
                return False
        except Exception as e:
            self.log(f"[获取code] 发生错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return False
        
    def get_auth_keys(self):
        """
        iwechat 获取账号授权码列表
        :return: 账号授权码列表
        """
        try:
            url = self.wx_code_url
            params = {
                "key": self.wx_code_token
            }
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            response_json = response.json()
            return response_json
        except Exception as e:
            self.log(f"[获取code] 发生错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return False
        
    def get_code_4(self, wx_id):
        """
        iwechat 获取code
        :param wx_id: 微信id
        :return: code
        """
        try:
            auth_keys = self.wx_accounts_list
            if not auth_keys:
                self.log(f"[获取code] 账号列表为空，未能获取到", level="error")
                return False
            target_key = self.get_target_key_by_wxid(auth_keys, wx_id)
            url = self.wx_code_url.split("/admin")[0] + "/applet/JsLogin"
            params = {
                "key": target_key
            }
            payload = {
                "AppId": self.wx_appid,
                "Data": "",
                "Opt": 1,
                "PackageName": "",
                "SdkName": ""
            }
            response = requests.post(url, params=params, json=payload, timeout=5)
            response.raise_for_status()
            response_json = response.json()
            if response_json.get('Code') == 200:
                self.log(f"[获取code] 成功，code: {response_json.get('Data', {}).get('Code', '')}")
                return response_json.get('Data', {}).get('Code', '')
            else:
                self.log(f"[获取code] 失败，错误信息: {response_json['Text']}", level="error")
                return False
        except Exception as e:
            self.log(f"[获取code] 发生错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return False
        
    def get_code_5(self, wx_id):
        """
        StarBot Pro 获取code
        :param wx_id: 微信id
        :return: code
        """
        try:
            url = self.wx_code_url
            headers = {
                "Authorization": self.wx_code_token,
                "Content-Type": "application/json"
            }
            payload =  {
                "type": "querySmallProgramCode",
                "params": {
                    "robotId": wx_id,
                    "appid": self.wx_appid
                }
            }
            response = requests.post(url, headers=headers, json=payload, timeout=5)
            response.raise_for_status()
            response_json = response.json()
            if response_json.get('code') == 200:
                return response_json.get('data', {}).get('code', '')
            else:
                self.log(f"[获取code] 失败，错误信息: {response_json['description']}", level="error")
                return False
        except Exception as e:
            self.log(f"[获取code] 发生错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return False

    # ==================== 新增：YYB-Go 获取 code ====================
    def get_code_6(self, wx_id):
        """
        YYB-Go 协议获取 code
        :param wx_id: 微信id（账号的 id / uin / openid）
        :return: code 或 False
        """
        try:
            ref = self._match_yybgo_ref(wx_id)
            if not ref:
                self.log(f"[YYB-Go] 未匹配到 wx_id={wx_id} 对应的账号", level="error")
                return False

            code_url = self._fix_url_schema(self.wx_code_url)
            headers = {"Content-Type": "application/json"}
            payload = {
                "ref": ref,
                "app_id": self.wx_appid
            }

            response = requests.post(code_url, headers=headers, json=payload, timeout=8)
            response.raise_for_status()
            res_json = response.json()

            if res_json.get("code") != 0:
                self.log(f"[YYB-Go] 获取code失败: {res_json.get('msg', '未知错误')}", level="error")
                return False

            data = res_json.get("data", {})
            # 兼容两种返回结构：data.code  /  data.result.code
            code = data.get("code") or (data.get("result") or {}).get("code")
            if not code:
                self.log("[YYB-Go] 响应中未找到 code 字段", level="error")
                return False

            return code
        except requests.RequestException as e:
            self.log(f"[YYB-Go] 网络请求错误: {str(e)}", level="error")
            return False
        except Exception as e:
            self.log(f"[YYB-Go] 未知错误: {str(e)}\n{traceback.format_exc()}", level="error")
            return False
        
    def get_code(self, wx_id):
        """
        获取code
        :param wx_id: 微信id
        :return: 指定wxid的code
        """
        protocol_type = self.get_protocol_type()
        if protocol_type == 1:
            return self.get_code_1(wx_id)
        elif protocol_type == 2:
            return self.get_code_2(wx_id)
        elif protocol_type == 3:
            return self.get_code_3(wx_id)
        elif protocol_type == 4:
            return self.get_code_4(wx_id)
        elif protocol_type == 5:
            return self.get_code_5(wx_id)
        elif protocol_type == 6:
            return self.get_code_6(wx_id)
        else:
            self.log(f"[获取code] 发生错误: 未知协议类型", level="error")
            return False