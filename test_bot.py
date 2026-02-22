import pytest
import pytest_asyncio
import aiosqlite
import json
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, User, Message, Chat, CallbackQuery

# Імпортуємо нашого бота
from main import OnlineShopBot

# ==================== НАЛАШТУВАННЯ СЕРЕДОВИЩА ====================
ADMIN_ID_TEST = 999111
CLIENT_ID_TEST = 111222

@pytest_asyncio.fixture
async def bot_instance(monkeypatch):
    """Створює ізольованого бота з наповненою базою даних для тестів"""

    monkeypatch.setattr('main.ADMIN_IDS', [ADMIN_ID_TEST])

    bot = OnlineShopBot()
    bot.conn = await aiosqlite.connect(':memory:')
    bot.conn.row_factory = aiosqlite.Row
    cursor = await bot.conn.cursor()

    # --- СТВОРЕННЯ ТАБЛИЦЬ (ТЕПЕР ПОВНІСТЮ ВІДПОВІДАЮТЬ РЕАЛЬНИМ) ---
    await cursor.execute('''CREATE TABLE products(id INTEGER PRIMARY KEY,name TEXT,price REAL,stock INTEGER,is_active INTEGER DEFAULT 1,variants TEXT,category TEXT,emoji TEXT,description TEXT,image_url TEXT)''')
    await cursor.execute('''CREATE TABLE cart (id INTEGER PRIMARY KEY, user_id INTEGER, product_id INTEGER, quantity INTEGER, selected_options TEXT)''')
    await cursor.execute('''CREATE TABLE users (user_id INTEGER PRIMARY KEY, full_name TEXT, email TEXT, phone TEXT, address TEXT, blocked INTEGER DEFAULT 0)''')
    await cursor.execute('''CREATE TABLE promocodes(code TEXT, discount INTEGER, max_uses INTEGER, current_uses INTEGER, is_reusable INTEGER)''')
    await cursor.execute('''CREATE TABLE orders(id INTEGER PRIMARY KEY,user_id INTEGER,user_name TEXT,full_name TEXT,products TEXT,total_amount REAL,phone TEXT,address TEXT,payment_method TEXT,email TEXT,promo_code TEXT,status TEXT DEFAULT 'pending',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # --- НАПОВНЕННЯ БАЗИ ТЕСТОВИМИ ДАНИМИ ---
    await cursor.execute("INSERT INTO products (id, name, price, stock, category, is_active) VALUES (1, 'Iphone 15', 1000.0, 5, 'Phones', 1)")
    await cursor.execute("INSERT INTO products (id, name, price, stock, category, is_active) VALUES (2, 'Out of Stock Case', 10.0, 0, 'Cases', 1)")

    variants_json = json.dumps({"Color": {"Red": {"qty": 2, "price": 100}, "Blue": {"qty": 0, "price": 100}}})
    await cursor.execute("INSERT INTO products (id, name, price, stock, category, is_active, variants) VALUES (3, 'Magic T-Shirt', 0.0, 2, 'Clothes', 1, ?)", (variants_json,))

    await cursor.execute("INSERT INTO users (user_id, full_name) VALUES (?, 'Test Client')", (CLIENT_ID_TEST,))
    await cursor.execute("INSERT INTO users (user_id, full_name) VALUES (?, 'Test Admin')", (ADMIN_ID_TEST,))

    await cursor.execute("INSERT INTO promocodes (code, discount, max_uses, current_uses, is_reusable) VALUES ('SALE50', 50, 10, 0, 1)")
    await cursor.execute("INSERT INTO promocodes (code, discount, max_uses, current_uses, is_reusable) VALUES ('USEDUP', 10, 1, 1, 1)")

    order_prods = json.dumps([{"product_id": 1, "name": "Iphone 15", "quantity": 1, "price": 1000.0, "total": 1000.0}])
    await cursor.execute("INSERT INTO orders (id, user_id, user_name, full_name, products, total_amount, phone, address, payment_method, email, status, created_at) VALUES (1, ?, 'TestUser', 'Test Fullname', ?, 1000.0, '123456789', 'Test Address', 'Online', 'test@test.com', 'pending', '2026-02-22 12:00:00')", (CLIENT_ID_TEST, order_prods))

    await bot.conn.commit()
    yield bot
    await bot.conn.close()

def create_mock_update(user_id=CLIENT_ID_TEST, text=None, callback_data=None):
    """Генератор дій користувача (натискання кнопок або ввід тексту)"""
    mock_user = MagicMock(spec=User)
    mock_user.id = user_id
    mock_user.first_name = "Test"
    mock_user.full_name = "Test User"

    mock_chat = MagicMock(spec=Chat)
    mock_chat.id = user_id

    mock_update = MagicMock(spec=Update)
    mock_update.effective_user = mock_user
    mock_update.effective_chat = mock_chat

    mock_context = MagicMock()
    mock_context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    mock_context.bot.edit_message_text = AsyncMock()
    mock_context.bot.delete_message = AsyncMock()
    mock_context.bot.answer_callback_query = AsyncMock()

    if callback_data:
        mock_query = AsyncMock(spec=CallbackQuery)
        mock_query.data = callback_data
        mock_query.from_user = mock_user

        # ВИПРАВЛЕННЯ ДЛЯ МЕТОДУ DELETE()
        mock_message = MagicMock()
        mock_message.chat_id = user_id
        mock_message.message_id = 999
        mock_message.delete = AsyncMock()
        mock_query.message = mock_message

        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query
        mock_update.message = None
    elif text is not None:
        mock_message = AsyncMock(spec=Message)
        mock_message.text = text
        mock_message.photo = []  # Щоб не шукав фотографії
        mock_message.successful_payment = None  # <--- ДОДАЙТЕ ЦЕ (щоб не блокувало ввід тексту)
        mock_message.chat_id = user_id
        mock_message.message_id = 1000
        mock_message.delete = AsyncMock()
        mock_update.message = mock_message
        mock_update.callback_query = None

    return mock_update, mock_context


# ==================== БЛОК 1: БЕЗПЕКА ТА ДОСТУП ====================

@pytest.mark.asyncio
async def test_admin_security_blocks_client(bot_instance):
    """Звичайний юзер не може відкрити адмінку"""
    update, context = create_mock_update(user_id=CLIENT_ID_TEST, callback_data="admin_panel")
    await bot_instance.admin_panel(update, context)
    update.callback_query.answer.assert_called_with(bot_instance.get_text('access_denied'))

@pytest.mark.asyncio
async def test_admin_security_allows_admin(bot_instance):
    """Адмін може відкрити адмінку"""
    update, context = create_mock_update(user_id=ADMIN_ID_TEST, callback_data="admin_panel")
    await bot_instance.admin_panel(update, context)
    update.callback_query.edit_message_text.assert_called_once()

# ==================== БЛОК 2: ПОШУК ТА КАТАЛОГ ====================

@pytest.mark.asyncio
async def test_search_finds_product(bot_instance):
    """Пошук знаходить існуючий товар"""
    update, context = create_mock_update(text="Iphone")
    await bot_instance.perform_search(update, context, "Iphone")
    args = context.bot.send_message.call_args
    assert "Iphone 15" in args.kwargs['text'] or str(args.kwargs['reply_markup'])

@pytest.mark.asyncio
async def test_search_empty_result(bot_instance):
    """Пошук неіснуючого товару видає помилку"""
    update, context = create_mock_update(text="NonExistentCar")
    await bot_instance.perform_search(update, context, "NonExistentCar")
    args = context.bot.send_message.call_args
    # ВИПРАВЛЕНО: тепер шукає текст "was found" замість "not found"
    assert "на жаль" in args.kwargs['text'].lower() or "was found" in args.kwargs['text'].lower()

# ==================== БЛОК 3: КОШИК ====================

@pytest.mark.asyncio
async def test_add_to_cart_success(bot_instance):
    """Додавання товару в кошик працює"""
    update, context = create_mock_update(callback_data="prod_plus_1_1_1")
    await bot_instance.handle_product_action(update, context)

    async with bot_instance.conn.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = 1", (CLIENT_ID_TEST,)) as c:
        assert (await c.fetchone())[0] == 1

@pytest.mark.asyncio
async def test_add_to_cart_stock_limit(bot_instance):
    """Кошик не дозволяє додати більше, ніж є на складі (ліміт 5)"""
    await bot_instance.conn.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (?, 1, 5)", (CLIENT_ID_TEST,))
    await bot_instance.conn.commit()

    update, context = create_mock_update(callback_data="prod_plus_1_1_1")
    await bot_instance.handle_product_action(update, context)

    update.callback_query.answer.assert_called_with(bot_instance.get_text('stock_limit', limit=5), show_alert=True)

@pytest.mark.asyncio
async def test_add_to_cart_out_of_stock(bot_instance):
    """Неможливо додати товар, якого 0 на складі"""
    update, context = create_mock_update(callback_data="prod_plus_2_1_1")
    await bot_instance.handle_product_action(update, context)
    update.callback_query.answer.assert_called_with(bot_instance.get_text('stock_limit', limit=0), show_alert=True)

@pytest.mark.asyncio
async def test_clear_cart(bot_instance):
    """Функція очищення кошика працює"""
    await bot_instance.conn.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (?, 1, 5)", (CLIENT_ID_TEST,))
    await bot_instance.conn.commit()

    update, context = create_mock_update(callback_data="clear_cart")
    await bot_instance.clear_cart(update, context)

    async with bot_instance.conn.execute("SELECT COUNT(*) FROM cart WHERE user_id = ?", (CLIENT_ID_TEST,)) as c:
        assert (await c.fetchone())[0] == 0

# ==================== БЛОК 4: ПРОМОКОДИ ====================

@pytest.mark.asyncio
async def test_promo_code_success(bot_instance):
    """Правильний промокод застосовується"""
    bot_instance.user_states[CLIENT_ID_TEST] = {'step': 'waiting_user_promo'}
    update, context = create_mock_update(text="SALE50")
    await bot_instance.handle_user_promo_input(update, context)

    assert bot_instance.user_promos[CLIENT_ID_TEST]['discount'] == 50

@pytest.mark.asyncio
async def test_promo_code_used_up(bot_instance):
    """Промокод з вичерпаним лімітом не застосовується"""
    bot_instance.user_states[CLIENT_ID_TEST] = {'step': 'waiting_user_promo'}
    update, context = create_mock_update(text="USEDUP")
    await bot_instance.handle_user_promo_input(update, context)

    assert CLIENT_ID_TEST not in bot_instance.user_promos
    assert 'promo_msg' in bot_instance.user_states[CLIENT_ID_TEST]

# ==================== БЛОК 5: АДМІНКА ====================

@pytest.mark.asyncio
async def test_admin_edit_price(bot_instance):
    """Адмін може змінити ціну товару"""
    update, context = create_mock_update(user_id=ADMIN_ID_TEST, callback_data="admin_edit_field_price_1")
    await bot_instance.admin_edit_field(update, context)

    update2, context2 = create_mock_update(user_id=ADMIN_ID_TEST, text="1500.50")
    await bot_instance.master_message_handler(update2, context2)

    async with bot_instance.conn.execute("SELECT price FROM products WHERE id = 1") as c:
        assert (await c.fetchone())[0] == 1500.5

@pytest.mark.asyncio
async def test_admin_delete_product(bot_instance):
    """Адмін може видалити товар (is_active стає 0)"""
    update, context = create_mock_update(user_id=ADMIN_ID_TEST, callback_data="admin_delete_product_confirm_1")
    await bot_instance.admin_delete_product_confirm(update, context)

    async with bot_instance.conn.execute("SELECT is_active FROM products WHERE id = 1") as c:
        assert (await c.fetchone())[0] == 0

# ==================== БЛОК 6: ЗАМОВЛЕННЯ ТА ЗАЛИШКИ ====================

@pytest.mark.asyncio
async def test_order_creation_deducts_stock(bot_instance):
    """Створення замовлення віднімає товар зі складу"""
    await bot_instance.conn.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (?, 1, 1)", (CLIENT_ID_TEST,))
    await bot_instance.conn.commit()

    update, context = create_mock_update(user_id=CLIENT_ID_TEST)
    bot_instance.user_states[CLIENT_ID_TEST] = {'full_name': 'Test', 'phone': '123'}
    await bot_instance.create_order(update, context)

    async with bot_instance.conn.execute("SELECT stock FROM products WHERE id = 1") as c:
        assert (await c.fetchone())[0] == 4

@pytest.mark.asyncio
async def test_admin_cancel_order_restores_stock(bot_instance):
    """Скасування замовлення адміном повертає товар на склад"""
    update, context = create_mock_update(user_id=ADMIN_ID_TEST, callback_data="admin_cancel_1_1")
    await bot_instance.admin_order_status_change(update, context)

    async with bot_instance.conn.execute("SELECT stock FROM products WHERE id = 1") as c:
        assert (await c.fetchone())[0] == 6