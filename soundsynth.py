"""合成小猫叫声 wav 文件 — 纯 stdlib（wave + struct + math）。

核心改进：所有猫叫都采用"mi-ao"双音节结构 + 颤音 + 鼻音谐波，
而不是简单的 sine 波扫频。

猫叫声学特征：
- "mi" 音节：高频 700-900Hz，短（0.08-0.12s），类似"咿"
- "ao" 音节：中低频 400-600Hz，稍长（0.15-0.25s），类似"嗷"
- 颤音：5-8Hz 频率调制，深度 2-5%
- 鼻音谐波：2x/3x 谐波比例较高，模拟鼻腔共鸣
- 包络：快速起音（10-20ms），缓慢衰减

输出：pet/assets/sounds/{name}.wav，16-bit mono 22050 Hz
"""

import math
import random
import struct
import wave
from pathlib import Path

SR = 22050


def _write_wav(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        for s in samples:
            v = max(-1.0, min(1.0, s))
            f.writeframes(struct.pack("<h", int(v * 32767)))


def _normalize(samples: list[float], peak: float = 0.85) -> list[float]:
    m = max(1e-9, max(abs(s) for s in samples))
    return [s * peak / m for s in samples]


def _adsr(n: int, attack: float, decay: float, sustain: float, release: float,
          sustain_level: float = 0.7) -> list[float]:
    """ADSR 包络。"""
    out = []
    a_n = int(attack * SR)
    d_n = int(decay * SR)
    r_n = int(release * SR)
    s_n = max(0, n - a_n - d_n - r_n)
    for i in range(n):
        if i < a_n:
            v = i / max(1, a_n)
        elif i < a_n + d_n:
            v = 1.0 - (1.0 - sustain_level) * (i - a_n) / max(1, d_n)
        elif i < a_n + d_n + s_n:
            v = sustain_level
        else:
            t = (i - a_n - d_n - s_n) / max(1, r_n)
            v = sustain_level * (1.0 - t)
        out.append(max(0.0, v))
    return out


def _synth_meow(
    duration: float = 0.35,
    f_mi: float = 750.0,       # "mi" 音节基频
    f_ao: float = 500.0,       # "ao" 音节基频
    mi_ratio: float = 0.35,    # "mi" 占总时长比例
    vibrato_rate: float = 6.0, # 颤音频率 Hz
    vibrato_depth: float = 0.04,  # 颤音深度（频率的百分比）
    syllables: int = 1,        # 几声喵
    gap: float = 0.08,         # 声与声之间的间隔
    volume: float = 0.8,
    pitch_rise: float = 0.0,   # 整体音高上扬量（开心时用）
) -> list[float]:
    """合成猫叫：mi-ao 双音节 + 颤音 + 鼻音谐波。

    相位累加（而非 freq*t）确保频率平滑变化。
    """
    if syllables <= 1:
        total_dur = duration
    else:
        total_dur = duration * syllables + gap * (syllables - 1)
    n = int(total_dur * SR)
    out = [0.0] * n

    for syl in range(syllables):
        # 每个音节的起始位置
        start_t = syl * (duration + gap)
        start_i = int(start_t * SR)
        syl_n = int(duration * SR)

        phase = 0.0
        prev_freq = f_mi
        for i in range(syl_n):
            t = i / SR
            global_t = start_t + t
            # 音节内进度
            p = t / duration

            # 频率曲线：mi 段高频 → 过渡 → ao 段中低频
            mi_end = mi_ratio
            if p < mi_end:
                # "mi"：频率略升
                freq = f_mi + 50 * (p / mi_end)
            elif p < mi_end + 0.15:
                # 过渡：mi→ao 下滑
                tp = (p - mi_end) / 0.15
                freq = f_mi - (f_mi - f_ao) * tp
            else:
                # "ao"：频率缓降
                ap = (p - mi_end - 0.15) / max(0.01, 1 - mi_end - 0.15)
                freq = f_ao - 80 * ap

            # 整体音高上扬（开心情绪）
            freq += pitch_rise * p

            # 颤音
            freq *= 1.0 + vibrato_depth * math.sin(2 * math.pi * vibrato_rate * global_t)

            # 相位累加（频率变化时更准确）
            phase += 2 * math.pi * (freq + prev_freq) / 2 / SR
            prev_freq = freq

            # 音色：基频 + 鼻音谐波（2x/3x 比例较高）
            v = (
                0.50 * math.sin(phase)
                + 0.30 * math.sin(2 * phase)
                + 0.15 * math.sin(3 * phase)
                + 0.05 * math.sin(4 * phase)
            )

            # 双音节包络：mi 段短强，ao 段长缓
            if p < mi_end:
                env = math.sin(math.pi * p / mi_end) * 0.9
            else:
                ap = (p - mi_end) / max(0.01, 1 - mi_end)
                env = 0.7 * math.exp(-ap * 3.0) + 0.3 * math.exp(-ap * 0.8)

            idx = start_i + i
            if idx < n:
                out[idx] += v * env * volume

    return _normalize(out)


def synth_pat_normal() -> list[float]:
    """普通抚摸：一声自然的"喵"，0.35s。"""
    return _synth_meow(
        duration=0.35, f_mi=720, f_ao=480,
        vibrato_rate=6.5, vibrato_depth=0.035,
        syllables=1, volume=0.8,
    )


def synth_pat_happy() -> list[float]:
    """开心抚摸：两声"喵喵"，音高上扬，0.3s x2。"""
    return _synth_meow(
        duration=0.28, f_mi=800, f_ao=550,
        vibrato_rate=7.0, vibrato_depth=0.04,
        syllables=2, gap=0.06, volume=0.85,
        pitch_rise=100,
    )


def synth_pat_annoyed() -> list[float]:
    """被摸烦了：短促低沉的"喵！"，带一点不耐烦。"""
    return _synth_meow(
        duration=0.22, f_mi=550, f_ao=350,
        vibrato_rate=5.0, vibrato_depth=0.02,
        syllables=1, volume=0.75,
        pitch_rise=-50,
    )


def synth_drag() -> list[float]:
    """被拖拽：拉长的抗议"喵——"，0.55s，频率下滑。"""
    return _synth_meow(
        duration=0.55, f_mi=650, f_ao=300,
        vibrato_rate=5.5, vibrato_depth=0.05,
        syllables=1, volume=0.85,
        pitch_rise=-150,
    )


def synth_dblclick() -> list[float]:
    """双击惊吓：高音短促的"喵！"，0.18s。"""
    return _synth_meow(
        duration=0.18, f_mi=950, f_ao=650,
        vibrato_rate=8.0, vibrato_depth=0.03,
        syllables=1, volume=0.8,
    )


def synth_wake() -> list[float]:
    """叫醒：慵懒的"喵~"，0.7s，低频慢起。"""
    return _synth_meow(
        duration=0.7, f_mi=500, f_ao=350,
        vibrato_rate=4.5, vibrato_depth=0.05,
        syllables=1, volume=0.7,
        pitch_rise=30,
    )


def synth_mutter() -> list[float]:
    """嘀咕：小声的"咕噜"+轻喵，0.3s，低音量。"""
    # 先合成轻喵
    meow = _synth_meow(
        duration=0.25, f_mi=600, f_ao=400,
        vibrato_rate=5.0, vibrato_depth=0.03,
        syllables=1, volume=0.4,
    )
    # 叠加一点咕噜声（25Hz 低频脉冲）
    n = len(meow)
    for i in range(n):
        t = i / SR
        purr = 0.15 * math.sin(2 * math.pi * 25 * t) * math.exp(-t * 2)
        meow[i] = meow[i] * 0.7 + purr
    return _normalize(meow, peak=0.5)


def synth_done_chime() -> list[float]:
    """任务完成：三声愉快的"喵喵喵"，音高递增。"""
    return _synth_meow(
        duration=0.22, f_mi=780, f_ao=520,
        vibrato_rate=7.0, vibrato_depth=0.04,
        syllables=3, gap=0.05, volume=0.85,
        pitch_rise=150,
    )


def synth_land() -> list[float]:
    """落地音：低沉"咚"，120Hz 基频快速衰减 + 噪声冲击，200ms。"""
    duration = 0.20
    n = int(duration * SR)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        freq = 180 - 100 * min(1.0, t / 0.08)
        phase += 2 * math.pi * freq / SR
        v = 0.7 * math.sin(phase) + 0.2 * math.sin(2 * phase)
        noise = random.uniform(-0.3, 0.3) if i < int(0.02 * SR) else 0
        env = math.exp(-t * 15) if t > 0.003 else t / 0.003
        out.append((v + noise) * env * 0.7)
    return _normalize(out)


def synth_pounce() -> list[float]:
    """扑击音：短促上滑"嗖"，400→900Hz，150ms，带噪声风感。"""
    duration = 0.15
    n = int(duration * SR)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        freq = 400 + 500 * (t / duration) ** 0.6
        phase += 2 * math.pi * freq / SR
        noise = random.uniform(-1, 1)
        wind = 0.3 * noise * math.sin(2 * math.pi * 800 * t)
        v = 0.5 * math.sin(phase) + wind
        env = math.sin(math.pi * t / duration)
        out.append(v * env * 0.6)
    return _normalize(out, peak=0.6)


def main():
    out_dir = Path(__file__).resolve().parent / "assets" / "sounds"
    out_dir.mkdir(parents=True, exist_ok=True)

    specs = [
        ("pat_normal.wav", synth_pat_normal),
        ("pat_happy.wav", synth_pat_happy),
        ("pat_annoyed.wav", synth_pat_annoyed),
        ("drag.wav", synth_drag),
        ("dblclick.wav", synth_dblclick),
        ("wake.wav", synth_wake),
        ("mutter.wav", synth_mutter),
        ("done_chime.wav", synth_done_chime),
        ("land.wav", synth_land),
        ("pounce.wav", synth_pounce),
    ]
    for name, fn in specs:
        path = out_dir / name
        samples = fn()
        _write_wav(path, samples)
        size_kb = path.stat().st_size / 1024
        print(f"  {name:18s} {len(samples)/SR:.2f}s  {size_kb:6.1f} KB")

    print(f"\n✓ {len(specs)} 个 wav 已写入 {out_dir}")


if __name__ == "__main__":
    main()
