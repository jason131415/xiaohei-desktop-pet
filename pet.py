"""WorkBuddy 桌面宠物「小黑」。

像素黑猫悬浮窗：
  * Windows 分层窗口（UpdateLayeredWindow）实现真每像素 alpha
    —— 透明区域自动穿透鼠标，不会挡住下面的窗口
  * Pillow 实时合成猫 + 气泡 + 状态位移
  * 轮询 ~/.workbuddy/pet/status.json 切换状态
  * 可拖拽、单击拍一下、右键切换状态 / 退出
  * idle 时自然眨眼、随机 reading/thinking 子动作
  * 180s 无交互自动入睡（sleeping），鼠标移动唤醒
  * done 状态切换时播放 C5→C6 琶音

降级：分层窗口不可用时自动回落到 tkinter transparentcolor 方案。

运行：python pet.py  或  start.bat
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
import threading
import tkinter as tk
from typing import Optional, Tuple

import ctypes
from ctypes import wintypes

# 启动日志（pythonw.exe 无控制台，异常静默——落盘排查用）
_LOG_PATH = os.path.join(os.path.expanduser("~"), ".workbuddy", "pet", "pet.log")


def _log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _excepthook(etype, value, tb):
    import traceback
    _log("UNCAUGHT EXCEPTION:\n" + "".join(traceback.format_exception(etype, value, tb)))


sys.excepthook = _excepthook

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageTk

# ---------- 路径常量 ----------
# PyInstaller 打包后资源在 sys._MEIPASS 临时目录；源码运行时在脚本所在目录
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
SPRITE_PNG = os.path.join(ASSETS_DIR, "sprite.png")
SOUNDS_DIR = os.path.join(ASSETS_DIR, "sounds")
FRAMES_DIR = os.path.join(ASSETS_DIR, "frames")

# Idle 动画帧：6 帧（坐 / 闭眼 / 打哈欠 / 舔 / 伸懒腰 / 举爪挥手），由行为状态机选择
IDLE_FRAME_FILES = ("frame_0_sit.png", "frame_1_blink.png",
                    "frame_2_yawn.png", "frame_3_lick.png",
                    "frame_4_stretch.png", "frame_5_wave.png")

USER_PET_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "pet")
STATUS_JSON = os.path.join(USER_PET_DIR, "status.json")
POSITION_JSON = os.path.join(USER_PET_DIR, "position.json")
PREFS_JSON = os.path.join(USER_PET_DIR, "prefs.json")
STATS_JSON = os.path.join(USER_PET_DIR, "stats.json")  # 记得你：累计摸头次数等

# 底带渲染 prefs（默认全开；用户在 pet_ctl.py 里改）
DEFAULT_PREFS = {
    "show_strip": True,
    "show_step_text": True,
    "show_progress_bar": True,
    "show_count": True,
    "compact": False,
    "sound": True,             # 总开关：false 时所有音效静音
}

# ---------- DPI 感知（必须在创建任何窗口之前设置）----------
def _init_dpi() -> float:
    """让进程感知 DPI，并返回缩放倍数。

    用 GetDpiForSystem() 取真实 DPI（不受感知模式影响），
    避免 SetProcessDpiAwareness 间歇性失败导致窗口大小不一致。
    """
    # 尝试设置 DPI 感知（结果不重要，GetDpiForSystem 总是返回真实值）
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    # GetDpiForSystem 不受感知模式影响，Win10+ 稳定可用
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi and dpi > 0:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    # 回退：GetDeviceCaps（可能受感知模式影响，但比直接返回 1.0 好）
    try:
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        u.GetDC.argtypes = [wintypes.HWND]
        u.GetDC.restype = wintypes.HDC
        g.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        hdc = u.GetDC(0)
        dpi = g.GetDeviceCaps(hdc, 88)        # LOGPIXELSX
        u.ReleaseDC(0, hdc)
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0


# ---------- 渲染常量 ----------
WIN_W, WIN_H = 220, 220      # 逻辑画布尺寸（渲染坐标都按这个来）
DPI_SCALE = _init_dpi()
PHYS_W = int(round(WIN_W * DPI_SCALE))   # 实际窗口物理尺寸
PHYS_H = int(round(WIN_H * DPI_SCALE))
CAT_SIZE = 160
ANIM_FPS = 14
TRANS_KEY = "#00ff00"          # 降级方案用的透明色
WIN_FONT = "C:/Windows/Fonts/segoeui.ttf"
# 气泡问号 / ✓ / ✗ 用上面这个（拉丁字符即可）。
# 中文步骤文字必须用 CJK 字体，否则渲染出 □□ 乱码（用户的痛点）。
CJK_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑（首选）
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    "C:/Windows/Fonts/simfang.ttf",
)


def _pick_cjk_font() -> str:
    """找一个能渲染中文的系统字体，找不到退回 WIN_FONT。"""
    for p in CJK_FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return WIN_FONT

STATES = ("idle", "thinking", "running", "waiting", "done", "failed", "sleeping")

# ============================================================
#  行为状态机（双层架构：主行为 + 微动作叠加 —— 借鉴真实猫的行为模式）
#
#  主行为（long-term）：持续 6-14 秒，决定猫的整体姿态，切换频率低
#  微动作（micro overlay）：在主行为期间随机触发，持续 0.4-1.4 秒，
#    不替换主行为，只是叠加一点身体微动（抖耳朵/甩尾巴/舔鼻子）
#  这样猫大部分时间在做一件事，期间偶尔有小动作，看起来自然不躁。
# ============================================================
MAIN_BEHAVIORS = {
    # name:       (weight, cooldown, min_dur, max_dur)
    "sit":        (45,      3.0,      6.0,      14.0),
    "lookaround": (15,      6.0,      3.0,      7.0),
    "groom":      (10,     10.0,      4.0,      7.0),
    "stretch":    (5,      15.0,      2.0,      3.0),
    "yawn":       (4,      12.0,      1.8,      2.5),
    "paw_lick":   (4,      12.0,      3.0,      5.0),
}

# 微动作：在主行为期间按概率触发，叠加在主行为之上
# name: (prob_per_check, cooldown, min_dur, max_dur)
MICRO_ACTIONS = {
    "ear_twitch":  (0.07, 3.0, 0.4, 0.8),
    "tail_flick":  (0.05, 4.0, 0.5, 1.0),
    "nose_lick":   (0.03, 6.0, 0.8, 1.4),
    "slow_blink":  (0.04, 5.0, 0.3, 0.6),
}

BEHAVIOR_CHECK_INTERVAL = 1.0       # 主行为每 1s 检查一次（降低频率）
MICRO_CHECK_INTERVAL = 0.5          # 微动作每 0.5s 掷一次骰子
BEHAVIOR_TRANSITION_BLEND = 0.3     # 主行为切换时的过渡时间（秒）

# 悬停挥手：光标在猫身上停留超过延迟后，猫举起爪子挥手打招呼
HOVER_WAVE_DELAY = 0.8              # 光标停留多久触发挥手
WAVE_DURATION = 1.2                 # 挥手持续时长
WAVE_COOLDOWN = 6.0                 # 挥手冷却（避免一直挥手）

# ============================================================
#  物理系统（拖拽瘫软 / 重力下落 / 落地蹲伏 —— 借鉴 WildxHV + Shimeji）
# ============================================================
GRAVITY = 1800.0                     # 重力加速度 px/s²
TERMINAL_VELOCITY = 900.0            # 终端速度 px/s
LANDING_DURATION = 0.35              # 落地蹲伏持续时间

# ============================================================
#  光标互动（注视 / 扑击 / 悬停挥手）
# ============================================================
CURSOR_POUNCE_DIST = 70              # 光标进入这个距离 → 可能扑击
POUNCE_CHANCE_PER_TICK = 0.015       # 每帧扑击概率（~14fps → 平均5s一次）
POUNCE_COOLDOWN = 6.0                # 扑击冷却
POUNCE_WINDUP = 0.25                 # 扑击前蹲伏蓄力时间
POUNCE_DURATION = 0.35               # 扑击动作持续时间

# ============================================================
#  爱心粒子系统（抚摸反馈）
# ============================================================
HEART_LIFETIME = 1.3                 # 爱心存活时间
HEART_RISE_SPEED = 55.0              # 上升速度 px/s
HEART_SWAY_AMP = 8.0                 # 左右摇摆幅度
HEART_START_SIZE = 14                # 初始大小
HEART_END_SIZE = 8                   # 结束大小
HEART_MAX_COUNT = 12                 # 同屏最大爱心数

# 渐进式空闲等级（随无交互时间递增，影响行为概率和动画）
# level 0: 警觉活跃 → level 4: 困到要睡
IDLE_LEVEL_THRESHOLDS = (0, 25, 50, 90, 140)   # 秒
IDLE_LEVEL_NAMES = ("alert", "relaxed", "bored", "sleepy", "dozing")

# idle 子动作 + 自动睡眠的时间常量
IDLE_SUB_CHECK_INTERVAL = 10.0      # 每 10s 掷一次随机子动作
IDLE_SUB_TRIGGER_PROB = 0.15        # 15% 概率触发 → 平均 60s+ 一次
IDLE_SUB_DURATION_RANGE = (3.0, 6.0)
SLEEP_AFTER_INACTIVITY = 180.0      # 180s 无交互自动 sleeping（给渐进式空闲留空间）
SLEEP_CHECK_INTERVAL = 2.0

# 完成音效：5 音琶音 wav（soundsynth.py 合成）—— 替代单调蜂鸣器
DONE_CHIME_WAV = "done_chime.wav"

# 互动音效库：每个 key 对应一个 wav 文件名（assets/sounds/ 下）。
# 其中 8 个是 Mixkit 下载的真实猫叫录音（免版权），land/pounce 两个物理
# 音效由 soundsynth.py 合成。详见 assets/sounds/README 或 soundsynth.py。
SOUNDS = {
    "pat_normal":   "pat_normal.wav",
    "pat_happy":    "pat_happy.wav",
    "pat_annoyed":  "pat_annoyed.wav",
    "drag":         "drag.wav",
    "dblclick":     "dblclick.wav",
    "wake":         "wake.wav",
    "mutter":       "mutter.wav",
    "land":         "land.wav",
    "pounce":       "pounce.wav",
}

# 音效元数据：priority（越大越优先，可打断低优先级）、probability（0-1 播放概率）、cooldown（秒）
SOUND_META = {
    "pat_normal":   {"priority": 2, "probability": 1.0, "cooldown": 0.15},
    "pat_happy":    {"priority": 3, "probability": 1.0, "cooldown": 0.15},
    "pat_annoyed":  {"priority": 4, "probability": 1.0, "cooldown": 0.3},
    "drag":         {"priority": 2, "probability": 1.0, "cooldown": 0.5},
    "dblclick":     {"priority": 3, "probability": 1.0, "cooldown": 0.3},
    "wake":         {"priority": 3, "probability": 1.0, "cooldown": 0.5},
    "mutter":       {"priority": 1, "probability": 0.6, "cooldown": 1.0},
    "land":         {"priority": 2, "probability": 0.8, "cooldown": 0.2},
    "pounce":       {"priority": 3, "probability": 1.0, "cooldown": 0.3},
    "done_chime":   {"priority": 5, "probability": 1.0, "cooldown": 0.5},
}


def _play_wav(path: str) -> None:
    """异步播一个 wav 文件。winsound.PlaySound 用 SND_ASYNC 不阻塞 UI。

    - 缺失文件 / 非 Windows 静默跳过
    - SOUNDS_DIR 找不到 wav 也不抛异常（优雅降级）
    """
    try:
        import winsound
        if not os.path.isfile(path):
            return
        # SND_ASYNC + SND_NODEFAULT：找不到时不响系统默认 ding
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        # 非 Windows / winsound 不可用 → 静默
        pass

# 状态气泡已全部移除（像素风遗留，与插画风不搭）；仅保留 say() 文字气泡用于互动台词
BUBBLE_KIND: dict = {}

# ============================================================
#  性格系统：心情 + 台词库
#  宠物不只会显示状态，会"说话"、有情绪、记得你摸过它几次。
# ============================================================
MOODS = ("normal", "happy", "sleepy", "annoyed", "lonely")

# 台词按触发场景分类，say() 时按当前心情挑合适的
QUIPS = {
    # 启动 / 久别重逢
    "greet": ["你回来啦", "诶，是你", "我等你半天了", "可算想起我了"],
    # 被摸（按心情分级）
    "pat_normal": ["喵~", "嗯…", "干嘛", "摸够没", "手拿开"],
    "pat_happy": ["再摸摸~", "呼噜噜", "这儿，这儿", "别停啊", "嗯——"],
    "pat_annoyed": ["别摸了！", "毛都秃了", "你烦不烦", "我咬人啊", "够了啊喂"],
    # 被拖来拖去
    "drag": ["放我下来！", "晕…", "你轻点啊", "别晃了", "我要吐了"],
    # 睡着被拍醒
    "wake": ["困…", "唔…几点了", "别吵", "刚梦见吃鱼"],
    # 自言自语 / idle 整活
    "mutter": ["有点无聊", "今天天气不错", "想吃小鱼干", "发会儿呆", "唔…"],
    "stretch": ["伸个懒腰", "唔——啊", "腰好酸"],
    "lookaround": ["嗯？", "有动静？", "看看"],
    # 长时间没人理
    "lonely": ["你在吗…", "理理我嘛", "陪我玩会儿", "好无聊啊"],
    # 被双击
    "dblclick": ["别戳了！", "又怎么了", "喵！"],
}

# 性格参数
SAY_DURATION = 2.8          # 台词气泡显示时长（秒）
MOOD_DECAY = 40.0           # 多久没有刺激就衰减回 normal
PAT_ANNOY_WINDOW = 3.5      # 在这个秒数内连点算"烦人"
PAT_ANNOY_COUNT = 5         # 连点这么多次 → annoyed
LONELY_AFTER = 45.0         # 这么久没人理 → 开始 lonely 自言自语
                            # 必须 < SLEEP_AFTER_INACTIVITY(60s)，否则先睡着就说不上了
LONELY_CHECK_INTERVAL = 25.0
MUTTER_CHECK_INTERVAL = 14.0
MUTTER_PROB = 0.18          # idle 时自言自语概率


# ============================================================
#  Win32 分层窗口渲染器
# ============================================================
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


def _get_toplevel_hwnd(hwnd: int) -> int:
    """取真正的顶层窗口句柄。

    tkinter 的 winfo_id() 在 Windows 上返回的是内部 wrapper（无标题、样式 0x4），
    真正的顶层窗口是它的祖先。对 wrapper 做分层/置顶不会有任何可见效果 ——
    这是个很容易踩的坑。
    """
    u = ctypes.windll.user32
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetAncestor.restype = wintypes.HWND
    GA_ROOT = 2
    top = u.GetAncestor(wintypes.HWND(hwnd), GA_ROOT)
    return int(top) if top else hwnd


class LayeredRenderer:
    """用 UpdateLayeredWindow 推送每像素 alpha 位图。"""

    def __init__(self, hwnd: int, w: int, h: int) -> None:
        self.hwnd = hwnd
        self.w, self.h = w, h
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self.ok = False
        self.topmost_ok = False
        self.hdc_screen = 0
        self.hdc_mem = 0
        self.hbm = 0
        self.bits = ctypes.c_void_p()

        # 64 位下必须显式声明签名，否则 HWND / 指针参数会被截断成 32 位
        u, g = self.user32, self.gdi32
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_longlong
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                        ctypes.c_longlong]
        u.SetWindowLongPtrW.restype = ctypes.c_longlong
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SetWindowPos.restype = wintypes.BOOL
        u.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, wintypes.HDC, ctypes.POINTER(_POINT),
            ctypes.POINTER(_SIZE), wintypes.HDC, ctypes.POINTER(_POINT),
            wintypes.COLORREF, ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD]
        u.UpdateLayeredWindow.restype = wintypes.BOOL
        u.GetDC.argtypes = [wintypes.HWND]
        u.GetDC.restype = wintypes.HDC
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateDIBSection.argtypes = [
            wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.SelectObject.restype = wintypes.HGDIOBJ

        hw = wintypes.HWND(hwnd)

        # 开启分层样式
        style = u.GetWindowLongPtrW(hw, GWL_EXSTYLE)
        if not style:
            style = 0
        u.SetWindowLongPtrW(hw, GWL_EXSTYLE, style | WS_EX_LAYERED)

        # 置顶必须用 SetWindowPos —— 只改 WS_EX_TOPMOST 样式位不会改变 Z 序。
        # （tkinter 的 -topmost 在 overrideredirect 窗口上经常不生效）
        self.topmost_ok = bool(u.SetWindowPos(
            hw, wintypes.HWND(-1), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))

        self.hdc_screen = u.GetDC(None)
        self.hdc_mem = g.CreateCompatibleDC(self.hdc_screen)

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h          # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0      # BI_RGB

        self.hbm = self.gdi32.CreateDIBSection(
            self.hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0
        )
        if not self.hbm:
            return
        self.gdi32.SelectObject(self.hdc_mem, self.hbm)
        self.buf_size = w * h * 4
        self.ok = True

    def dispose(self) -> None:
        """释放 GDI 句柄（屏幕 DC / 内存 DC / DIB 位图）。

        不释放会泄漏 GDI 对象——激光笔反复开关后系统会拒绝创建新句柄，
        表现为窗口/位图创建失败。
        """
        g, u = self.gdi32, self.user32
        try:
            if self.hbm:
                g.DeleteObject(self.hbm)
        except Exception:
            pass
        try:
            if self.hdc_mem:
                g.DeleteDC(self.hdc_mem)
        except Exception:
            pass
        try:
            if self.hdc_screen:
                u.ReleaseDC(None, self.hdc_screen)
        except Exception:
            pass
        self.hbm = 0
        self.hdc_mem = 0
        self.hdc_screen = 0
        self.ok = False

    def update(self, bgra: bytes) -> bool:
        if not self.ok or len(bgra) != self.buf_size:
            return False
        ctypes.memmove(self.bits, bgra, self.buf_size)
        size = _SIZE(self.w, self.h)
        pt_src = _POINT(0, 0)
        blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        res = self.user32.UpdateLayeredWindow(
            self.hwnd, self.hdc_screen, None,
            ctypes.byref(size), self.hdc_mem, ctypes.byref(pt_src),
            0, ctypes.byref(blend), ULW_ALPHA,
        )
        return bool(res)


# ============================================================
#  激光笔红点（独立小分层窗口，跟随光标/被猫追逐）
# ============================================================
class LaserDot:
    """一个 20x20 的红色光点分层窗口，用于激光笔模式。"""

    def __init__(self) -> None:
        self.hwnd = 0
        self.renderer: Optional[LayeredRenderer] = None
        self.size = 20
        self._create()

    def _create(self) -> None:
        try:
            # 用 tkinter 创建一个隐形窗口，拿 hwnd
            self._tk = tk.Tk()
            self._tk.overrideredirect(True)
            self._tk.attributes("-topmost", True)
            self._tk.geometry(f"{self.size}x{self.size}+0+0")
            self._tk.withdraw()
            self._tk.update_idletasks()
            hwnd = _get_toplevel_hwnd(int(self._tk.winfo_id()))
            r = LayeredRenderer(hwnd, self.size, self.size)
            if r.ok:
                self.hwnd = hwnd
                self.renderer = r
                self._draw_dot()
                self._tk.deiconify()
        except Exception:
            self.hwnd = 0
            self.renderer = None

    def _draw_dot(self) -> None:
        """画一个红色发光圆点。"""
        if not self.renderer:
            return
        img = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        cx, cy = self.size // 2, self.size // 2
        # 外发光
        for r in range(8, 2, -1):
            alpha = int(60 * (1 - r / 9))
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 0, 0, alpha))
        # 核心
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(255, 60, 60, 255))
        self.renderer.update(_to_bgra(img))

    def move(self, x: int, y: int) -> None:
        """移动红点到屏幕坐标 (x, y)。"""
        if not self.hwnd:
            return
        u = ctypes.windll.user32
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SetWindowPos(self.hwnd, wintypes.HWND(-1), x, y, 0, 0,
                       SWP_NOSIZE | SWP_NOACTIVATE)

    def destroy(self) -> None:
        try:
            if self.renderer:
                self.renderer.dispose()   # 释放 GDI 句柄，防泄漏
            if self.hwnd:
                ctypes.windll.user32.DestroyWindow(self.hwnd)
            if hasattr(self, "_tk"):
                self._tk.destroy()
        except Exception:
            pass
        self.hwnd = 0
        self.renderer = None


# ============================================================
#  会话活动探测（SessionMonitor）
#  仅用 ctypes 调 Windows API，不依赖 psutil。
#  角色已收窄：只负责"你回来操作时唤醒打盹的宠物"。
#  真实的 working/thinking/running/waiting/done 状态由 agent 通过
#  pet_ctl.py set 主动驱动；进程信号分不出这些语义，喊"在忙…"没意义。
# ============================================================
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.CHAR * 260),
    ]


class _IO_COUNTERS(ctypes.Structure):
    # 6 个 ULONGLONG：Read/Write/Other 的 Operations + Bytes
    _fields_ = [(f"_v{i}", ctypes.c_ulonglong) for i in range(6)]


class SessionMonitor:
    """采样 WorkBuddy 主进程的 CPU / IO 活跃度，推断"会话是否在忙"。

    纯进程信号只能感知 忙/闲 二态，无法区分 thinking / running / waiting 这类语义
    —— 那些只有 agent 主动 set 才能精确。本类只回答一个问题：
    "WorkBuddy 当前有没有在干活？" 用于 agent 长期不写状态时自动接管。
    """

    def __init__(self, exe_name: str = "WorkBuddy.exe",
                 cpu_threshold: float = 3.0) -> None:
        self.exe = exe_name.encode("ascii", "ignore")
        self.cpu_threshold = cpu_threshold
        self._k = ctypes.windll.kernel32
        self._k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self._k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self._k.Process32First.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(_PROCESSENTRY32)]
        self._k.Process32First.restype = wintypes.BOOL
        self._k.Process32Next.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(_PROCESSENTRY32)]
        self._k.Process32Next.restype = wintypes.BOOL
        self._k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._k.OpenProcess.restype = wintypes.HANDLE
        self._k.GetProcessTimes.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(_FILETIME),
                                            ctypes.POINTER(_FILETIME),
                                            ctypes.POINTER(_FILETIME),
                                            ctypes.POINTER(_FILETIME)]
        self._k.GetProcessTimes.restype = wintypes.BOOL
        self._k.GetProcessIoCounters.argtypes = [wintypes.HANDLE,
                                                 ctypes.POINTER(_IO_COUNTERS)]
        self._k.GetProcessIoCounters.restype = wintypes.BOOL
        self._k.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k.CloseHandle.restype = wintypes.BOOL
        self._baseline: dict = {}        # pid -> (wall_ns, cpu_ns, io_total)
        self.last_active = False

    def _enum_pids(self) -> list:
        h = self._k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not h or h == wintypes.HANDLE(-1).value:
            return []
        pe = _PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        pids = []
        if self._k.Process32First(h, ctypes.byref(pe)):
            while True:
                raw = pe.szExeFile               # bytes，如 b"WorkBuddy.exe"
                name = raw if isinstance(raw, bytes) else raw.value
                if name.startswith(self.exe):
                    pids.append(pe.th32ProcessID)
                if not self._k.Process32Next(h, ctypes.byref(pe)):
                    break
        self._k.CloseHandle(h)
        return pids

    def _sample_pid(self, pid: int, now_ns: int):
        h = self._k.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                                False, pid)
        if not h:
            return None
        try:
            ctime = _FILETIME(); etime = _FILETIME()
            ktime = _FILETIME(); utime = _FILETIME()
            if not self._k.GetProcessTimes(h, ctypes.byref(ctime),
                                           ctypes.byref(etime),
                                           ctypes.byref(ktime),
                                           ctypes.byref(utime)):
                return None
            io = _IO_COUNTERS()
            io_ok = self._k.GetProcessIoCounters(h, ctypes.byref(io))
            user = (utime.dwHighDateTime << 32) | utime.dwLowDateTime
            kernel = (ktime.dwHighDateTime << 32) | ktime.dwLowDateTime
            io_total = (io._v1 + io._v3) if io_ok else 0   # ReadBytes + WriteBytes
            return (now_ns, user + kernel, io_total)
        finally:
            self._k.CloseHandle(h)

    def sample(self) -> bool:
        """返回 True 表示 WorkBuddy 当前在忙（CPU 或 IO 有明显活动）。"""
        now_ns = time.time_ns()
        pids = self._enum_pids()
        if not pids:
            self._baseline.clear()
            self.last_active = False
            return False
        cur = {}
        active = False
        for pid in pids:
            s = self._sample_pid(pid, now_ns)
            if not s:
                continue
            cur[pid] = s
            base = self._baseline.get(pid)
            if base:
                wall = (s[0] - base[0]) / 1e9
                if wall > 0.2:
                    cpu = (s[1] - base[1]) / wall / 1e7     # 100ns 单位 → 百分比
                    io_delta = s[2] - base[2]
                    if cpu > self.cpu_threshold or io_delta > 1_000_000:
                        active = True
        self._baseline = cur
        self.last_active = active
        return active


# ============================================================
#  状态读写
# ============================================================
def _ensure_user_dir() -> None:
    os.makedirs(USER_PET_DIR, exist_ok=True)


def write_status(state: str, msg: str = "", step: str = "",
                 progress: Optional[dict] = None) -> None:
    _ensure_user_dir()
    payload = {
        "state": state,
        "msg": msg,
        "step": step,
        "progress": progress,
        "updated": time.time(),
    }
    tmp = STATUS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, STATUS_JSON)


def read_status() -> Tuple[str, str, str, Optional[dict]]:
    try:
        with open(STATUS_JSON, "r", encoding="utf-8") as f:
            d = json.load(f)
        st = d.get("state", "idle")
        if st not in STATES:
            st = "idle"
        return (
            st,
            d.get("msg", ""),
            d.get("step", ""),
            d.get("progress"),
        )
    except Exception:
        return "idle", "", "", None


def read_prefs() -> dict:
    """读 prefs.json；缺失/坏文件用默认值。"""
    out = dict(DEFAULT_PREFS)
    try:
        with open(PREFS_JSON, "r", encoding="utf-8") as f:
            d = json.load(f)
        for k in DEFAULT_PREFS:
            if k in d and isinstance(d[k], bool):
                out[k] = d[k]
    except Exception:
        pass
    return out


def read_position() -> Tuple[int, int]:
    try:
        with open(POSITION_JSON, "r", encoding="utf-8") as f:
            d = json.load(f)
        return int(d.get("x", 700)), int(d.get("y", 400))
    except Exception:
        return 700, 400


def _window_rect_ok(hwnd: int) -> Optional[Tuple[int, int, int, int, int]]:
    """检查一个窗口是否适合栖息，适合返回 (left, top, w, h, hwnd)。

    过滤：桌面/任务栏/不可见/极小/最小化/坐标异常/自己（标题含"小黑"）。
    """
    if not hwnd:
        return None
    u = ctypes.windll.user32
    try:
        # 类名过滤：桌面（Progman/WorkerW）和任务栏（Shell_TrayWnd）不能栖息
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(wintypes.HWND(hwnd), cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "DV2ControlHost"):
            return None
        if not u.IsWindowVisible(wintypes.HWND(hwnd)):
            return None
        rect = wintypes.RECT()
        if not u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        # 排除极小窗口（菜单、工具提示）
        if w < 300 or h < 150:
            return None
        # 排除最小化窗口
        if u.IsIconic(wintypes.HWND(hwnd)):
            return None
        # 排除坐标异常的窗口（右键菜单等可能返回 -16384 之类的坐标）
        sw = u.GetSystemMetrics(0)   # SM_CXSCREEN
        sh = u.GetSystemMetrics(1)   # SM_CYSCREEN
        if rect.left < -100 or rect.top < -100 or rect.left > sw or rect.top > sh:
            return None
        # 排除标题含"小黑"的自己窗口
        length = u.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            u.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
            if "小黑" in buf.value:
                return None
        return (rect.left, rect.top, w, h, int(hwnd))
    except Exception:
        return None


def get_foreground_window_rect() -> Optional[Tuple[int, int, int, int, int]]:
    """获取适合栖息的前台窗口 (x, y, w, h, hwnd)。

    先试前台窗口；右键菜单关闭后焦点常回到宠物自身（被标题过滤），
    此时枚举 Z 序最上层的可用普通窗口兜底——用户在用的应用窗口
    通常在 Z 序顶部，保证"去窗口上栖息"总能找到一个目标。
    """
    u = ctypes.windll.user32
    try:
        r = _window_rect_ok(u.GetForegroundWindow())
        if r:
            return r
    except Exception:
        pass

    # 枚举兜底：EnumWindows 按 Z 序从顶层往下枚举，取第一个合适的
    result: list = [None]
    cb_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(h, _l):
        r = _window_rect_ok(int(h))
        if r:
            result[0] = r
            return False   # 找到就停
        return True

    try:
        u.EnumWindows(cb_type(_cb), 0)
    except Exception:
        pass
    return result[0]


def write_position(x: int, y: int) -> None:
    _ensure_user_dir()
    try:
        with open(POSITION_JSON, "w", encoding="utf-8") as f:
            json.dump({"x": int(x), "y": int(y)}, f)
    except Exception:
        pass


# ---- 统计（让宠物"记得你"）----
DEFAULT_STATS = {"total_pats": 0, "last_seen": 0.0, "launches": 0, "affection": 0}


def read_stats() -> dict:
    """读取累计统计。文件缺失/损坏时返回默认值。"""
    if not os.path.exists(STATS_JSON):
        return dict(DEFAULT_STATS)
    try:
        with open(STATS_JSON, encoding="utf-8") as f:
            d = json.load(f)
        out = dict(DEFAULT_STATS)
        for k in DEFAULT_STATS:
            if k in d and isinstance(d[k], (int, float)):
                out[k] = d[k]
        return out
    except Exception:
        return dict(DEFAULT_STATS)


def write_stats(stats: dict) -> None:
    _ensure_user_dir()
    try:
        tmp = STATS_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False)
        os.replace(tmp, STATS_JSON)
    except Exception:
        pass


# ============================================================
#  渲染
# ============================================================
def load_sprite() -> Image.Image:
    """加载默认坐姿帧（插画风，与 idle 帧保持一致）。

    优先用 frame_0_sit.png，缺失时回退到 sprite.png。
    用 LANCZOS 平滑缩放（插画风，不用 NEAREST 像素化）。
    """
    sit_path = os.path.join(FRAMES_DIR, "frame_0_sit.png")
    if os.path.isfile(sit_path):
        img = Image.open(sit_path).convert("RGBA")
    else:
        img = Image.open(SPRITE_PNG).convert("RGBA")
    img.thumbnail((CAT_SIZE, CAT_SIZE), Image.LANCZOS)
    return img


def load_idle_frames() -> list[Image.Image]:
    """加载 idle 帧循环的全部 sprite（插画风，平滑缩放）。

    缺失文件时回退到坐姿帧补位，整体仍能跑（不抛）。
    """
    fallback = load_sprite()
    frames: list[Image.Image] = []
    for fname in IDLE_FRAME_FILES:
        path = os.path.join(FRAMES_DIR, fname)
        if os.path.isfile(path):
            im = Image.open(path).convert("RGBA")
            im.thumbnail((CAT_SIZE, CAT_SIZE), Image.LANCZOS)
            frames.append(im)
        else:
            frames.append(fallback.copy())
    return frames


def _composite(cat: Image.Image, state: str, t: float,
               step: str = "", progress: Optional[dict] = None,
               prefs: Optional[dict] = None,
               idle_sub: str = "",
               say_text: str = "",
               behavior_offset: Tuple[int, int] = (0, 0),
               phys_state: str = "ground",
               pounce_state: str = "idle",
               facing: int = 1,
               hearts: Optional[list] = None) -> Image.Image:
    """合成一帧 RGBA（保留真 alpha，供分层窗口使用）。

    say_text 非空时优先画台词气泡（性格互动），盖住状态气泡。
    behavior_offset: 行为状态机带来的身体位移（x, y）。
    phys_state: ground/dragged/falling/landing，影响猫的姿态。
    pounce_state: idle/windup/pouncing，扑击动画。
    facing: 1=朝右, -1=朝左（注视光标）。
    hearts: 爱心粒子列表。
    """
    canvas = Image.new("RGBA", (WIN_W, WIN_H), (0, 0, 0, 0))

    bx, by = behavior_offset
    ox, oy = 0, 0
    # 状态偏移：全部设为固定值，不再有 sin 持续摆动（用户要求不晃）
    if state == "sleeping":
        oy = 6
    elif state == "done":
        # done 时一次性小跳（庆祝），不是持续晃动
        phase = (t % 3.0) / 3.0
        if phase < 0.32:
            oy = -int(round(20 * math.sin(phase / 0.32 * math.pi)))

    # 物理状态叠加
    if phys_state == "dragged":
        oy += 8   # 被抓起时瘫软下沉
    elif phys_state == "falling":
        oy += int(math.sin(t * 12) * 2)  # 下落时微微晃动（降落伞感）
    elif phys_state == "landing":
        oy += 6   # 落地蹲伏压扁

    # 扑击状态叠加
    if pounce_state == "windup":
        oy += 5    # 蓄力蹲伏
    elif pounce_state == "pouncing":
        ox += facing * 12  # 前冲

    # 朝向翻转（朝左时水平镜像）
    cat_to_draw = cat
    if facing < 0:
        cat_to_draw = cat.transpose(Image.FLIP_LEFT_RIGHT)

    cx = (WIN_W - cat_to_draw.width) // 2 + ox + bx
    cy = (WIN_H - cat_to_draw.height) // 2 + 12 + oy + by
    canvas.alpha_composite(cat_to_draw, (cx, cy))

    # 气泡：台词气泡优先（性格互动），否则用状态对应气泡；idle 子动作时也显示
    if say_text:
        _draw_bubble(canvas, "say", t, text=say_text)
    else:
        # 状态气泡已全部移除（BUBBLE_KIND 为空），仅保留 say() 文字气泡
        kind = BUBBLE_KIND.get(state)
        if kind:
            _draw_bubble(canvas, kind, t)

    # 状态 + 步骤 + 进度条（窗口底部）
    _draw_status_strip(canvas, state, step, progress, prefs)

    # 爱心粒子（在最上层）
    if hearts:
        _draw_hearts_on_canvas(canvas, hearts)

    return canvas


def _draw_status_strip(canvas: Image.Image, state: str,
                       step: str, progress: Optional[dict],
                       prefs: Optional[dict]) -> None:
    """窗口底部窄带：左边步骤文字，右边进度条 + i/n 计数。

    prefs 控制：
      - show_strip = False → 整条不画
      - compact = True → 自动关步骤文字（更省空间）
      - 其余三项细粒度控制

    **没有真实内容（无 step 也无 progress）时整条不画**——避免显示装饰性的
    "待命中"等状态名回退。状态本身已经由气泡 + 动画传达。
    文字超 22 字符自动截断。
    """
    if prefs is None:
        prefs = DEFAULT_PREFS
    if not prefs.get("show_strip", True):
        return

    # 没东西可显示就别画（避免角落出现"待命中"之类装饰性回退）
    if not step and not progress:
        return

    compact = prefs.get("compact", False)
    show_text = prefs.get("show_step_text", True) and not compact
    show_bar = prefs.get("show_progress_bar", True)
    show_count = prefs.get("show_count", True)

    if not (show_text or show_bar or show_count):
        return

    d = ImageDraw.Draw(canvas)

    text_color = (60, 60, 60, 255)
    try:
        font = ImageFont.truetype(_pick_cjk_font(), 13)
    except Exception:
        font = ImageFont.load_default()

    pad_x = 8
    bar_h = 4
    bar_w = WIN_W - 2 * pad_x
    strip_top = WIN_H - 22

    if show_text and step:
        text = step
        if len(text) > 22:
            text = text[:21] + "…"
        d.text((pad_x, strip_top - 2), text, fill=text_color, font=font)

    if progress and show_bar:
        cur = max(0, min(progress.get("current", 0), progress.get("total", 1)))
        total = max(1, progress.get("total", 1))
        ratio = cur / total

        bar_y = WIN_H - 7
        d.rounded_rectangle(
            [pad_x, bar_y, pad_x + bar_w, bar_y + bar_h],
            radius=2, fill=(220, 220, 220, 255),
        )
        fill_w = max(2, int(bar_w * ratio)) if ratio > 0 else 0
        if fill_w > 2:
            d.rounded_rectangle(
                [pad_x, bar_y, pad_x + fill_w, bar_y + bar_h],
                radius=2, fill=(80, 80, 80, 255),
            )

    if progress and show_count:
        cur = progress.get("current", 0)
        total = progress.get("total", 1)
        pct_txt = f"{cur}/{total}"
        try:
            tw = d.textlength(pct_txt, font=font)
        except AttributeError:
            tw = len(pct_txt) * 7
        d.text(
            (WIN_W - pad_x - tw, strip_top - 2),
            pct_txt, fill=text_color, font=font,
        )


def _draw_bubble(canvas: Image.Image, kind: str, t: float, text: str = "") -> None:
    """画气泡。kind="say" 时用 text 画中文台词气泡（CJK 字体 + 自动折行）。"""
    d = ImageDraw.Draw(canvas)
    bx, by = WIN_W - 56, 22
    tail = [(bx + 24, by + 30), (bx + 38, by + 38), (bx + 32, by + 26)]

    if kind == "dots":
        d.ellipse([bx - 18, by - 18, bx + 66, by + 30],
                  fill=(255, 255, 255, 255), outline=(40, 40, 40, 255), width=2)
        d.polygon(tail, fill=(255, 255, 255, 255), outline=(40, 40, 40, 255))
        active = int((t * 1.6) % 3)
        for i in range(3):
            color = (50, 50, 50) if i == active else (180, 180, 180)
            cx = bx + 2 + i * 18
            d.ellipse([cx - 4, by - 2, cx + 4, by + 6], fill=color)
    elif kind == "qmark":
        try:
            font = ImageFont.truetype(WIN_FONT, 22)
        except Exception:
            font = ImageFont.load_default()
        d.ellipse([bx - 18, by - 18, bx + 66, by + 30],
                  fill=(255, 255, 255, 255), outline=(40, 40, 40, 255), width=2)
        d.polygon(tail, fill=(255, 255, 255, 255), outline=(40, 40, 40, 255))
        d.text((bx + 8, by - 8), "?", fill=(40, 40, 40, 255), font=font)
    elif kind == "check":
        d.ellipse([bx - 18, by - 18, bx + 66, by + 30],
                  fill=(190, 245, 200, 255), outline=(40, 120, 40, 255), width=2)
        d.polygon(tail, fill=(190, 245, 200, 255), outline=(40, 120, 40, 255))
        d.line([(bx + 8, by + 4), (bx + 18, by + 12), (bx + 36, by - 10)],
               fill=(40, 120, 40, 255), width=3)
    elif kind == "cross":
        d.ellipse([bx - 18, by - 18, bx + 66, by + 30],
                  fill=(255, 210, 210, 255), outline=(180, 40, 40, 255), width=2)
        d.polygon(tail, fill=(255, 210, 210, 255), outline=(180, 40, 40, 255))
        d.line([(bx + 12, by - 4), (bx + 32, by + 14)],
               fill=(180, 40, 40, 255), width=3)
        d.line([(bx + 32, by - 4), (bx + 12, by + 14)],
               fill=(180, 40, 40, 255), width=3)
    elif kind == "zzz":
        # Zzz 气泡（睡眠）
        bubble_color = (230, 230, 255, 255)
        outline_color = (80, 80, 140, 255)
        text_color = (60, 60, 140, 255)
        d.ellipse([bx - 18, by - 18, bx + 66, by + 30],
                  fill=bubble_color, outline=outline_color, width=2)
        d.polygon(tail, fill=bubble_color, outline=outline_color)
        try:
            font_big = ImageFont.truetype(WIN_FONT, 18)
            font_sm = ImageFont.truetype(WIN_FONT, 14)
        except Exception:
            font_big = font_sm = ImageFont.load_default()
        # 三个 Z，由大到小错位
        d.text((bx + 6, by - 6), "Z", fill=text_color, font=font_big)
        d.text((bx + 24, by - 14), "z", fill=text_color, font=font_sm)
        d.text((bx + 36, by - 22), "z", fill=text_color, font=font_sm)

    elif kind == "say":
        # 台词气泡：中文文字 + 自动折行 + 尾巴指向猫
        if not text:
            return
        try:
            font = ImageFont.truetype(_pick_cjk_font(), 14)
        except Exception:
            font = ImageFont.load_default()

        # ---- 按像素宽度折行（中英文都准）----
        max_text_w = WIN_W - 46
        lines: list[str] = []
        cur = ""
        for ch in text:
            test = cur + ch
            try:
                w = d.textlength(test, font=font)
            except AttributeError:
                w = len(test) * 14
            if w > max_text_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = test
        if cur:
            lines.append(cur)
        lines = lines[:3]                      # 最多 3 行，超了截断

        try:
            bbox = font.getbbox("喵")
            line_h = (bbox[3] - bbox[1]) + 5
        except Exception:
            line_h = 19

        text_w = 0
        for ln in lines:
            try:
                text_w = max(text_w, d.textlength(ln, font=font))
            except AttributeError:
                text_w = max(text_w, len(ln) * 14)

        pad = 10
        bw = int(text_w) + pad * 2
        bh = line_h * len(lines) + pad * 2 - 6
        x0 = max(6, (WIN_W - bw) // 2)
        y0 = 8
        x1 = min(WIN_W - 6, x0 + bw)
        y1 = y0 + bh

        # 先画尾巴再画气泡主体，让尾巴上沿被主体盖住，接缝干净
        tail_x = x0 + max(10, bw // 3)
        d.polygon([(tail_x, y1 - 2), (tail_x + 12, y1 + 11), (tail_x + 22, y1 - 2)],
                  fill=(255, 255, 255, 255), outline=(40, 40, 40, 255))
        d.rounded_rectangle([x0, y0, x1, y1], radius=10,
                            fill=(255, 255, 255, 255),
                            outline=(40, 40, 40, 255), width=2)

        ty = y0 + pad - 3
        for ln in lines:
            d.text((x0 + pad, ty), ln, fill=(45, 45, 45, 255), font=font)
            ty += line_h


def _draw_hearts_on_canvas(canvas: Image.Image, hearts: list) -> None:
    """模块级爱心粒子绘制（供 _composite 调用）。"""
    if not hearts:
        return
    now = time.time()
    d = ImageDraw.Draw(canvas)
    for h in hearts:
        age = now - h["t0"]
        if age >= HEART_LIFETIME:
            continue
        progress = age / HEART_LIFETIME
        y = h["y"] - HEART_RISE_SPEED * age
        x = h["x"] + math.sin(age * 4) * HEART_SWAY_AMP * progress
        size = int(HEART_START_SIZE + (HEART_END_SIZE - HEART_START_SIZE) * progress)
        alpha = 255
        if progress > 0.6:
            alpha = int(255 * (1 - (progress - 0.6) / 0.4))
        r = size // 2
        color = (255, 80, 100, alpha)
        d.ellipse([x - r, y - r, x, y], fill=color)
        d.ellipse([x, y - r, x + r, y], fill=color)
        d.polygon([(x - r, y - 1), (x + r, y - 1), (x, y + r)], fill=color)


def _to_bgra(rgba: Image.Image) -> bytes:
    """RGBA → 预乘 alpha 的 BGRA 字节流。

    UpdateLayeredWindow 要求 BGRA 排列且**已预乘 alpha**（channel = c * a / 255）。
    不预乘的话，透明像素残留的 RGB 会被当成预乘值直接显示，
    表现为整块浅色方块而不是透明的猫 —— 这是最坑的一个点。
    """
    r, g, b, a = rgba.split()
    r = ImageChops.multiply(r, a)      # multiply 内部即 c*a/255
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def _to_hardcut_rgb(rgba: Image.Image) -> Image.Image:
    """降级方案：alpha 硬切成 RGB，透明区填透明色。"""
    rgb = Image.new("RGB", rgba.size, (0, 255, 0))
    src = rgba.load()
    dst = rgb.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r_, g_, b_, a_ = src[x, y]
            if a_ >= 128:
                dst[x, y] = (r_, g_, b_)
    return rgb


# ============================================================
#  主窗口
# ============================================================
class PetApp:
    def __init__(self) -> None:
        _log(f"[init] start pid={os.getpid()} sys.stdout={sys.stdout} sys.stderr={sys.stderr}")
        _ensure_user_dir()
        if not os.path.exists(STATUS_JSON):
            write_status("idle", "")

        self.cat_img = load_sprite()  # 默认坐姿帧（其他状态 / 帧缺失回退用）
        self.frames = load_idle_frames()  # idle 5 帧循环（坐/闭眼/嗷呜/舔爪/伸懒腰）
        self.state, self.msg, self.step, self.progress = read_status()
        self._boot = True  # 首次轮询要清掉可能残留的"工作中"态，从 idle 重新开始
        self.prefs = read_prefs()
        self.t0 = time.time()

        # ---- 内部动画状态（不进 status.json）----
        # idle 随机子动作
        self.idle_sub = ""
        self.idle_sub_until = 0.0
        self.idle_check_at = 0.0
        # 自动睡眠
        self.last_interact = time.time()
        self.sleep_check_at = 0.0
        # ---- 行为状态机（主行为 + 微动作双层）----
        self.behavior = "sit"                # 当前主行为
        self.behavior_start = time.time()
        self.behavior_end = time.time() + 8.0
        self.behavior_cooldowns: dict[str, float] = {}
        self.behavior_check_at = 0.0
        self.behavior_blend_until = 0.0
        # 微动作（叠加在主行为之上）
        self.micro_action: Optional[str] = None
        self.micro_until = 0.0
        self.micro_cooldowns: dict[str, float] = {}
        self.micro_check_at = 0.0
        # 渐进式空闲等级
        self.idle_level = 0
        # ---- 物理系统 ----
        self.phys_state = "ground"         # ground / dragged / falling / landing
        self.phys_vy = 0.0                 # 垂直速度
        self.phys_landing_until = 0.0      # 落地蹲伏结束时间
        self.floor_y = 0                   # 地面 y 坐标（屏幕底部）
        # ---- 光标互动 ----
        self.cursor_x = 0
        self.cursor_y = 0
        self.cursor_in_window = False
        self.pounce_state = "idle"         # idle / windup / pouncing
        self.pounce_until = 0.0
        self.pounce_cooldown_until = 0.0
        self.facing = 1                    # 1=右, -1=左（注视方向）
        # ---- 激光笔 ----
        self.laser_active = False
        self.laser_dot: Optional[LaserDot] = None
        self.laser_target_x = 0
        self.laser_target_y = 0
        self.laser_caught_until = 0.0
        # ---- 窗口栖息 ----
        self.perched = False
        self.perched_hwnd = 0
        self.perch_offset_x = 0
        self.perch_check_at = 0.0
        # ---- 爱心粒子 ----
        self.hearts: list[dict] = []       # {x, y, t0, size}
        # 状态变化（音效触发用）
        self.last_state = self.state
        # ---- 性格系统 ----
        self.stats = read_stats()
        self.stats["launches"] = int(self.stats.get("launches", 0)) + 1
        write_stats(self.stats)
        self.affection = int(self.stats.get("affection", 0))
        self.mood = "normal"
        self.mood_since = time.time()
        self.say_text = ""
        self.say_until = 0.0
        self.pat_times: list[float] = []       # 最近摸头时间戳（连点→烦人检测）
        self.mutter_check_at = 0.0
        self.lonely_check_at = 0.0
        self.greeted = False
        self.hover_since: Optional[float] = None   # 光标进入窗口的时间（悬停挥手用）
        self.wave_cooldown_until = 0.0
        self.wave_done_this_hover = False          # 本次悬停已挥过手（离开再回来才重置）
        # 音效节流（防止连点刷屏）
        self._last_sound_at = 0.0
        self._last_sound_priority = 0
        self._sound_last: dict[str, float] = {}

        self.root = tk.Tk()
        self.root.title("小黑")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=TRANS_KEY)

        x, y = read_position()
        # clamp 到屏幕内：换分辨率 / 多屏变化 / 栖息到异常窗口后，
        # 保存的坐标可能在屏幕外，不修正就会"启动了但看不见"。
        try:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            x = max(0, min(int(x), max(0, sw - PHYS_W)))
            y = max(0, min(int(y), max(0, sh - PHYS_H)))
        except Exception:
            pass
        self.root.geometry(f"{PHYS_W}x{PHYS_H}+{x}+{y}")
        self.root.withdraw()
        self.root.update_idletasks()

        # ---- 优先分层窗口 ----
        self.renderer: Optional[LayeredRenderer] = None
        try:
            hwnd = _get_toplevel_hwnd(int(self.root.winfo_id()))
            r = LayeredRenderer(hwnd, PHYS_W, PHYS_H)
            if r.ok:
                self.renderer = r
        except Exception as e:
            print(f"[pet] 分层窗口不可用，回落 transparentcolor: {e}")

        if self.renderer is None:
            # 降级：Label + 透明色
            self.root.attributes("-transparentcolor", TRANS_KEY)
            self.label = tk.Label(self.root, bg=TRANS_KEY, bd=0,
                                  highlightthickness=0)
            self.label.pack()
        else:
            self.label = None

        # ---- 事件（分层窗口下透明区自动穿透，只有猫身可点） ----
        self._drag = {"dx": 0, "dy": 0, "moved": False}
        target = self.root if self.label is None else self.label
        for seq, fn in (
            ("<Button-1>", self.on_press),
            ("<B1-Motion>", self.on_drag),
            ("<ButtonRelease-1>", self.on_release),
            ("<Double-Button-1>", self.on_dblclick),
            ("<Button-3>", self.on_right),
            ("<Motion>", self.on_mouse_move),
            ("<Leave>", self.on_mouse_leave),
        ):
            target.bind(seq, fn)

        # 计算地面位置（屏幕底部 - 任务栏预留）
        try:
            screen_h = self.root.winfo_screenheight()
            self.floor_y = screen_h - PHYS_H - 40   # 底部留40px给任务栏
        except Exception:
            self.floor_y = 800

        # 顺序很关键：先让窗口显示（Tk 会画一次白底），
        # 再用分层帧覆盖它。反过来会被 Tk 的重绘盖掉。
        self.root.deiconify()
        self.root.update_idletasks()
        self._tick()
        # 会话活动探测器（让宠物在 agent 不写状态时自动感知忙/闲）
        self.monitor = SessionMonitor()
        self.last_agent_write = 0.0
        self.auto_mode = True     # True = 探测接管；False = agent 状态优先
        self._auto_tick()
        self._poll_status()
        # 见面先打个招呼（久别重逢说得不一样）
        self.root.after(600, self._greet)
        _log("[init] entering mainloop")
        self.root.mainloop()
        _log("[init] mainloop returned (should not happen normally)")

    # ---- 渲染 ----
    def _tick(self) -> None:
        try:
            self._tick_body()
        except Exception as e:
            _log(f"[tick] error: {e!r}")
        # 无论是否出错都调度下一帧，避免单帧异常卡死宠物
        self.root.after(int(1000 / ANIM_FPS), self._tick)

    def _tick_body(self) -> None:
        now = time.time()
        t = now - self.t0
        dt = 1.0 / ANIM_FPS  # 近似帧间隔

        # ---- 全局光标轮询（悬停挥手/注视/扑击都依赖光标位置；
        #      分层窗口的 <Motion> 事件并不可靠，改为每帧主动读鼠标）----
        try:
            px, py = self.root.winfo_pointerxy()
            wx, wy = self.root.winfo_x(), self.root.winfo_y()
            in_win = (wx <= px < wx + PHYS_W and wy <= py < wy + PHYS_H)
        except Exception:
            px, py, in_win = self.cursor_x, self.cursor_y, self.cursor_in_window
        self.cursor_x, self.cursor_y = px, py
        if in_win and not self.cursor_in_window:
            self.hover_since = now
        elif not in_win:
            self.hover_since = None
        self.cursor_in_window = in_win

        # ---- 自动睡眠检查（每 2s 一次，节省判断开销）----
        if now - self.sleep_check_at >= SLEEP_CHECK_INTERVAL:
            self.sleep_check_at = now
            if (self.state == "idle"
                    and now - self.last_interact > SLEEP_AFTER_INACTIVITY):
                write_status("sleeping", "打盹中…")

        # ---- 行为状态机（idle 时才运行；其他状态由 agent 驱动）----
        if self.state == "idle" and now - self.behavior_check_at >= BEHAVIOR_CHECK_INTERVAL:
            self.behavior_check_at = now
            self._update_behavior()
        # ---- 微动作（在主行为期间叠加小动作）----
        if self.state == "idle" and now - self.micro_check_at >= MICRO_CHECK_INTERVAL:
            self.micro_check_at = now
            self._update_micro_action()

        # ---- 物理更新（下落 / 落地恢复）----
        if self.phys_state == "falling":
            self._update_physics(dt)
        elif self.phys_state == "landing" and now >= self.phys_landing_until:
            self.phys_state = "ground"

        # ---- 激光笔追逐 ----
        if self.laser_active:
            self._update_laser_chase()

        # ---- 窗口栖息跟随 ----
        if self.perched:
            self._update_perch()

        # ---- 爱心粒子更新 ----
        self._update_hearts()

        # ---- 光标互动：注视方向 + 扑击 ----
        if self.state == "idle" and self.phys_state == "ground" and self.cursor_in_window:
            win_x = self.root.winfo_x() + PHYS_W // 2
            win_y = self.root.winfo_y() + PHYS_H // 2
            dx = self.cursor_x - win_x
            dy = self.cursor_y - win_y
            dist = math.hypot(dx, dy)
            # 注视方向
            self.facing = 1 if dx >= 0 else -1
            # 扑击逻辑
            if (self.pounce_state == "idle"
                    and dist < CURSOR_POUNCE_DIST
                    and now >= self.pounce_cooldown_until
                    and random.random() < POUNCE_CHANCE_PER_TICK):
                self.pounce_state = "windup"
                self.pounce_until = now + POUNCE_WINDUP
                self.behavior = "sit"  # 打断当前行为
            elif self.pounce_state == "windup" and now >= self.pounce_until:
                self.pounce_state = "pouncing"
                self.pounce_until = now + POUNCE_DURATION
                self._play_sound("pounce")
            elif self.pounce_state == "pouncing" and now >= self.pounce_until:
                self.pounce_state = "idle"
                self.pounce_cooldown_until = now + POUNCE_COOLDOWN
        else:
            if self.pounce_state != "idle" and now >= self.pounce_until:
                self.pounce_state = "idle"

        # ---- idle 随机子动作（reading / thinking）----
        if self.state == "idle" and not self.idle_sub:
            if now - self.idle_check_at >= IDLE_SUB_CHECK_INTERVAL:
                self.idle_check_at = now
                if random.random() < IDLE_SUB_TRIGGER_PROB:
                    self.idle_sub = random.choice(("reading", "thinking"))
                    self.idle_sub_until = now + random.uniform(*IDLE_SUB_DURATION_RANGE)
        if self.idle_sub and now > self.idle_sub_until:
            self.idle_sub = ""

        # ---- 性格：台词过期 / 心情衰减 / 自言自语 / 孤独 ----
        if self.say_text and now > self.say_until:
            self.say_text = ""
        if self.mood != "normal" and now - self.mood_since > MOOD_DECAY:
            self._set_mood("normal")
        if self.state == "idle":
            # 自言自语（整活）
            if now - self.mutter_check_at >= MUTTER_CHECK_INTERVAL:
                self.mutter_check_at = now
                if not self.say_text and random.random() < MUTTER_PROB:
                    kind = random.choice(("mutter", "stretch", "lookaround"))
                    self.say(random.choice(QUIPS[kind]))
                    self._play_sound("mutter")
            # 很久没人理 → 主动找你说话（必须早于自动睡眠才说得着）
            if now - self.lonely_check_at >= LONELY_CHECK_INTERVAL:
                self.lonely_check_at = now
                if (not self.say_text
                        and now - self.last_interact > LONELY_AFTER):
                    self._set_mood("lonely")
                    self.say(random.choice(QUIPS["lonely"]))

        behavior_ox, behavior_oy = self._get_behavior_offset(t) if self.state == "idle" else (0, 0)
        frame = _composite(self._pick_cat(t), self.state, t,
                           self.step, self.progress, self.prefs,
                           idle_sub=self.idle_sub,
                           say_text=self.say_text,
                           behavior_offset=(behavior_ox, behavior_oy),
                           phys_state=self.phys_state,
                           pounce_state=self.pounce_state,
                           facing=self.facing,
                           hearts=self.hearts)

        # 逻辑画布 → 物理尺寸（插画风用 LANCZOS 平滑，保持和帧一致）
        if DPI_SCALE != 1.0:
            frame = frame.resize((PHYS_W, PHYS_H), Image.LANCZOS)

        if self.renderer is not None:
            self.renderer.update(_to_bgra(frame))
        else:
            photo = ImageTk.PhotoImage(_to_hardcut_rgb(frame))
            self.label.configure(image=photo)
            self.label.image = photo

    def _pick_cat(self, t: float) -> Image.Image:
        """根据当前行为选择对应帧。

        5 帧分别是：0=sit, 1=blink, 2=yawn, 3=lick, 4=stretch
        不同行为显示完全不同的猫姿势，而不是统一循环。
        """
        # sleeping 直接用闭眼帧（毛色已盖住眼睛、闭眼弧线位置精准），
        # 不再在睁眼图上叠眼线，避免金色眼睛残留 + 眼线错位。
        if self.state == "sleeping" and self.frames:
            return self.frames[1]
        if self.state != "idle" or not self.frames:
            return self.cat_img

        # 行为 → 帧索引映射
        behavior_frame_map = {
            "sit":        0,   # 坐姿
            "lookaround": 0,   # 坐姿+左右偏移（偏移在 _get_behavior_offset 处理）
            "groom":      3,   # 舔毛姿势
            "stretch":    4,   # 伸懒腰姿势（明显变宽变扁）
            "yawn":       2,   # 打哈欠姿势
            "paw_lick":   3,   # 舔爪姿势
        }
        base_idx = behavior_frame_map.get(self.behavior, 0)

        # sit 时偶尔眨眼（用 blink 帧），模拟自然眨眼
        if self.behavior == "sit":
            # 每 4-6 秒眨一次眼，持续 0.3 秒
            blink_cycle = (t % 5.0)
            if 4.7 < blink_cycle < 5.0:
                base_idx = 1

        # 微动作 slow_blink 时也用眨眼帧
        if self.micro_action == "slow_blink":
            base_idx = 1
        # 挥爪：用专门的"举右爪"帧（frame_5，由 sit 基准生成，毛色一致）
        if self.micro_action == "wave":
            base_idx = 5 if len(self.frames) > 5 else 0

        return self.frames[base_idx]

    def _get_idle_level(self) -> int:
        """根据无交互时长计算渐进式空闲等级 0-4。"""
        idle_time = time.time() - self.last_interact
        level = 0
        for i, thresh in enumerate(IDLE_LEVEL_THRESHOLDS):
            if idle_time >= thresh:
                level = i
        return min(level, len(IDLE_LEVEL_THRESHOLDS) - 1)

    def _pick_next_behavior(self) -> str:
        """从主行为表中选下一个行为，跳过冷却中的，渐进式空闲影响权重。"""
        now = time.time()
        self.idle_level = self._get_idle_level()
        candidates = []
        total_weight = 0.0
        for name, (weight, cooldown, _min, _max) in MAIN_BEHAVIORS.items():
            if name in self.behavior_cooldowns and now < self.behavior_cooldowns[name]:
                continue
            if name == self.behavior and name != "sit":
                continue
            w = float(weight)
            if self.idle_level >= 2:
                if name in ("sit", "yawn", "stretch"):
                    w *= 1.0 + self.idle_level * 0.4
                elif name in ("lookaround", "groom", "paw_lick"):
                    w *= max(0.3, 1.0 - self.idle_level * 0.15)
            candidates.append((name, w))
            total_weight += w
        if not candidates:
            return "sit"
        r = random.uniform(0, total_weight)
        acc = 0.0
        for name, w in candidates:
            acc += w
            if r <= acc:
                return name
        return candidates[-1][0]

    def _update_behavior(self) -> None:
        """主行为状态机：检查当前主行为是否结束，结束则选下一个。"""
        now = time.time()
        if now < self.behavior_end:
            return
        _, cooldown, _, _ = MAIN_BEHAVIORS.get(self.behavior, (1, 2, 1, 3))
        self.behavior_cooldowns[self.behavior] = now + cooldown
        next_b = self._pick_next_behavior()
        if next_b != self.behavior:
            self.behavior_blend_until = now + BEHAVIOR_TRANSITION_BLEND
        self.behavior = next_b
        _, _, min_d, max_d = MAIN_BEHAVIORS.get(next_b, (1, 2, 1, 3))
        self.behavior_start = now
        self.behavior_end = now + random.uniform(min_d, max_d)

    def _update_micro_action(self) -> None:
        """微动作：在主行为期间按概率触发，叠加在主行为之上。"""
        now = time.time()
        # 当前微动作结束了
        if self.micro_action and now >= self.micro_until:
            self.micro_action = None
        # 已有微动作在进行中，不触发新的
        if self.micro_action:
            return
        # 非 idle 状态不触发微动作
        if self.state != "idle":
            return
        # 悬停挥手：光标在猫身上停留够久就触发（优先于随机微动作）
        # 一次悬停只挥一次，鼠标离开再回来才会再挥，避免反复晃
        if (self.cursor_in_window and self.hover_since is not None
                and now - self.hover_since >= HOVER_WAVE_DELAY
                and now >= self.wave_cooldown_until
                and not self.wave_done_this_hover):
            self.micro_action = "wave"
            self.micro_until = now + WAVE_DURATION
            self.wave_cooldown_until = now + WAVE_COOLDOWN
            self.wave_done_this_hover = True
            self.say(random.choice(["嗨！", "喵~", "你好呀"]))
            return
        # 逐个掷骰子
        for name, (prob, cooldown, min_d, max_d) in MICRO_ACTIONS.items():
            if name in self.micro_cooldowns and now < self.micro_cooldowns[name]:
                continue
            if random.random() < prob:
                self.micro_action = name
                self.micro_until = now + random.uniform(min_d, max_d)
                self.micro_cooldowns[name] = now + cooldown
                break

    def _get_behavior_offset(self, t: float) -> Tuple[int, int]:
        """根据当前主行为 + 微动作计算猫的位移偏移。

        偏移量放大到肉眼可见的程度（5-20px），配合不同行为的不同帧，
        让用户能明显看出猫在做不同的动作。
        """
        now = time.time()
        bt = now - self.behavior_start
        ox, oy = 0, 0

        # ---- 主行为偏移：全部设为 0，不再有持续摆动（用户要求不晃） ----
        # 行为之间的区别完全由帧本身（sit/blink/yawn/lick/stretch/wave）体现。

        # ---- 微动作偏移：同样不晃 ----
        # wave 举爪帧本身就是挥手动作，不需要额外偏移。

        # 主行为切换过渡混合
        if now < self.behavior_blend_until:
            blend = 1.0 - (self.behavior_blend_until - now) / BEHAVIOR_TRANSITION_BLEND
            ox = int(ox * blend)
            oy = int(oy * blend)
        return ox, oy

    # ---- 状态轮询 ----
    def _poll_status(self) -> None:
        st, msg, step, prog = read_status()
        new_prefs = read_prefs()
        prefs_changed = new_prefs != self.prefs
        # 启动后首次轮询：不把文件里残留的状态当成 agent 主动写入，
        # 一律从 idle 重新开始，避免卡在上一会话遗留的"思考中/运行中"。
        if self._boot:
            self._boot = False
            self.state = "idle"
            self.msg = ""
            self.step = ""
            self.progress = None
            self.last_state = "idle"
            self.root.title("小黑 · idle")
            write_status("idle", "", "", None)
            if prefs_changed:
                self.prefs = new_prefs
            self.root.after(300, self._poll_status)
            return
        if (st != self.state or msg != self.msg
                or step != self.step or prog != self.progress):
            prev_state = self.state
            self.state, self.msg, self.step, self.progress = st, msg, step, prog
            self.root.title(f"小黑 · {self.state}")
            # 状态进入 done 时播音效（仅从非 done 切换过来触发）
            if st == "done" and prev_state != "done":
                self._play_done_chime()
            # sleeping → 任何非 sleeping 视为唤醒，重置交互计时器
            if prev_state == "sleeping" and st != "sleeping":
                self.last_interact = time.time()
            self.last_state = st
            # agent 主动写状态：记录时间；非 idle 状态立即接管（auto_mode=False）
            self.last_agent_write = time.time()
            self.auto_mode = (st == "idle")
        if prefs_changed:
            self.prefs = new_prefs
        self.root.after(300, self._poll_status)

    # ---- 会话活动自动接管 ----
    def _auto_tick(self) -> None:
        now = time.time()
        # 超过 15s 没有任何 agent 写入 → 进入自动模式，让探测器纠正状态
        if now - self.last_agent_write > 15:
            self.auto_mode = True
        if self.auto_mode:
            busy = self.monitor.sample()
            # 探测器只负责两件事，绝不喊"在忙…"（那只是个 CPU 计步器，永远在忙）：
            # 1) 你回来操作时，把打盹的宠物唤醒；
            # 2) 进程空闲 + 仍停在 agent 留下的"工作中"态 → 清回 idle，避免卡在过时状态。
            # 真实的工作状态（thinking/running/waiting/done）由 agent 主动 set 驱动。
            if busy:
                if self.state == "sleeping":
                    write_status("idle", "待命")
            else:
                if self.state in ("thinking", "running", "waiting"):
                    write_status("idle", "待命")
        self.root.after(2000, self._auto_tick)

    # ---- 交互 ----
    # ---- 性格系统 ----
    def _greet(self) -> None:
        """启动打招呼。久别重逢（>30 分钟）说得不一样。"""
        if self.greeted:
            return
        self.greeted = True
        last = float(self.stats.get("last_seen", 0) or 0)
        away = time.time() - last if last else 0
        if away > 1800:
            self.say(random.choice(("你回来啦", "可算想起我了", "我等你半天了")))
        else:
            self.say(random.choice(QUIPS["greet"]))
        self.stats["last_seen"] = time.time()
        write_stats(self.stats)

    def say(self, text: str, duration: float = SAY_DURATION) -> None:
        """让宠物说话（气泡显示台词 duration 秒）。"""
        self.say_text = text
        self.say_until = time.time() + duration

    def _set_mood(self, mood: str) -> None:
        if mood != self.mood:
            self.mood = mood
            self.mood_since = time.time()

    def _pick_quip(self, kind: str) -> str:
        """按场景 + 当前心情挑台词。pat 场景会按心情分级。"""
        if kind == "pat":
            if self.mood == "annoyed":
                key = "pat_annoyed"
            elif self.mood == "happy":
                key = "pat_happy"
            else:
                key = "pat_normal"
        else:
            key = kind
        pool = QUIPS.get(key) or QUIPS.get("mutter") or ["…"]
        return random.choice(pool)

    def _play_sound(self, kind: str) -> None:
        """带优先级/概率/冷却的音效播放。

        - probability < 1 时按概率触发（模拟自然随机性，不是每次都响）
        - 高优先级音效可以在低优先级冷却期内播放
        - 每个音效有独立的 cooldown，避免同音效刷屏
        - prefs.sound=False 完全静音
        """
        if not self.prefs.get("sound", True):
            return
        meta = SOUND_META.get(kind, {"priority": 1, "probability": 1.0, "cooldown": 0.2})
        # 概率判定
        if meta["probability"] < 1.0 and random.random() > meta["probability"]:
            return
        wav_name = SOUNDS.get(kind)
        if not wav_name:
            return
        now = time.time()
        # 独立冷却检查
        last_play = self._sound_last.get(kind, 0)
        if now - last_play < meta["cooldown"]:
            return
        # 全局节流：同优先级或更低优先级的音效在 80ms 内不叠加；
        # 更高优先级音效可以打断刚播过的低优先级（priority 字段真正生效）
        if now - self._last_sound_at < 0.08 and meta["priority"] <= self._last_sound_priority:
            return
        self._last_sound_at = now
        self._last_sound_priority = meta["priority"]
        self._sound_last[kind] = now
        full_path = os.path.join(SOUNDS_DIR, wav_name)
        threading.Thread(target=_play_wav, args=(full_path,), daemon=True).start()

    def on_press(self, e: tk.Event) -> None:
        self.last_interact = time.time()
        # sleeping 状态单击立刻唤醒（拍一下头）
        if self.state == "sleeping":
            write_status("idle", "伸懒腰")
            self.idle_sub = ""
            self.say(self._pick_quip("wake"))
            self._play_sound("wake")
            return
        self._drag.update(dx=e.x, dy=e.y, moved=False,
                          last_x_root=e.x_root, last_y_root=e.y_root)
        # 抓起：解除栖息（否则拖完松手 _update_perch 会把猫拉回窗口标题栏）
        self.perched = False
        # 抓起：进入拖拽状态，瘫软
        self.phys_state = "dragged"
        self.phys_vy = 0
        self.pounce_state = "idle"  # 打断扑击

    def on_drag(self, e: tk.Event) -> None:
        self.last_interact = time.time()
        self._drag["moved"] = True
        # 追踪拖拽速度（必须用屏幕绝对坐标 e.x_root/y_root：
        # 窗口跟随鼠标移动时 e.x/e.y 相对坐标恒≈0，速度会算成 0）
        now = time.time()
        if self._drag.get("last_t"):
            dt = now - self._drag["last_t"]
            if dt > 0:
                dx = e.x_root - self._drag["last_x_root"]
                dy = e.y_root - self._drag["last_y_root"]
                speed = (dx*dx + dy*dy) ** 0.5 / dt
                self._drag["speed"] = speed
        self._drag["last_x_root"] = e.x_root
        self._drag["last_y_root"] = e.y_root
        self._drag["last_t"] = now
        nx = self.root.winfo_x() + (e.x - self._drag["dx"])
        ny = self.root.winfo_y() + (e.y - self._drag["dy"])
        self.root.geometry(f"+{nx}+{ny}")

    def on_release(self, _e: tk.Event) -> None:
        self.last_interact = time.time()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        write_position(x, y)
        was_dragged = self._drag["moved"]
        speed = self._drag.get("speed", 0)
        self._drag["moved"] = False
        self._drag["speed"] = 0
        self._drag["last_t"] = None
        self._drag["last_x_root"] = 0
        self._drag["last_y_root"] = 0
        if not was_dragged:
            # 没移动 = 抚摸
            self.phys_state = "ground"
            self._pat()
        else:
            # 慢速释放（< 500 px/s）= 放置在原地，不掉落
            # 快速释放（>= 500 px/s）= 扔出去，带初速度下落
            if speed < 500:
                self.phys_state = "ground"
                self.phys_vy = 0
                self.say(random.choice(["就放这儿吧", "好的", "嗯"]))
            else:
                self.say(self._pick_quip("drag"))
                self._play_sound("drag")
                if y < self.floor_y:
                    self.phys_state = "falling"
                    # 初速度：甩得越快，飞得越远（限制最大初速度）
                    self.phys_vy = min(400.0, max(100.0, speed * 0.15))
                else:
                    self.phys_state = "ground"

    def on_dblclick(self, _e: tk.Event) -> None:
        write_status("idle", "")
        self.say(self._pick_quip("dblclick"))
        self._play_sound("dblclick")
        # 双击惊吓：小跳一下
        self.phys_vy = -300.0
        self.phys_state = "falling"

    def on_right(self, e: tk.Event) -> None:
        """右键菜单：互动选项 + 只读信息。"""
        menu = tk.Menu(self.root, tearoff=0)
        mode = "分层窗口(真alpha)" if self.renderer else "transparentcolor(降级)"
        level_names = ["陌生", "认识", "熟悉", "亲近", "信赖", "挚友"]
        lvl = self._get_affection_level()
        menu.add_command(label=f"渲染: {mode}", state="disabled")
        menu.add_command(label=f"状态: {self.state}", state="disabled")
        menu.add_command(label=f"行为: {self.behavior} (idle_level={self.idle_level})", state="disabled")
        menu.add_command(label=f"好感度: {self.affection} ({level_names[lvl]})", state="disabled")
        if self.step:
            menu.add_command(label=f"步骤: {self.step}", state="disabled")
        menu.add_separator()
        menu.add_command(label="去窗口上栖息", command=lambda: self._perch_on_foreground())
        menu.add_command(label="激光笔", command=self._toggle_laser)
        menu.add_command(label="叫醒它", command=lambda: self._wake_up())
        menu.add_command(label="重置位置", command=lambda: self._reset_position())
        menu.add_separator()
        menu.add_command(label="退出", command=self._quit_app)
        menu.tk_popup(e.x_root, e.y_root)

    def on_mouse_move(self, e: tk.Event) -> None:
        """光标位置主要由 _tick_body 每帧轮询维护；
        这里仅作事件辅助更新。"""
        self.cursor_x = e.x_root
        self.cursor_y = e.y_root
        self.cursor_in_window = True

    def on_mouse_leave(self, _e: tk.Event) -> None:
        self.cursor_in_window = False
        self.hover_since = None
        self.wave_done_this_hover = False

    # ---- 爱心粒子 ----
    def _spawn_heart(self, x: int, y: int) -> None:
        """在指定位置生成一个爱心粒子。"""
        if len(self.hearts) >= HEART_MAX_COUNT:
            self.hearts.pop(0)
        self.hearts.append({
            "x": x + random.uniform(-8, 8),
            "y": y,
            "t0": time.time(),
            "size": HEART_START_SIZE,
        })

    def _update_hearts(self) -> None:
        """更新所有爱心粒子（上升、摇摆、过期移除）。"""
        now = time.time()
        alive = []
        for h in self.hearts:
            age = now - h["t0"]
            if age < HEART_LIFETIME:
                alive.append(h)
        self.hearts = alive

    # ---- 物理系统 ----
    def _update_physics(self, dt: float) -> None:
        """物理更新：下落、落地检测。"""
        if self.phys_state != "falling":
            return
        now = time.time()
        # 重力加速
        self.phys_vy = min(TERMINAL_VELOCITY, self.phys_vy + GRAVITY * dt)
        cur_y = self.root.winfo_y()
        new_y = cur_y + int(self.phys_vy * dt)
        # 落地检测
        if new_y >= self.floor_y:
            new_y = self.floor_y
            self.phys_state = "landing"
            self.phys_landing_until = now + LANDING_DURATION
            self.phys_vy = 0
            # 落地音效
            self._play_sound("land")
        self.root.geometry(f"+{self.root.winfo_x()}+{new_y}")

    def _wake_up(self) -> None:
        """叫醒宠物。"""
        self.last_interact = time.time()
        if self.state == "sleeping":
            write_status("idle", "伸懒腰")
            self.say(self._pick_quip("wake"))
            self._play_sound("wake")

    def _reset_position(self) -> None:
        """重置到屏幕中间偏右下。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        nx = sw - PHYS_W - 100
        ny = sh - PHYS_H - 120
        self.root.geometry(f"+{nx}+{ny}")
        write_position(nx, ny)
        self.phys_state = "ground"
        self.phys_vy = 0
        self.perched = False

    def _perch_on_foreground(self) -> None:
        """跳到前台窗口的标题栏上栖息。

        延迟 200ms 执行，避免右键菜单还在前台时获取到菜单窗口。
        最大化窗口时猫放在屏幕顶部可见位置，不会跑到屏幕外。
        """
        self.root.after(200, self._do_perch)

    def _do_perch(self) -> None:
        rect = get_foreground_window_rect()
        if not rect:
            self.say("没找到合适的窗口")
            return
        wx, wy, ww, wh, hwnd = rect
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        # 坐在标题栏中间偏右
        target_x = wx + ww // 2 - PHYS_W // 2 + random.randint(-60, 60)
        # 最大化窗口（wy<=0）时，猫放在屏幕顶部 y=0（可见），否则放在标题栏上方
        if wy <= 0:
            target_y = 0
        else:
            target_y = max(0, wy - PHYS_H + 8)
        # 确保在屏幕范围内
        target_x = max(0, min(sw - PHYS_W, target_x))
        target_y = max(0, min(sh - PHYS_H - 60, target_y))
        self.root.geometry(f"+{target_x}+{target_y}")
        write_position(target_x, target_y)
        self.perched = True
        # 用 get_foreground_window_rect 返回的真实 hwnd（枚举兜底找到的那个），
        # 而不是重新 GetForegroundWindow——后者可能是宠物自己
        self.perched_hwnd = hwnd
        self.perch_offset_x = target_x - wx
        self.phys_state = "ground"
        self.say(random.choice(["就在这儿歇会儿", "视野不错", "这个位置好"]))
        self.behavior = "sit"

    def _update_perch(self) -> None:
        """栖息时跟随窗口移动。"""
        if not self.perched:
            return
        now = time.time()
        if now - self.perch_check_at < 0.3:
            return
        self.perch_check_at = now
        try:
            u = ctypes.windll.user32
            if not u.IsWindow(self.perched_hwnd) or not u.IsWindowVisible(self.perched_hwnd):
                self.perched = False
                return
            rect = wintypes.RECT()
            u.GetWindowRect(self.perched_hwnd, ctypes.byref(rect))
            if u.IsIconic(self.perched_hwnd):
                # 窗口最小化了，猫掉下来
                self.perched = False
                self.phys_state = "falling"
                self.phys_vy = 50
                return
            new_x = rect.left + self.perch_offset_x
            # 最大化窗口（top<=0）时保持在屏幕顶部可见位置
            if rect.top <= 0:
                new_y = 0
            else:
                new_y = max(0, rect.top - PHYS_H + 8)
            cur_x = self.root.winfo_x()
            cur_y = self.root.winfo_y()
            if abs(new_x - cur_x) > 2 or abs(new_y - cur_y) > 2:
                self.root.geometry(f"+{new_x}+{new_y}")
        except Exception:
            self.perched = False

    def _get_affection_level(self) -> int:
        """好感度等级 0-5（每 50 点一级）。"""
        return min(5, self.affection // 50)

    def _add_affection(self, amount: int) -> None:
        """增加好感度并保存。"""
        self.affection += amount
        self.stats["affection"] = self.affection
        # 每升一级说一句话
        old_level = (self.affection - amount) // 50
        new_level = self.affection // 50
        if new_level > old_level and new_level <= 5:
            level_names = ["陌生", "认识", "熟悉", "亲近", "信赖", "挚友"]
            self.say(f"好感度提升！现在是「{level_names[new_level]}」")
            self._play_sound("pat_happy")

    def _toggle_laser(self) -> None:
        """激光笔模式开关：创建/销毁红点，切换追逐行为。"""
        self.laser_active = not self.laser_active
        if self.laser_active:
            self.laser_dot = LaserDot()
            self.say("红点！")
            self.last_interact = time.time()
            # 红点初始在全局光标位置
            try:
                px, py = self.root.winfo_pointerxy()
            except Exception:
                px, py = self.cursor_x, self.cursor_y
            self.laser_target_x = px or (self.root.winfo_x() + 100)
            self.laser_target_y = py or (self.root.winfo_y() + 100)
            if self.laser_dot and self.laser_dot.hwnd:
                self.laser_dot.move(self.laser_target_x, self.laser_target_y)
        else:
            if self.laser_dot:
                self.laser_dot.destroy()
                self.laser_dot = None
            self.say("不玩了")
            self.phys_state = "ground"

    def _quit_app(self) -> None:
        """退出：先清理激光笔红点等独立窗口，再销毁主窗口。"""
        try:
            if self.laser_dot:
                self.laser_dot.destroy()
                self.laser_dot = None
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _update_laser_chase(self) -> None:
        """激光笔追逐逻辑：红点全局跟随光标（带延迟），猫向红点移动。"""
        if not self.laser_active or not self.laser_dot:
            return
        now = time.time()
        # 红点全局跟随光标（平滑跟随，不是完全跟随，让猫有机会抓住）
        try:
            px, py = self.root.winfo_pointerxy()
        except Exception:
            px, py = self.cursor_x, self.cursor_y
        self.laser_target_x += (px - self.laser_target_x) * 0.08
        self.laser_target_y += (py - self.laser_target_y) * 0.08
        # 猫抓住红点后，红点"逃跑"到随机位置
        if now < self.laser_caught_until:
            return
        win_cx = self.root.winfo_x() + PHYS_W // 2
        win_cy = self.root.winfo_y() + PHYS_H // 2
        dist = math.hypot(self.laser_target_x - win_cx, self.laser_target_y - win_cy)
        if dist < 50:
            # 抓住了！庆祝一下，然后红点逃跑
            self.laser_caught_until = now + 0.8
            self.pounce_state = "pouncing"
            self.pounce_until = now + 0.3
            self._play_sound("pounce")
            self.say(random.choice(["抓到了！", "我的！", "又跑了"]))
            # 红点逃到随机位置
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.laser_target_x = random.randint(100, sw - 100)
            self.laser_target_y = random.randint(100, sh - 200)
        else:
            # 猫向红点方向缓慢移动
            speed = 3.0
            dx = self.laser_target_x - win_cx
            dy = self.laser_target_y - win_cy
            d = max(1.0, math.hypot(dx, dy))
            nx = self.root.winfo_x() + int(dx / d * speed)
            ny = self.root.winfo_y() + int(dy / d * speed)
            # clamp 到屏幕内，避免追逐时把猫推出屏幕
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            nx = max(0, min(nx, max(0, sw - PHYS_W)))
            ny = max(0, min(ny, max(0, sh - PHYS_H)))
            self.root.geometry(f"+{nx}+{ny}")
            self.facing = 1 if dx >= 0 else -1
            # 追逐时行为切换为 lookaround（警觉状）
            self.behavior = "lookaround"
        # 更新红点位置
        if self.laser_dot and self.laser_dot.hwnd:
            self.laser_dot.move(int(self.laser_target_x), int(self.laser_target_y))

    def _pat(self) -> None:
        """被摸一下：记次数、判连点、按心情回话、冒爱心粒子。"""
        now = time.time()
        # 只保留滑动窗口内的摸头记录（连点检测）
        self.pat_times = [t for t in self.pat_times
                          if now - t < PAT_ANNOY_WINDOW]
        self.pat_times.append(now)

        self.stats["total_pats"] = int(self.stats.get("total_pats", 0)) + 1
        self.stats["last_seen"] = now
        write_stats(self.stats)

        # 连点太多 → 烦了；摸了几下 → 开心
        if len(self.pat_times) >= PAT_ANNOY_COUNT:
            self._set_mood("annoyed")
            self.pat_times = []
            self._add_affection(-2)  # 被摸烦了，好感度微降
        elif len(self.pat_times) >= 3 and self.mood != "annoyed":
            self._set_mood("happy")
            self._add_affection(2)   # 开心时多加点
        else:
            self._add_affection(1)   # 正常抚摸加一点

        # 按当前心情播对应音效（normal/happy/annoyed 三套）
        if self.mood == "annoyed":
            sound_key = "pat_annoyed"
        elif self.mood == "happy":
            sound_key = "pat_happy"
        else:
            sound_key = "pat_normal"
        self._play_sound(sound_key)

        # 冒爱心粒子（在猫头顶位置）
        cat_cx = WIN_W // 2
        cat_cy = WIN_H // 2 - 30
        num_hearts = 2 if self.mood == "happy" else 1
        for _ in range(num_hearts):
            self._spawn_heart(cat_cx + random.randint(-20, 20), cat_cy)

        # 摸够整十次偷偷提一句（它记得你）
        n = self.stats["total_pats"]
        if n and n % 25 == 0:
            self.say(f"都被摸 {n} 次了")
            return

        if self.mood != "annoyed":
            write_status("waiting", "摸头！")
        self.say(self._pick_quip("pat"))

    def _play_done_chime(self) -> None:
        """5 音琶音 wav（避免蜂鸣器音色单调）。"""
        if not self.prefs.get("sound", True):
            return
        full_path = os.path.join(SOUNDS_DIR, DONE_CHIME_WAV)
        threading.Thread(target=_play_wav, args=(full_path,), daemon=True).start()


if __name__ == "__main__":
    PetApp()