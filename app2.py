from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI
import os
import re

app = Flask(__name__)
client = OpenAI()

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN_2"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET_2"])

# 🆕 新增：判斷是否該跳過翻譯
def should_skip_translation(text):
    text = text.strip()

    # 條件 1：純網址
    if re.fullmatch(r"https?://\S+|www\.\S+", text):
        return True

    # 條件 2：純 emoji（使用 Unicode 區段簡單偵測）
    emoji_pattern = re.compile(r"^[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\s]+$")
    if emoji_pattern.fullmatch(text):
        return True

    # 條件 3：純標點或 LINE 表情貼 (如 (angel Moon) )
    if re.fullmatch(r"[.?!,…!！\s]+", text):  # 純標點
        return True
    if re.fullmatch(r"\([^\)]{1,20}\)", text):  # LINE 表情貼格式
        return True

    return False

def translate(prompt):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

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

    # 🛑 不翻譯：純網址、純 emoji、純標點、表情貼
    if should_skip_translation(user_input):
        return

    # 偵測語言
    detect_prompt = f"Detect the language of the following text. Only respond with one word from [Chinese, Korean, English]:\n{user_input}"
    lang = translate(detect_prompt)

    # 中文輸入 → 英 + 韓
    if "Chinese" in lang:
        eng = translate(f"Translate the following Traditional Chinese to English. Only output the result. No explanation:\n{user_input}")
        kor = translate(f"Translate the following English to Korean. Only output the Korean sentence in polite and natural tone:\n{eng}")
        reply = f"{eng}\n{kor}"

    # 韓文輸入 → 英 + 中
    elif "Korean" in lang:
        eng = translate(f"Translate the following Korean to English. Only output the result. No explanation:\n{user_input}")
        zh = translate(f"Translate the following English to Traditional Chinese. Only output the result. No explanation:\n{eng}")
        reply = f"{eng}\n{zh}"

    # 英文輸入 → 中 + 韓
    elif "English" in lang:
        zh = translate(f"Translate the following English to Traditional Chinese. Only output the result. No explanation:\n{user_input}")
        kor = translate(f"Translate the following English to Korean. Only output the Korean sentence in polite and natural tone:\n{user_input}")
        reply = f"{zh}\n{kor}"

    else:
        reply = "無法辨識語言，請輸入中文、韓文或英文。"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
