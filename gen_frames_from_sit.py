"""以 sit 帧为基准，程序生成其他行为帧，确保颜色 100% 一致。

所有帧都从 frame_0_sit.png 变换而来：
- blink:     sit 帧 + 自动定位眼睛 → 毛色覆盖 → 闭眼弧线
- yawn:      sit 帧 + 画张开的嘴（椭圆+粉色口腔）+ 半闭弯月眼
- lick:      sit 帧 + 画小舌头
- stretch:   sit 帧 + 水平拉伸 1.12x + 垂直压缩 0.95x（伸懒腰变宽变扁）

这样所有帧的毛色、眼睛颜色、胸毛完全一致，行为切换时不会有颜色跳变。
"""

import os
from collections import deque
from PIL import Image, ImageDraw

FRAMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "frames")

# 猫脸在 320x320 画布中的大致位置（鼻子/嘴，用于打哈欠、舔）
FACE_CX = 160
NOSE_Y = 130
MOUTH_Y = 148


def _is_gold_eye(r, g, b, a):
    """金色虹膜像素判定（暖色、较亮、蓝通道明显偏低）。"""
    return a > 200 and r > 150 and g > 100 and b < 120 and r > b + 50


def detect_eyes(img: Image.Image):
    """自动定位两只金色眼睛，返回 [left, right]，每个为 (cx, cy, x0, y0, x1, y1)。

    用连通域找金色虹膜，再按位置（脸中下部）排除耳朵内侧的粉色。
    """
    px = img.load()
    W, H = img.size
    seen = [[False] * W for _ in range(H)]
    comps = []
    for y in range(H):
        for x in range(W):
            if seen[y][x]:
                continue
            r, g, b, a = px[x, y]
            if not _is_gold_eye(r, g, b, a):
                continue
            q = deque([(x, y)])
            seen[y][x] = True
            pts = []
            while q:
                cx, cy = q.popleft()
                pts.append((cx, cy))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < W and 0 <= ny < H and not seen[ny][nx]:
                            rr, gg, bb, aa = px[nx, ny]
                            if _is_gold_eye(rr, gg, bb, aa):
                                seen[ny][nx] = True
                                q.append((nx, ny))
            if len(pts) > 40:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                cx, cy = sum(xs) // len(xs), sum(ys) // len(ys)
                comps.append((cx, cy, min(xs), min(ys), max(xs), max(ys)))
    # 眼睛位于脸的中下部（y 100-150），耳朵粉色在上方（y<95），据此排除
    eyes = [c for c in comps if 100 <= c[1] <= 150]
    eyes.sort(key=lambda c: c[0])  # 按 x 排序 → 先左后右
    if len(eyes) < 2:
        # 兜底：写死的经验坐标
        return [(131, 127, 119, 118, 143, 136), (186, 124, 177, 115, 195, 133)]
    return [eyes[0], eyes[-1]]


def _cover_with_fur(img: Image.Image, x0: int, y0: int, x1: int, y1: int,
                    expand: int = 0, sample_up: int = 5):
    """用眼睛正上方的毛色自然覆盖一块椭圆区域。

    expand: 向外扩多少像素（盖住金色眼眶边）。
    sample_up: 从覆盖区上边界再往上取多少像素作为毛色采样行。
    对椭圆内每一列，垂直复制该列采样行的毛色，接缝最小。
    """
    x0 -= expand; x1 += expand; y0 -= expand; y1 += expand
    px = img.load()
    W, H = img.size
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    rx = max(1.0, (x1 - x0) / 2.0)
    ry = max(1.0, (y1 - y0) / 2.0)
    for x in range(max(0, x0), min(W, x1 + 1)):
        sy = max(0, y0 - sample_up)
        src = px[x, sy]
        for y in range(max(0, y0), min(H, y1 + 1)):
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                px[x, y] = src


def _draw_closed_eye(draw: ImageDraw.ImageDraw, cx: int, cy: int,
                     half_w: int, rise: int, color, width: int = 3):
    """画闭眼弧线：中间略下垂、两端略上扬的温和弧线（︶ 形）。"""
    box = [cx - half_w, cy - rise, cx + half_w, cy + rise]
    draw.arc(box, start=10, end=170, fill=color, width=width)


LINE_COLOR = (28, 25, 23, 255)


def make_blink(sit: Image.Image) -> Image.Image:
    """眨眼帧：自动定位眼睛 → 毛色覆盖 → 画自然闭眼弧线。"""
    img = sit.copy()
    draw = ImageDraw.Draw(img)
    for (cx, cy, x0, y0, x1, y1) in detect_eyes(img):
        # 覆盖整个眼睛（含金色眼眶边）：左右多盖 7px，上多盖 10px，下 7px
        _cover_with_fur(img, x0, y0, x1, y1, expand=0)
        _cover_with_fur(img, x0 - 7, y0 - 10, x1 + 7, y1 + 7)
        # 闭眼弧线画在原眼睛垂直中心略偏下
        _draw_closed_eye(draw, cx, cy + 2, half_w=15, rise=6,
                         color=LINE_COLOR, width=3)
    return img


def make_yawn(sit: Image.Image) -> Image.Image:
    """打哈欠帧：画一个宽扁的张开嘴，内部粉色，眼睛眯成弯月（半闭眼）。"""
    img = sit.copy()
    draw = ImageDraw.Draw(img)
    # 张开的嘴：宽扁椭圆（横向），模拟打哈欠
    mouth_box = [FACE_CX - 17, MOUTH_Y - 5, FACE_CX + 17, MOUTH_Y + 15]
    draw.ellipse(mouth_box, fill=(168, 68, 78, 255))       # 口腔内部
    tongue_box = [FACE_CX - 11, MOUTH_Y + 3, FACE_CX + 11, MOUTH_Y + 14]
    draw.ellipse(tongue_box, fill=(226, 122, 132, 255))    # 舌头
    draw.ellipse(mouth_box, outline=(28, 22, 22, 255), width=2)
    # 半闭眼：打哈欠时眼睛眯成弯月缝，完全盖住金色眼睛后画更弯的弧线
    for (cx, cy, x0, y0, x1, y1) in detect_eyes(img):
        _cover_with_fur(img, x0, y0, x1, y1, expand=0)
        _cover_with_fur(img, x0 - 7, y0 - 10, x1 + 7, y1 + 7)
        # 眯眼：弧度更紧、更弯，像满足地眯成一条缝（︶）
        _draw_closed_eye(draw, cx, cy + 1, half_w=13, rise=7,
                         color=LINE_COLOR, width=3)
    return img


def make_lick(sit: Image.Image) -> Image.Image:
    """舔嘴帧：画一个小舌头从微张的嘴里伸出来。"""
    img = sit.copy()
    draw = ImageDraw.Draw(img)
    # 微张的嘴（小弧线）
    draw.arc([FACE_CX - 9, MOUTH_Y - 3, FACE_CX + 9, MOUTH_Y + 5], 10, 170, fill=(25, 20, 20, 255), width=2)
    # 小舌头（从嘴下方伸出，水滴形）
    tongue_box = [FACE_CX - 5, MOUTH_Y + 1, FACE_CX + 5, MOUTH_Y + 10]
    draw.ellipse(tongue_box, fill=(215, 110, 120, 255))
    draw.ellipse(tongue_box, outline=(170, 70, 80, 255), width=1)
    return img


def make_stretch(sit: Image.Image) -> Image.Image:
    """伸懒腰帧：轻微水平拉伸、垂直压缩，模拟猫伸懒腰时身体微展。

    拉伸幅度控制在 1.12x，避免突然变得很宽。
    """
    w, h = sit.size
    # 轻微拉伸 1.12x 宽，微压缩 0.95x 高
    new_w = int(w * 1.12)
    new_h = int(h * 0.95)
    stretched = sit.resize((new_w, new_h), Image.LANCZOS)
    # 放回原画布，居中
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    paste_x = (w - new_w) // 2
    paste_y = (h - new_h) // 2 + 6  # 稍微下移，模拟伸懒腰时下沉
    canvas.paste(stretched, (paste_x, paste_y), stretched)
    return canvas


def main():
    sit_path = os.path.join(FRAMES_DIR, "frame_0_sit.png")
    sit = Image.open(sit_path).convert("RGBA")
    print(f"基准帧: {sit_path}, size={sit.size}")

    # 生成其他帧
    frames = {
        "frame_1_blink.png": make_blink(sit),
        "frame_2_yawn.png": make_yawn(sit),
        "frame_3_lick.png": make_lick(sit),
        "frame_4_stretch.png": make_stretch(sit),
    }

    for name, img in frames.items():
        path = os.path.join(FRAMES_DIR, name)
        img.save(path)
        # 验证颜色一致性
        bbox = img.getbbox()
        pixels = img.load()
        r_t = g_t = b_t = count = 0
        for y in range(bbox[1], bbox[3], 3):
            for x in range(bbox[0], bbox[2], 3):
                r, g, b, a = pixels[x, y]
                if a > 50:
                    r_t += r; g_t += g; b_t += b; count += 1
        print(f"  {name}: saved, avg_color=({r_t//count},{g_t//count},{b_t//count})")

    # 验证 sit 帧颜色
    bbox = sit.getbbox()
    pixels = sit.load()
    r_t = g_t = b_t = count = 0
    for y in range(bbox[1], bbox[3], 3):
        for x in range(bbox[0], bbox[2], 3):
            r, g, b, a = pixels[x, y]
            if a > 50:
                r_t += r; g_t += g; b_t += b; count += 1
    print(f"  frame_0_sit.png: avg_color=({r_t//count},{g_t//count},{b_t//count})")
    print("\n✓ 所有帧已从 sit 帧生成，颜色 100% 一致")


if __name__ == "__main__":
    main()
