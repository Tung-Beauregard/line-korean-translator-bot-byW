from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI
import os
import re

app = Flask(__name__)

# 環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

@app.route("/")
def home():
    return "✅ Second Line Bot is Running!"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text.strip()

    if re.search(r"(只在.*翻譯|不要翻譯|翻譯規則|語言規則|開啟|關閉)", user_input):
        return

    chinese_chars = re.findall(r"[\u4e00-\u9FFF]", user_input)
    korean_chars = re.findall(r"[ㄱ-ㅎ가-힣]", user_input)
    english_chars = re.findall(r"[A-Za-z]", user_input)

    is_chinese = len(chinese_chars) > 0
    is_korean = len(korean_chars) > 0
    is_english = len(english_chars) > 0

    if is_chinese and not is_korean and not is_english:
        instruction = "請將以下中文翻譯成：\n1. 韓文\n2. 英文\n請回傳兩行：第一行韓文、第二行英文。"
    elif is_korean and not is_chinese and not is_english:
        instruction = "請將以下韓文翻譯成：\n1. 中文（台灣用字）\n2. 英文\n請回傳兩行：第一行中文、第二行英文。"
    elif is_english and not is_chinese and not is_korean:
        instruction = "請將以下英文翻譯成：\n1. 韓文\n2. 中文（台灣用字）\n請回傳兩行：第一行韓文、第二行中文。"
    elif is_chinese and is_english and not is_korean:
        instruction = "請將中英混合句中的所有中文字翻譯為韓文，英文保留原樣。\n然後將整句再翻譯為自然英文。回傳兩行：第一行是中韓混合，第二行是英文翻譯。"
    elif is_korean and is_english and not is_chinese:
        instruction = "請將以下韓英混合句翻譯成：\n1. 中文（台灣用字）\n2. 英文\n請回傳兩行。"
    elif is_korean and is_chinese:
        instruction = "請將以下中韓混合句的韓文部分翻譯成中文（台灣用字），中文部分保留。\n然後整句翻成自然英文。回傳兩行：第一行是純中文、第二行是英文。"
    else:
        return

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": user_input}
            ]
        )
        reply_text = response.choices[0].message.content.strip()
    except Exception as e:
        reply_text = f"❌ 翻譯失敗：{str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
