"""全局常量。

规矩 R5：音频参数单一真相源——Recorder 与 AsrClient 必须共用这里的常量，
任何一处不得自行硬编码采样率。
"""

# 音频参数：16kHz / 单声道 / 16bit（与云端 ASR 要求对齐）
AUDIO_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_WIDTH = 2  # bytes per sample, int16

# 规矩 R15 相关：前置缓冲，用于补偿冷启动丢音头
PREROLL_MS = 300

# 规矩 R9：录音时长门禁
MIN_RECORD_MS = 300
# 上限 55 秒：腾讯云"一句话识别"硬限制音频 ≤60 秒，留 5 秒余量
MAX_RECORD_MS = 55_000
# 一句话识别还限制音频 ≤3MB，60 秒 16k 单声道 wav 约 1.9MB，安全
ASR_MAX_AUDIO_BYTES = 3 * 1024 * 1024

# 规矩 R13：ASR 超时与重试
ASR_TIMEOUT_S = 8.0
ASR_RETRY = 1

# 音频块大小：每块 30ms
BLOCK_MS = 30

# 注入时序（毫秒）
PASTE_DELAY_MS = 150
RESTORE_DELAY_MS = 200

# 单块缓冲字节数
BLOCK_BYTES = int(AUDIO_RATE * BLOCK_MS / 1000) * AUDIO_CHANNELS * AUDIO_WIDTH
PREROLL_BLOCKS = max(1, PREROLL_MS // BLOCK_MS)
