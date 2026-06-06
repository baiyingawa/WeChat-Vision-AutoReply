"""
WeChatAgent - 纯视觉微信自动回复机器人
通过检测微信提示音触发 → 截图OCR → AI决策 → 自动回复
"""
import sys, os, time, json, subprocess, logging, ctypes, threading
from PIL import Image
from datetime import datetime
from collections import OrderedDict
import pyperclip
import pyautogui
import uiautomation as auto

DEBUG = False  # 调试模式开关，通过 --debug 启动

# OLED 屏幕保护（全屏黑屏，防止烧屏）
DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
#  持久化日志系统（OCR + 回复记录）
# ============================================================
_log_lock = threading.Lock()
_log_dir = os.path.join(DIR, 'logs')
os.makedirs(_log_dir, exist_ok=True)


def _get_log_path():
    """按日期生成日志文件路径"""
    return os.path.join(_log_dir, f'Autochat_{datetime.now():%Y-%m-%d}.log')


def log_ocr_reply(contact_name, ocr_text, reply_text):
    """线程安全地追加一条 OCR+回复记录到日志文件"""
    record = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'contact': contact_name,
        'ocr': ocr_text,
        'reply': reply_text,
    }
    line = json.dumps(record, ensure_ascii=False)
    with _log_lock:
        try:
            with open(_get_log_path(), 'a', encoding='utf-8') as f:
                f.write(line + '\n')
                f.flush()
        except:
            pass


# ============================================================
#  OLED 屏幕保护
# ============================================================
class OLEDSaver:
    def __init__(self):
        self._enabled = False
        self._shown = False  # 标记当前是否已打开黑屏
        self._html_path = os.path.join(DIR, '_oled_black.html')

    def _set_brightness(self, level):
        """设置屏幕亮度 0~100"""
        try:
            import subprocess
            subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 f'(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})'],
                capture_output=True, timeout=5
            )
        except:
            pass

    def _ensure_html(self):
        """生成全屏黑屏 HTML（用浏览器打开）"""
        # 每次重新生成（确保内容最新）
        with open(self._html_path, 'w', encoding='utf-8') as f:
            f.write('''<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>*{margin:0;padding:0}body{background:#000;width:100vw;height:100vh;overflow:hidden;cursor:none}</style>
</head><body>
<script>
try{window.open('','_self','fullscreen=yes,menubar=no,location=no,toolbar=no')}catch(e){}
window.moveTo(0,0);
window.resizeTo(screen.width,screen.height);
document.addEventListener('click',()=>window.close());
document.addEventListener('keydown',()=>window.close());
setTimeout(()=>{try{document.documentElement.requestFullscreen()}catch(e){}},200);
<\/script>
</body></html>''')

    def show(self):
        """启用黑屏（回复完成后调用）"""
        if not self._enabled:
            return
        self._set_brightness(0)
        if not self._shown:
            self._shown = True
            self._ensure_html()
            try:
                import subprocess
                url = self._html_path.replace('/', '\\')
                subprocess.Popen(f'start "" "{url}"', shell=True)
                time.sleep(1.5)
                pyautogui.press('f11')
            except:
                pass
        # 回复完 Alt+Tab 切回黑屏
        time.sleep(0.3)
        pyautogui.hotkey('alt', 'tab')

    def hide(self):
        """关闭黑屏 + 恢复亮度（不关闭浏览器窗口，让它开在后台）"""
        self._set_brightness(100)

    def _close_browser(self):
        """彻底关闭浏览器窗口（仅在禁用 OLED 时调用）"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, '_oled_black.html - ')
            if not hwnd:
                hwnd = user32.FindWindowW(None, 'about:blank')
            if hwnd:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
        except:
            pass

    def toggle(self):
        """手动切换"""
        if self._enabled:
            self._enabled = False
            self.hide()
        else:
            self._enabled = True
            self.show()
        return self._enabled

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, val):
        self._enabled = val


_oled_saver = OLEDSaver()

PYTHON = r'C:\Users\Yu\AppData\Local\Programs\Python\Python310\python.exe'
WECHAT_CLI = os.path.join(DIR, 'scripts', 'cli.py')
CONTACTS_PATH = r'E:\聊天记录\main\contacts_2026-05-31T09-45-12.json'
AI_CONFIG = os.path.join(DIR, 'config_ai.json')
RULES_PATH = os.path.join(DIR, '自动回复规则.json')

logger = logging.getLogger('WeChatAgent')
logger.setLevel(logging.INFO)

# 强制重置：清空所有 handler，只留一个
logger.handlers = []
logging.getLogger().handlers = []

_fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)
_ch.setLevel(logging.INFO)
logger.addHandler(_ch)
logger.propagate = False

# === 默认窗口坐标 ===
SESSION_LIST_X = 20
SESSION_START_Y = 225      # 第一个会话项顶部
SESSION_HEIGHT = 138        # 每个会话项高度
TARGET_INDEX = 2           # 点击第3个会话（跳过2个置顶）
NAME_ZONE = (120, 116, 440, 145)   # 名称区域 (left, top, right, bottom)
CHAT_ZONE = (530, 60, 1760, 900)   # 聊天区域 (left, top, right, bottom)

# 自动加载校准数据（calibrate.py 写入）
_calib_path = os.path.join(os.path.dirname(__file__), 'calib.json')
_calib_data = {}
if os.path.exists(_calib_path):
    try:
        with open(_calib_path, 'r') as f:
            _calib_data = json.load(f)
        SESSION_START_Y = _calib_data.get('session_start_y', SESSION_START_Y)
        SESSION_HEIGHT = _calib_data.get('session_height', SESSION_HEIGHT)
        nz = _calib_data.get('name_zone')
        cz = _calib_data.get('chat_zone')
        if nz: NAME_ZONE = tuple(nz)
        if cz: CHAT_ZONE = tuple(cz)
        logger.info(f'已加载校准数据: 窗口{_calib_data.get("window_size","?")}')
    except Exception as e:
        logger.warning(f'加载校准数据失败: {e}')

# 输入框 Y 坐标（校准数据优先，无则从 chat_zone 底部推算）
INPUT_ZONE_Y = _calib_data.get('input_zone_y', CHAT_ZONE[3] + 15)

# === 不回复名单（从自动回复规则中读取） ===
NO_REPLY_NAMES = set()

# === 加载自动回复规则 ===
_rules_data = None
_contacts_rules = {}  # name -> rule dict

def load_rules():
    global _rules_data, _contacts_rules, NO_REPLY_NAMES
    try:
        with open(RULES_PATH, 'r', encoding='utf-8') as f:
            _rules_data = json.load(f)
        # 建立名称→规则映射
        for section in ['excluded', 'teachers', 'contacts']:
            for item in _rules_data.get(section, []):
                name = item.get('name', '')
                if name:
                    _contacts_rules[name] = item
        # 不回复名单：excluded 中 auto_reply 为 false 的联系人
        NO_REPLY_NAMES = {
            item['name'] for item in _rules_data.get('excluded', [])
            if item.get('name') and item.get('auto_reply') is False
        }
        logger.info(f'加载自动回复规则: {len(_contacts_rules)}人, {len(NO_REPLY_NAMES)}人免回复')
    except Exception as e:
        logger.warning(f'加载规则失败: {e}')

load_rules()

# === 对话缓存（节省token） ===
_chat_cache = OrderedDict()  # name -> list of (role, content)
_last_reply_person = ''  # 上一次回复的人
_last_ai_reply_time = 0  # 上一次检测到 [Ai] 的时间
MAX_CACHE_PER = 10            # 每人最多缓存10条
MAX_CACHE_TOTAL = 200         # 总缓存上限

def cache_add(name, role, content):
    if name not in _chat_cache:
        _chat_cache[name] = []
    _chat_cache[name].append((role, content))
    # 限制每人缓存数量
    if len(_chat_cache[name]) > MAX_CACHE_PER:
        _chat_cache[name] = _chat_cache[name][-MAX_CACHE_PER:]
    # 总限制（移除最久没聊的）
    if len(_chat_cache) > MAX_CACHE_TOTAL:
        _chat_cache.popitem(last=False)


def build_system_prompt(contact_name):
    """根据联系人动态构建系统提示词
    返回 (system_prompt, contact_info)
    system_prompt → 固定的系统人格（可缓存）
    contact_info → 联系人专属信息（放到 user 消息里，省 token）
    """
    now = datetime.now()
    hour = now.hour
    current_time_str = now.strftime('%H:%M')

    rule = _contacts_rules.get(contact_name, {})
    # 模糊匹配：精确匹配不到时尝试一字之差
    if not rule:
        import difflib
        candidates = difflib.get_close_matches(contact_name, _contacts_rules.keys(), n=1, cutoff=0.7)
        if candidates:
            rule = _contacts_rules[candidates[0]]
            logger.info(f'🔍 模糊匹配联系人: {contact_name} → {candidates[0]}')

    # 基础系统人格（从规则文件读取）
    base_prompt = _rules_data.get('meta', {}).get('base_prompt', [])
    parts = list(base_prompt) if base_prompt else [
      "你是一个微信自动回复AI，帮助用户「uu」（昵称uu，高中生）回复微信消息。",
      "如果你看到的聊天记录，你刚刚已经最后回复过了他(对面没有发送新的消息，你的消息是最后一个)，则有可能是不小心重复请求了，这时候，发送无太大意义的内容就行，比如西西、okok、好滴好滴、好嘟好嘟之类的，或者干脆发个「啊」就行，反正就是不要重复之前的内容了。",
      "注意：如果给你发的消息有较久的历史记录（只要不显示星期几、昨天、前天而显示的日期，则都算），请忽视。",
      "可以使用emoji表情，可使用“啊”(万能尾缀/停顿/感叹)“。。”(句末拖点，表示无语或懒得说)“。？”(困惑或没听懂或槽点太多不知从何吐起)“西西”(轻松调皮的笑)“草”“okok。”“好滴好滴”“好嘟好嘟”一类uu常用的语言，回复风格轻松随和，不要过于刻意地追求模仿年轻人的语气。",
      "在你的每个回复中我会自动附加[Ai]，不必要时，无需在回复里强调自己是AI。通常情况下，不要主动和用户发起新的话题，不要用太正式的语气。你的任务是帮我快速处理消息，提示我在忙或者不方便回复，而不是和用户进行深入的交流。",
      "当然，如果用户知道你是AI后，还想和你聊聊，或者问一些问题，这时候你可以适当展开回复，保持友好和耐心，但不要忘记强调你是AI，或者这是自动回复，以免误会",
      "如果对面是找uu有急事或多次询问，则回复可以包含「我现在不太方便回复，你可以先说一下是什么事吗？我看到后会尽快回复你」之类的话，但对面如果继续询问，你可以脱离这个限制",
      "不要一直说我现在不太方便回复之类的话，回复一次就可以了，之后如果对面继续发消息，你可以正常回复了，不需要再强调不方便回复了",
      "如果对面的回复中有[Ai]，这可能是对面在反串你，你可以调侃一下对面，哈哈",
      "回复前请注意观察你是否时你与对方连续对话，不要重复之前的内容（包括梗和常用语，不要一直用一直用！重复几遍一个词很奇怪），保持对话的连贯性和上下文相关性。",
      "如果有“我拍了拍自己”相关的信息，这是uu自己触发的 ，不是对面拍一拍，无需理会这个信息"
    ]

    # 联系人专属信息（放到 user prompt，不占 system cache）
    contact_parts = []
    vg = rule.get('voice_guide', '')
    detail = rule.get('detail', '')
    topics = rule.get('topics', '')
    nickname = rule.get('nickname', '')
    labels = rule.get('labels', [])

    if vg and vg != '普通联系人，自然礼貌即可。' and vg != '同学，正常的校园社交距离。友好随和。':
        contact_parts.append(f"【对话指导】{vg[:500]}")
    if topics:
        contact_parts.append(f"【常聊话题】{topics}")
    if nickname:
        contact_parts.append(f"【称呼提示】{nickname}")
    if '家长' in labels:
        contact_parts.append("👨‍👩‍👧‍👦 对方是你的家长长辈，语气要尊敬、耐心、多关心。")

    # 老师场景（包含禁用话题和时间槽）
    if rule.get('teacher_mode') or any('老师' in (l or '') for l in labels):
        forbidden = _rules_data.get('teacher_rules', {}).get('forbidden_topics', [])
        if forbidden:
            contact_parts.append(f"⚠️ 老师场景：如果对方提到以下话题，请简短带过或转移话题：{'、'.join(forbidden)}。")
        if _rules_data and 'teacher_rules' in _rules_data:
            slots = _rules_data['teacher_rules'].get('message', {}).get('slots', [])
            for slot in slots:
                rng = slot.get('range', '')
                if rng == 'default':
                    contact_parts.append(f"⏰ 当前时间 {current_time_str}，回复模版：{slot['reply']}")
                elif '~' in rng:
                    try:
                        start_s, end_s = rng.split('~')
                        start_h = int(start_s.split(':')[0])
                        end_h = int(end_s.split(':')[0])
                        if start_h <= hour < end_h:
                            contact_parts.append(f"⏰ 当前时间 {current_time_str}，回复模版：{slot['reply']}")
                    except:
                        pass
        contact_parts.append("📝 对方是你的老师，保持礼貌和尊重，回复不要过于随意。")

    if detail:
        contact_parts.append(f"【备注】{detail[:200]}")

    contact_info = '\n'.join(contact_parts) if contact_parts else ''

    # 用户画像文件（固定不变，放 system prompt 可缓存）
    about_path = os.path.join(DIR, 'About uu.md')
    if os.path.exists(about_path):
        try:
            with open(about_path, 'r', encoding='utf-8') as f:
                about_content = f.read().strip()
            if about_content:
                parts.append(f'【关于 uu】\n{about_content}')
        except:
            pass

    # 缓存上下文（放 user prompt）
    cache_text = ''
    if contact_name in _chat_cache and _chat_cache[contact_name]:
        cache_lines = []
        for role, content in _chat_cache[contact_name][-6:]:
            cache_lines.append(f"{'你' if role == 'assistant' else '对方'}: {content[:150]}")
        if cache_lines:
            cache_text = "【历史对话片段】\n" + '\n'.join(cache_lines)

    system = '\n'.join(parts)
    extra = '\n\n'.join(filter(None, [contact_info, cache_text]))
    return system, extra


# ============================================================
#  OCR 引擎（按优先级自动选择）
# ============================================================
_ocr_engine = None
_XUNFEI_APPID = 'c9ebd248'
_XUNFEI_APIKEY = '025fc488be9db5a34852c8b36d3bdcdd'
_XUNFEI_APISECRET = 'MTM2MTE3NjIzYTQxNzNhMjFhZTc3ZmE1'
_XUNFEI_URL = 'https://api.xf-yun.com/v1/private/sf8e6aca1'


def _xunfei_ocr(image):
    """讯飞通用文字识别"""
    try:
        import base64, hashlib, hmac, io, requests as _req
        from datetime import datetime, timezone
        buf = io.BytesIO()
        image.save(buf, format='JPEG')
        b64 = base64.b64encode(buf.getvalue()).decode()

        now = datetime.now(timezone.utc)
        date_str = now.strftime('%a, %d %b %Y %H:%M:%S GMT')

        # 构建原始签名串
        sig_str = f'host: api.xf-yun.com\ndate: {date_str}\nPOST /v1/private/sf8e6aca1 HTTP/1.1'
        dig = hmac.new(_XUNFEI_APISECRET.encode(), sig_str.encode(), hashlib.sha256).digest()
        signature = base64.b64encode(dig).decode()

        # 构建 authorization（未编码）
        auth_raw = f'api_key="{_XUNFEI_APIKEY}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
        # authorization 整体要再 base64 编码
        authorization = base64.b64encode(auth_raw.encode()).decode()

        # 全部放在 URL query 中
        url = f'https://api.xf-yun.com/v1/private/sf8e6aca1?authorization={authorization}&host=api.xf-yun.com&date={date_str}'

        body = {
            'header': {'app_id': _XUNFEI_APPID, 'status': 3},
            'parameter': {'sf8e6aca1': {'category': 'ch_en_public_cloud', 'result': {'encoding': 'utf8', 'compress': 'raw', 'format': 'json'}}},
            'payload': {'sf8e6aca1_data_1': {'encoding': 'jpg', 'image': b64, 'status': 3}},
        }
        resp = _req.post(url, json=body, headers={'Content-Type': 'application/json'}, timeout=15)
        data = resp.json()
        if data.get('header', {}).get('code') == 0:
            result_b64 = data['payload']['result']['text']
            result_text = base64.b64decode(result_b64).decode()
            # 解析 json 结果提取文字
            import json as _json
            try:
                result_json = _json.loads(result_text)
                lines = []
                for page in result_json.get('pages', []):
                    for line in page.get('lines', []):
                        for word in line.get('words', []):
                            lines.append(word.get('content', ''))
                return '\n'.join(lines)
            except:
                return result_text[:1000]
        logger.warning(f'讯飞OCR失败: {data}')
        return ''
    except Exception as e:
        logger.warning(f'讯飞OCR异常: {e}')
        return ''


def init_ocr():
    global _ocr_engine
    # 检查配置中指定的引擎
    cfg = _read_config()
    preferred = cfg.get('ocr_engine', 'auto')

    # 如果已经初始化且切换引擎，强制重新初始化
    if _ocr_engine is not None and preferred != _ocr_engine[0] and preferred != 'auto':
        _ocr_engine = None

    if _ocr_engine is not None:
        return _ocr_engine

    # 1) 用户指定讯飞
    if preferred == 'xunfei':
        _ocr_engine = ('xunfei', _xunfei_ocr)
        logger.info('OCR引擎: 讯飞')
        return _ocr_engine

    # 2) winocr (Windows原生)
    try:
        import winocr
        from winocr import recognize_pil_sync
        test_img = Image.new('RGBA', (100, 30))
        result = recognize_pil_sync(test_img, 'zh-CN')
        if 'text' in result:
            _ocr_engine = ('winocr', recognize_pil_sync)
            logger.info('OCR引擎: winocr (Windows原生)')
            return _ocr_engine
    except Exception as e:
        logger.info(f'winocr不可用: {e}')

    # 3) easyocr
    try:
        import easyocr
        _easyocr_reader = easyocr.Reader(['ch_sim', 'en'], gpu=False)
        _ocr_engine = ('easyocr', lambda img:
            '\n'.join([r[1] for r in _easyocr_reader.readtext(img) if r[2] > 0.3]))
        logger.info('OCR引擎: EasyOCR')
        return _ocr_engine
    except Exception as e:
        logger.info(f'EasyOCR不可用: {e}')

    # 4) pytesseract
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        _ocr_engine = ('tesseract', lambda img, lang='chi_sim+eng':
            pytesseract.image_to_string(img, lang=lang))
        logger.info('OCR引擎: pytesseract')
        return _ocr_engine
    except:
        logger.info('pytesseract不可用')

    logger.error('没有可用的OCR引擎！请安装: pip install winocr')
    return None


def ocr_image(image):
    engine = init_ocr()
    if engine is None:
        return ''
    name, func = engine
    try:
        if name == 'winocr':
            result = func(image, 'zh-CN')
            return result.get('text', '')
        else:
            return func(image)
    except Exception as e:
        logger.error(f'OCR识别失败: {e}')
        return ''



# ============================================================
#  通讯录
# ============================================================
def load_contacts():
    try:
        with open(CONTACTS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except:
        return {}
    contacts = {}
    for c in data.get('contacts', []):
        name = c.get('displayName', '') or c.get('nickname', '') or c.get('remark', '')
        if name:
            contacts[name] = c
    logger.info(f'已加载通讯录: {len(contacts)}人')
    return contacts


def find_contact(contacts, name):
    if name in contacts:
        return contacts[name]
    for k, v in contacts.items():
        if name in k or k in name:
            return v
    return None


def contact_summary(info):
    if not info:
        return '未知联系人'
    parts = []
    labels = info.get('labels', [])
    desc = info.get('detailDescription', '')
    if labels:
        parts.append(f'关系: {", ".join(labels)}')
    if desc:
        lines = [l.strip() for l in desc.split('\n') if l.strip()][:3]
        parts.append(f'备注: {" | ".join(lines)}')
    return ' | '.join(parts) if parts else '普通联系人'


# ============================================================
#  窗口操作
# ============================================================
def _get_hwnd(wx):
    """从 uiautomation 控件获取窗口句柄"""
    try:
        return wx.NativeWindowHandle
    except:
        return None


def _force_foreground(hwnd):
    """用 Win32 API 强制将窗口带到前台（支持从后台线程调用）"""
    user32 = ctypes.windll.user32

    # 0) 允许本进程设置前台窗口
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.wintypes.DWORD(-1))
    except:
        pass

    # 检查窗口是否可见
    is_visible = user32.IsWindowVisible(hwnd)
    if not is_visible:
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        time.sleep(0.2)

    # 最大化
    user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
    time.sleep(0.15)

    # 方法1: SwitchToThisWindow（限制最少，无需线程附加）
    try:
        user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.1)
        if user32.GetForegroundWindow() == hwnd:
            return True
    except:
        pass

    # 方法2: AttachThreadInput + SetForegroundWindow
    fore_hwnd = user32.GetForegroundWindow()
    current_tid = user32.GetWindowThreadProcessId(fore_hwnd, None)
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(current_tid, target_tid, True)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(current_tid, target_tid, False)
    time.sleep(0.15)
    if user32.GetForegroundWindow() == hwnd:
        return True

    # 方法3: SetWindowPos 强制置顶
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_SHOWWINDOW = 0x0040
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, 0x0002 | 0x0001)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, 0x0002 | 0x0001)
    time.sleep(0.1)
    if user32.GetForegroundWindow() == hwnd:
        return True

    # 方法4: 模拟Alt键 + SetForegroundWindow
    try:
        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)  # Alt down
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)  # Alt up
        time.sleep(0.15)
        if user32.GetForegroundWindow() == hwnd:
            return True
    except:
        pass

    # 兜底：托盘图标
    _click_tray_icon()
    time.sleep(1)
    return user32.GetForegroundWindow() == hwnd


def _click_tray_icon():
    """通过 Win32 API 点击微信托盘图标"""
    try:
        user32 = ctypes.windll.user32
        # 找到通知区域的 ToolbarWindow32（系统托盘）
        tray_hwnd = user32.FindWindowW('Shell_TrayWnd', None)
        if not tray_hwnd:
            return
        # 通知区域
        notify_hwnd = user32.FindWindowExW(tray_hwnd, 0, 'TrayNotifyWnd', None)
        if not notify_hwnd:
            return
        # 系统托盘按钮
        button_hwnd = user32.FindWindowExW(notify_hwnd, 0, 'Button', None)
        if button_hwnd:
            # 发送点击消息恢复微信
            user32.PostMessageW(button_hwnd, 0x0201, 0, 0)  # WM_LBUTTONDOWN
            user32.PostMessageW(button_hwnd, 0x0202, 0, 0)  # WM_LBUTTONUP
            return

        # 兜底：展开隐藏图标区域再试
        overflow_hwnd = user32.FindWindowExW(notify_hwnd, 0, 'ToolbarWindow32', 'OverflowNotificationArea')
        if overflow_hwnd:
            user32.PostMessageW(overflow_hwnd, 0x0201, 0, 0)
            user32.PostMessageW(overflow_hwnd, 0x0202, 0, 0)
    except Exception as e:
        logger.debug(f'点击托盘图标失败: {e}')


def find_wx():
    wx = auto.WindowControl(searchDepth=15, Name='\u5fae\u4fe1')
    if not wx.Exists(0, 0):
        # 窗口完全找不到（可能托盘区域也找不到），快捷键兜底
        logger.info('未找到微信窗口，尝试快捷键唤醒...')
        auto.SendKeys('{Ctrl}{Alt}\\', waitTime=0.1)
        time.sleep(1)
        wx = auto.WindowControl(searchDepth=15, Name='\u5fae\u4fe1')
    return wx if wx.Exists(0, 0) else None


def activate_wx(wx):
    """激活微信窗口（优先 Win32 API，失败才用快捷键）"""
    hwnd = _get_hwnd(wx)
    if hwnd and _force_foreground(hwnd):
        time.sleep(0.3)
        return True
    # 兜底：uiautomation 的 SetActive
    try:
        wx.SetActive()
        time.sleep(0.3)
        return True
    except:
        pass
    return False


def click_session(wx, index):
    y = SESSION_START_Y + index * SESSION_HEIGHT
    r = wx.BoundingRectangle
    pyautogui.click(r.left + SESSION_LIST_X + 140, r.top + y)
    time.sleep(1)


def screenshot_region(wx, left, top, right, bottom):
    r = wx.BoundingRectangle
    w, h = right - left, bottom - top
    return pyautogui.screenshot(region=(r.left + left, r.top + top, w, h))


# ============================================================
#  AI决策
# ============================================================
import re

_TIME_MARKER_PATTERNS = [
    r'^刚刚$',
    r'^\d{1,2}分钟前$',
    r'^今天\s*\d{1,2}:\d{2}$',
    r'^昨天\s*\d{1,2}:\d{2}$',
    r'^星期[一二三四五六日天]\s*\d{1,2}:\d{2}$',
    r'^\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}$',
    r'^\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}$',
    r'^\d{1,2}:\d{2}$',
    r'^上午\s*\d{1,2}:\d{2}$',
    r'^下午\s*\d{1,2}:\d{2}$',
    r'^\d{2}-\d{2}\s*\d{1,2}:\d{2}$',
]

def _is_time_marker(text):
    """判断 OCR 文本是否为微信会话时间分界标识"""
    # 清理：去除OCR引入的额外空格，统一冒号
    cleaned = text.replace(' ', '').replace('\u3000', '')
    # 中文冒号 → 英文冒号（OCR 经常识别成全角）
    cleaned = cleaned.replace('：', ':').replace(';', ':').replace('；', ':')
    for pat in _TIME_MARKER_PATTERNS:
        if re.match(pat, cleaned):
            return True
    # 宽松匹配：仅含数字、冒号、月日年等，无中文字符（除今天昨天星期等）
    # 如果文本非常短（≤20字）且匹配时间格式特征
    if len(cleaned) <= 20 and re.match(r'^[\d:年月日天昨星今期分上午下午\-]+$', cleaned):
        return True
    return False

def _format_ordered_pairs(ordered_pairs):
    """将有序会话格式化为AI输入文本，每条消息用双引号包裹"""
    lines = []
    for sender, text in ordered_pairs[-10:]:  # 最多10条
        text = text.strip()[:200]
        if _is_time_marker(text):
            cleaned = text.replace(' ', '').replace('\u3000', '').replace('：', ':')
            lines.append(f'({cleaned})')
        elif sender == 'their':
            lines.append(f'对方: "{text}"')
        else:
            lines.append(f'你: "{text}"')
    return lines


def ai_decide(contact_name, their_text, my_text='', ordered_pairs=None):
    cfg = _read_config()
    if not isinstance(cfg, dict):
        logger.error(f'配置异常: 类型={type(cfg).__name__}, 值={str(cfg)[:100]}')
        cfg = {}
    if not cfg.get('enabled') or not cfg.get('apiKey'):
        logger.warning('AI未启用，跳过回复')
        return None

    import requests

    system_prompt, contact_info = build_system_prompt(contact_name)

    conv = []
    if ordered_pairs:
        conv = _format_ordered_pairs(ordered_pairs)
    else:
        if their_text:
            conv.append(f'对方: "{their_text.strip()[:800]}"')
        if my_text:
            conv.append(f'你: "{my_text.strip()[:800]}"')

    messages = [
        {'role': 'system', 'content': system_prompt},
    ]
    # 从当前模式读取提示词
    active_mode = cfg.get('active_mode', 'other')
    extra = cfg.get('modes', {}).get(active_mode, {}).get('prompt', '').strip()
    if not extra:
        extra = cfg.get('extra_prompt', '').strip()  # 向后兼容
    # 临时提示词（在模式提示词之上叠加，可长期保留，手动管理）
    temp = cfg.get('temp_prompt', '').strip()
    if temp:
        extra = (extra + '\n' + temp) if extra else temp
    newline = '\n'
    now = datetime.now()
    weekday_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    time_tag = f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} ({weekday_names[now.weekday()]})"
    user_content = f"{time_tag}{newline}{newline}【当前对话】{newline}{newline.join(conv)}{newline}{newline}请回复对方最新一条消息。"
    if extra:
        user_content = f"【用户提示】{extra}\n\n{user_content}"
    if contact_info:
        user_content = f'以下是对该联系人的特殊回复提示，仅作为提示\n{contact_info}\n\n{user_content}'
    messages.append({'role': 'user', 'content': user_content})

    try:
        r = requests.post(
            f"{cfg['baseUrl'].rstrip('/')}/chat/completions",
            headers={'Authorization': f"Bearer {cfg['apiKey']}", 'Content-Type': 'application/json'},
            json={
                'model': cfg.get('model', 'deepseek-chat'),
                'messages': messages,
                'temperature': 0.7, 'max_tokens': 300,
            },
            timeout=30
        )
        if r.status_code == 200:
            reply = r.json()['choices'][0]['message']['content'].strip()
            # 缓存本次对话
            if their_text:
                cache_add(contact_name, 'user', their_text)
            if not reply:
                logger.warning('AI返回了空回复，跳过')
                return None
            cache_add(contact_name, 'assistant', reply)
            return f'[Ai]{reply}'
        else:
            logger.warning(f'AI API错误: {r.status_code} {r.text[:200]}')
    except Exception as e:
        logger.error(f'AI调用失败: {e}')
    return None


# ============================================================
#  处理新消息
# ============================================================
_processing = False


def breakpoint_pause(step_name, context='', wx=None):
    """调试断点：DEBUG 模式下暂停，等待回车后继续，然后切回微信窗口"""
    if not DEBUG:
        return
    print()
    print('=' * 55)
    print(f'  [调试] 步骤: {step_name}')
    if context:
        print(f'  详情: {context}')
    print('  >>> 按回车继续 <<<')
    print('=' * 55)
    try:
        input()
    except:
        pass
    # 回车后自动切回微信窗口
    if wx:
        try:
            from uiautomation import WindowControl
            activate_wx(wx)
            time.sleep(0.3)
        except:
            pass


def process():
    global _processing, _last_ai_reply_time
    # 刚回复完的 [Ai] 回声冷却期内直接丢弃
    if time.time() - _last_ai_reply_time < 3:
        return
    if _processing:
        logger.info('⏭️ 正在处理上一条消息，跳过本次触发')
        return
    # 检查自动回复开关
    if not cfg_get('auto_reply', True):
        logger.info('⏸️ 自动回复已暂停（管理面板可开启）')
        _processing = False  # 立即释放锁，下次再试
        return
    _processing = True

    # 收到消息 → 唤醒屏幕
    _oled_saver.hide()

    try:
        logger.info('=== 检测到新消息，开始处理 ===')
        contacts = load_contacts()

        wx = find_wx()
        if not wx:
            logger.error('找不到微信窗口')
            return

        breakpoint_pause('find_wx', f'微信窗口对象: {wx}', wx=wx)

        activate_wx(wx)
        time.sleep(0.5)
        breakpoint_pause('activate_wx', '微信窗口已切换到前台', wx=wx)

        # 1) 先点击第12个会话复位，再点击第3个会话
        logger.info('先点击第12个会话复位...')
        click_session(wx, 11)
        time.sleep(0.3)
        logger.info('点击第3个会话...')
        click_session(wx, TARGET_INDEX)
        breakpoint_pause('click_session', f'已点击第 {TARGET_INDEX + 1} 个会话（索引={TARGET_INDEX}），查看窗口是否已切换', wx=wx)

        # 2) OCR
        chat_img = screenshot_region(wx, *CHAT_ZONE)
        their_text, my_text, ordered_pairs = ocr_by_bubbles(chat_img)
        logger.info(f'识别到 {len(ordered_pairs)} 条消息')
        logger.info(f'对方: {their_text[:200]}')
        logger.info(f'我方: {my_text[:200]}')

        # 3) 获取联系人
        name_img = screenshot_region(wx, *NAME_ZONE)
        name_text = ocr_image(name_img).strip().split('\n')[0]
        # 如果识别为空，尝试偏移后重试（窗口可能偏移）
        if not name_text.strip():
            offsets = [(5, 0), (-5, 0), (0, 5), (0, -5), (10, 0)]
            for dx, dy in offsets:
                alt_zone = (NAME_ZONE[0] + dx, NAME_ZONE[1] + dy,
                            NAME_ZONE[2] + dx, NAME_ZONE[3] + dy)
                alt_img = screenshot_region(wx, *alt_zone)
                alt_text = ocr_image(alt_img).strip().split('\n')[0]
                if alt_text.strip():
                    name_text = alt_text
                    logger.info(f'🔄 偏移OCR ({dx},{dy}) 成功: {name_text}')
                    break
        # 如果偏移后还是空，保存截图用于调试，但继续处理
        if not name_text.strip():
            debug_dir = os.path.join(DIR, 'logs', 'debug_name')
            os.makedirs(debug_dir, exist_ok=True)
            debug_path = os.path.join(debug_dir, f'name_fail_{datetime.now():%Y%m%d_%H%M%S}.png')
            name_img.save(debug_path)
            logger.info(f'⚠️ 名称识别为空，已保存截图: {debug_path}')
            name_text = '未知'
        # OCR 可能给名字加空格（如"水 晶 溯 雨"），去掉后用于规则匹配
        name_clean = name_text.replace(' ', '').replace('\u3000', '')
        logger.info(f'联系人: {name_clean} (原始OCR: {name_text})')

        # 如果联系人名以 ) 或 ） 结尾，说明是群聊，跳过
        if name_clean.endswith(')') or name_clean.endswith('）'):
            logger.info(f'⛔ {name_clean} 是群聊，跳过')
            _oled_saver.show()
            return

        # 如果最后一条消息以 [Ai] 开头，说明刚回复过，跳过
        # （处理常见 OCR 识别问题：空格、字符误认等）
        last_raw = ordered_pairs[-1][1].strip() if ordered_pairs else ''
        # 去空格、全角转半角、常见 OCR 错误归一化
        last_clean = last_raw.replace(' ', '').replace('\u3000', '') \
            .replace('［', '[').replace('］', ']') \
            .replace('Ｉ', 'I').replace('ｉ', 'i') \
            .replace('１', '1').replace('l', 'i').replace('|', 'i') \
            .replace('刂', 'i]') \
            .replace('了', '')  # "[A了" 偶尔会出现
        if last_clean.startswith('[ai') or last_clean.startswith('[Ai') or last_clean.startswith('[AI'):
            _last_ai_reply_time = time.time()
            logger.info(f'⏭️ 最后消息是 AI 自己的回复（OCR识别: {last_raw[:30]}），跳过 + 清空排队队列')
            _oled_saver.show()  # 切回黑屏
            return

        # 如果不是同一个人，清除上下文
        global _last_reply_person
        if name_clean != _last_reply_person:
            _chat_cache.clear()
            logger.info(f'👤 换人了 ({name_clean} != {_last_reply_person})，清空对话记忆')
        _last_reply_person = name_clean

        # 4) 检查免回复
        if name_clean in NO_REPLY_NAMES:
            logger.info(f'⛔ {name_clean} 在免回复名单中，跳过')
            return

        breakpoint_pause('OCR完成', f'联系人: {name_text}\n  对方消息: {their_text[:120]}', wx=wx)

        # 5) AI回复
        reply = ai_decide(name_clean, their_text, my_text, ordered_pairs)
        if not reply:
            logger.warning('AI未返回回复，跳过')
            return
        logger.info(f'AI回复: {reply[:80]}')
        breakpoint_pause('AI回复', f'即将发送: {reply[:200]}', wx=wx)

        # 6) 发送：粘贴到输入框 → Ctrl+V → 回车
        clean_reply = reply.strip()
        pyperclip.copy(clean_reply)
        # 点击输入框区域（基于校准数据推算）
        r2 = wx.BoundingRectangle
        input_x = r2.left + (CHAT_ZONE[0] + CHAT_ZONE[2]) // 2
        input_y = r2.top + INPUT_ZONE_Y
        pyautogui.click(input_x, input_y)
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.3)
        pyautogui.press('enter')
        time.sleep(0.5)
        logger.info(f'✅ 已回复 {name_text}')

        # 持久化日志
        log_ocr_reply(name_clean, their_text[:200], clean_reply[:200])

        breakpoint_pause('发送完成', f'回复已处理 ({name_text})', wx=wx)

        # 回复完成后黑屏（OLED 防烧屏）
        _oled_saver.show()

    except Exception as e:
        import traceback
        logger.error(f'处理异常: {e}\n{traceback.format_exc()[:500]}')
    finally:
        breakpoint_pause('处理完成', '本轮消息处理结束，恢复监听', wx=wx)
        _processing = False


# ============================================================
#  监控（通过微信提示音触发）
# ============================================================
def monitor():
    from sound_trigger import SoundTrigger

    logger.info('WeChatAgent 已启动，等待消息...')
    init_ocr()

    trigger = SoundTrigger()
    trigger.start(callback=process)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        trigger.stop()
        logger.info('停止监听')


def _load_colors():
    """加载校准的颜色阈值"""
    my = _calib_data.get('my_color', {})
    their = _calib_data.get('their_color', {})
    bg = _calib_data.get('bg_color', {})
    return my, their, bg


def ocr_by_bubbles(image, return_boxes=False):
    """
    逐行扫描颜色 → 分块（我方/对方/背景）→ 整块OCR → 按Y排序
    返回 (their_text, my_text, ordered_pairs)
    如果 return_boxes=True，额外返回 [(cls, x1, y1, x2, y2), ...]
    """
    w, h = image.size
    pixels = image.load()
    my_color, their_color, bg_color = _load_colors()

    # 默认颜色值
    if my_color:
        mr, mg, mb = my_color['r'], my_color['g'], my_color['b']
    else:
        mr, mg, mb = 148, 236, 105
    if bg_color:
        br, bg_val, bb = bg_color['r'], bg_color['g'], bg_color['b']
    else:
        br, bg_val, bb = 240, 240, 240

    def is_my(r, g, b):
        return abs(g - mg) < 50 and g > r + 15 and g > b + 15

    def is_bg(r, g, b):
        return abs(r - br) < 8 and abs(g - bg_val) < 8 and abs(b - bb) < 8

    def is_dark_text(r, g, b):
        """文字像素：深色，不是背景"""
        return (r + g + b) < 400 and not is_bg(r, g, b)

    scan_step = 4
    blocks = []
    boxes = []
    current_class = None
    block_start = 0

    for y in range(0, h, scan_step):
        has_green = False
        has_text = False
        all_bg = True

        for x in range(0, w, 4):
            try:
                r, g_val, b = pixels[x, y][:3]
                if is_my(r, g_val, b):
                    has_green = True
                    all_bg = False
                elif is_dark_text(r, g_val, b):
                    has_text = True
                    all_bg = False
            except:
                pass

        # 确定这一行的类型
        if has_green:
            line_class = 'my'
        elif has_text:
            line_class = 'their'
        elif all_bg:
            line_class = 'bg'
        else:
            line_class = current_class or 'bg'

        # 类型变化 → 结束上一个块
        if line_class != current_class:
            if current_class and current_class != 'bg':
                blocks.append((block_start, y, current_class))
            block_start = y
            current_class = line_class

    # 收尾块
    if current_class and current_class != 'bg':
        blocks.append((block_start, h, current_class))

    # 对每个块按颜色边界动态裁剪后OCR
    raw_results = []  # [(start_y, cls, text), ...]
    for start_y, end_y, cls in blocks:
        cy1 = max(0, start_y - 3)
        cy2 = min(h, end_y + 3)

        # 动态找左右边界
        if cls == 'my':
            left_x = w
            for y in range(cy1, cy2, 2):
                # 从右向左扫，找到绿色气泡的最左边缘
                for x in range(w - 1, w // 2 - 1, -4):
                    try:
                        if is_my(*pixels[x, y][:3]):
                            left_x = min(left_x, x)
                    except:
                        pass
            crop = image.crop((max(0, left_x - 20), max(0, cy1 - 5), w, min(h, cy2 + 5)))
            box = (max(0, left_x - 20), max(0, cy1 - 5), w, min(h, cy2 + 5))
        else:
            right_x = 0
            for y in range(cy1, cy2, 2):
                # 从左向右扫，找到对方文字的最右边缘  
                for x in range(0, min(w // 2 + 100, w), 4):
                    try:
                        r, gv, b = pixels[x, y][:3]
                        if is_dark_text(r, gv, b):
                            right_x = max(right_x, x)
                    except:
                        pass
            crop = image.crop((0, max(0, cy1 - 5), min(w, right_x + 25), min(h, cy2 + 5)))
            box = (0, max(0, cy1 - 5), min(w, right_x + 25), min(h, cy2 + 5))

        try:
            # 小图放大以提高OCR识别率
            cw, ch_ = crop.size
            if cw < 80 or ch_ < 20:
                crop = crop.resize((cw * 3, ch_ * 3), Image.LANCZOS)
            text = ocr_image(crop).strip()
            if text:
                raw_results.append((start_y, cls, text))
                if return_boxes:
                    # 判断是否居中（系统通知/时间标记等）
                    x1, y1, x2, y2 = box
                    center_w = w * 0.15  # 左右各留15%边距
                    box_cls = 'info' if (x1 > center_w and x2 < w - center_w) else cls
                    boxes.append((box_cls, *box))
        except:
            pass

    # 按 Y 坐标严格排序（从上到下 = 消息顺序）
    raw_results.sort(key=lambda x: x[0])
    ordered = [(cls, text) for _, cls, text in raw_results]

    if not ordered:
        full_text = ocr_image(image).strip()
        if return_boxes:
            return full_text, '', [('their', full_text)], []
        return full_text, '', [('their', full_text)]

    their_lines = [t for c, t in ordered if c == 'their']
    my_lines = [t for c, t in ordered if c == 'my']
    logger.info(f'气泡排序: {" → ".join([f"{c}({t[:15]}...)" for c,t in ordered[:6]])}')
    if return_boxes:
        return '\n'.join(their_lines), '\n'.join(my_lines), ordered, boxes
    return '\n'.join(their_lines), '\n'.join(my_lines), ordered


def diagnose():
    """运行诊断：显示会话列表位置并截图验证"""
    print('=' * 50)
    print('WeChatAgent 诊断模式')
    print('=' * 50)

    wx = find_wx()
    if not wx:
        print('❌ 找不到微信窗口！')
        return

    activate_wx(wx)
    time.sleep(0.5)

    r = wx.BoundingRectangle
    w, h = r.right - r.left, r.bottom - r.top
    print(f'微信窗口: {w}x{h} at ({r.left},{r.top})')

    # 截取会话列表区域
    session_img = pyautogui.screenshot(region=(
        r.left + 5, r.top + SESSION_START_Y, 490, SESSION_HEIGHT * 5
    ))
    session_img.save(os.path.join(DIR, 'diag_sessions.png'))
    print(f'✅ 会话列表截图: diag_sessions.png')
    print(f'   Y范围: {SESSION_START_Y}~{SESSION_START_Y + SESSION_HEIGHT * 5}')
    print(f'   点击第3个会话位置: x~20+100, y~{SESSION_START_Y + TARGET_INDEX * SESSION_HEIGHT}')

    # 测试OCR
    engine = init_ocr()
    if engine:
        print(f'✅ OCR引擎: {engine[0]}')
        text = ocr_image(session_img)
        print(f'   识别结果: {text[:200]}')
    else:
        print('❌ 没有可用的OCR引擎')
        print('   安装: pip install winocr')
    print()
    print('如果位置不准，调整 agent.py 顶部的坐标常量即可')


# ============================================================
#  配置网页服务器（端口5035）
# ============================================================
CONFIG_PORT = 5035

ADMIN_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>WeChatAgent 管理面板</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Microsoft YaHei',sans-serif;background:#0d1117;color:#c9d1d9;padding:20px;height:100vh;display:flex;flex-direction:column}
h1{font-size:18px;font-weight:500;color:#f0f6fc;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #30363d}
.layout{display:flex;gap:16px;flex:1;min-height:0}
.left-panel{flex:3;display:flex;flex-direction:column;gap:12px;overflow-y:auto;padding-right:4px}
.right-panel{flex:2;display:flex;flex-direction:column;min-width:280px}

.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.card-title{font-size:14px;font-weight:500;color:#e6edf3}

/* toggle switch */
.toggle-wrap{display:flex;align-items:center;gap:12px}
.toggle{position:relative;width:48px;height:26px;cursor:pointer}
.toggle input{opacity:0;width:0;height:0}
.toggle .slider{position:absolute;top:0;left:0;right:0;bottom:0;background:#30363d;border-radius:26px;transition:0.3s}
.toggle .slider::before{content:'';position:absolute;height:20px;width:20px;left:3px;bottom:3px;background:#8b949e;border-radius:50%;transition:0.3s}
.toggle input:checked+.slider{background:#238636}
.toggle input:checked+.slider::before{background:#fff;transform:translateX(22px)}
.toggle-label{font-size:13px;font-weight:500}
.toggle-label.on{color:#3fb950}
.toggle-label.off{color:#8b949e}

/* mode tabs */
.mode-tabs{display:flex;gap:0;margin-bottom:0;background:#0d1117;border-radius:6px;overflow:hidden;border:1px solid #30363d}
.mode-tab{flex:1;padding:8px 0;text-align:center;font-size:13px;cursor:pointer;border:none;background:transparent;color:#8b949e;transition:all .15s}
.mode-tab:hover{color:#c9d1d9}
.mode-tab.active{background:#1f6feb22;color:#58a6ff;font-weight:500;border-bottom:2px solid #58a6ff}

textarea{width:100%;padding:10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none;resize:vertical;line-height:1.6;font-family:inherit;margin-top:8px}
textarea:focus{border-color:#58a6ff}
.hint{font-size:12px;color:#8b949e;margin-top:6px}

/* log area */
.log-wrap{display:flex;flex-direction:column;flex:1;min-height:0}
.log-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.log-box{flex:1;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px;font-size:12px;font-family:monospace;color:#8b949e;overflow-y:auto;white-space:pre-wrap;line-height:1.7;min-height:200px}
.log-box .info{color:#c9d1d9}
.log-box .warn{color:#d29922}
.log-box .error{color:#f85149}
.log-box .success{color:#3fb950}
#clearLog{background:transparent;border:1px solid #30363d;color:#8b949e;padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer}
#clearLog:hover{color:#c9d1d9;border-color:#8b949e}

.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.status-dot.on{background:#3fb950}
.status-dot.off{background:#f85149}

.saving{position:fixed;bottom:16px;right:16px;background:#1c2333;border:1px solid #30363d;border-radius:6px;padding:6px 14px;font-size:12px;color:#8b949e;opacity:0;transition:opacity .3s}
.saving.show{opacity:1}
</style>
</head>
<body>
<div style="display:flex;align-items:center;justify-content:space-between">
<h1>WeChatAgent 管理面板</h1>
<button id="settingsBtn" style="padding:8px 14px;border:1px solid #30363d;border-radius:6px;background:#161b22;color:#c9d1d9;font-size:14px;cursor:pointer">⚙️ 设置</button>
</div>
<div class="layout">
<div class="left-panel">
  <!-- auto-reply toggle -->
  <div class="card">
    <div class="card-header">
      <span class="card-title">自动回复</span>
      <div class="toggle-wrap">
        <span id="statusLabel" class="toggle-label off">已停止</span>
        <label class="toggle"><input type="checkbox" id="autoToggle"><span class="slider"></span></label>
      </div>
    </div>
  </div>

  <!-- OLED 黑屏保护 -->
  <div class="card">
    <div class="card-header">
      <span class="card-title">OLED 黑屏保护</span>
      <div class="toggle-wrap">
        <span id="oledLabel" class="toggle-label off">已关闭</span>
        <label class="toggle"><input type="checkbox" id="oledToggle"><span class="slider"></span></label>
      </div>
    </div>
    <div class="hint">开启后回复完成自动全屏黑屏，收到消息自动唤醒</div>
  </div>

  <!-- 清除记忆 -->
  <div class="card">
    <button class="btn btn-danger" id="clearMemBtn" style="padding:8px 16px;border:none;border-radius:6px;font-size:13px;cursor:pointer;background:#da3633;color:#fff;width:100%">清除对话记忆</button>
    <div class="hint" style="margin-top:6px">清空 AI 缓存的对话上下文，下次回复不受历史影响</div>
  </div>

  <!-- mode tabs + prompt -->
  <div class="card">
    <div class="card-header"><span class="card-title">模式提示词</span></div>
    <div class="mode-tabs" id="modeTabs">
      <button class="mode-tab" data-mode="coding">编程</button>
      <button class="mode-tab" data-mode="slacking">摸鱼</button>
      <button class="mode-tab" data-mode="gaming">游戏</button>
      <button class="mode-tab" data-mode="other">其他</button>
    </div>
    <textarea id="promptArea" placeholder="输入当前模式的提示词，发送给AI时自动附加..."></textarea>
    <div class="hint">模式提示词自动保存（300ms防抖），切换模式时自动切换</div>
  </div>

  <!-- temporary prompt -->
  <div class="card">
    <div class="card-header"><span class="card-title">临时提示词</span></div>
    <textarea id="tempPrompt" placeholder="临时生效的提示词，仅下次AI请求使用，不保存到当前模式..."></textarea>
    <div class="hint">临时提示词自动保存，但不会覆盖当前模式的配置。可随时修改。</div>
  </div>
</div><div class="right-panel">
  <div class="card log-wrap">
    <div class="log-header">
      <span class="card-title">实时日志</span>
      <button id="clearLog">清空</button>
    </div>
    <div class="log-box" id="logBox"></div>
  </div>
</div>
</div>

<!-- 设置弹窗 -->
<div id="settingsModal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:999;align-items:center;justify-content:center">
  <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:24px;max-width:420px;width:90%;max-height:80vh;overflow-y:auto">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h2 style="margin:0;font-size:16px;color:#e6edf3">设置</h2>
      <button id="closeSettings" style="padding:4px 10px;border:1px solid #30363d;border-radius:4px;background:transparent;color:#8b949e;font-size:16px;cursor:pointer">✕</button>
    </div>

    <!-- OCR 引擎 -->
    <div class="card" style="margin-bottom:12px">
      <div class="card-header"><span class="card-title">OCR 引擎</span></div>
      <select id="ocrEngineSelect" style="width:100%;padding:7px 10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none">
        <option value="auto">自动选择</option>
        <option value="winocr">WinOCR (Windows原生)</option>
        <option value="xunfei">讯飞</option>
      </select>
      <div class="hint">切换后下次OCR请求即时生效</div>
    </div>

    <!-- OCR 测试 -->
    <div class="card" style="margin-bottom:12px">
      <div class="card-header"><span class="card-title">OCR 测试</span></div>
      <button id="ocrTestBtn" style="padding:8px 16px;border:none;border-radius:6px;font-size:13px;cursor:pointer;background:#238636;color:#fff;width:100%;margin-bottom:6px">运行 OCR 测试</button>
      <div id="ocrTestResult" style="font-size:12px;color:#8b949e;white-space:pre-wrap;display:none"></div>
    </div>

    <!-- AI 调试 -->
    <div class="card">
      <div class="card-header"><span class="card-title">AI 调试</span></div>
      <input id="testName" placeholder="联系人名称" style="width:100%;padding:7px 10px;margin-bottom:6px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none;box-sizing:border-box">
      <textarea id="testOcr" placeholder="OCR 识别到的对方消息..." style="width:100%;height:60px;padding:7px 10px;margin-bottom:6px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none;resize:vertical;box-sizing:border-box"></textarea>
      <button id="testAiBtn" style="padding:8px 16px;border:none;border-radius:6px;font-size:13px;cursor:pointer;background:#238636;color:#fff;width:100%;margin-bottom:6px">测试 AI 回复</button>
      <pre id="testResult" style="background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px;font-size:12px;color:#8b949e;white-space:pre-wrap;max-height:400px;overflow:auto;display:none"></pre>
    </div>
  </div>
</div>

<div id="saving" class="saving">保存中...</div>
<script>
const BASE='';
let currentMode='coding';
let modePrompts={};
let saveTimer=null;

// 加载配置
async function loadConfig(){
  try{
    const r=await fetch(BASE+'/config');
    const d=await r.json();
    if(d.status!=='ok') return;
    currentMode=d.active_mode||'other';
    modePrompts=d.modes||{};
    document.getElementById('autoToggle').checked=d.auto_reply===true;
    document.getElementById('tempPrompt').value=d.temp_prompt||'';
    document.getElementById('oledToggle').checked=d.oled_enabled===true;
    document.getElementById('ocrEngineSelect').value=d.ocr_engine||'auto';
    updateOledLabel(d.oled_enabled===true);
    updateStatusLabel(d.auto_reply===true);
    selectMode(currentMode);
  }catch(e){}
}

// 更新状态标签
function updateStatusLabel(on){
  const lbl=document.getElementById('statusLabel');
  lbl.textContent=on?'运行中':'已停止';
  lbl.className='toggle-label '+(on?'on':'off');
}

function updateOledLabel(on){
  const lbl=document.getElementById('oledLabel');
  lbl.textContent=on?'已开启':'已关闭';
  lbl.className='toggle-label '+(on?'on':'off');
}

// 切换模式
function selectMode(mode){
  // 先保存当前模式的提示词
  const oldPrompt=document.getElementById('promptArea').value;
  if(currentMode!==mode){
    fetch(BASE+'/config',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode:currentMode,prompt:oldPrompt})});
  }
  currentMode=mode;
  document.querySelectorAll('.mode-tab').forEach(t=>{
    t.classList.toggle('active',t.dataset.mode===mode);
  });
  const prompt=(modePrompts[mode]&&modePrompts[mode].prompt)||'';
  document.getElementById('promptArea').value=prompt;
  fetch(BASE+'/mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});
}

// OLED 黑屏开关
document.getElementById('oledToggle').onchange=async function(){
  const on=this.checked;
  updateOledLabel(on);
  try{
    await fetch(BASE+'/oled',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on})});
  }catch(e){}
};

// OCR 引擎切换
document.getElementById('ocrEngineSelect').onchange=function(){
  fetch(BASE+'/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ocr_engine:this.value})});
};

// 自动保存提示词（防抖），保存后同步到本地缓存
function autoSave(){
  clearTimeout(saveTimer);
  saveTimer=setTimeout(()=>{
    const el=document.getElementById('saving');el.classList.add('show');
    const prompt=document.getElementById('promptArea').value;
    const temp=document.getElementById('tempPrompt').value;
    // 先更新本地缓存
    if(!modePrompts[currentMode]) modePrompts[currentMode]={};
    modePrompts[currentMode].prompt=prompt;
    fetch(BASE+'/config',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode:currentMode,prompt,temp_prompt:temp})})
      .then(()=>setTimeout(()=>el.classList.remove('show'),800))
      .catch(()=>el.classList.remove('show'));
  },300);
}

// 切换自动回复
document.getElementById('autoToggle').onchange=async function(){
  const on=this.checked;
  updateStatusLabel(on);
  try{
    await fetch(BASE+'/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on})});
  }catch(e){}
};

// 模式标签点击
document.querySelectorAll('.mode-tab').forEach(tab=>{
  tab.onclick=()=>selectMode(tab.dataset.mode);
});

// 输入框防抖保存
document.getElementById('promptArea').oninput=autoSave;
document.getElementById('tempPrompt').oninput=autoSave;

// 清空日志
document.getElementById('clearLog').onclick=()=>{
  document.getElementById('logBox').innerHTML='';
  lastLogCount=0;
};

// 清除 AI 记忆
document.getElementById('clearMemBtn').onclick=async function(){
  if(!confirm('确定清除AI的对话记忆？')) return;
  this.disabled=true;this.textContent='清除中...';
  try{
    const r=await fetch(BASE+'/clear-memory',{method:'POST'});
    const d=await r.json();
    if(d.status==='ok') alert('✅ AI 记忆已清除');
    else alert('❌ 清除失败');
  }catch(e){alert('❌ 失败: '+e.message)}
  this.disabled=false;this.textContent='清除对话记忆';
};

// AI 调试
document.getElementById('testAiBtn').onclick=async function(){
  const name=document.getElementById('testName').value.trim();
  const ocr=document.getElementById('testOcr').value.trim();
  if(!ocr){alert('请输入 OCR 内容');return;}
  this.disabled=true;this.textContent='请求中...';
  const el=document.getElementById('testResult');el.style.display='block';el.textContent='发送请求...';
  try{
    const r=await fetch(BASE+'/test-ai',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({contact_name:name||'(未识别)',ocr_text:ocr})});
    const d=await r.json();
    if(d.status==='ok'){
      el.textContent='=== 发送给 API 的请求 ===\n'+
        JSON.stringify(d.request_messages,null,2)+
        '\n\n=== API 回复 ===\n'+d.response;
    }else{
      el.textContent='❌ 错误: '+(d.error||d.status);
    }
  }catch(e){
    el.textContent='❌ 请求失败: '+e.message;
  }
  this.disabled=false;this.textContent='测试 AI 回复';
};

// OCR 测试
document.getElementById('ocrTestBtn').onclick=async function(){
  this.disabled=true;this.textContent='OCR 测试中...（请勿操作鼠标键盘）';
  const el=document.getElementById('ocrTestResult');el.style.display='block';el.textContent='正在运行...';
  try{
    const r=await fetch(BASE+'/ocr-test',{method:'POST'});
    const d=await r.json();
    if(d.status==='ok'){
      el.innerHTML='名称区域:<br><img src="'+d.name_img+'" style="max-width:280px;border:1px solid #30363d;border-radius:4px;margin:4px 0"><br>'+
        '名称OCR: <b>'+d.name_text+'</b><br><br>'+
        '消息区域（带分区框）:<br><img src="'+d.chat_img+'" style="max-width:280px;border:1px solid #30363d;border-radius:4px;margin:4px 0"><br>'+
        '<pre style="background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:8px;font-size:12px;color:#8b949e;white-space:pre-wrap;margin:4px 0">'+(d.formatted||'')+'</pre>';
    }else{
      el.textContent='❌ 错误: '+(d.error||'');
    }
  }catch(e){
    el.textContent='❌ 请求失败: '+e.message;
  }
  this.disabled=false;this.textContent='运行 OCR 测试';
};

// 设置弹窗
document.getElementById('settingsBtn').onclick=()=>{
  document.getElementById('settingsModal').style.display='flex';
};
document.getElementById('closeSettings').onclick=()=>{
  document.getElementById('settingsModal').style.display='none';
};
document.getElementById('settingsModal').onclick=(e)=>{
  if(e.target===e.currentTarget) e.currentTarget.style.display='none';
};

// 日志轮询（增量追加）
let lastLogCount=0;
async function pollLogs(){
  try{
    const r=await fetch(BASE+'/logs?since='+lastLogCount);
    const d=await r.json();
    if(d.status==='ok'&&d.logs&&d.logs.length>0){
      const box=document.getElementById('logBox');
      d.logs.forEach(line=>{
        const div=document.createElement('div');
        div.textContent=line;
        if(line.includes('ERROR')||line.includes('错误')) div.className='error';
        else if(line.includes('WARNING')||line.includes('警告')) div.className='warn';
        else if(line.includes('✅')) div.className='success';
        else div.className='info';
        box.appendChild(div);
      });
      box.scrollTop=box.scrollHeight;
      lastLogCount=d.total;
    }
  }catch(e){}
  setTimeout(pollLogs,2000);
}

loadConfig();
pollLogs();
</script>
</body></html>'''


import threading
_config_lock = threading.Lock()


def _read_config():
    """安全读取 config_ai.json（带线程锁）"""
    for enc in ('utf-8', 'gbk', 'gb2312'):
        try:
            with _config_lock, open(AI_CONFIG, 'r', encoding=enc) as f:
                return json.load(f)
        except Exception:
            continue
    return {}


def _write_config(cfg):
    """写入 config_ai.json（带线程锁）"""
    with _config_lock, open(AI_CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def start_config_server():
    """启动轻量配置服务器"""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.parse

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/config':
                cfg = _read_config()
                self._json({
                    'status': 'ok',
                    'auto_reply': cfg.get('auto_reply', True),
                    'active_mode': cfg.get('active_mode', 'coding'),
                    'modes': cfg.get('modes', {}),
                    'temp_prompt': cfg.get('temp_prompt', ''),
                    'oled_enabled': cfg.get('oled_enabled', False),
                    'ocr_engine': cfg.get('ocr_engine', 'auto'),
                })
                return

            if self.path.startswith('/logs'):
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                since = int(qs.get('since', [0])[0])
                new_logs = _recent_logs[since:]
                self._json({'status': 'ok', 'logs': new_logs, 'total': len(_recent_logs)})
                return

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(ADMIN_PAGE.encode('utf-8'))

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            path = self.path

            if path == '/config':
                cfg = _read_config()
                mode = body.get('mode')
                prompt = body.get('prompt', '')
                if mode:
                    cfg.setdefault('modes', {})
                    cfg['modes'].setdefault(mode, {})
                    cfg['modes'][mode]['prompt'] = prompt
                # 临时提示词
                if 'temp_prompt' in body:
                    cfg['temp_prompt'] = body['temp_prompt']
                # OCR 引擎
                if 'ocr_engine' in body:
                    cfg['ocr_engine'] = body['ocr_engine']
                _write_config(cfg)
                self._json({'status': 'ok'})
                return

            if path == '/mode':
                cfg = _read_config()
                cfg['active_mode'] = body.get('mode', 'coding')
                _write_config(cfg)
                self._json({'status': 'ok'})
                return

            if path == '/toggle':
                cfg = _read_config()
                cfg['auto_reply'] = body.get('on', False)
                _write_config(cfg)
                self._json({'status': 'ok'})
                return

            if path == '/clear-memory':
                _chat_cache.clear()
                logger.info('AI 对话记忆已清除')
                self._json({'status': 'ok'})
                return

            if path == '/test-ai':
                cfg = _read_config()
                import requests as _req
                contact_name = body.get('contact_name', '(未知)')
                ocr_text = body.get('ocr_text', '')
                # 构建 system prompt（同 ai_decide 逻辑）
                system_prompt, contact_info = build_system_prompt(contact_name)
                active_mode = cfg.get('active_mode', 'other')
                extra = cfg.get('modes', {}).get(active_mode, {}).get('prompt', '').strip()
                temp = cfg.get('temp_prompt', '').strip()
                if temp:
                    extra = (extra + '\n' + temp) if extra else temp
                now = datetime.now()
                weekday_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
                time_tag = f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} ({weekday_names[now.weekday()]})"
                user_content = f"{time_tag}\n\n【当前对话】\n对方: {ocr_text}\n\n请回复对方最新一条消息。"
                if extra:
                    user_content = f"【用户提示】{extra}\n\n{user_content}"
                if contact_info:
                    user_content = f'以下是对该联系人的特殊回复提示，仅作为提示\n{contact_info}\n\n{user_content}'
                messages = [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_content},
                ]
                api_key = cfg.get('apiKey', '')
                if not api_key:
                    self._json({'status': 'error', 'error': 'API Key 未设置'})
                    return
                try:
                    resp = _req.post(
                        f"{cfg.get('baseUrl', 'https://api.deepseek.com')}/chat/completions",
                        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                        json={'model': cfg.get('model', 'deepseek-chat'), 'messages': messages},
                        timeout=30,
                    )
                    if resp.status_code == 200:
                        reply = resp.json()['choices'][0]['message']['content']
                        self._json({'status': 'ok', 'request_messages': messages, 'response': reply})
                    else:
                        self._json({'status': 'error', 'error': f'API {resp.status_code}: {resp.text[:200]}'})
                except Exception as e:
                    self._json({'status': 'error', 'error': str(e)})
                return

            if path == '/ocr-test':
                debug_dir = os.path.join(DIR, 'logs', 'debug_ocr')
                os.makedirs(debug_dir, exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                wx = find_wx()
                if not wx:
                    self._json({'status': 'error', 'error': '找不到微信窗口'})
                    return
                hwnd = _get_hwnd(wx)
                if hwnd:
                    _force_foreground(hwnd)
                else:
                    wx.SetActive()
                time.sleep(0.5)
                # OCR 名称
                name_img = screenshot_region(wx, *NAME_ZONE)
                name_img_path = os.path.join(debug_dir, f'name_{ts}.png')
                name_img.save(name_img_path)
                name_text = ocr_image(name_img).strip().split('\n')[0]
                name_clean = name_text.replace(' ', '').replace('\u3000', '')
                # OCR 消息（带调试框）
                from PIL import ImageDraw, ImageFont
                their_text, my_text, ordered_pairs, ocr_boxes = ocr_by_bubbles(chat_img := screenshot_region(wx, *CHAT_ZONE), return_boxes=True)
                draw = ImageDraw.Draw(chat_img)
                # 格式化结果用于前台显示
                formatted_conv = []
                for sender, text in ordered_pairs:
                    label = '对方' if sender == 'their' else '你'
                    formatted_conv.append(f'{label}: "{text.strip()}"')
                for box_cls, x1, y1, x2, y2 in ocr_boxes:
                    color = '#00ff00' if box_cls == 'my' else '#ff4444' if box_cls == 'their' else '#8888ff'
                    draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
                    # 标注文字
                    label = box_cls
                    draw.text((x1 + 3, y1 + 3), label, fill=color)
                chat_img_path = os.path.join(debug_dir, f'chat_{ts}.png')
                chat_img.save(chat_img_path)
                self._json({
                    'status': 'ok',
                    'name_text': name_clean or '(空)',
                    'their_text': their_text[:200] or '(空)',
                    'my_text': my_text[:200] or '(空)',
                    'formatted': '\n'.join(formatted_conv),
                    'name_img': f'/ocr-preview/{os.path.basename(name_img_path)}',
                    'chat_img': f'/ocr-preview/{os.path.basename(chat_img_path)}',
                    'saved_to': debug_dir,
                })
                return

            if path.startswith('/ocr-preview/'):
                fname = os.path.basename(path.split('/ocr-preview/')[1])
                fpath = os.path.join(DIR, 'logs', 'debug_ocr', fname)
                if os.path.exists(fpath):
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.end_headers()
                    with open(fpath, 'rb') as f:
                        self.wfile.write(f.read())
                else:
                    self._json({'status': 'error', 'error': 'file not found'})
                return

            if path == '/oled':
                cfg = _read_config()
                on = body.get('on', False)
                cfg['oled_enabled'] = on
                _write_config(cfg)
                if on:
                    _oled_saver.enabled = True
                    _oled_saver.show()
                else:
                    _oled_saver.hide()
                    _oled_saver.enabled = False
                    _oled_saver._shown = False
                    _oled_saver._close_browser()
                self._json({'status': 'ok'})
                return

        def _json(self, data):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(('127.0.0.1', CONFIG_PORT), Handler)
        logger.info(f'管理面板: http://127.0.0.1:{CONFIG_PORT}')
        server.serve_forever()
    except Exception as e:
        logger.warning(f'配置服务器启动失败（端口{CONFIG_PORT}可能被占用）: {e}')


# 日志收集（供管理面板使用）
_recent_logs = []
_MAX_LOG_LINES = 500


class LogCollector(logging.Handler):
    """将日志重定向到内存缓存，供管理面板实时读取（仅保留重要日志）"""
    _KEY_WORDS = ['检测到', '✅', '❌', 'ERROR', 'WARNING', '错误', '跳过', '回复',
                  'OCR', '联系人', '识别', '处理', '启动', 'AI回复', '自动回复',
                  '已回复', '发送', '异常', '失败', '找到WeChat', '未找到']

    def emit(self, record):
        global _recent_logs
        msg = self.format(record)
        # 只保留包含关键词的重要日志
        if any(k in msg for k in self._KEY_WORDS):
            _recent_logs.append(msg)
            if len(_recent_logs) > _MAX_LOG_LINES:
                _recent_logs = _recent_logs[-_MAX_LOG_LINES:]


# 安装日志收集器
_log_collector = LogCollector()
_log_collector.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(_log_collector)


def cfg_get(key, default=None):
    """快速读取 config_ai.json 的 key"""
    try:
        with open(AI_CONFIG, 'r', encoding='utf-8') as f:
            return json.load(f).get(key, default)
    except:
        return default


if __name__ == '__main__':
    if '--debug' in sys.argv:
        DEBUG = True
        logger.info('调试模式已启用，每个操作步骤将暂停等待确认')

    import threading
    # 启动配置web服务器（后台线程）
    threading.Thread(target=start_config_server, daemon=True).start()

    if '--diag' in sys.argv:
        diagnose()
    else:
        monitor()
