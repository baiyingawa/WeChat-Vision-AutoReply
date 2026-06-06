"""
WeChatListener - 消息监听 + SSE + AI 自动回复 + 配置页面
支持 OpenClaw (WebSocket) 与标准 OpenAI API 两种后端
"""
import requests
import time
import json
import threading
import logging
import subprocess
import os
import asyncio
import uuid
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('Listener')

CIPHER_BASE = 'http://127.0.0.1:5031'
TOKEN = 'uu233TAT'
CIPHER_HEADERS = {'Authorization': f'Bearer {TOKEN}'}
POLL_INTERVAL = 2
LISTEN_PORT = 5034

sse_clients = []
sse_lock = threading.Lock()
last_timestamps = defaultdict(int)
bootstrapped = False

DIR = os.path.dirname(os.path.abspath(__file__))
WECHAT_AUTO_CLI = os.path.join(DIR, 'scripts', 'cli.py')
PYTHON = r'C:\Users\Yu\AppData\Local\Programs\Python\Python310\python.exe'
CONFIG_AI = os.path.join(DIR, 'config_ai.json')

# === AI 配置 ===
DEFAULT_CONFIG = {
    'enabled': False,
    'apiKey': '',
    'baseUrl': 'https://api.deepseek.com',
    'model': 'deepseek-chat',
    'systemPrompt': '你是一个智能助手，请用简洁自然的中文回复。',
    'maxHistory': 6,
    'openclawEnabled': False,
    'openclawGateway': 'ws://localhost:18789',
    'openclawSessionKey': 'wechat-auto-reply',
}

ai_config = dict(DEFAULT_CONFIG)


def load_config():
    global ai_config
    try:
        if os.path.exists(CONFIG_AI):
            with open(CONFIG_AI, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 加载标准配置项
                for k in DEFAULT_CONFIG:
                    if k in data:
                        ai_config[k] = data[k]
                # 加载额外状态字段
                for k in data:
                    if k.startswith('_'):
                        ai_config[k] = data[k]
    except Exception as e:
        logger.warning(f'加载AI配置失败: {e}')


def save_config(data):
    global ai_config
    ai_config.update(data)
    with open(CONFIG_AI, 'w', encoding='utf-8') as f:
        json.dump(ai_config, f, ensure_ascii=False, indent=2)
    logger.info('AI配置已保存')


load_config()

# === OpenClaw WebSocket AI (async) ===

async def _openclaw_send(session_key, message, gateway_url):
    """通过 WebSocket 发送消息到 OpenClaw 会话并获取回复"""
    import websockets
    logger.info(f'[DEBUG] 正在连接 OpenClaw Gateway: {gateway_url}')
    async with websockets.connect(gateway_url, max_size=2**20, close_timeout=10) as ws:
        await ws.send(json.dumps({
            "type": "chat.send",
            "params": {
                "sessionKey": session_key,
                "message": message,
                "idempotencyKey": str(uuid.uuid4()),
                "deliver": False,
                "timeoutMs": 60000,
            }
        }))

        reply = ""
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            ev = data.get("event", {})
            state = ev.get("state")

            if state == "delta":
                reply += ev.get("deltaText", "")
            elif state == "final":
                reply += ev.get("message", "")
                break
            elif state == "aborted":
                if reply:
                    break
                logger.warning(f"OpenClaw 回复被中断: {ev.get('stopReason', '未知')}")
                return None
            elif state == "error":
                logger.error(f"OpenClaw 错误: {ev.get('message', '未知错误')}")
                return None

        return reply.strip()


def ai_reply_openclaw(display_name, content):
    """向 OpenClaw 会话转发微信消息，并获取 AI 回复"""
    session_key = ai_config.get('openclawSessionKey', 'wechat-auto-reply')
    gateway = ai_config.get('openclawGateway', 'ws://localhost:18789')
    forward_msg = f"【wechat转发】{display_name}: {content}"
    logger.info(f'[DEBUG] ai_reply_openclaw: session={session_key} gateway={gateway}')
    try:
        # 检查 websockets 是否可用
        try:
            import websockets
            logger.info('[DEBUG] websockets 库可用')
        except ImportError:
            logger.error('[DEBUG] websockets 库未安装！请在 Windows 执行: pip install websockets')
            return None
        result = asyncio.run(_openclaw_send(session_key, forward_msg, gateway))
        logger.info(f'[DEBUG] OpenClaw 回复: {result}')
        return result
    except Exception as e:
        logger.error(f'[DEBUG] OpenClaw WebSocket 异常: {e}')
        return None


# === 标准 OpenAI API AI ===
def ai_reply_api(messages):
    """通过 OpenAI 兼容 API 获取 AI 回复"""
    if not ai_config.get('apiKey'):
        return None

    try:
        headers = {
            'Authorization': f"Bearer {ai_config['apiKey']}",
            'Content-Type': 'application/json',
        }
        body = {
            'model': ai_config['model'],
            'messages': [{'role': 'system', 'content': ai_config['systemPrompt']}] + messages,
            'temperature': 0.7,
            'max_tokens': 500,
        }
        r = requests.post(
            f"{ai_config['baseUrl'].rstrip('/')}/chat/completions",
            headers=headers, json=body, timeout=30
        )
        if r.status_code == 200:
            return r.json()['choices'][0]['message']['content'].strip()
        logger.warning(f'AI API 错误: {r.status_code} {r.text[:200]}')
    except Exception as e:
        logger.error(f'AI API 异常: {e}')
    return None


def ai_reply_text(context_messages):
    """统一入口：根据配置选择 OpenClaw 或标准 API"""
    logger.info(f'[DEBUG] ai_reply_text 被调用, enabled={ai_config.get("enabled")}, openclawEnabled={ai_config.get("openclawEnabled")}')
    if not ai_config['enabled']:
        logger.warning('[DEBUG] enabled=False, 跳过 AI 回复')
        return None

    if ai_config.get('openclawEnabled'):
        # OpenClaw 模式：取最新一条非自己消息转发到专用会话
        display_name = context_messages.get('displayName', '未知')
        content = ''
        for m in reversed(context_messages.get('messages', [])):
            if not m.get('isSelf'):
                content = m.get('content', '') or '[非文本消息]'
                break
        logger.info(f'[DEBUG] OpenClaw 模式: 转发 {display_name}: {content[:30]}')
        return ai_reply_openclaw(display_name, content)

    # 标准 API 模式：构建对话上下文发送到外部 API
    msgs_for_ai = []
    for m in context_messages.get('messages', []):
        role = 'user' if not m.get('isSelf') else 'assistant'
        content = m.get('content', '') or '[非文本消息]'
        msgs_for_ai.append({'role': role, 'content': content})
    return ai_reply_api(msgs_for_ai)


def auto_reply(display_name, session_id, context_messages):
    context = {
        'displayName': display_name,
        'sessionId': session_id,
        'messages': context_messages,
    }
    reply_text = ai_reply_text(context)
    if not reply_text:
        reply_text = '[Ai]busy'
    else:
        reply_text = f'[Ai]{reply_text}'

    try:
        result = subprocess.run(
            [PYTHON, WECHAT_AUTO_CLI, '--to', display_name,
             '--content', reply_text, '--json'],
            capture_output=True, text=True, timeout=30,
            cwd=DIR
        )
        tag = 'OpenClaw' if ai_config.get('openclawEnabled') else ('AI' if ai_config['enabled'] else 'Test')
        if result.returncode == 0:
            logger.info(f"[{tag}] 回复 {display_name}: {reply_text[:50]}")
        else:
            logger.warning(f"[{tag}] 回复 {display_name} 失败: {result.stdout.strip()[:100]}")
    except Exception as e:
        logger.error(f"回复异常: {e}")


def fetch_json(url):
    try:
        r = requests.get(url, headers=CIPHER_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning(f'请求失败 {url}: {e}')
    return None


def broadcast(event, data):
    msg = f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
    with sse_lock:
        dead = []
        for client in sse_clients:
            try:
                client.write(msg.encode())
                client.flush()
            except:
                dead.append(client)
        for c in dead:
            sse_clients.remove(c)


def poll_loop():
    global bootstrapped
    logger.info('开始轮询 CipherTalk...')
    while True:
        try:
            data = fetch_json(f'{CIPHER_BASE}/v1/sessions?limit=5')
            if not data or not data.get('success'):
                time.sleep(POLL_INTERVAL)
                continue

            sessions = data['data'].get('sessions', [])
            for sess in sessions:
                sid = sess.get('username', '')
                last_ts = int(sess.get('lastTimestamp', 0))
                prev_ts = last_timestamps.get(sid, 0)

                if last_ts > prev_ts:
                    msg_data = fetch_json(
                        f'{CIPHER_BASE}/v1/messages?sessionId={sid}&limit=5&sort=createTime_desc'
                    )
                    if msg_data and msg_data.get('success'):
                        msgs = msg_data['data'].get('messages', [])
                        if msgs:
                            msgs.reverse()
                            push = {
                                'sessionId': sid,
                                'displayName': sess.get('displayName', ''),
                                'messages': [{
                                    'content': m.get('parsedContent', ''),
                                    'timestamp': m.get('createTime', 0),
                                    'direction': m.get('direction', ''),
                                    'isSelf': m.get('sender', {}).get('isSelf', False),
                                } for m in msgs],
                            }
                            broadcast('message.new', push)
                            latest = msgs[-1].get('parsedContent', '')[:50]
                            logger.info(f"新消息 from {push['displayName']}: {latest}")

                            if bootstrapped and not msgs[-1].get('sender', {}).get('isSelf', False):
                                threading.Thread(
                                    target=auto_reply,
                                    args=(push['displayName'], sid, push['messages']),
                                    daemon=True
                                ).start()

                            # /test 触发：uu 发任何含 /test 的消息都触发回复
                            if bootstrapped and push['displayName'] == 'uu':
                                last_content = msgs[-1].get('parsedContent', '')
                                if '/test' in last_content:
                                    threading.Thread(
                                        target=auto_reply,
                                        args=(push['displayName'], sid, push['messages']),
                                        daemon=True
                                    ).start()

                    last_timestamps[sid] = last_ts

            if not bootstrapped:
                bootstrapped = True
                logger.info('首次同步完成')
        except Exception as e:
            logger.error(f'轮询异常: {e}')
        time.sleep(POLL_INTERVAL)


# === 管理页面 HTML ===
ADMIN_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>WeChatAuto 管理后台</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Microsoft YaHei',sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin-bottom:16px;max-width:500px}
h2{font-size:16px;margin-bottom:16px;color:#58a6ff}
label{display:block;font-size:13px;color:#8b949e;margin:10px 0 4px}
input,select,textarea{width:100%;padding:8px 10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none}
input:focus,textarea:focus{border-color:#58a6ff}
textarea{height:70px;resize:vertical}
.btn{padding:8px 20px;border:none;border-radius:6px;font-size:13px;cursor:pointer;margin-top:12px}
.btn-primary{background:#238636;color:#fff}
.btn-primary:hover{background:#2ea043}
.btn-primary:disabled{opacity:.5;cursor:not-allowed}
.switch{position:relative;display:inline-block;width:44px;height:24px;margin:8px 0}
.switch input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#30363d;border-radius:24px;transition:.3s}
.slider:before{content:"";position:absolute;height:18px;width:18px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.3s}
input:checked+.slider{background:#238636}
input:checked+.slider:before{transform:translateX(20px)}
.status{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px}
.status.ok{background:#23863633;color:#3fb950;border:1px solid #238636}
.status.off{background:#da363333;color:#f85149;border:1px solid #da3633}
#toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);padding:10px 24px;border-radius:8px;font-size:13px;display:none;z-index:99}
#toast.show{display:block}
#toast.success{background:#238636;color:#fff}
#toast.error{background:#da3633;color:#fff}
</style></head>
<body>

<div style="display:flex;justify-content:space-between;align-items:center;max-width:500px;margin-bottom:12px">
  <h2 style="margin:0">WeChatAuto 配置</h2>
  <span id="statusBadge" class="status ok">关闭</span>
</div>

<div class="card" style="border-color:#58a6ff">
  <label style="display:flex;align-items:center;gap:10px;margin:0">
    <span style="color:#58a6ff;font-weight:700;font-size:15px">\u2601 OpenClaw / Breeze</span>
    <label class="switch" style="margin:0">
      <input type="checkbox" id="openclawEnabled">
      <span class="slider"></span>
    </label>
  </label>
  <div id="openclawSettings" style="margin-top:14px">
    <label>Gateway 地址</label>
    <input id="openclawGateway" placeholder="ws://localhost:18789">
    <label>会话标识</label>
    <input id="openclawSessionKey" placeholder="wechat-auto-reply">
    <div style="font-size:12px;color:#8b949e;margin-top:8px">
      WSL 的 OpenClaw 可通过 ws://localhost:18789 直连
      | <a href="http://localhost:18789" target="_blank" style="color:#58a6ff">控制台</a> 查看会话
      | 在会话里写入你的个人信息决定语气
    </div>
  </div>
</div>

<div class="card">
  <div style="color:#8b949e;font-size:13px;margin-bottom:8px">状态</div>
  <div id="infoCipher">CipherTalk: --</div>
  <div id="infoSSE">SSE客户端: --</div>
  <div id="infoAI">AI: --</div>
</div>

<button class="btn btn-primary" id="saveBtn">保存配置</button>

<div id="toast"></div>

<script>
const BASE = '';
async function loadConfig() {
  try {
    const r = await fetch(BASE + '/config');
    const d = await r.json();
    if (d.status === 'ok') {
      const c = d.config;
      const on = c.enabled && c.openclawEnabled;
      document.getElementById('openclawEnabled').checked = on;
      document.getElementById('openclawGateway').value = c.openclawGateway || 'ws://localhost:18789';
      document.getElementById('openclawSessionKey').value = c.openclawSessionKey || 'wechat-auto-reply';
      updateBadge(on);
    }
  } catch(e){}
}
function updateBadge(on) {
  const badge = document.getElementById('statusBadge');
  if (!on) {
    badge.textContent = '\u25cb \u5df2\u5173\u95ed';
    badge.className = 'status off';
  } else {
    badge.textContent = '\u2601 Breeze';
    badge.className = 'status ok';
  }
}
async function loadStatus() {
  try {
    const r = await fetch(BASE + '/health');
    const d = await r.json();
    document.getElementById('infoCipher').textContent = 'CipherTalk: \u25cf 运行中';
    document.getElementById('infoSSE').textContent = 'SSE客户端: ' + (d.clients || 0);
    const on = document.getElementById('openclawEnabled').checked;
    document.getElementById('infoAI').textContent = 'AI: ' + (on ? '\u25cf 已启用' : '\u25cb 未启用');
  } catch(e){
    document.getElementById('infoCipher').textContent = 'CipherTalk: --';
  }
}
function toast(msg, type) {
  const el = document.getElementById('toast');
  el.textContent = msg; el.className = 'show ' + type;
  setTimeout(() => el.className = '', 2500);
}
document.getElementById('openclawEnabled').onchange = function() {
  updateBadge(this.checked);
};
document.getElementById('saveBtn').onclick = async () => {
  const btn = document.getElementById('saveBtn');
  btn.disabled = true; btn.textContent = '保存中...';
  try {
    const r = await fetch(BASE + '/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        enabled: document.getElementById('openclawEnabled').checked,
        openclawEnabled: document.getElementById('openclawEnabled').checked,
        openclawGateway: document.getElementById('openclawGateway').value,
        openclawSessionKey: document.getElementById('openclawSessionKey').value || 'wechat-auto-reply',
      })
    });
    const d = await r.json();
    if (d.status === 'ok') {
      toast('保存成功', 'success');
      updateBadge(d.config.enabled && d.config.openclawEnabled);
    } else { toast('保存失败: ' + (d.error||''), 'error'); }
  } catch(e) { toast('保存失败: ' + e.message, 'error'); }
  btn.disabled = false; btn.textContent = '保存配置';
};
loadConfig(); loadStatus();
setInterval(loadStatus, 5000);
</script>
</body></html>'''


# === HTTP 路由 ===
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == '/push/messages':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with sse_lock:
                sse_clients.append(self.wfile)
            self.wfile.write(b'event: connected\ndata: {"status":"ok"}\n\n')
            self.wfile.flush()
            try:
                while True:
                    time.sleep(30)
                    try:
                        self.wfile.write(b': ping\n\n')
                        self.wfile.flush()
                    except:
                        break
            except:
                pass
            finally:
                with sse_lock:
                    if self.wfile in sse_clients:
                        sse_clients.remove(self.wfile)
            return

        if path == '/health':
            self.json_response({'status': 'ok', 'clients': len(sse_clients)})
            return

        if path == '/config':
            self.json_response({'status': 'ok', 'config': ai_config})
            return

        if path == '/check':
            # 诊断信息
            info = {
                'enabled': ai_config.get('enabled', False),
                'openclawEnabled': ai_config.get('openclawEnabled', False),
                'gateway': ai_config.get('openclawGateway', ''),
                'sessionKey': ai_config.get('openclawSessionKey', ''),
                'websockets_ok': False,
            }
            try:
                import websockets
                info['websockets_ok'] = True
            except ImportError:
                info['websockets_ok'] = False
            self.json_response({'status': 'ok', 'diagnostics': info})
            return

        if path == '/admin.html' or path == '/admin' or path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(ADMIN_HTML.encode('utf-8'))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path == '/config':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                save_config(data)
                self.json_response({'status': 'ok', 'config': ai_config})
            except Exception as e:
                self.json_response({'status': 'error', 'error': str(e)}, 400)
            return

        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def json_response(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        pass


def main():
    poller = threading.Thread(target=poll_loop, daemon=True)
    poller.start()

    server = HTTPServer(('127.0.0.1', LISTEN_PORT), Handler)
    logger.info(f'服务启动 http://127.0.0.1:{LISTEN_PORT}')
    logger.info(f'  SSE推送: /push/messages')
    logger.info(f'  管理页面: http://127.0.0.1:{LISTEN_PORT}/admin')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info('服务停止')


if __name__ == '__main__':
    main()
