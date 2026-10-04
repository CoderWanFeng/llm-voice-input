"""把 build.bat 规范成 cmd.exe 能正确解析的形态：GBK 编码 + CRLF 换行。

为什么需要这个脚本：
    cmd.exe 按控制台默认代码页（简体中文 Windows 是 GBK/936）逐字节解析 .bat，
    并且靠 CRLF 定位行边界。文件若是 UTF-8 或 LF，含中文的 `if (...)` / `for (...)`
    多行块会被解析错位，表现为满屏「'xxx' 不是内部或外部命令」，且报错内容与
    源码完全对不上，极难排查。

用法（改完 build.bat 之后跑一次）：
    python packaging/normalize_bat.py
"""

import sys
from pathlib import Path

TARGET = Path(__file__).with_name("build.bat")


def main() -> int:
    if not TARGET.exists():
        print(f"找不到 {TARGET}")
        return 1

    raw = TARGET.read_bytes()

    # 兼容三种来源：UTF-8（含 BOM）、GBK、已损坏的混合
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = raw.decode(enc)
            src = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        print("无法解码 build.bat，请检查文件是否损坏")
        return 1

    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")

    try:
        out = text.encode("gbk")
    except UnicodeEncodeError as exc:
        print(f"含 GBK 无法表示的字符（如 emoji）：{exc}")
        return 1

    if out != raw:
        TARGET.write_bytes(out)

    crlf = out.count(b"\r\n")
    lone_lf = out.count(b"\n") - crlf
    print(f"完成：源编码={src}，{crlf} 行 CRLF，孤立 LF={lone_lf}，{len(out)} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
