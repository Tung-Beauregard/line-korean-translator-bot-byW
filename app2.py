import logging
import os
import re
import threading
from collections import OrderedDict

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
client = OpenAI()

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN_2"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET_2"])

# ----------------------------------------
# 🔧 規則設定區
# ----------------------------------------

LANG_TARGETS = {
    "Chinese": ["English", "Korean"],
    "Korean": ["English", "Chinese"],
    "English": ["Chinese", "Korean"]
}

CACHE_SIZE = 1000
_translation_cache: "OrderedDict[str, str]" = OrderedDict()
_cache_lock = threading.Lock()

def _cache_get(key: str):
    with _cache_lock:
        try:
            value = _translation_cache.pop(key)
            _translation_cache[key] = value
            return value
        except KeyError:
            return None

def _cache_put(key: str, value: str):
    with _cache_lock:
        if key in _translation_cache:
            _translation_cache.pop(key)
        elif len(_translation_cache) >= CACHE_SIZE:
            _translation_cache.popitem(last=False)
        _translation_cache[key] = value

REFUSAL_PATTERN = re.compile(
    r"(?i)\bI apologize\b|\bI'm sorry\b|\bI am sorry\b|\bas an AI\b|"
    r"as a language model|I cannot (?:provide|assist|translate)|"
    r"could you (?:please )?(?:clarify|provide more context|verify)"
)

def translate(system_prompt: str, text: str) -> str:
    cache_key = f"{system_prompt}\u241f{text}"
    if cached := _cache_get(cache_key):
        return cached
    response = client.chat.completions.create(
        model="gpt-5.6-luna",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )
    result = response.choices[0].message.content.strip()
    if REFUSAL_PATTERN.search(result):
        raise ValueError(f"model returned a refusal-like response: {result[:100]!r}")
    _cache_put(cache_key, result)
    return result

def is_preserved_abbreviation(text: str) -> bool:
    preserved = {"ROC", "DRK", "SVS", "HQ", "FFA"}  # 可擴充
    return text.strip().upper() in preserved

def should_skip_translation(text: str) -> bool:
    text = text.strip()

    # 網址
    if re.fullmatch(r"https?://\S+|www\.\S+", text):
        return True

    # emoji 或符號
    if re.fullmatch(r"[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\s]+", text):
        return True

    # 純數字（含符號）
    if re.fullmatch(r"[-+]?[\d,]+(\.\d+)?", text):
        return True

    # 不含中英韓字元
    if not re.search(r"[\u4e00-\u9fff\uac00-\ud7afA-Za-z]", text):
        return True

    return False

DETECT_SYSTEM_PROMPT = (
    "You are a language detector for text that may mix Chinese, Korean, and "
    "English. Identify which one of these three languages the text is "
    "PRIMARILY written in (the dominant one, by intent, not just by raw "
    "character count). Respond with exactly one word from this set: "
    "Chinese, Korean, English. No punctuation, no explanation, no other words."
)

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
HANGUL_RE = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")
LATIN_RE = re.compile(r"[A-Za-z]")

def _script_counts(text: str) -> dict:
    return {
        "Chinese": len(HAN_RE.findall(text)),
        "Korean": len(HANGUL_RE.findall(text)),
        "English": len(LATIN_RE.findall(text)),
    }

def detect_by_script(text: str):
    """用字元範圍做規則式語言判斷。中文/韓文的 Unicode 區段完全不重疊，
    不會有互相誤判的問題；領先幅度不夠大（代表混雜程度高）則回傳 None，
    交給 GPT 輔助判斷。"""
    counts = _script_counts(text)
    total = sum(counts.values())
    if total == 0:
        return None
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top_lang, top_count = ranked[0]
    _, second_count = ranked[1]
    top_ratio = top_count / total
    lead_ratio = (top_count - second_count) / total
    if top_ratio >= 0.6 and lead_ratio >= 0.2:
        return top_lang
    return None

def safe_detect(text: str) -> str:
    script_lang = detect_by_script(text)
    if script_lang:
        return script_lang

    # 規則判斷不出主要語言（混雜程度較高），交給 GPT 輔助判斷
    raw = translate(DETECT_SYSTEM_PROMPT, text)
    parts = raw.split()
    if not parts:
        return ""
    first = parts[0]
    lower = first.lower()
    if lower.startswith("chinese"):
        return "Chinese"
    if lower.startswith("korean"):
        return "Korean"
    if lower.startswith("english"):
        return "English"
    return first

@app.route("/callback2", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        abort(400)
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

def _handle_message_inner(event):
    user_input = event.message.text.strip()

    # 忽略問號或符號
    if user_input in {"?", "？", "??", "？？"} or re.fullmatch(r"[?？]+", user_input):
        return
    if len(user_input) <= 2 and re.fullmatch(r"[\W_]+", user_input):
        return

    # 特定縮寫照抄回覆
    if is_preserved_abbreviation(user_input):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=user_input))
        return

    # 跳過翻譯條件
    if should_skip_translation(user_input):
        return

    lang = safe_detect(user_input)
    if lang not in LANG_TARGETS:
        reply = "無法辨識語言，請輸入中文、韓文或英文。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    results = []

    for target in LANG_TARGETS[lang]:
        try:
            if lang == "Chinese" and target == "Korean":
                matured_en = translate(
                    "You are rewriting Traditional Chinese text into natural, mature "
                    "English, to be used as an intermediate step before Korean "
                    "translation. Output ONLY the English text - no explanations, "
                    "no commentary. Always produce your best-effort rewrite, even if "
                    "the input is short, informal, or slang - never refuse or ask for "
                    "clarification.",
                    user_input,
                )
                result = translate(
                    "You are a translation engine. Translate the given English text "
                    "into Korean. Use polite tone (-요/-습니다) and natural word order. "
                    "Output ONLY the Korean translation - no explanations, no "
                    "commentary. If a proper noun (person, place, character, brand) "
                    "has a well-known, commonly used Korean name, use that name "
                    "instead of a literal translation. Always produce your "
                    "best-effort translation, even if the input is short or "
                    "ambiguous - never refuse or ask for clarification.",
                    matured_en,
                )
            elif target == "English":
                result = translate(
                    "You are a translation engine. Translate the given text into "
                    "natural English. Output ONLY the translated text - no "
                    "explanations, no commentary. If the text contains a proper "
                    "noun (person, place, character, brand) that has a well-known, "
                    "commonly used English name, use that established name instead "
                    "of a literal translation. Always produce your best-effort "
                    "translation, even if the input is short, informal, or "
                    "ambiguous - never refuse or ask for clarification.",
                    user_input,
                )
            elif target == "Chinese":
                result = translate(
                    "You are a translation engine. Translate the given text into "
                    "Traditional Chinese. Output ONLY the translated text - no "
                    "explanations, no commentary. If the text contains a proper "
                    "noun that has a well-known, locally used Chinese name, use "
                    "that name instead of a literal translation. Always produce "
                    "your best-effort translation, even if the input is short, "
                    "informal, or ambiguous - never refuse or ask for "
                    "clarification.",
                    user_input,
                )
            elif target == "Korean":
                result = translate(
                    "You are a translation engine. Translate the given text into "
                    "Korean. Use polite tone (-요/-습니다) and natural word order. "
                    "Output ONLY the translated text - no explanations, no "
                    "commentary. If the text contains a proper noun that has a "
                    "well-known, commonly used Korean name, use that name instead "
                    "of a literal translation. Always produce your best-effort "
                    "translation, even if the input is short, informal, or "
                    "ambiguous - never refuse or ask for clarification.",
                    user_input,
                )
            else:
                result = "(略)"
        except Exception:
            logger.exception("translate failed: lang=%s target=%s text=%s", lang, target, user_input)
            result = "(略)"
        results.append(result or "(略)")

    reply = "\n".join(results)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        _handle_message_inner(event)
    except Exception:
        logger.exception("handle_message failed")
        try:
            line_bot_api.reply_message(
                event.reply_token, TextSendMessage(text="翻譯發生錯誤，請稍後再試")
            )
        except Exception:
            logger.exception("failed to send error reply")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
