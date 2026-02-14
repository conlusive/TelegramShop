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
SHIPPING_MODE = 'INTERNATIONAL' # 'UKRAINE' або 'INTERNATIONAL'
CURRENCY_CODE = 'USD'           # 'UAH', 'USD', 'EUR'
CURRENCY_SYMBOL = "$"

# ==========================================
# 🏪 БРЕНДИНГ ТА КОНТАКТИ (White Label)
# ==========================================
SHOP_NAME = "My Awesome Shop"
SUPPORT_USER = "@your_support_account" # Тільки юзернейм
CHANNEL_LINK = "https://t.me/your_channel"

# Цей контент буде автоматично підставлятися в розділ Допомога (Help/About)
SHOP_INFO = {
    'UKRAINE': {
        'delivery': "• Самовивіз: Київ\n• Нова Пошта: 1-2 дні",
        'payment_desc': "• Оплата при отриманні\n• Онлайн на сайті",
        'email': "support_ua@example.com"
    },
    'INTERNATIONAL': {
        'delivery': "• Worldwide Shipping via DHL/FedEx\n• Terms: 7-14 business days",
        'payment_desc': "• Secure Card Payment / Apple Pay\n• Bank Transfer",
        'email': "global_support@example.com"
    }
}