from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI
import os

app = Flask(__name__)
client = OpenAI()

# Render 上的環境變數
line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN_2"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET_2"])

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
    user_input = event.message.text

    # 偵測語言
    detect_prompt = f"Detect the language of the following text and return one word only from [Chinese, Korean, English]: {user_input}"
    lang = translate(detect_prompt)

    if "Chinese" in lang:
        eng = translate(f"Translate the following Traditional Chinese to English:\n{user_input}")
        kor = translate(f"Translate the following English to Korean with natural word order and polite tone:\n{eng}")
        reply = f"[中] {user_input}\n[英] {eng}\n[韓] {kor}"

    elif "Korean" in lang:
        eng = translate(f"Translate the following Korean to English:\n{user_input}")
        zh = translate(f"Translate the following English to Traditional Chinese:\n{eng}")
        reply = f"[韓] {user_input}\n[英] {eng}\n[中] {zh}"

    elif "English" in lang:
        zh = translate(f"Translate the following English to Traditional Chinese:\n{user_input}")
        kor = translate(f"Translate the following English to Korean with natural word order and polite tone:\n{user_input}")
        reply = f"[英] {user_input}\n[中] {zh}\n[韓] {kor}"

    else:
        reply = "無法辨識語言，請輸入中文、韓文或英文。"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
