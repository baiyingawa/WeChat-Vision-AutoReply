"""
WeChatAuto - Qt 微信自动化控制器
基于 pyautogui 坐标点击 + 剪贴板粘贴 + uiautomation SendKeys
适配新版本 Qt 微信 (ClassName=Qt51514QWindowIcon)
"""
import uiautomation as auto
import pyautogui
import time
import logging
import requests
import os
import struct
import tempfile
from dataclasses import dataclass
from PIL import Image
from io import BytesIO
import win32clipboard
import win32con
from pathlib import Path
import hashlib
import pyperclip

logger = logging.getLogger(__name__)

# 窗口相对坐标（微信客户端窗口左上角为原点）
# 由用户实测，针对 1786x1293 窗口
SEARCH_BOX = (335, 113)
CHAT_INPUT = (1178, 1053)


@dataclass
class SendResult:
    """发送操作结果"""
    success: bool
    code: str
    message: str


class WeChatController:
    """微信控制器 - 自动化发送消息"""

    def __init__(self):
        self.wx = None
        self.cache_dir = os.path.join(tempfile.gettempdir(), 'wechat_image_cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        # 重定向 uiautomation 日志到临时目录
        _log_dir = os.path.join(tempfile.gettempdir(), 'wechat_auto_logs')
        os.makedirs(_log_dir, exist_ok=True)
        try:
            auto.Logger.SetLogDir(_log_dir)
        except Exception:
            pass

    def _ok(self, msg):
        return SendResult(True, "OK", msg)

    def _fail(self, code, msg):
        return SendResult(False, code, msg)

    def _is_url(self, v):
        return v.lower().startswith(("http://", "https://"))

    def _resolve_path(self, file_path):
        try:
            p = os.path.expandvars(os.path.expanduser(file_path.strip().strip('"')))
            path = Path(p)
            if not path.is_absolute():
                path = Path.cwd() / path
            path = path.resolve()
            if not path.exists():
                return self._fail("NOT_FOUND", f"文件不存在: {path}"), None
            if not path.is_file():
                return self._fail("NOT_FILE", f"不是文件: {path}"), None
            return self._ok("有效路径"), str(path)
        except Exception as e:
            return self._fail("PATH_ERR", str(e)), None

    # 查找微信窗口
    def _find_wx(self):
        try:
            wx = auto.WindowControl(searchDepth=15, Name='\u5fae\u4fe1')
            if wx.Exists(0, 0):
                return self._ok("已找到微信窗口"), wx
            auto.SendKeys('{Ctrl}{Alt}\\', waitTime=0.1)
            time.sleep(1)
            wx = auto.WindowControl(searchDepth=15, Name='\u5fae\u4fe1')
            if wx.Exists(0, 0):
                return self._ok("已唤醒微信窗口"), wx
            return self._fail("WX_NOT_FOUND", "未找到微信窗口，请确认微信已启动登录"), None
        except Exception as e:
            return self._fail("WX_ERR", f"获取窗口异常: {e}"), None

    # 窗口相对坐标 → 屏幕绝对坐标
    def _screen(self, rx, ry):
        _, wx = self._find_wx()
        if wx is None:
            return None
        r = wx.BoundingRectangle
        return (r.left + rx, r.top + ry)

    # 在窗口相对位置点击
    def _click(self, rx, ry, delay=0.3):
        pos = self._screen(rx, ry)
        if pos is None:
            return False
        pyautogui.click(pos[0], pos[1])
        time.sleep(delay)
        return True

    # 剪贴板文本
    def _set_clip(self, text, retry=3):
        for i in range(retry):
            try:
                pyperclip.copy(text)
                time.sleep(0.05)
                if pyperclip.paste() == text:
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    # 搜索联系人
    def search_contact(self, contact):
        try:
            r, wx = self._find_wx()
            if not r.success:
                return r
            wx.SetActive()
            time.sleep(0.3)

            if not self._click(*SEARCH_BOX, 0.3):
                return self._fail("SEARCH_FAIL", "无法定位搜索框")

            auto.SendKeys('{Ctrl}a', waitTime=0.1)
            auto.SendKeys('{Del}', waitTime=0.1)

            if self._set_clip(contact):
                auto.SendKeys('{Ctrl}v', waitTime=0.2)
                time.sleep(0.3)
            else:
                return self._fail("CLIP_ERR", "剪贴板写入失败")

            auto.SendKeys('{Enter}', waitTime=0.2)
            time.sleep(0.8)
            return self._ok(f"已搜索: {contact}")
        except Exception as e:
            return self._fail("SEARCH_ERR", str(e))

    # 发送文本
    def send_text(self, text):
        try:
            r, wx = self._find_wx()
            if not r.success:
                return r
            wx.SetActive()
            time.sleep(0.2)

            if not self._click(*CHAT_INPUT, 0.2):
                return self._fail("INPUT_FAIL", "无法定位聊天输入框")

            if not self._set_clip(text):
                return self._fail("CLIP_ERR", "剪贴板写入失败")

            auto.SendKeys('{Ctrl}v', waitTime=0.2)
            time.sleep(0.2)
            auto.SendKeys('{Enter}', waitTime=0.2)
            time.sleep(0.3)
            return self._ok("文本发送成功")
        except Exception as e:
            return self._fail("SEND_ERR", str(e))

    # 下载图片到缓存
    def _dl_img(self, url, retry=3):
        try:
            md5 = hashlib.md5(url.encode()).hexdigest()
            cache = os.path.join(self.cache_dir, f"{md5}.png")
            if os.path.exists(cache) and os.path.getsize(cache) > 0:
                return self._ok("缓存图片"), cache

            for i in range(retry):
                try:
                    resp = requests.get(url, timeout=30, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    })
                    resp.raise_for_status()
                    ct = resp.headers.get('content-type', '')
                    if ct and not ct.startswith('image/'):
                        if 'text/' in ct or 'json' in ct:
                            return self._fail("NOT_IMG", f"非图片类型: {ct}"), None
                    img = Image.open(BytesIO(resp.content))
                    img.verify()
                    img = Image.open(BytesIO(resp.content))
                    img.save(cache, 'PNG')
                    return self._ok("下载成功"), cache
                except requests.RequestException as e:
                    if i < retry - 1:
                        time.sleep(1)
                    else:
                        return self._fail("DL_FAIL", str(e)), None
                except Exception as e:
                    return self._fail("IMG_ERR", str(e)), None
            return self._fail("DL_FAIL", "下载失败"), None
        except Exception as e:
            return self._fail("DL_ERR", str(e)), None

    # 图片 → 剪贴板
    def _img_to_clip(self, img_path, retry=3):
        for i in range(retry):
            opened = False
            try:
                img = Image.open(img_path)
                out = BytesIO()
                img.convert('RGB').save(out, 'BMP')
                data = out.getvalue()[14:]
                out.close()
                win32clipboard.OpenClipboard()
                opened = True
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                win32clipboard.CloseClipboard()
                return self._ok("图片已复制到剪贴板")
            except Exception as e:
                if opened:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass
                if i < retry - 1:
                    time.sleep(0.2)
        return self._fail("CLIP_IMG_FAIL", "图片复制剪贴板失败")

    # 文件 → 剪贴板 (CF_HDROP)
    def _file_to_clip(self, file_path, retry=3):
        r, path = self._resolve_path(file_path)
        if not r.success:
            return r

        header = struct.pack("IiiII", 20, 0, 0, 0, 1)
        flist = (path + "\0\0").encode("utf-16le")
        data = header + flist

        for i in range(retry):
            opened = False
            try:
                win32clipboard.OpenClipboard()
                opened = True
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
                win32clipboard.CloseClipboard()
                return self._ok(f"文件已复制到剪贴板: {path}")
            except Exception as e:
                if opened:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass
                if i < retry - 1:
                    time.sleep(0.2)
        return self._fail("CLIP_FILE_FAIL", f"文件复制剪贴板失败: {path}")

    # 直接粘贴并发送（不点击输入框，适用于搜索后焦点已在输入框）
    def _paste_only(self, delay=0.3):
        try:
            auto.SendKeys('{Ctrl}v', waitTime=0.2)
            time.sleep(delay)
            auto.SendKeys('{Enter}', waitTime=0.2)
            time.sleep(0.3)
            return self._ok("发送成功")
        except Exception as e:
            return self._fail("PASTE_ERR", str(e))

    # ================================================================
    # 对外接口
    # ================================================================
    def send_to(self, contact, text):
        """搜索联系人并发送文本（搜索后焦点已在输入框，直接粘贴）"""
        r = self.search_contact(contact)
        if not r.success:
            return r
        if not self._set_clip(text):
            return self._fail("CLIP_ERR", "剪贴板写入失败")
        return self._paste_only()

    def send_pic_to(self, contact, url_or_path):
        """搜索联系人并发送图片（URL 或本地路径）"""
        r = self.search_contact(contact)
        if not r.success:
            return r

        if self._is_url(url_or_path):
            dl_r, cache = self._dl_img(url_or_path)
            if not dl_r.success:
                return dl_r
            cb_r = self._img_to_clip(cache)
            if not cb_r.success:
                return cb_r
        else:
            cb_r = self._file_to_clip(url_or_path)
            if not cb_r.success:
                return cb_r

        return self._paste_only(0.5)

    def send_file_to(self, contact, file_path):
        """搜索联系人并发送本地文件"""
        r = self.search_contact(contact)
        if not r.success:
            return r

        cb_r = self._file_to_clip(file_path)
        if not cb_r.success:
            return cb_r

        return self._paste_only(0.8)

    def send_direct(self, text):
        """直接发送到当前已打开的聊天（不搜索联系人）
        适用于：agent已点击会话，输入框已获得焦点"""
        return self.send_text(text)
