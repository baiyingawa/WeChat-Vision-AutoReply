"""
OCR 调试工具 - 截图并可视化气泡识别结果
"""
import sys, os, json, time
import pyautogui
import uiautomation as auto
from PIL import Image, ImageDraw

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
from agent import (find_wx, activate_wx, screenshot_region,
                   NAME_ZONE, CHAT_ZONE, SESSION_START_Y,
                   SESSION_HEIGHT, click_session)


def visualize():
    print('=' * 50)
    print('OCR 可视化调试')
    print('截取聊天区域 → 分析颜色块 → 标记可视化')
    print('=' * 50)

    wx = find_wx()
    if not wx:
        print('❌ 找不到微信窗口')
        return

    activate_wx(wx)
    time.sleep(0.5)
    click_session(wx, 0)

    # 截图聊天区域
    img = screenshot_region(wx, *CHAT_ZONE)
    w, h = img.size
    pixels = img.load()

    # 加载颜色配置
    calib_path = os.path.join(DIR, 'calib.json')
    calib = {}
    if os.path.exists(calib_path):
        with open(calib_path, 'r') as f:
            calib = json.load(f)

    my_color = calib.get('my_color', {})
    bg_color = calib.get('bg_color', {})

    mr, mg, mb = my_color.get('r', 148), my_color.get('g', 236), my_color.get('b', 105)
    br, bg_val, bb = bg_color.get('r', 240), bg_color.get('g', 240), bg_color.get('b', 240)

    def is_my(r, g, b):
        return abs(g - mg) < 50 and g > r + 15 and g > b + 15

    def is_bg(r, g, b):
        return abs(r - br) < 8 and abs(g - bg_val) < 8 and abs(b - bb) < 8

    def is_dark_text(r, g, b):
        return (r + g + b) < 400 and not is_bg(r, g, b)

    # ============================================================
    #  创建可视化图
    # ============================================================
    vis = img.copy()
    draw = ImageDraw.Draw(vis)
    scan_step = 4

    blocks = []
    current_class = None
    block_start = 0

    line_colors_y = []  # 每行的颜色标记（用于显示）

    for y in range(0, h, scan_step):
        has_green = False
        has_text = False
        all_bg = True

        for x in range(0, w, 4):
            try:
                r, gv, b = pixels[x, y][:3]
                if is_my(r, gv, b):
                    has_green = True
                    all_bg = False
                elif is_dark_text(r, gv, b):
                    has_text = True
                    all_bg = False
            except:
                pass

        if has_green:
            line_class = 'my'
        elif has_text:
            line_class = 'their'
        elif all_bg:
            line_class = 'bg'
        else:
            line_class = current_class or 'bg'

        line_colors_y.append((y, line_class))

        if line_class != current_class:
            if current_class and current_class != 'bg':
                blocks.append((block_start, y, current_class))
            block_start = y
            current_class = line_class

    if current_class and current_class != 'bg':
        blocks.append((block_start, h, current_class))

    # 画分块框
    for start_y, end_y, cls in blocks:
        color = '#00ff00' if cls == 'my' else '#0088ff'
        draw.rectangle([0, start_y, w-1, end_y], outline=color, width=2)
        draw.text((5, start_y + 2), f'{cls}', fill=color)

    # 画扫描线颜色标记（左侧窄条）
    for y, cls in line_colors_y:
        dot_color = '#00ff00' if cls == 'my' else '#0088ff' if cls == 'their' else '#444444'
        draw.point((3, y), fill=dot_color)

    # 保存
    out_path = os.path.join(DIR, 'ocr_debug.png')
    vis.save(out_path)

    # 打印统计
    my_count = sum(1 for _, c in line_colors_y if c == 'my')
    their_count = sum(1 for _, c in line_colors_y if c == 'their')
    bg_count = sum(1 for _, c in line_colors_y if c == 'bg')

    print(f'\n图片: {w}x{h}')
    print(f'我方行: {my_count}  |  对方行: {their_count}  |  背景行: {bg_count}')
    print(f'检测到 {len(blocks)} 个消息块')
    print(f'\n可视化保存: {out_path}')
    print('\n打开图片查看绿色=我方, 蓝色=对方, 灰色=背景')


if __name__ == '__main__':
    visualize()
