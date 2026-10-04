"""腾讯云 ASR 连通自检：拿到密钥后跑这个脚本验证。

保存密钥（存进 Windows 凭据管理器，不落明文）：
    .venv/Scripts/python.exe tests/asr_check.py --save <SecretId> <SecretKey>

自检（不给文件则现场录 3 秒）：
    .venv/Scripts/python.exe tests/asr_check.py
    .venv/Scripts/python.exe tests/asr_check.py 我的录音.wav
"""

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from voice_input.asr import TencentAsrClient  # noqa: E402
from voice_input.config import Config, Credential  # noqa: E402
from voice_input.models import AudioClip  # noqa: E402
from voice_input.recorder import Recorder  # noqa: E402


def record(seconds: float = 3.0) -> AudioClip | None:
    rec = Recorder()
    if not rec.open():
        print("麦克风不可用：", rec.error)
        return None
    rec.start()
    print(f"请说话…（{seconds:.0f} 秒）")
    for i in range(int(seconds)):
        time.sleep(1)
        print(f"  {i + 1}/{int(seconds)}")
    clip = rec.stop()
    rec.close()
    return clip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", nargs=2, metavar=("SECRET_ID", "SECRET_KEY"),
                    help="把密钥存进 Windows 凭据管理器")
    ap.add_argument("audio", nargs="?", help="wav 文件路径；不给则现场录 3 秒")
    args = ap.parse_args()

    if args.save:
        sid, sk = args.save
        ok1 = Credential.set("asr_secret_id", sid)
        ok2 = Credential.set("asr_secret_key", sk)
        print("保存结果：", "成功" if (ok1 and ok2) else "失败（请检查 keyring 后端）")
        return 0 if (ok1 and ok2) else 1

    sid = Credential.get("asr_secret_id")
    sk = Credential.get("asr_secret_key")
    if not sid or not sk:
        print("未找到密钥。请先执行 --save，或设置环境变量：")
        print("  set VOICE_INPUT_ASR_SECRET_ID=xxx")
        print("  set VOICE_INPUT_ASR_SECRET_KEY=xxx")
        return 1

    print(f"SecretId：{sid[:8]}…（已脱敏）")

    if args.audio:
        data = pathlib.Path(args.audio).read_bytes()
        # 去掉 44 字节 wav 头得到裸 PCM
        pcm = data[44:] if data[:4] == b"RIFF" else data
        clip = AudioClip(pcm=pcm)
    else:
        clip = record(3.0)

    if clip is None or clip.duration_ms == 0:
        print("没有拿到音频，退出")
        return 1

    cfg = Config.load()
    client = TencentAsrClient(sid, sk, region=cfg.asr_region, engine=cfg.asr_engine)
    print(f"识别中（{clip.duration_ms} ms，引擎 {cfg.asr_engine}，地域 {cfg.asr_region}）…")

    result = client.transcribe(clip)
    if result.ok:
        print("\n识别成功：", result.text)
        return 0
    print("\n识别失败：", result.error)
    return 1


if __name__ == "__main__":
    sys.exit(main())
