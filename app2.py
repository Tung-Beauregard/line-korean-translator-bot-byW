from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI
import os
import re
from collections import OrderedDict

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

def _cache_get(key: str):
    try:
        value = _translation_cache.pop(key)
        _translation_cache[key] = value
        return value
    except KeyError:
        return None

def _cache_put(key: str, value: str):
    if key in _translation_cache:
        _translation_cache.pop(key)
    elif len(_translation_cache) >= CACHE_SIZE:
        _translation_cache.popitem(last=False)
    _translation_cache[key] = value

def translate(prompt: str) -> str:
    if cached := _cache_get(prompt):
        return cached
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    result = response.choices[0].message.content.strip()
    _cache_put(prompt, result)
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

def safe_detect(text: str) -> str:
    raw = translate(
        f"Detect the language of the following text. Only respond with one word from [Chinese, Korean, English]:\n{text}"
    )
    first = raw.split()[0]
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
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
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
                    f"Rewrite the following Traditional Chinese text into mature, natural English. Only output English:\n{user_input}"
                )
                result = translate(
                    f"Translate the following English text to Korean. Use polite tone (-요/-습니다) and natural word order. Only output Korean:\n{matured_en}"
                )
            elif target == "English":
                result = translate(
                    f"Translate the following text to English. Only output the translation. If it's a cultural term like 'Monkey King', translate it accordingly:\n{user_input}"
                )
            elif target == "Chinese":
                result = translate(
                    f"Translate the following text to Traditional Chinese. Only output the translation. If it's a cultural name, use the locally known version:\n{user_input}"
                )
            elif target == "Korean":
                result = translate(
                    f"Translate the following text to Korean. Use polite tone (-요/-습니다) and natural word order. If it's a cultural name like 'Monkey King', use the commonly known Korean equivalent. Only output the result:\n{user_input}"
                )
            else:
                result = "(略)"
        except Exception:
            result = "(略)"
        results.append(result or "(略)")

    reply = "\n".join(results)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
