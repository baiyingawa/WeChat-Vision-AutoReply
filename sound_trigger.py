"""
SoundTrigger - 通过 pycaw 事件回调检测微信提示音
不轮询，Windows 主动通知
"""
import time, logging, threading
import psutil

logger = logging.getLogger('SoundTrigger')

try:
    from comtypes import COMObject
    from pycaw.utils import AudioUtilities
    from pycaw.api.audiopolicy import IAudioSessionEvents
    _HAS_PYCAW = True
except Exception as e:
    _HAS_PYCAW = False
    logger.warning(f'pycaw加载失败: {e}')


if _HAS_PYCAW:
    class WeChatSoundSink(COMObject):
        """COM 回调对象 - Windows 在音频状态变化时调用"""
        _com_interfaces_ = [IAudioSessionEvents]

        def __init__(self, callback, pid):
            super().__init__()
            self._user_callback = callback
            self._pid = pid
            self._last_state = None

        def IAudioSessionEvents_OnStateChanged(self, new_state):
            """有音频 session 状态变化时触发"""
            if new_state == 1:  # Active - 正在播放声音
                logger.info(f'音频状态变化 -> Active (PID={self._pid})')
                if self._user_callback and self._pid > 0:
                    try:
                        proc = psutil.Process(self._pid)
                        if 'wechat' in proc.name().lower() or 'weixin' in proc.name().lower():
                            logger.info(f'✅ 检测到微信提示音 (PID={self._pid})')
                            self._user_callback()
                    except:
                        pass
            self._last_state = new_state
            return 0  # S_OK

        def IAudioSessionEvents_OnDisplayNameChanged(self, new_name, ctx):
            return 0
        def IAudioSessionEvents_OnIconPathChanged(self, path, ctx):
            return 0
        def IAudioSessionEvents_OnSimpleVolumeChanged(self, vol, mute, ctx):
            return 0
        def IAudioSessionEvents_OnChannelVolumeChanged(self, cnt, arr, ch, ctx):
            return 0
        def IAudioSessionEvents_OnGroupingParamChanged(self, gp, ctx):
            return 0
        def IAudioSessionEvents_OnSessionDisconnected(self, reason):
            return 0


class SoundTrigger:
    def __init__(self):
        self._sinks = []       # list of (session, sink)

    def get_wechat_sessions(self):
        """找到 WeChat 的所有音频 session"""
        if not _HAS_PYCAW:
            return []
        results = []
        try:
            sessions = AudioUtilities.GetAllSessions()
            for s in sessions:
                try:
                    pid = s.ProcessId
                    if pid <= 0:
                        continue
                    proc = psutil.Process(pid)
                    name = proc.name().lower()
                    if 'wechat' in name or 'weixin' in name:
                        logger.info(f'找到WeChat音频会话: PID={pid}')
                        results.append(s)
                except:
                    continue
        except Exception as e:
            logger.warning(f'查找WeChat session失败: {e}')
        return results

    def start(self, callback=None):
        """在所有 WeChat 音频 session 上注册事件回调"""
        if not _HAS_PYCAW:
            logger.error('pycaw不可用')
            return

        sessions = self.get_wechat_sessions()
        if not sessions:
            logger.warning('未找到WeChat音频会话，将重试...')
            def retry():
                time.sleep(2)
                self.start(callback)
            t = threading.Thread(target=retry, daemon=True)
            t.start()
            return

        for session in sessions:
            pid = session.ProcessId
            sink = WeChatSoundSink(callback, pid)
            try:
                session.register_notification(sink)
                self._sinks.append((session, sink))
                logger.info(f'已注册回调: PID={pid}')
            except Exception as e:
                logger.warning(f'注册回调失败 PID={pid}: {e}')

        logger.info(f'音频事件回调注册完成 ({len(self._sinks)}个session)，等待提示音...')

    def stop(self):
        """取消所有回调注册"""
        for session, sink in self._sinks:
            try:
                session.unregister_notification()
            except:
                pass
        self._sinks = []

    def __del__(self):
        self.stop()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    trigger = SoundTrigger()

    def on_sound():
        print(f'[{time.strftime("%H:%M:%S")}] ✅ 检测到微信提示音!')

    trigger.start(callback=on_sound)
    print('监听已启动，请给自己发微信消息... 按 Ctrl+C 退出')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        trigger.stop()
        print('\n停止监听')
