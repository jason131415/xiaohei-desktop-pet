# 小黑桌面宠物

Windows 桌面互动宠物，一只蹲坐在屏幕底部的小黑猫。

## 功能

- **行为状态机**：坐姿、左右张望、舔毛、伸懒腰、打哈欠、舔爪，6 种主行为 + 微动作叠加
- **物理互动**：拖拽移动、快速扔出、重力下落、落地缓冲
- **悬停挥手**：鼠标停在猫身上 0.8 秒，猫会举右爪打招呼
- **激光笔**：右键菜单开启，红点全局跟随鼠标，猫会追逐扑击
- **窗口栖息**：右键"去窗口休息"，猫跳到当前前台窗口标题栏上
- **真实猫叫**：8 个 Mixkit 真实猫叫录音，对应抚摸/拖拽/双击/唤醒等场景
- **好感度系统**：累计互动提升好感，6 个等级从陌生到挚友
- **agent 状态同步**：通过 pet_ctl.py 写入 status.json，宠物 300ms 轮询显示工作状态

## 运行

### 源码运行

需要 Python 3.10+ 和 Pillow：

```bash
pip install Pillow
python pet.py
```

或双击 `start.bat`。

### 打包 exe

```bash
pip install pyinstaller
build.bat
```

产物在 `dist/XiaoheiPet/`，双击 `XiaoheiPet.exe` 即可运行，无需 Python。

## 项目结构

```
pet.py                  主程序（tkinter 分层窗口 + 行为状态机 + 物理引擎）
pet_ctl.py              状态桥 CLI（agent 侧写入 status.json）
soundsynth.py           land/pounce 音效合成脚本
gen_frames_from_sit.py  从坐姿帧生成其他动画帧（眨眼/打哈欠/舔爪/伸懒腰）
start.bat               启动脚本
build.bat               PyInstaller 打包脚本
assets/
  frames/               6 帧精灵（sit/blink/yawn/lick/stretch/wave）
  sounds/               10 个 wav 音效
  sprite.png            帧缺失时的 fallback
```

## 数据目录

运行时数据保存在 `%USERPROFILE%\.workbuddy\pet\`：
- `status.json` — 当前状态
- `position.json` — 窗口位置
- `prefs.json` — 用户偏好
- `stats.json` — 好感度统计
- `pet.log` — 运行日志

## 技术栈

- Python + tkinter（分层透明窗口，WS_EX_LAYERED）
- Pillow（图像合成、帧缩放）
- ctypes Win32 API（窗口枚举、前台窗口检测、DPI 感知）
- winsound（音效播放）
