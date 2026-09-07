import logging
import os
import re

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI, RateLimitError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

# ----------------------------------------
# 🔧 規則設定區
# ----------------------------------------

# 來源語言 -> 要翻成的目標語言列表（順序 = 輸出行順序）
# 英文只在「來源不是英文」時才出現，且一律放在最後一行，用來給使用者核對翻譯是否正確
LANG_TARGETS = {
    "Chinese": ["Thai", "English"],
    "Thai": ["Chinese", "English"],
    "English": ["Thai"],
}

REFUSAL_PATTERN = re.compile(
    r"(?i)\bI apologize\b|\bI'm sorry\b|\bI am sorry\b|\bas an AI\b|"
    r"as a language model|I cannot (?:provide|assist|translate)|"
    r"could you (?:please )?(?:clarify|provide more context|verify)"
)

def translate(system_prompt: str, text: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )
    result = response.choices[0].message.content.strip()
    if REFUSAL_PATTERN.search(result):
        raise ValueError(f"model returned a refusal-like response: {result[:100]!r}")
    return result

# ----------------------------------------
# 語言判斷（規則式優先，中/泰/英 Unicode 區段互不重疊，不會互相誤判）
# ----------------------------------------

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
LATIN_RE = re.compile(r"[A-Za-z]")

def _script_counts(text: str) -> dict:
    return {
        "Chinese": len(HAN_RE.findall(text)),
        "Thai": len(THAI_RE.findall(text)),
        "English": len(LATIN_RE.findall(text)),
    }

def detect_by_script(text: str):
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

DETECT_SYSTEM_PROMPT = (
    "You are a language detector for text that may mix Chinese, Thai, and "
    "English. Identify which one of these three languages the text is "
    "PRIMARILY written in (the dominant one, by intent, not just by raw "
    "character count). Respond with exactly one word from this set: "
    "Chinese, Thai, English. No punctuation, no explanation, no other words."
)

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
    if lower.startswith("thai"):
        return "Thai"
    if lower.startswith("english"):
        return "English"
    return first

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

    # 不含中文、泰文、英文字元
    if not re.search(r"[\u4e00-\u9fff\u0E00-\u0E7FA-Za-z]", text):
        return True

    return False

# ----------------------------------------
# 各目標語言的翻譯 system prompt
# ----------------------------------------

THAI_SYSTEM_PROMPT = (
    "你是一個翻譯引擎，將使用者輸入的文字翻譯成泰文。"
    "語氣設定為男性對女性說話，句尾語氣助詞一律使用「ครับ」。"
    "用字遣詞需禮貌且溫柔，避免生硬、命令式或過於直接的講法，"
    "但不可為了溫柔而更動原文語意或添加原文沒有的內容。"
    "若輸入包含英文技術詞彙或品牌名稱（如 Bug、Line），該英文部分保留原樣，不要翻譯。"
    "若輸入包含中文人名、暱稱等專有名詞，且沒有通用的泰文譯名，請依發音音譯成泰文拼寫，"
    "不可原封不動保留中文字元，輸出內容除了保留原樣的英文詞彙與 emoji 外，其餘必須全部是泰文。"
    "若輸入為中文笑聲詞（如 哈哈、呵呵、哈哈哈），請翻譯為對應的泰文笑聲（如 555、5555），不得照抄原文。"
    "若輸入包含 emoji 或顏文字，一律原樣保留在泰文譯文中的對應位置，"
    "不可翻譯成文字描述、不可換成其他符號、也不可自行增加或刪除；"
    "原文沒有的 emoji 不可自行加上。"
    "括號內的羅馬拼音只標泰文的讀音，不要把 emoji 放進括號，也不要為 emoji 標音。"
    "只輸出一行內容：泰文翻譯，後面接一個空格，再接括號包住的羅馬拼音讀音，"
    "格式範例：เจ๋งมากครับ (jèng mâak kráp)。"
    "不要在泰文句尾自行加上句號、逗號等標點符號，除非原文本身就明確帶有該標點；"
    "不要換行、不要任何說明文字，不要照抄原文；"
    "就算輸入很短、口語或模糊，也要盡力給出最佳翻譯，絕不可拒絕翻譯或反問使用者。"
)

CHINESE_SYSTEM_PROMPT = (
    "你是一個翻譯引擎，將使用者輸入的文字翻譯成繁體中文（台灣用字）。"
    "不可使用簡體字，也不可混入日文平假名或片假名（如：ちゃん）。"
    "若輸入為泰文笑聲（如 555、5555），請翻譯為繁體中文笑聲詞（如 哈哈、呵呵），不得改寫為其他語意。"
    "輸出內容除 emoji 外必須全部是繁體中文，不可保留任何泰文原文字元；"
    "若原文句尾有泰文語氣助詞（如 ครับ、ค่ะ），這類助詞沒有實際字面意思，"
    "翻譯時請直接省略，不要把泰文助詞原文照抄貼在中文句尾。"
    "若原文包含人名、暱稱等專有名詞，且沒有通用的中文譯名，請依發音音譯成中文，不可原封不動保留泰文寫法。"
    "若原文包含 emoji 或顏文字，一律原樣保留在譯文中的對應位置，"
    "不可翻譯成文字描述、不可換成其他符號、也不可自行增加或刪除；"
    "原文沒有的 emoji 不可自行加上。"
    "不要在句尾自行加上句號、逗號等標點符號，除非原文本身就明確帶有該標點。"
    "只輸出翻譯後的純文字內容，不附加任何說明、標籤或原文；"
    "就算輸入很短、口語或模糊，也要盡力給出最佳翻譯，絕不可拒絕翻譯或反問使用者。"
)

ENGLISH_SYSTEM_PROMPT = (
    "You are a translation engine. Translate the given text into natural, "
    "accurate English. This English output is shown purely as a "
    "verification reference so the reader can cross-check the other "
    "translation, so prioritize precise, literal accuracy over stylistic "
    "flourish. Apart from emoji, the output must be entirely in English "
    "- do not leave any proper nouns or names in their original script; "
    "transliterate them into English instead. "
    "If the source ends with a Thai politeness "
    "particle (e.g. ครับ, ค่ะ), it has no literal meaning - simply omit "
    "it from the English output rather than copying the original Thai "
    "characters. If the source contains emoji or kaomoji, keep them "
    "exactly as-is, in the corresponding position of the English output "
    "- do not translate them into words, do not replace them with other "
    "symbols, and do not add or remove any. Never introduce emoji that "
    "are not present in the source. "
    "Do not add trailing punctuation (period, comma, etc.) at "
    "the end of the sentence unless the source text clearly has it. "
    "Output ONLY the English translation - no explanations, no "
    "commentary. Always produce your best-effort translation, even if "
    "the input is short, informal, or ambiguous - never refuse or ask "
    "for clarification."
)

TARGET_PROMPTS = {
    "Thai": THAI_SYSTEM_PROMPT,
    "Chinese": CHINESE_SYSTEM_PROMPT,
    "English": ENGLISH_SYSTEM_PROMPT,
}

@app.route("/")
def home():
    return "✅ Line Bot 正常運作中！"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    if not signature:
        abort(400)
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

def _handle_message_inner(event):
    user_input = event.message.text.strip()

    # 泰式笑聲（連續3個以上的5，如 555、5555）強制視為泰文來源
    is_thai_laugh = bool(re.fullmatch(r"5{3,}", user_input))

    if not is_thai_laugh and should_skip_translation(user_input):
        logger.info("略過（符號/網址/emoji/純數字/無中泰英字元）：%s", user_input)
        return

    # 跳過描述翻譯條件或指令型的語句
    if re.search(r"(只在.*翻譯|不要翻譯|開啟翻譯|關閉翻譯|翻譯規則|語言規則|才翻譯|才會翻譯)", user_input):
        logger.info("指令性說明語句略過：%s", user_input)
        return

    lang = "Thai" if is_thai_laugh else safe_detect(user_input)
    if lang not in LANG_TARGETS:
        reply = "無法辨識語言，請輸入中文、泰文或英文。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    results = []
    for target in LANG_TARGETS[lang]:
        try:
            result = translate(TARGET_PROMPTS[target], user_input)
        except RateLimitError:
            logger.exception("OpenAI 額度已用完或超過速率限制：target=%s", target)
            result = "(額度用完)"
        except Exception:
            logger.exception("translate failed: lang=%s target=%s text=%s", lang, target, user_input)
            result = "(略)"
        results.append(result or "(略)")

    reply_text = "\n".join(results)

    logger.info("收到訊息：%s", user_input)
    logger.info("回應訊息：%s", reply_text)

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
