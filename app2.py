from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import os

app = Flask(__name__)

# 設定金鑰
openai.api_key = os.environ["OPENAI_API_KEY"]
line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN_2"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET_2"])

def translate(prompt):
    response = openai.ChatCompletion.create(
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
    user_input = event.message.text

    # 判斷輸入語言
    detect_prompt = f"Detect the language of the following text and return only one word from [Chinese, Korean, English]: {user_input}"
    lang = translate(detect_prompt)

    # 中文 → 英文 → 韓文
    if "Chinese" in lang:
        eng = translate(f"Translate the following Chinese to English: {user_input}")
        kor = translate(f"Translate the following English to Korean in a natural and polite way with fluent word order: {eng}")
        reply = f"[中] {user_input}\n[英] {eng}\n[韓] {kor}"

    # 韓文 → 英文 → 中文
    elif "Korean" in lang:
        eng = translate(f"Translate the following Korean to English: {user_input}")
        zh = translate(f"Translate the following English to Traditional Chinese: {eng}")
        reply = f"[韓] {user_input}\n[英] {eng}\n[中] {zh}"

    # 英文 → 中文 + 韓文
    elif "English" in lang:
        zh = translate(f"Translate the following English to Traditional Chinese: {user_input}")
        kor = translate(f"Translate the following English to Korean in a natural and polite way with fluent word order: {user_input}")
        reply = f"[英] {user_input}\n[中] {zh}\n[韓] {kor}"

    else:
        reply = "無法辨識語言。請輸入中文、英文或韓文。"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run()
