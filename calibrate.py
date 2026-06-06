"""
WeChatAuto 坐标校准工具
按照提示点击微信窗口对应区域，自动计算并写入 agent.py
"""
import sys, os, time, json, re
import pyautogui
import uiautomation as auto
from PIL import ImageGrab

DIR = os.path.dirname(os.path.abspath(__file__))
CALIB_FILE = os.path.join(DIR, 'calib.json')
AGENT_FILE = os.path.join(DIR, 'agent.py')


def find_wx():
    wx = auto.WindowControl(searchDepth=15, Name='\u5fae\u4fe1')
    if wx.Exists(0, 0):
        return wx
    auto.SendKeys('{Ctrl}{Alt}\\', waitTime=0.1)
    time.sleep(1)
    wx = auto.WindowControl(searchDepth=15, Name='\u5fae\u4fe1')
    return wx if wx.Exists(0, 0) else None


def wait_click(prompt, show_rect=True):
    r = wx.BoundingRectangle
    if show_rect:
        print(f'  窗口大小: {r.right-r.left}x{r.bottom-r.top}')
    print(f'  👆 {prompt}')
    print(f'  鼠标移到目标位置后点击，然后按 Enter 确认')
    input('  > ')
    x, y = pyautogui.position()
    if r.left <= x <= r.right and r.top <= y <= r.bottom:
        rel_x, rel_y = x - r.left, y - r.top
        print(f'  ✅ 相对位置 ({rel_x}, {rel_y})')
        return rel_x, rel_y
    print('  ❌ 不在微信窗口内，重试')
    return wait_click(prompt, False)


def apply_to_agent(calib):
    """将校准值写入 agent.py"""
    with open(AGENT_FILE, 'r', encoding='utf-8') as f:
        code = f.read()

    replacements = {
        'SESSION_START_Y': str(calib['session_start_y']),
        'SESSION_HEIGHT': str(calib['session_height']),
        'TARGET_INDEX': '2',
    }

    for name, val in replacements.items():
        code = re.sub(
            rf'^{name}\s*=\s*\d+',
            f'{name} = {val}',
            code,
            flags=re.MULTILINE
        )

    # 写入区域常量
    nz = calib['name_zone']  # [l, t, r, b]
    cz = calib['chat_zone']  # [l, t, r, b]

    with open(AGENT_FILE, 'w', encoding='utf-8') as f:
        f.write(code)

    # 另存 calib.json 供运行时读取
    with open(CALIB_FILE, 'w', encoding='utf-8') as f:
        json.dump(calib, f, indent=2, ensure_ascii=False)

    print(f'\n✅ 已更新 {AGENT_FILE} 和 {CALIB_FILE}')
    print(f'   SESSION_START_Y = {calib["session_start_y"]}')
    print(f'   SESSION_HEIGHT = {calib["session_height"]}')
    print(f'   名称区域: {nz}')
    print(f'   聊天区域: {cz}')
    print(f'   窗口大小: {calib["window_size"]}')


if __name__ == '__main__':
    print('=' * 55)
    print('  WeChatAuto 坐标校准工具')
    print('  按提示依次点击微信窗口的对应位置')
    print('  每次点击后按 Enter 确认')
    print('=' * 55)
    print()

    wx = find_wx()
    if not wx:
        print('❌ 找不到微信窗口')
        sys.exit(1)

    wx.SetActive()
    time.sleep(0.5)
    r = wx.BoundingRectangle

    print(f'微信窗口: {r.right-r.left}x{r.bottom-r.top}')
    print()
    print('选择要校准的步骤（输入编号，可组合）:')
    print('  0 = 全部步骤')
    print('  1 = 第1个会话位置（点击目标定位）')
    print('  2 = 第3个会话位置')
    print('  3 = 名称区域左上角')
    print('  4 = 名称区域右下角')
    print('  5 = 我方气泡颜色（绿色）')
    print('  6 = 对方气泡颜色（白色）')
    print('  7 = 聊天背景颜色')
    print('  8 = 聊天区域左上角')
    print('  9 = 聊天区域右下角')
    print('  10 = 输入框位置')
    print('  11 = 第12个会话位置（复位用）')
    print()
    print('  示例:')
    print('    0        → 全部校准')
    print('    1-4      → 只做1~4步')
    print('    5,6,7    → 只做5~7步')
    print('    1-4,8-9  → 做1~4和8~9步')
    print()

    sel = input('请输入: ').strip()
    if not sel or sel == '0':
        selected = set(range(1, 12))
    else:
        selected = set()
        for part in sel.split(','):
            part = part.strip()
            if '-' in part:
                a, b = part.split('-', 1)
                selected.update(range(int(a), int(b) + 1))
            else:
                selected.add(int(part))

    skip_steps = set(range(1, 10)) - selected
    if skip_steps:
        print(f'⏭️  跳过步骤: {", ".join(map(str, sorted(skip_steps)))}')
    else:
        print('✅ 执行全部步骤')
    print()

    input('按 Enter 开始校准...\n')
    input('👉 请先点击一个会话，让聊天区域显示完整内容，然后按 Enter 继续...\n')

    results = {}
    # 尝试加载已有的结果（用于跳过步骤时保留之前的数据）
    if os.path.exists(CALIB_FILE):
        try:
            with open(CALIB_FILE, 'r') as f:
                old = json.load(f)
            results['session_1'] = tuple(old.get('_raw', {}).get('session_1', (0, 0)))
            results['session_3'] = tuple(old.get('_raw', {}).get('session_3', (0, 0)))
            results['session_12'] = tuple(old.get('_raw', {}).get('session_12', (0, 0)))
            results['name_lt'] = tuple(old.get('_raw', {}).get('name_lt', (0, 0)))
            results['name_rb'] = tuple(old.get('_raw', {}).get('name_rb', (0, 0)))
            results['my_msg_pos'] = tuple(old.get('_raw', {}).get('my_msg_pos', (0, 0)))
            results['their_msg_pos'] = tuple(old.get('_raw', {}).get('their_msg_pos', (0, 0)))
            results['bg_pos'] = tuple(old.get('_raw', {}).get('bg_pos', (0, 0)))
            results['chat_lt'] = tuple(old.get('_raw', {}).get('chat_lt', (0, 0)))
            results['chat_rb'] = tuple(old.get('_raw', {}).get('chat_rb', (0, 0)))
        except:
            pass

    def should_skip(step):
        return step in skip_steps

    # 1) 第1个会话
    if not should_skip(1):
        print('\n【第1步】第1个会话')
        print('  目的：确定会话列表起始位置')
        c1x, c1y = wait_click('请点击会话列表最上面第一个联系人或群聊')
        results['session_1'] = (c1x, c1y)
    else:
        print('⏭️ 跳过第1步')

    # 2) 第3个会话
    if not should_skip(2):
        print('\n【第2步】第3个会话')
        print('  目的：定位要点击的目标位置')
        c3x, c3y = wait_click('请点击第3个会话（往下数第3个）')
        results['session_3'] = (c3x, c3y)
    else:
        print('\n⏭️ 跳过第2步')

    # 3) 名称区域 - 左上角
    if not should_skip(3):
        print('\n【第3步】名称区域 - 左上角')
        print('  目的：识别当前处于哪个联系人的聊天')
        nlx, nty = wait_click('请点击聊天窗口顶部"联系人名称"的左上角')
        results['name_lt'] = (nlx, nty)
    else:
        print('\n⏭️ 跳过第3步')

    # 4) 名称区域 - 右下角
    if not should_skip(4):
        print('\n【第4步】名称区域 - 右下角')
        nrx, nby = wait_click('请点击"联系人名称"的右下角（确保圈住整个名称）')
        results['name_rb'] = (nrx, nby)
    else:
        print('\n⏭️ 跳过第4步')

    my_color = {}
    their_color = {}
    bg_color = {}

    # 5) 我方消息（绿色气泡）颜色校准
    if not should_skip(5):
        print('\n【第5步】我方消息颜色校准')
        print('  目的：采样绿色气泡的RGB值，用于自动分界')
        mx, my = wait_click('请点击你最近发出的一条消息"气泡背景"（绿色区域中间）')
        results['my_msg_pos'] = (mx, my)
        # 立即采样颜色
        try:
            r = wx.BoundingRectangle
            mx_abs, my_abs = r.left + mx, r.top + my
            px = ImageGrab.grab().getpixel((mx_abs, my_abs))
            print(f'  采样RGB: ({px[0]}, {px[1]}, {px[2]})')
            my_color = {'r': int(px[0]), 'g': int(px[1]), 'b': int(px[2])}
        except Exception as e:
            print(f'  ⚠️ 采样失败: {e}')
    else:
        print('\n⏭️ 跳过第5步')

    # 6) 对方消息（白色气泡）颜色校准
    if not should_skip(6):
        print('\n【第6步】对方消息颜色校准')
        print('  目的：采样对方白色/灰色气泡的RGB值')
        tx, ty = wait_click('请点击对方最近一条消息的"气泡背景"（白色区域中间）')
        results['their_msg_pos'] = (tx, ty)
        try:
            r = wx.BoundingRectangle
            tx_abs, ty_abs = r.left + tx, r.top + ty
            px2 = ImageGrab.grab().getpixel((tx_abs, ty_abs))
            print(f'  采样RGB: ({px2[0]}, {px2[1]}, {px2[2]})')
            their_color = {'r': int(px2[0]), 'g': int(px2[1]), 'b': int(px2[2])}
        except Exception as e:
            print(f'  ⚠️ 采样失败: {e}')
    else:
        print('\n⏭️ 跳过第6步')

    # 7) 背景颜色校准
    if not should_skip(7):
        print('\n【第7步】背景颜色校准')
        print('  目的：采样聊天区域空白背景的RGB值，用于区分背景和消息气泡')
        bgx, bgy = wait_click('请点击聊天区域中没有任何消息的空白背景处')
        results['bg_pos'] = (bgx, bgy)
        try:
            r = wx.BoundingRectangle
            bg_abs_x, bg_abs_y = r.left + bgx, r.top + bgy
            px3 = ImageGrab.grab().getpixel((bg_abs_x, bg_abs_y))
            print(f'  采样RGB: ({px3[0]}, {px3[1]}, {px3[2]})')
            bg_color = {'r': int(px3[0]), 'g': int(px3[1]), 'b': int(px3[2])}
        except Exception as e:
            print(f'  ⚠️ 采样失败: {e}')
    else:
        print('\n⏭️ 跳过第7步')

    # 8) 聊天区域 - 左上角
    if not should_skip(8):
        print('\n【第8步】聊天区域 - 左上角')
        print('  目的：划定OCR识别对话内容的范围')
        clx, cty = wait_click('请点击聊天内容区域的左上角（第一条消息上方偏左）')
        results['chat_lt'] = (clx, cty)
    else:
        print('\n⏭️ 跳过第8步')

    # 9) 聊天区域 - 右下角
    if not should_skip(9):
        print('\n【第9步】聊天区域 - 右下角')
        crx, cby = wait_click('请点击聊天内容区域的右下角（输入框上方，偏右）')
        results['chat_rb'] = (crx, cby)
    else:
        print('\n⏭️ 跳过第9步')

    # 10) 输入框位置
    if not should_skip(10):
        print('\n【第10步】输入框位置')
        print('  目的：定位输入框点击位置，用于粘贴回复后自动发送')
        ix, iy = wait_click('请点击聊天输入框的中间位置')
        results['input_pos'] = (ix, iy)
        print(f'  输入框 Y 坐标: {iy} (相对于窗口顶部)')
    else:
        print('\n⏭️ 跳过第10步')

    # 11) 第12个会话（复位用）
    if not should_skip(11):
        print('\n【第11步】第12个会话')
        print('  目的：定位复位点击的目标位置（往下数第12个）')
        s12x, s12y = wait_click('请点击第12个会话（往下数第12个）')
        results['session_12'] = (s12x, s12y)
        print(f'  第12个会话 Y 坐标: {s12y} (相对于窗口顶部)')
    else:
        print('\n⏭️ 跳过第11步')

    # ============================================================
    #  计算
    # ============================================================
    s1y = results['session_1'][1]
    s3y = results['session_3'][1]
    session_start_y = int(s1y - 15)
    session_height = round((s3y - s1y) / 2)  # session_1到session_3跨2个间隔

    # 名称区域
    name_zone = [
        min(results['name_lt'][0], results['name_rb'][0]),
        min(results['name_lt'][1], results['name_rb'][1]),
        max(results['name_lt'][0], results['name_rb'][0]),
        max(results['name_lt'][1], results['name_rb'][1]),
    ]

    # 聊天区域
    chat_zone = [
        min(results['chat_lt'][0], results['chat_rb'][0]),
        min(results['chat_lt'][1], results['chat_rb'][1]),
        max(results['chat_lt'][0], results['chat_rb'][0]),
        max(results['chat_lt'][1], results['chat_rb'][1]),
    ]

    # 输入框位置
    input_pos = results.get('input_pos', (0, 0))

    calib = {
        'session_start_y': session_start_y,
        'session_height': session_height,
        'target_index': 2,
        'name_zone': name_zone,
        'chat_zone': chat_zone,
        'input_zone_y': input_pos[1],  # 输入框相对于窗口顶部的Y坐标
        'my_color': my_color,
        'their_color': their_color,
        'bg_color': bg_color,
        '_raw': results,
        'window_size': [r.right - r.left, r.bottom - r.top],
    }

    print('\n' + '=' * 55)
    print('  校准完成，正在写入...')
    print('=' * 55)

    apply_to_agent(calib)

    print('\n现在运行 python agent.py --diag 验证坐标是否准确')
