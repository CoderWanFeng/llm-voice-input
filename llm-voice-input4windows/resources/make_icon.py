"""生成托盘图标 ico 文件。

用 Pillow 程序化绘制一个麦克风风格图标，保存为 resources/icon.ico。
对应 macOS 版 Resources/Info.plist 中引用的应用图标。

运行：python resources/make_icon.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw


def build_image(size: int = 64) -> Image.Image:
    """绘制麦克风风格图标。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 圆角深色背景
    pad = size // 16
    draw.rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=size // 5,
        fill=(60, 120, 200, 255),
    )
    # 麦克风头：圆角矩形
    mw = size * 0.2
    mh = size * 0.34
    cx = size / 2
    cy_head = size * 0.43
    draw.rounded_rectangle(
        (cx - mw / 2, cy_head - mh / 2, cx + mw / 2, cy_head + mh / 2),
        radius=mw / 2,
        fill=(255, 255, 255, 255),
    )
    # 麦克风支架
    sw = max(2, size // 32)
    draw.line((cx, cy_head + mh / 2, cx, cy_head + mh / 2 + size * 0.13),
              fill=(255, 255, 255, 255), width=sw)
    # U 形托架
    arc_box = (cx - size * 0.16, cy_head + mh / 2 - size * 0.02,
               cx + size * 0.16, cy_head + mh / 2 + size * 0.26)
    draw.arc(arc_box, start=0, end=180, fill=(255, 255, 255, 255), width=sw)
    # 底座
    base_y = arc_box[3]
    draw.line((arc_box[0], base_y, arc_box[2], base_y),
              fill=(255, 255, 255, 255), width=sw)
    return img


def main() -> None:
    """生成多个尺寸的 ico 文件。"""
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    # ICO 支持多尺寸
    sizes = [16, 32, 48, 64, 128, 256]
    images = [build_image(s) for s in sizes]
    images[0].save(out_path, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"已生成 {out_path}")


if __name__ == "__main__":
    main()
