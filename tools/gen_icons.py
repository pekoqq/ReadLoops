#!/usr/bin/env python3
"""从矢量几何生成 ReadLoops 全套品牌图标（自包含，无第三方设计工具）。

输出（相对仓库根）：
  app/web/icon.svg               渐变带底主图标（favicon 矢量源）
  app/web/favicon-16.png / favicon-32.png / favicon.ico
  app/web/apple-touch-icon.png   180，满幅无圆角（iOS 自裁）
  app/web/icon-192.png / icon-512.png
  app/web/icon-maskable-512.png  满幅 + 图形缩入 80% 安全区
  assets/logo.png                256，README 用

依赖：本机 Google Chrome（headless 栅格化）+ Pillow（合成 ico 与缩放）。
改 logo：调整下方几何参数后 `python3 tools/gen_icons.py` 即可整套重生成。
"""
import math
import pathlib
import subprocess
import tempfile

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "app" / "web"
ASSETS = ROOT / "assets"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CX = CY = 256
BG = "#1a1818"   # 与深色主题 --bg 一致
FG = "#fdfcfc"   # 与 --text 一致

HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0}}html,body{{width:1024px;height:1024px;overflow:hidden;background:transparent}}
svg{{width:1024px;height:1024px;display:block}}</style></head><body>{svg}</body></html>"""


def logo_svg(radius, line_w, rounded):
    """记忆循环环：不闭合弧（暗→亮轨迹渐变）+ 末端箭头。"""
    gap_c, gap = -52, 42

    def pt(a, r=radius):
        a = math.radians(a)
        return CX + r * math.cos(a), CY + r * math.sin(a)

    a0, a1 = gap_c + gap / 2, gap_c - gap / 2 + 360
    x0, y0 = pt(a0)
    x1, y1 = pt(a1)
    arc = f"M {x0:.2f} {y0:.2f} A {radius} {radius} 0 1 1 {x1:.2f} {y1:.2f}"
    tang = math.radians(a1 + 90)
    tx, ty = math.cos(tang), math.sin(tang)
    nx, ny = -math.cos(math.radians(a1)), -math.sin(math.radians(a1))
    tip = (x1 + tx * 0.89 * line_w, y1 + ty * 0.89 * line_w)
    b1 = (x1 + nx * (line_w / 2 + 6), y1 + ny * (line_w / 2 + 6))
    b2 = (x1 - nx * (line_w / 2 + 6), y1 - ny * (line_w / 2 + 6))
    arrow = (f"M {tip[0]:.2f} {tip[1]:.2f} L {b1[0]:.2f} {b1[1]:.2f} "
             f"L {b2[0]:.2f} {b2[1]:.2f} Z")
    rx = 104 if rounded else 0
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="ReadLoops">
  <rect width="512" height="512" rx="{rx}" fill="{BG}"/>
  <defs>
    <linearGradient id="trail" gradientUnits="userSpaceOnUse" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}">
      <stop offset="0%" stop-color="{FG}" stop-opacity="0.32"/>
      <stop offset="55%" stop-color="{FG}" stop-opacity="0.72"/>
      <stop offset="100%" stop-color="{FG}" stop-opacity="1"/>
    </linearGradient>
  </defs>
  <path d="{arc}" fill="none" stroke="url(#trail)" stroke-width="{line_w}" stroke-linecap="round"/>
  <path d="{arrow}" fill="{FG}" stroke-linejoin="round"/>
</svg>
'''


def render_master(svg: str) -> Image.Image:
    """大窗口渲染 1024 母版（规避 Chrome 小窗口最小宽度钳制）。"""
    with tempfile.TemporaryDirectory() as td:
        hp = pathlib.Path(td) / "p.html"
        hp.write_text(HTML.format(svg=svg))
        raw = pathlib.Path(td) / "raw.png"
        subprocess.run([CHROME, "--headless=new", "--disable-gpu",
                        f"--screenshot={raw}", "--window-size=1024,1024",
                        "--force-device-scale-factor=1",
                        "--default-background-color=00000000",
                        f"file://{hp}"], check=True, capture_output=True)
        return Image.open(raw).convert("RGBA")


def save(im: Image.Image, size: int, name: str):
    im.resize((size, size), Image.LANCZOS).save(WEB / name)
    print("  ", name, size)


def main():
    WEB.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)

    rounded_svg = logo_svg(148, 38, True)
    full_svg = logo_svg(148, 38, False)
    mask_svg = logo_svg(118, 32, False)
    (WEB / "icon.svg").write_text(rounded_svg)
    print("icon.svg")

    rounded = render_master(rounded_svg)
    full = render_master(full_svg)
    maskable = render_master(mask_svg)

    for n in (16, 32, 192, 512):
        save(rounded, n, {16: "favicon-16.png", 32: "favicon-32.png",
                          192: "icon-192.png", 512: "icon-512.png"}[n])
    save(full, 180, "apple-touch-icon.png")
    save(maskable, 512, "icon-maskable-512.png")
    rounded.resize((256, 256), Image.LANCZOS).save(ASSETS / "logo.png")
    print("   ../assets/logo.png 256")

    # favicon.ico：满幅 16/32/48 三尺寸合一
    ico48 = full.resize((48, 48), Image.LANCZOS)
    ico48.save(WEB / "favicon.ico", format="ICO",
               sizes=[(16, 16), (32, 32), (48, 48)])
    print("   favicon.ico [16/32/48]")
    print("done.")


if __name__ == "__main__":
    main()
