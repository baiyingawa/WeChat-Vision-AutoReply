"""
SoundTrigger 快速测试 - 事件回调模式
"""
import time, logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

from sound_trigger import SoundTrigger

trigger = SoundTrigger()

def on_sound():
    print(f'[{time.strftime("%H:%M:%S")}] ✅ 检测到微信提示音!')

print('请给自己发一条微信消息（触发提示音）')
print('按 Ctrl+C 退出\n')

trigger.start(callback=on_sound)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    trigger.stop()
    print('\n测试结束')
