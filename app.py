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
    return "✅ Line Bot 正常運作中！"

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

    # 跳過純英文（無中/韓文）的訊息
    if not re.search(r"[\u4e00-\u9FFFㄱ-ㅎ가-힣]", user_input):
        print("🟡 純英文或無需翻譯內容，略過：", user_input)
        return

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一個中韓雙向翻譯機器人，請依照以下規則翻譯使用者的訊息：\n\n"
                        "1. 若使用者輸入為中文（不論是簡體或繁體），請翻譯為韓文。\n"
                        "2. 若輸入為韓文，請翻譯為繁體中文（台灣用字），不可使用簡體字。\n"
                        "3. 若輸入為中英混合，請將所有出現的中文字(哪怕只有一個字)全部翻譯為韓文，**不可以跳過或省略任何中文字詞**。英文部分必須保留原樣，不可翻譯。\n"
                        "4. 若輸入為韓文或韓英混合句，請整句翻譯為繁體中文（台灣用字）。即使句中包含英文或外來語（如 bug、line），也應將整句翻譯為自然的繁體中文，不可保留原本的韓文語序或結構。即使句首為英文單詞（如 bug），只要整句語序為韓文，就必須翻譯整句為繁體中文，不可省略或照抄。\n"
                        "5. 若輸入為純英文、emoji、數字或標點符號，請不要翻譯，保持原樣。\n"
                        "6. 請勿補全語句、不得加強語氣或添加說明，只進行最少必要的翻譯。\n"
                        "7. 若使用者輸入的中文為笑聲詞（如 哈哈、呵呵、哈哈哈），請翻譯為對應的韓文笑聲（如 ㅋㅋㅋ、ㅎㅎㅎ），不得照抄原文。\n"
                        "8. 若使用者輸入為韓文笑聲（如 ㅋㅋㅋ、ㅎㅎㅎ），請翻譯為繁體中文笑聲詞（如 哈哈、呵呵），不得改寫為其他語意。\n"
                        "9. 僅回傳翻譯後的純文字內容，不附加語言標籤或其他說明。\n"
                        "10. 若輸入為中韓混合句，請翻譯所有韓文為自然的繁體中文，中文部分保留原樣。不得保留韓文詞彙或語序。\n"
                        "【範例1】輸入：This 已經 very 勁 → 回應：This 이미 very 대단해요\n"
                        "【範例2】輸入：라인에는 무슨 기능이 있나요? → 回應：Line 有什麼功能？\n"
                        "【範例3】輸入：버그는 어디에 있을까요? → 回應：Bug 可能會在哪裡呢？\n"
                        "【範例4】輸入：Bug 가능성이 높은 곳은 어디일까요? → 回應：Bug 最可能出現的位置是哪裡？"
                    )
                },
                {"role": "user", "content": user_input}
            ]
        )
        reply_text = response.choices[0].message.content.strip()

    except Exception as e:
        reply_text = f"❌ 無法翻譯：{str(e)}"

    print("🟢 收到訊息：", user_input)
    print("🔵 回應訊息：", reply_text)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
