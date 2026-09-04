import logging
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from flask import Flask, abort, jsonify, request
from openai import OpenAI

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent


# ============================================================
# 基本設定
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

app = Flask(__name__)


# ============================================================
# 環境變數
# ============================================================

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get(
    "LINE_CHANNEL_ACCESS_TOKEN_2"
)

LINE_CHANNEL_SECRET = os.environ.get(
    "LINE_CHANNEL_SECRET_2"
)


def validate_environment() -> None:
    """啟動時檢查必要環境變數。"""

    missing = []

    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")

    if not LINE_CHANNEL_ACCESS_TOKEN:
        missing.append("LINE_CHANNEL_ACCESS_TOKEN_2")

    if not LINE_CHANNEL_SECRET:
        missing.append("LINE_CHANNEL_SECRET_2")

    if missing:
        raise RuntimeError(
            "缺少必要環境變數："
            + ", ".join(missing)
        )


validate_environment()


# ============================================================
# OpenAI 設定
# ============================================================

openai_client = OpenAI(
    api_key=OPENAI_API_KEY,
    timeout=40.0,
    max_retries=2,
)


# ============================================================
# LINE Bot 設定
# ============================================================

line_configuration = Configuration(
    access_token=LINE_CHANNEL_ACCESS_TOKEN
)

line_handler = WebhookHandler(
    LINE_CHANNEL_SECRET
)


# ============================================================
# 翻譯規則
# ============================================================

LANG_TARGETS = {
    "Chinese": ["English", "Korean"],
    "Korean": ["English", "Chinese"],
    "English": ["Chinese", "Korean"],
}

LANGUAGE_DISPLAY_NAMES = {
    "Chinese": "中文",
    "English": "英文",
    "Korean": "韓文",
}

# 這些縮寫直接照原文回覆，不進行翻譯
PRESERVED_ABBREVIATIONS = {
    "ROC",
    "DRK",
    "SVS",
    "HQ",
    "FFA",
}

MAX_INPUT_LENGTH = 4000
MAX_LINE_OUTPUT_LENGTH = 4900
CACHE_SIZE = 1000


# ============================================================
# 翻譯快取
# ============================================================

_translation_cache: "OrderedDict[str, str]" = OrderedDict()
_cache_lock = threading.Lock()


def cache_get(key: str) -> Optional[str]:
    """讀取 LRU 快取。"""

    with _cache_lock:
        try:
            value = _translation_cache.pop(key)
            _translation_cache[key] = value
            return value
        except KeyError:
            return None


def cache_put(key: str, value: str) -> None:
    """寫入 LRU 快取。"""

    with _cache_lock:
        if key in _translation_cache:
            _translation_cache.pop(key)

        elif len(_translation_cache) >= CACHE_SIZE:
            _translation_cache.popitem(last=False)

        _translation_cache[key] = value


# ============================================================
# 字元與語言判斷
# ============================================================

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
HANGUL_RE = re.compile(
    r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]"
)
# 英文以「詞」為單位計算，中文與韓文以「字」為單位。
# 一個漢字或韓文字承載的資訊量接近一個英文單字；
# 若逐個字母計算，夾在中文句子裡的英文人名
# （例如「被kelly貼」）會被誤判成英文句子。
LATIN_WORD_RE = re.compile(
    r"[A-Za-z]+(?:['’][A-Za-z]+)*"
)

URL_RE = re.compile(
    r"^(?:https?://\S+|www\.\S+)$",
    re.IGNORECASE,
)

NUMBER_RE = re.compile(
    r"^[-+]?(?:\d{1,3}(?:,\d{3})*|\d+)"
    r"(?:\.\d+)?$"
)

SYMBOL_ONLY_RE = re.compile(
    r"^[\W_]+$",
    re.UNICODE,
)

EMOJI_SYMBOL_RE = re.compile(
    r"^["
    r"\U0001F300-\U0001FAFF"
    r"\u2600-\u26FF"
    r"\u2700-\u27BF"
    r"\s"
    r"]+$"
)

REFUSAL_PATTERN = re.compile(
    r"\bI apologize\b|"
    r"\bI'm sorry\b|"
    r"\bI am sorry\b|"
    r"\bas an AI\b|"
    r"as a language model|"
    r"I cannot (?:provide|assist|translate)|"
    r"could you (?:please )?"
    r"(?:clarify|provide more context|verify)",
    re.IGNORECASE,
)


def script_counts(text: str) -> dict[str, int]:
    """計算中文字、韓文字與英文單字的數量。"""

    return {
        "Chinese": len(HAN_RE.findall(text)),
        "Korean": len(HANGUL_RE.findall(text)),
        "English": len(LATIN_WORD_RE.findall(text)),
    }


def detect_by_script(text: str) -> Optional[str]:
    """
    優先使用 Unicode 字元比例判斷語言。

    若文字明顯以某一語言為主，直接回傳；
    若混合程度較高，回傳 None，交給模型判斷。
    """

    counts = script_counts(text)
    total = sum(counts.values())

    if total == 0:
        return None

    ranked = sorted(
        counts.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    top_language, top_count = ranked[0]
    second_count = ranked[1][1]

    top_ratio = top_count / total
    lead_ratio = (top_count - second_count) / total

    if top_ratio >= 0.60 and lead_ratio >= 0.20:
        return top_language

    return None


def is_preserved_abbreviation(text: str) -> bool:
    """判斷是否為不翻譯的保留縮寫。"""

    return text.strip().upper() in PRESERVED_ABBREVIATIONS


def should_skip_translation(text: str) -> bool:
    """判斷是否應忽略該訊息。"""

    cleaned = text.strip()

    if not cleaned:
        return True

    # 單純問號
    if re.fullmatch(r"[?？]+", cleaned):
        return True

    # 網址
    if URL_RE.fullmatch(cleaned):
        return True

    # 純數字
    if NUMBER_RE.fullmatch(cleaned):
        return True

    # 純 emoji 或符號
    if EMOJI_SYMBOL_RE.fullmatch(cleaned):
        return True

    # 很短且只有標點符號
    if len(cleaned) <= 2 and SYMBOL_ONLY_RE.fullmatch(cleaned):
        return True

    # 完全不含中文、韓文或英文字母
    if not re.search(
        r"[\u4e00-\u9fff"
        r"\uac00-\ud7af"
        r"A-Za-z]",
        cleaned,
    ):
        return True

    return False


# ============================================================
# OpenAI 呼叫
# ============================================================

def ask_openai(
    instructions: str,
    text: str,
    *,
    use_cache: bool = True,
) -> str:
    """
    呼叫 OpenAI Responses API。

    使用 instructions 作為系統規則，
    text 作為使用者輸入。
    """

    cache_key = (
        f"{OPENAI_MODEL}\u241f"
        f"{instructions}\u241f"
        f"{text}"
    )

    if use_cache:
        cached = cache_get(cache_key)

        if cached is not None:
            logger.info("Translation cache hit")
            return cached

    logger.info(
        "Calling OpenAI model=%s input_length=%d",
        OPENAI_MODEL,
        len(text),
    )

    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=text,
    )

    result = (response.output_text or "").strip()

    if not result:
        raise ValueError(
            "OpenAI 回傳空白結果"
        )

    if REFUSAL_PATTERN.search(result):
        raise ValueError(
            "模型回傳疑似拒絕內容："
            f"{result[:150]!r}"
        )

    if use_cache:
        cache_put(cache_key, result)

    return result


# ============================================================
# 語言偵測
# ============================================================

DETECT_SYSTEM_PROMPT = """
You are a language detector.

The input may contain a mixture of Traditional Chinese, Korean,
and English.

Identify the language that is primarily used according to the
meaning and intent of the sentence, not merely raw character count.

Return exactly one of the following words:

Chinese
Korean
English

Do not return punctuation, explanations, Markdown, or any other text.
""".strip()


def safe_detect_language(text: str) -> str:
    """先使用規則判斷，必要時再交給 OpenAI。"""

    script_language = detect_by_script(text)

    if script_language:
        return script_language

    raw_result = ask_openai(
        DETECT_SYSTEM_PROMPT,
        text,
    )

    normalized = raw_result.strip().lower()

    if normalized.startswith("chinese"):
        return "Chinese"

    if normalized.startswith("korean"):
        return "Korean"

    if normalized.startswith("english"):
        return "English"

    logger.warning(
        "Unexpected language detection result: %r",
        raw_result,
    )

    return ""


# ============================================================
# 翻譯提示詞
# ============================================================

TRANSLATION_PROMPTS = {
    "English": """
You are a professional translation engine.

Translate the input into natural, fluent English.

Requirements:
- Preserve the original meaning and tone.
- Handle short messages, slang, informal language, and fragments.
- Use established English names for people, places, characters,
  products, and brands when such names exist.
- Do not add explanations.
- Do not add quotation marks.
- Do not add labels.
- Do not use Markdown.
- Return only the English translation.
- Always provide the best possible translation.
""".strip(),

    "Chinese": """
You are a professional translation engine.

Translate the input into natural Traditional Chinese as used in
Taiwan.

Requirements:
- Preserve the original meaning and tone.
- Handle short messages, slang, informal language, and fragments.
- Use established Traditional Chinese names used in Taiwan for
  people, places, characters, products, and brands when available.
- Avoid Simplified Chinese wording unless it is part of a proper noun.
- Do not add explanations.
- Do not add quotation marks.
- Do not add labels.
- Do not use Markdown.
- Return only the Traditional Chinese translation.
- Always provide the best possible translation.
""".strip(),

    "Korean": """
You are a professional translation engine.

Translate the input into natural Korean.

Requirements:
- Preserve the original meaning and tone.
- Use natural Korean word order.
- Use a polite and conversational Korean tone, generally ending
  in -요 or an appropriate polite form.
- Handle short messages, slang, informal language, and fragments.
- Use established Korean names for people, places, characters,
  products, and brands when available.
- Do not add explanations.
- Do not add quotation marks.
- Do not add labels.
- Do not use Markdown.
- Return only the Korean translation.
- Always provide the best possible translation.
""".strip(),
}


def translate_text(text: str, target_language: str) -> str:
    """將文字直接翻譯成指定語言。"""

    prompt = TRANSLATION_PROMPTS.get(target_language)

    if not prompt:
        raise ValueError(
            f"不支援的目標語言：{target_language}"
        )

    return ask_openai(
        prompt,
        text,
    )


def translate_one_target(
    text: str,
    source_language: str,
    target_language: str,
) -> str:
    """翻譯成單一目標語言；失敗時回傳提示字串而不拋出例外。"""

    try:
        return translate_text(
            text,
            target_language,
        )

    except Exception:
        logger.exception(
            "Translation failed: "
            "source=%s target=%s text=%r",
            source_language,
            target_language,
            text[:200],
        )

        return (
            f"［{LANGUAGE_DISPLAY_NAMES[target_language]}"
            "翻譯失敗］"
        )


def translate_to_targets(
    text: str,
    source_language: str,
    target_languages: list[str],
) -> list[str]:
    """
    同時翻譯成多個目標語言。

    每個目標語言各要呼叫一次 OpenAI，串行時最壞情況是單次逾時
    的兩倍，可能拖過 LINE reply token 的有效時間。這裡改成並行
    送出，回傳順序仍與 target_languages 一致。
    """

    if len(target_languages) <= 1:
        return [
            translate_one_target(
                text,
                source_language,
                target_language,
            )
            for target_language in target_languages
        ]

    with ThreadPoolExecutor(
        max_workers=len(target_languages),
        thread_name_prefix="translate",
    ) as executor:
        futures = [
            executor.submit(
                translate_one_target,
                text,
                source_language,
                target_language,
            )
            for target_language in target_languages
        ]

        return [future.result() for future in futures]


# ============================================================
# LINE 回覆
# ============================================================

def truncate_line_message(text: str) -> str:
    """避免超過 LINE 單則文字訊息長度限制。"""

    if len(text) <= MAX_LINE_OUTPUT_LENGTH:
        return text

    return (
        text[:MAX_LINE_OUTPUT_LENGTH - 20]
        + "\n\n［內容過長，已截斷］"
    )


def reply_line_message(
    reply_token: str,
    text: str,
) -> None:
    """使用 LINE Messaging API v3 回覆訊息。"""

    safe_text = truncate_line_message(text)

    with ApiClient(line_configuration) as api_client:
        messaging_api = MessagingApi(api_client)

        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(text=safe_text)
                ],
            )
        )


# ============================================================
# Flask 路由
# ============================================================

@app.route("/", methods=["GET", "HEAD"])
def home():
    """Render 首頁與服務狀態。"""

    return jsonify(
        {
            "status": "ok",
            "service": "W Chinese-English-Korean Translator",
            "model": OPENAI_MODEL,
            "webhook": "/callback2",
        }
    ), 200


@app.route("/health", methods=["GET", "HEAD"])
def health():
    """Render 健康檢查端點。"""

    return "OK", 200


@app.route("/callback2", methods=["POST"])
def callback():
    """接收 LINE Webhook。"""

    signature = request.headers.get(
        "X-Line-Signature"
    )

    if not signature:
        logger.warning(
            "Missing X-Line-Signature header"
        )
        abort(400)

    body = request.get_data(as_text=True)

    logger.info(
        "LINE webhook received: body_length=%d",
        len(body),
    )

    try:
        line_handler.handle(
            body,
            signature,
        )

    except InvalidSignatureError:
        logger.warning(
            "Invalid LINE webhook signature"
        )
        abort(400)

    except Exception:
        logger.exception(
            "Unhandled LINE webhook error"
        )
        abort(500)

    return "OK", 200


# ============================================================
# LINE 訊息處理
# ============================================================

def handle_translation_message(
    event: MessageEvent,
) -> None:
    """實際處理使用者文字訊息。"""

    user_input = event.message.text.strip()

    logger.info(
        "Message received: input_length=%d",
        len(user_input),
    )

    if should_skip_translation(user_input):
        logger.info(
            "Message skipped by rule"
        )
        return

    if len(user_input) > MAX_INPUT_LENGTH:
        reply_line_message(
            event.reply_token,
            "文字太長，請分段輸入。"
            f"每次最多 {MAX_INPUT_LENGTH} 個字元。",
        )
        return

    if is_preserved_abbreviation(user_input):
        reply_line_message(
            event.reply_token,
            user_input,
        )
        return

    source_language = safe_detect_language(
        user_input
    )

    if source_language not in LANG_TARGETS:
        reply_line_message(
            event.reply_token,
            "無法辨識語言，請輸入中文、英文或韓文。",
        )
        return

    target_languages = LANG_TARGETS[
        source_language
    ]

    results = translate_to_targets(
        user_input,
        source_language,
        target_languages,
    )

    reply = "\n".join(results)

    reply_line_message(
        event.reply_token,
        reply,
    )


@line_handler.add(
    MessageEvent,
    message=TextMessageContent,
)
def handle_message(event: MessageEvent):
    """LINE 文字訊息事件入口。"""

    try:
        handle_translation_message(event)

    except Exception:
        logger.exception(
            "Message handler failed"
        )

        try:
            reply_line_message(
                event.reply_token,
                "翻譯發生錯誤，請稍後再試。",
            )

        except Exception:
            logger.exception(
                "Failed to send LINE error reply"
            )


# ============================================================
# 本機啟動
# ============================================================

if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", "5000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
