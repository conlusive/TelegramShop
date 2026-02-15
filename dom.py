# ==========================================
# 🔧 ТЕХНІЧНІ НАЛАШТУВАННЯ (API & SECURITY)
# ==========================================
BOT_TOKEN = "8383113616:AAE4CfMMLjkBRxDZYrrWffVY20B-vWvfPKQ"
ADMIN_ID = 8027188846
DB_NAME = "shop_database.db"  # Назва бази даних для цього клієнта
BOT_TIMEZONE = 'Europe/Kyiv'

# ПЛАТІЖНІ ШЛЮЗИ (Telegram Payments)
# Заміни на 'LIVE' токени для реальних оплат
PAYMENT_TOKENS = {
    'REDSYS': "2051251535:TEST:OTk5MDA4ODgxLTAwNQ",
    'PORTMONE': "1661751239:TEST:VGM9-X6gR-IUtB-Djc5"
}

# ==========================================
# 🌍 РЕГІОНАЛЬНІ НАЛАШТУВАННЯ
# ==========================================
SHIPPING_MODE = 'UKRAINE' # 'UKRAINE' або 'INTERNATIONAL'
CURRENCY_CODE = 'UAH'           # 'UAH', 'USD', 'EUR'
CURRENCY_SYMBOL = "₴"           # €, ₴

# ==========================================
# 🏪 БРЕНДИНГ ТА КОНТАКТИ (White Label)
# ==========================================
# ==========================================
# 🏪 БРЕНДИНГ ТА КОНТАКТИ
# ==========================================
SHOP_NAME = "My Awesome Shop"
SUPPORT_USER = "@your_support_account"  # Тільки юзернейм
CHANNEL_LINK = "https://t.me/your_channel"

# ==========================================
# ✍️ ПЕРСОНАЛІЗАЦІЯ ТЕКСТУ (BRAND VOICE)
# ==========================================
# Використовуй {shop_name}, {support}, {channel} для автоматичної підстановки значень вище.

STORE_MESSAGES = {
    'UKRAINE': {
        # Текст на головному екрані
        'welcome': """
👋 <b>Вітаємо у {shop_name}!</b>

Ми пропонуємо найкращі товари. Оберіть категорію нижче! 🚀
""",

        # Повний текст розділу "Допомога"
        'help': """
ℹ️ <b>ІНФОРМАЦІЯ ТА ПІДТРИМКА</b>

📍 <b>Доставка:</b> Нова Пошта (1-2 дні)
💳 <b>Оплата:</b> Картка / Готівка

📢 <b>Наш канал:</b> {channel}
📞 <b>Підтримка:</b> {support}

<i>Будь ласка, пишіть нам з будь-яких питань!</i>
""",
        # Текст, коли кошик порожній
        'cart_empty': """
🛒 <b>Ваш кошик поки що порожній.</b>

Загляньте в каталог, там багато цікавого! 👇
""",

        # Повідомлення після успішного замовлення
        'order_success': """
✅ <b>Дякуємо! Замовлення #{order_id} успішно оформлено!</b>

Ми вже почали його готувати. Очікуйте повідомлення! 📦
""",
    },




    'INTERNATIONAL': {
        'welcome': """
👋 <b>Welcome to {shop_name}!</b>

Discover our premium collection. Select a category below! 🚀
""",
        'help': """
ℹ️ <b>HELP & SUPPORT</b>

📍 <b>Shipping:</b> Worldwide (7-14 days)
💳 <b>Payment:</b> Card / Apple Pay

📢 <b>Our Channel:</b> {channel}
📞 <b>Support:</b> {support}

<i>Feel free to message us if you need any assistance!</i>
""",
        'cart_empty': """
🛒 <b>Your cart is currently empty.</b>
                      
Check out our catalog to find your next favorite item! 👇
""",

        'order_success': """
✅ <b>Success! Order #{order_id} placed!</b>

We will send you a confirmation email with tracking details shortly. 📦
""",
    }
}