import telebot
from telebot import types
from flask import Flask, request

import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup
import re
import time
import threading

# مكتبة MongoDB
from pymongo import MongoClient

# ----------------------------------
# إعدادات MongoDB
# ----------------------------------
MONGO_URI = "mongodb+srv://azal12345zz:KhKZxYFldC2Uz5BC@cluster0.fruat.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(MONGO_URI)
DB_NAME = "mydatabase"
db = client[DB_NAME]

admins_coll = db["admins"]                # لتخزين أسماء الأدمن
users_coll = db["users"]                  # تخزين بيانات المستخدم في مستند واحد { username: ..., accounts: [...] }
accounts_for_sale_coll = db["accounts_for_sale"]  # لتخزين الحسابات المعروضة للبيع
subscribers_coll = db["subscribers"]      # لتخزين أرقام الـchat_id للمشتركين

def init_db():
    """
    تهيئة وإضافة فهارس (Indexes) فريدة لتحسين أداء MongoDB
    وإنشاء قيود uniqueness حيث يلزم.
    """
    admins_coll.create_index("username", unique=True)
    # نجعل username فريدًا (كل مستخدم له وثيقة واحدة):
    users_coll.create_index("username", unique=True)
    accounts_for_sale_coll.create_index("account")
    subscribers_coll.create_index("chat_id", unique=True)


# ========== دوال خاصة بالأدمن ==========
def add_admin(username: str):
    """ إضافة أدمن جديد. إذا كان موجودًا مسبقًا، فلن يضيفه مجددًا. """
    try:
        admins_coll.insert_one({"username": username})
    except:
        pass

def is_admin(username: str) -> bool:
    """ التحقق هل المستخدم أدمن أم لا. """
    doc = admins_coll.find_one({"username": username})
    return doc is not None

def remove_admin(username: str):
    """ حذف أدمن من القائمة. """
    admins_coll.delete_one({"username": username})

# ========== دوال خاصة بالمستخدمين (users) ==========
def create_user_if_not_exists(username: str):
    """
    ينشئ مستخدمًا جديدًا بهيكل أساسي إن لم يكن موجودًا.
    {
      "username": "someUser",
      "accounts": []
    }
    """
    user_doc = users_coll.find_one({"username": username})
    if not user_doc:
        users_coll.insert_one({
            "username": username,
            "accounts": []
        })

def add_allowed_user_account(username: str, account: str):
    """
    إضافة حساب واحد لمستخدم معيّن داخل قائمة accounts.
    يخزن بشكل كائن {"account": account_string}
    """
    create_user_if_not_exists(username)
    users_coll.update_one(
        {"username": username},
        {"$push": {"accounts": {"account": account}}}
    )

def get_allowed_accounts(username: str) -> list:
    """
    جلب جميع الحسابات المرتبطة بمستخدم معيّن.
    يعيد قائمة سترينغ فقط (account) رغم أنها مخزنة ككائنات.
    """
    user_doc = users_coll.find_one({"username": username})
    if not user_doc or "accounts" not in user_doc:
        return []
    return [acc_obj["account"] for acc_obj in user_doc["accounts"]]

def delete_allowed_accounts(username: str, accounts: list = None):
    """
    حذف حسابات من مستخدم معيّن. 
    - إذا لم تُمرر قائمة حسابات -> حذف كل شيء.
    - إذا مررت قائمة -> حذف هذه الحسابات فقط.
    """
    user_doc = users_coll.find_one({"username": username})
    if not user_doc:
        return

    if not accounts:
        # احذف المستخدم كليًا أو أفرغ قائمة accounts
        users_coll.update_one(
            {"username": username},
            {"$set": {"accounts": []}}
        )
    else:
        for acc in accounts:
            users_coll.update_one(
                {"username": username},
                {"$pull": {"accounts": {"account": acc}}}
            )

def get_users_count() -> int:
    """
    إرجاع عدد المستخدمين (عدد الوثائق في users_coll)
    """
    return users_coll.count_documents({})

# ========== دوال خاصة بالحسابات المعروضة للبيع (accounts_for_sale) ==========
def add_account_for_sale(account: str):
    """ إضافة حساب واحد لقائمة البيع. """
    accounts_for_sale_coll.insert_one({"account": account})

def add_accounts_for_sale(accounts: list):
    """ إضافة عدة حسابات لقائمة البيع دفعة واحدة. """
    docs = [{"account": acc} for acc in accounts]
    accounts_for_sale_coll.insert_many(docs)

def get_accounts_for_sale() -> list:
    """ جلب جميع الحسابات المعروضة للبيع. """
    docs = accounts_for_sale_coll.find()
    return [doc["account"] for doc in docs]

def remove_accounts_from_sale(accounts: list):
    """ حذف مجموعة من الحسابات من قائمة البيع. """
    for acc in accounts:
        accounts_for_sale_coll.delete_one({"account": acc})

# ========== دوال خاصة بالمشتركين (subscribers) ==========
def add_subscriber(chat_id: int):
    """
    إضافة مشترك (بالاعتماد على chat_id).
    إذا كان موجودًا، لن يضيفه مرة أخرى (بسبب الاندكس الفريد).
    """
    subscribers_coll.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id}},
        upsert=True
    )

def get_subscribers() -> list:
    """ جلب كل المشتركين (قائمة بـ chat_id). """
    docs = subscribers_coll.find()
    return [doc["chat_id"] for doc in docs]


# ----------------------------------
# إعداد البوت و Flask
# ----------------------------------
TOKEN = "7801426148:AAERaD89BYEKegqGSi8qSQ-Xooj8yJs41I4"
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

EMAIL = "azal12345zz@gmail.com"
PASSWORD = "pbnr pihp anhm vlxp"
IMAP_SERVER = "imap.gmail.com"

# قاموس مؤقت في الذاكرة لتخزين الحساب المحدد لكل مستخدم
user_accounts = {}

# فتح اتصال البريد مرة واحدة
mail = imaplib.IMAP4_SSL(IMAP_SERVER)
mail.login(EMAIL, PASSWORD)

# ----------------------------------
# دوال مساعدة
# ----------------------------------

def clean_text(text):
    return text.strip()

def retry_imap_connection():
    global mail
    for attempt in range(3):
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(EMAIL, PASSWORD)
            print("✅ اتصال IMAP ناجح.")
            return
        except Exception as e:
            print(f"❌ فشل الاتصال (المحاولة {attempt + 1}): {e}")
            time.sleep(2)
    print("❌ فشل إعادة الاتصال بعد عدة محاولات.")

def retry_on_error(func):
    """ ديكورتر لإعادة المحاولة عند حدوث خطأ في جلب الرسائل """
    def wrapper(*args, **kwargs):
        retries = 3
        for attempt in range(retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if "EOF occurred" in str(e) or "socket" in str(e):
                    time.sleep(2)
                    print(f"Retrying... Attempt {attempt + 1}/{retries}")
                else:
                    return f"Error fetching emails: {e}"
        return "Error: Failed after multiple retries."
    return wrapper

@retry_on_error
def fetch_email_with_link(account, subject_keywords, button_text):
    retry_imap_connection()
    try:
        mail.select("inbox")
        _, data = mail.search(None, 'ALL')
        mail_ids = data[0].split()[-5:]  # آخر 5 رسائل
        for mail_id in reversed(mail_ids):
            _, msg_data = mail.fetch(mail_id, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding if encoding else "utf-8")

            if any(keyword in subject for keyword in subject_keywords):
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        html_content = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        if account in html_content:
                            soup = BeautifulSoup(html_content, 'html.parser')
                            for a in soup.find_all('a', href=True):
                                if button_text in a.get_text():
                                    return a['href']
        return "طلبك غير موجود."
    except Exception as e:
        return f"Error fetching emails: {e}"

@retry_on_error
def fetch_email_with_code(account, subject_keywords):
    retry_imap_connection()
    try:
        mail.select("inbox")
        _, data = mail.search(None, 'ALL')
        mail_ids = data[0].split()[-5:]
        for mail_id in reversed(mail_ids):
            _, msg_data = mail.fetch(mail_id, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding if encoding else "utf-8")

            if any(keyword in subject for keyword in subject_keywords):
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        html_content = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        if account in html_content:
                            code_match = re.search(r'\b\d{4}\b', BeautifulSoup(html_content, 'html.parser').get_text())
                            if code_match:
                                return code_match.group(0)
        return "طلبك غير موجود."
    except Exception as e:
        return f"Error fetching emails: {e}"

# ----------------------------------
# دالة لمعالجة الطلبات (Thread)
# ----------------------------------
def handle_request_async(chat_id, account, message_text):
    if message_text == 'طلب رابط تحديث السكن':
        response = fetch_email_with_link(account, ["تحديث السكن"], "نعم، أنا قدمت الطلب")
    elif message_text == 'طلب رمز السكن':
        response = fetch_email_with_link(account, ["رمز الوصول المؤقت"], "الحصول على الرمز")
    elif message_text == 'طلب استعادة كلمة المرور':
        response = fetch_email_with_link(account, ["إعادة تعيين كلمة المرور"], "إعادة تعيين كلمة المرور")
    elif message_text == 'طلب رمز تسجيل الدخول':
        response = fetch_email_with_code(account, ["رمز تسجيل الدخول"])
    elif message_text == 'طلب رابط عضويتك معلقة':
        response = fetch_email_with_link(account, ["عضويتك في Netflix معلّقة"], "إضافة معلومات الدفع")
    else:
        response = "ليس لديك صلاحية لتنفيذ هذا الطلب."

    bot.send_message(chat_id, response)

# ----------------------------------
# أوامر البوت
# ----------------------------------

@bot.message_handler(commands=['start'])
def start_message(message):
    telegram_username = clean_text(message.from_user.username)
    # إذا المستخدم غير موجود في الداتا، ننشئه بدون حسابات
    create_user_if_not_exists(telegram_username)

    user_accounts_list = get_allowed_accounts(telegram_username)
    if is_admin(telegram_username) or user_accounts_list:
        bot.send_message(message.chat.id, "يرجى إدخال اسم الحساب الذي ترغب في العمل عليه:")
        bot.register_next_step_handler(message, process_account_name)
    else:
        bot.send_message(message.chat.id, "غير مصرح لك باستخدام هذا البوت.")

def process_account_name(message):
    user_name = clean_text(message.from_user.username)
    account_name = clean_text(message.text)
    user_allowed_accounts = get_allowed_accounts(user_name)

    if (account_name in user_allowed_accounts) or is_admin(user_name):
        user_accounts[user_name] = account_name

        markup = types.ReplyKeyboardMarkup(row_width=1)
        # أزرار عامة للمستخدم العادي
        btns = [
            types.KeyboardButton('طلب رابط تحديث السكن'),
            types.KeyboardButton('طلب رمز السكن'),
            types.KeyboardButton('طلب استعادة كلمة المرور'),
            types.KeyboardButton('عرض الحسابات المرتبطة بي'),
            # بدل "عرض الحسابات المعروضة للبيع" -> "شراء حسابات للبيع"
            types.KeyboardButton('شراء حسابات للبيع')
        ]
        # أزرار إضافية للأدمن
        if is_admin(user_name):
            btns.extend([
                types.KeyboardButton('طلب رمز تسجيل الدخول'),
                types.KeyboardButton('طلب رابط عضويتك معلقة'),
                types.KeyboardButton('إضافة حسابات للبيع'),
                types.KeyboardButton('عرض الحسابات للبيع'),  # للأدمن فقط
                types.KeyboardButton('حذف حسابات من المعروضة للبيع'),
                types.KeyboardButton('إرسال رسالة جماعية'),
                types.KeyboardButton('إضافة مستخدم جديد'),
                types.KeyboardButton('إضافة حسابات لمستخدم'),
                types.KeyboardButton('حذف مستخدم مع جميع حساباته'),
                types.KeyboardButton('حذف جزء من حسابات المستخدم'),
                types.KeyboardButton('إضافة مشترك'),
                types.KeyboardButton('عرض عدد المستخدمين')
            ])
        markup.add(*btns)
        bot.send_message(message.chat.id, "اختر العملية المطلوبة:", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "اسم الحساب غير موجود ضمن الحسابات المصرح بها.")


@bot.message_handler(func=lambda message: message.text in [
    'طلب رابط تحديث السكن',
    'طلب رمز السكن',
    'طلب استعادة كلمة المرور',
    'طلب رمز تسجيل الدخول',
    'طلب رابط عضويتك معلقة'
])
def handle_requests(message):
    user_name = clean_text(message.from_user.username)
    account = user_accounts.get(user_name)
    if not account:
        bot.send_message(message.chat.id, "لم يتم تحديد حساب بعد.")
        return

    bot.send_message(message.chat.id, "جاري الطلب...")
    thread = threading.Thread(target=handle_request_async, args=(message.chat.id, account, message.text))
    thread.start()

@bot.message_handler(func=lambda message: message.text == 'عرض الحسابات المرتبطة بي')
def show_user_accounts(message):
    user_name = clean_text(message.from_user.username)
    user_accounts_list = get_allowed_accounts(user_name)
    if user_accounts_list:
        response = "✅ الحسابات المرتبطة بك:\n" + "\n".join(user_accounts_list)
    else:
        response = "❌ لا توجد حسابات مرتبطة بحسابك."
    bot.send_message(message.chat.id, response)


# ----------------------------------
# ================================
#       الحسابات المعروضة للبيع
# ================================
# ----------------------------------

@bot.message_handler(func=lambda message: message.text == 'إضافة حسابات للبيع')
def add_accounts_for_sale_handler(message):
    if not is_admin(message.from_user.username):
        return bot.send_message(message.chat.id, "❌ أنت لست أدمن.")
    bot.send_message(message.chat.id, "📝 الرجاء إدخال الحسابات (كل حساب في سطر):")
    bot.register_next_step_handler(message, save_accounts_for_sale)

def save_accounts_for_sale(message):
    new_accounts = message.text.strip().split('\n')
    add_accounts_for_sale(new_accounts)
    bot.send_message(message.chat.id, "✅ تم إضافة الحسابات إلى قائمة البيع بنجاح.")

@bot.message_handler(func=lambda message: message.text in ['عرض الحسابات للبيع', 'عرض الحسابات المعروضة للبيع'])
def show_accounts_for_sale_handler(message):
    """
    هذا الزر للأدمن فقط (إذا أردت). المستخدم العادي لا يراه.
    """
    accounts = get_accounts_for_sale()
    if not accounts:
        bot.send_message(message.chat.id, "❌ لا توجد حسابات متوفرة للبيع حاليًا.")
    else:
        accounts_text = "\n".join(accounts)
        bot.send_message(message.chat.id, f"📋 الحسابات المتوفرة للبيع:\n{accounts_text}")

@bot.message_handler(func=lambda message: message.text == 'حذف حسابات من المعروضة للبيع')
def remove_accounts_from_sale_handler(message):
    if not is_admin(message.from_user.username):
        return bot.send_message(message.chat.id, "❌ أنت لست أدمن.")
    bot.send_message(message.chat.id, "📝 أرسل الحسابات التي تريد حذفها من المعروضة للبيع (حساب في كل سطر):")
    bot.register_next_step_handler(message, process_accounts_removal)

def process_accounts_removal(message):
    accounts_to_remove = message.text.strip().split("\n")
    remove_accounts_from_sale(accounts_to_remove)
    bot.send_message(message.chat.id, "✅ تم حذف الحسابات من قائمة البيع بنجاح.")

# ----------------------------------
# شراء عدد معين من الحسابات (مسموح للمستخدم العادي)
# ----------------------------------
@bot.message_handler(func=lambda message: message.text == 'شراء حسابات للبيع')
def buy_account_from_sale_start(message):
    """
    عند النقر على زر "شراء حسابات للبيع"،
    نعرض عدد الحسابات المتوفرة ثم يطلب عدد يريد شراءه.
    """
    available_accounts = get_accounts_for_sale()
    if not available_accounts:
        return bot.send_message(message.chat.id, "❌ لا توجد حسابات للبيع حالياً.")
    # عرض عدد الحسابات المتاحة
    count_available = len(available_accounts)
    bot.send_message(message.chat.id,
                     f"يوجد حالياً {count_available} حساب معروض للبيع.\n"
                     "كم حساباً ترغب بشرائه؟")
    bot.register_next_step_handler(message, process_buy_accounts_count)

def process_buy_accounts_count(message):
    user_name = message.from_user.username
    available_accounts = get_accounts_for_sale()

    if not available_accounts:
        return bot.send_message(message.chat.id, "❌ لا توجد حسابات للبيع حالياً.")

    try:
        count_to_buy = int(message.text.strip())
    except ValueError:
        return bot.send_message(message.chat.id, "❌ الرجاء إدخال رقم صحيح.")

    if count_to_buy <= 0:
        return bot.send_message(message.chat.id, "❌ لا يمكن شراء عدد صفر أو أقل.")
    if count_to_buy > len(available_accounts):
        return bot.send_message(message.chat.id,
                                f"❌ العدد المطلوب ({count_to_buy}) أكبر من المتوفر حالياً ({len(available_accounts)}).")

    # خذ أول count_to_buy حساب
    purchased = available_accounts[:count_to_buy]
    # احذفها من قائمة البيع
    remove_accounts_from_sale(purchased)
    # أضفها للمستخدم
    for acc in purchased:
        add_allowed_user_account(user_name, acc)

    # أظهر للمستخدم الأسماء أو لا حسب رغبتك
    # إذا أردت عدم إظهار الأسماء:
    # bot.send_message(message.chat.id, f"✅ تم شراء {count_to_buy} حسابات وإضافتها لحسابك.")
    # أو إظهارها:
    bought_text = "\n".join(purchased)
    bot.send_message(message.chat.id,
                     f"✅ تم شراء الحسابات التالية وإضافتها لحسابك:\n{bought_text}")

# ----------------------------------
# ================================
#        إدارة المستخدمين
# ================================
# ----------------------------------

@bot.message_handler(func=lambda message: message.text == 'إضافة مستخدم جديد')
def add_new_user_with_accounts_start(message):
    if not is_admin(message.from_user.username):
        return bot.send_message(message.chat.id, "❌ أنت لست أدمن.")
    bot.send_message(message.chat.id, "📝 الرجاء إدخال اسم المستخدم الجديد:")
    bot.register_next_step_handler(message, process_new_user)

def process_new_user(message):
    new_username = message.text.strip()
    create_user_if_not_exists(new_username)
    bot.send_message(message.chat.id,
                     f"تم إنشاء المستخدم {new_username}.\n"
                     f"أرسل الحسابات التي تريد ربطها به (حساب في كل سطر):")
    bot.register_next_step_handler(message, process_new_user_accounts, new_username)

def process_new_user_accounts(message, new_username):
    accounts = message.text.strip().split('\n')
    for acc in accounts:
        add_allowed_user_account(new_username, acc.strip())
    bot.send_message(message.chat.id, f"✅ تم ربط الحسابات بالمستخدم {new_username} بنجاح.")

@bot.message_handler(func=lambda message: message.text == 'إضافة حسابات لمستخدم')
def add_accounts_to_existing_user_start(message):
    if not is_admin(message.from_user.username):
        return bot.send_message(message.chat.id, "❌ أنت لست أدمن.")
    bot.send_message(message.chat.id, "📝 الرجاء إدخال اسم المستخدم:")
    bot.register_next_step_handler(message, process_add_accounts_step1)

def process_add_accounts_step1(message):
    user_to_edit = message.text.strip()
    create_user_if_not_exists(user_to_edit)
    bot.send_message(message.chat.id, f"أرسل الحسابات التي تريد إضافتها للمستخدم {user_to_edit} (حساب في كل سطر):")
    bot.register_next_step_handler(message, process_add_accounts_step2, user_to_edit)

def process_add_accounts_step2(message, user_to_edit):
    accounts_to_add = message.text.strip().split('\n')
    for acc in accounts_to_add:
        add_allowed_user_account(user_to_edit, acc.strip())
    bot.send_message(message.chat.id, f"✅ تم إضافة الحسابات للمستخدم {user_to_edit} بنجاح.")

@bot.message_handler(func=lambda message: message.text == 'حذف مستخدم مع جميع حساباته')
def delete_user_all_accounts_start(message):
    if not is_admin(message.from_user.username):
        return bot.send_message(message.chat.id, "❌ أنت لست أدمن.")
    bot.send_message(message.chat.id, "📝 الرجاء إدخال اسم المستخدم الذي تريد حذفه مع حساباته:")
    bot.register_next_step_handler(message, process_delete_user_all)

def process_delete_user_all(message):
    user_to_delete = message.text.strip()
    # نحذف كل حساباته
    delete_allowed_accounts(user_to_delete)
    bot.send_message(message.chat.id, f"✅ تم حذف الحسابات كلها من المستخدم {user_to_delete} بنجاح.")

@bot.message_handler(func=lambda message: message.text == 'حذف جزء من حسابات المستخدم')
def delete_part_of_user_accounts_start(message):
    if not is_admin(message.from_user.username):
        return bot.send_message(message.chat.id, "❌ أنت لست أدمن.")
    bot.send_message(message.chat.id, "📝 الرجاء إدخال اسم المستخدم:")
    bot.register_next_step_handler(message, process_delete_part_step1)

def process_delete_part_step1(message):
    user_to_edit = message.text.strip()
    current_accounts = get_allowed_accounts(user_to_edit)
    if not current_accounts:
        bot.send_message(message.chat.id, f"❌ لا توجد حسابات للمستخدم {user_to_edit}.")
        return
    bot.send_message(message.chat.id,
                     f"✅ لدى المستخدم {user_to_edit} الحسابات التالية:\n"
                     + "\n".join(current_accounts)
                     + "\n📝 أرسل الحسابات التي تريد حذفها (حساب في كل سطر):")
    bot.register_next_step_handler(message, process_delete_part_step2, user_to_edit)

def process_delete_part_step2(message, user_to_edit):
    accounts_to_delete = message.text.strip().split('\n')
    delete_allowed_accounts(user_to_edit, accounts_to_delete)
    bot.send_message(message.chat.id, f"✅ تم حذف الحسابات المطلوبة من المستخدم {user_to_edit}.")


# ----------------------------------
# (جديد) إضافة مشترك
# ----------------------------------
@bot.message_handler(func=lambda message: message.text == "إضافة مشترك")
def add_subscriber_handler(message):
    """
    زر خاص بالأدمن لإضافة Chat ID إلى قائمة المشتركين
    """
    user_name = message.from_user.username
    if not is_admin(user_name):
        return bot.send_message(message.chat.id, "❌ أنت لست أدمن.")
    
    bot.send_message(message.chat.id, "📝 الرجاء إدخال الـ Chat ID المراد إضافته للمشتركين:")
    bot.register_next_step_handler(message, process_subscriber_id)

def process_subscriber_id(message):
    try:
        chat_id_to_add = int(message.text.strip())
        add_subscriber(chat_id_to_add)
        bot.send_message(message.chat.id, f"✅ تم إضافة المشترك {chat_id_to_add} بنجاح إلى قائمة المشتركين.")
    except ValueError:
        bot.send_message(message.chat.id, "❌ الرجاء إدخال رقم صحيح للـ Chat ID.")

# ----------------------------------
# (جديد) زر عرض عدد المستخدمين
# ----------------------------------
@bot.message_handler(func=lambda message: message.text == "عرض عدد المستخدمين")
def show_users_count(message):
    """
    للأدمن فقط: يعرض عدد المستخدمين المسجلين في كولكشن users
    """
    user_name = message.from_user.username
    if not is_admin(user_name):
        return bot.send_message(message.chat.id, "❌ أنت لست أدمن.")
    count = get_users_count()
    bot.send_message(message.chat.id, f"عدد المستخدمين المسجلين حالياً هو: {count}")

# ----------------------------------
# إرسال رسالة جماعية (للأدمن)
# ----------------------------------
@bot.message_handler(func=lambda message: message.text == 'إرسال رسالة جماعية')
def handle_broadcast_request(message):
    user_name = message.from_user.username
    if not is_admin(user_name):
        return bot.send_message(message.chat.id, "❌ أنت لست أدمن.")
    bot.send_message(message.chat.id, "اكتب الرسالة التي تريد إرسالها لجميع المشتركين:")
    bot.register_next_step_handler(message, send_broadcast_message)

def send_broadcast_message(message):
    broadcast_text = message.text
    all_subscribers = get_subscribers()
    for chat_id in all_subscribers:
        try:
            bot.send_message(chat_id, f"📢 رسالة من الإدارة:\n{broadcast_text}")
        except Exception as e:
            print(f"فشل الإرسال إلى {chat_id}: {e}")
    bot.send_message(message.chat.id, "✅ تم إرسال الرسالة إلى جميع المشتركين بنجاح.")

# ----------------------------------
# Webhook (إذا كنت ستستعمله)
# ----------------------------------
@app.route('/' + TOKEN, methods=['POST'])
def webhook():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return '', 200

# ----------------------------------
# تشغيل السيرفر Flask + تهيئة DB
# ----------------------------------
if __name__ == '__main__':
    init_db()  # تهيئة وإنشاء الفهارس في MongoDB
    app.run(host='0.0.0.0', port=5000)
