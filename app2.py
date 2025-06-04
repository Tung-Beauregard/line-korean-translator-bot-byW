from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI
import os

app = Flask(__name__)
client = OpenAI()

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN_2"])
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET_2"])

# 輸入語言對應應翻譯的其他兩種語言
LANG_TARGETS = {
    "Chinese": ["English", "Korean"],
    "Korean": ["English", "Chinese"],
    "English": ["Chinese", "Korean"]
}

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

    # 偵測語言
    detect_prompt = f"Detect the language of the following text. Only respond with one word from [Chinese, Korean, English]:\n{user_input}"
    lang = translate(detect_prompt)

    if lang not in LANG_TARGETS:
        reply = "無法辨識語言，請輸入中文、韓文或英文。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    results = []

    for target in LANG_TARGETS[lang]:
        if target == "English":
            result = translate(
                f"Translate the following text to English. Only output the translation. If it's a cultural term like 'Monkey King', translate it accordingly:\n{user_input}"
            )
        elif target == "Chinese":
            result = translate(
                f"Translate the following text to Traditional Chinese. Only output the translation. If it's a cultural name, use the locally known version:\n{user_input}"
            )
        elif target == "Korean":
            result = translate(
                f"Translate the following text to Korean. Use polite tone and natural word order. If it's a cultural name like 'Monkey King', use the commonly known Korean equivalent. Only output the result:\n{user_input}"
            )
        results.append(result)

    reply = "\n".join(results)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
