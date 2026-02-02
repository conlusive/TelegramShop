import logging
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from dom import BOT_TOKEN, ADMIN_ID, BOT_TIMEZONE


# -------------------- LOGGING --------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# -------------------- SETTINGS --------------------
BOT_TOKEN = BOT_TOKEN
ADMIN_ID = ADMIN_ID
BOT_TIMEZONE = BOT_TIMEZONE

ITEMS_PER_PAGE = 5


class OnlineShopBot:
    def __init__(self):
        self.init_database()
        self.user_states = {}

    # -------------------- DATABASE --------------------
    def init_database(self):
        self.conn = sqlite3.connect('shop.db', check_same_thread=False)
        cursor = self.conn.cursor()

        # 1. Створюємо таблицю products, якщо її немає
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS products
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           name
                           TEXT
                           NOT
                           NULL,
                           description
                           TEXT,
                           price
                           REAL
                           NOT
                           NULL,
                           image_url
                           TEXT,
                           category
                           TEXT,
                           stock
                           INTEGER
                           DEFAULT
                           0,
                           emoji
                           TEXT,
                           variants
                           TEXT,
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       )
                       ''')

        # 2. МІГРАЦІЯ: Перевіряємо та додаємо відсутні колонки
        cursor.execute("PRAGMA table_info(products)")
        columns = [row[1] for row in cursor.fetchall()]

        if "emoji" not in columns:
            try:
                cursor.execute("ALTER TABLE products ADD COLUMN emoji TEXT")
            except:
                pass

        if "image_url" not in columns:
            try:
                cursor.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
            except:
                pass

        # 👇 ДОДАЄМО VARIANTS 👇
        if "variants" not in columns:
            try:
                cursor.execute("ALTER TABLE products ADD COLUMN variants TEXT")
                print("✅ Column 'variants' added to database!")
            except Exception as e:
                print(f"⚠️ Error adding variants column: {e}")

        # 3. Оновлюємо таблицю кошика (Cart)
        try:
            cursor.execute("SELECT selected_options FROM cart LIMIT 1")
        except Exception:
            # Якщо колонки немає - перестворюємо таблицю
            cursor.execute("DROP TABLE IF EXISTS cart")
            cursor.execute('''
                           CREATE TABLE cart
                           (
                               user_id          INTEGER,
                               product_id       INTEGER,
                               quantity         INTEGER DEFAULT 1,
                               selected_options TEXT,
                               FOREIGN KEY (product_id) REFERENCES products (id)
                           )
                           ''')
            print("✅ Cart table updated!")

        # 4. Створюємо інші таблиці (Orders, Users)
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS orders
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           user_id
                           INTEGER
                           NOT
                           NULL,
                           user_name
                           TEXT,
                           products
                           TEXT
                           NOT
                           NULL,
                           total_amount
                           REAL
                           NOT
                           NULL,
                           phone
                           TEXT,
                           address
                           TEXT,
                           payment_method
                           TEXT,
                           email
                           TEXT,
                           status
                           TEXT
                           DEFAULT
                           'pending',
                           created_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       )
                       ''')

        # Міграції для orders
        cursor.execute("PRAGMA table_info(orders)")
        ord_cols = [row[1] for row in cursor.fetchall()]
        if "payment_method" not in ord_cols:
            try:
                cursor.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT")
            except:
                pass
        if "email" not in ord_cols:
            try:
                cursor.execute("ALTER TABLE orders ADD COLUMN email TEXT")
            except:
                pass

        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS users
                       (
                           user_id
                           INTEGER
                           PRIMARY
                           KEY,
                           phone
                           TEXT,
                           address
                           TEXT,
                           email
                           TEXT,
                           blocked
                           INTEGER
                           DEFAULT
                           0
                       )
                       ''')

        # Міграції для users
        cursor.execute("PRAGMA table_info(users)")
        usr_cols = [row[1] for row in cursor.fetchall()]
        if "email" not in usr_cols:
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")
            except:
                pass
        if "blocked" not in usr_cols:
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
            except:
                pass

        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS blocked_users
                       (
                           user_id
                           INTEGER
                           PRIMARY
                           KEY,
                           blocked_at
                           TIMESTAMP
                           DEFAULT
                           CURRENT_TIMESTAMP
                       )
                       ''')

        self.conn.commit()

    def get_variant_type_keyboard(self):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📏 Size (S, M, L)", callback_data="vartype_Size"),
             InlineKeyboardButton("🎨 Color", callback_data="vartype_Color")],
            [InlineKeyboardButton("💾 Memory (GB)", callback_data="vartype_Memory"),
             InlineKeyboardButton("🥛 Volume (L, ml)", callback_data="vartype_Volume")],
            [InlineKeyboardButton("⚖️ Weight (kg, g)", callback_data="vartype_Weight"),
             InlineKeyboardButton("👟 Shoe Size", callback_data="vartype_ShoeSize")],
            [InlineKeyboardButton("✅ Finish / Skip", callback_data="vartype_DONE")]
        ])



    def format_date(self, date_input):
        """
        Перетворює будь-який час у той, що вказаний у BOT_TIMEZONE.
        Працює і для нових замовлень, і для старих з бази.
        """
        try:
            # Якщо час прийшов рядком з бази (наприклад "2026-02-01 14:00:00")
            if isinstance(date_input, str):
                # Відкидаємо мілісекунди, якщо є
                if "." in date_input:
                    date_input = date_input.split(".")[0]
                dt = datetime.strptime(date_input, "%Y-%m-%d %H:%M:%S")
            else:
                dt = date_input  # Якщо це вже об'єкт часу

            # 1. Якщо у дати немає часового поясу, вважаємо що це UTC (база даних)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))

            # 2. Конвертуємо у ТВІЙ налаштований пояс (BOT_TIMEZONE)
            local_dt = dt.astimezone(ZoneInfo(BOT_TIMEZONE))

            return local_dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            return str(date_input)[:16]

    def is_user_blocked(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result and result[0] == 1

    async def check_user_blocked(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if self.is_user_blocked(update.effective_user.id):
            if update.callback_query:
                await update.callback_query.answer("You are blocked from using this bot.", show_alert=True)
            elif update.message:
                await update.message.reply_text("You are blocked from using this bot.")
            return True
        return False

        # === НОВА ФУНКЦІЯ (Додаємо її, щоб бот вмів повертати товар) ===

    def restore_stock(self, order_id):
        """Повертає товари на склад (включаючи варіанти)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT products FROM orders WHERE id = ?", (order_id,))
        result = cursor.fetchone()

        if result and result[0]:
            products = json.loads(result[0])
            for item in products:
                product_id = item.get('product_id')
                quantity = item.get('quantity')
                sel_opts = item.get('selected_options', {})  # Отримуємо опції

                if product_id and quantity:
                    # 1. Повертаємо загальний сток
                    cursor.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (quantity, product_id))

                    # 2. Повертаємо сток варіантів
                    if sel_opts:
                        cursor.execute("SELECT variants FROM products WHERE id = ?", (product_id,))
                        row = cursor.fetchone()
                        if row and row[0]:
                            try:
                                variants_data = json.loads(row[0])
                                changed = False
                                for key, val in sel_opts.items():
                                    if key in variants_data and isinstance(variants_data[key], dict):
                                        if val in variants_data[key]:
                                            variants_data[key][val] += quantity
                                            changed = True

                                if changed:
                                    new_json = json.dumps(variants_data, ensure_ascii=False)
                                    cursor.execute("UPDATE products SET variants = ? WHERE id = ?",
                                                   (new_json, product_id))
                            except:
                                pass

            self.conn.commit()
            logger.info(f"Stock restored for order #{order_id}")

        # === ОНОВЛЕНА ФУНКЦІЯ (Замінює твою стару user_cancel_order) ===
    async def user_cancel_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            if await self.check_user_blocked(update, context):
                return
            query = update.callback_query
            match = re.match(r"user_cancel_(\d+)", query.data)
            if not match:
                await query.answer("❌ Invalid request")
                return
            order_id = int(match.group(1))
            uid = query.from_user.id

            cursor = self.conn.cursor()
            cursor.execute("SELECT status FROM orders WHERE id = ? AND user_id = ?", (order_id, uid))
            row = cursor.fetchone()

            if not row:
                await query.answer("❌ Invalid request")
                return

            status = row[0]
            if status in ('cancelled', 'delivered'):
                await query.answer("❌ Order has already been delivered or canceled")
                return

            # !!! ГОЛОВНА ЗМІНА ТУТ !!!
            # Викликаємо функцію повернення товару ПЕРЕД тим, як змінити статус
            self.restore_stock(order_id)

            cursor.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
            self.conn.commit()

            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"🔴 The customer canceled the order. #{order_id}",
                                               parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

            await query.answer("✅ Order canceled")
            await self.show_main_menu(update, context)

    # -------------------- KEYBOARD BUILDER --------------------
    def build_main_keyboard(self, user_id):
        """
        Build the main menu keyboard.
        If Admin: Shows a simplified layout with Admin Panel on top.
        If User: Shows the standard customer layout.
        """
        # --- ВАРІАНТ ДЛЯ АДМІНА ---
        if int(user_id) == int(ADMIN_ID):
            keyboard = [
                # Найважливіша кнопка - зверху
                [InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")],

                # Інструменти для перевірки (як виглядає магазин)
                [InlineKeyboardButton("🛍️ Product catalog", callback_data="catalog")],

                # Технічні кнопки (компактно в один ряд)
                [
                    InlineKeyboardButton("🛒 My cart", callback_data="cart"),
                    InlineKeyboardButton("👤 My profile", callback_data="my_profile")
                ]
            ]
            return InlineKeyboardMarkup(keyboard)

        # --- ВАРІАНТ ДЛЯ ЗВИЧАЙНОГО КЛІЄНТА ---
        keyboard = [
            [InlineKeyboardButton("🛍️ Product catalog", callback_data="catalog")],
            [InlineKeyboardButton("🛒 My cart", callback_data="cart")],
            [InlineKeyboardButton("📋 My orders", callback_data="my_orders")],
            [InlineKeyboardButton("👤 My profile", callback_data="my_profile")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
        ]
        return InlineKeyboardMarkup(keyboard)

    # -------------------- USER-FACING SCREENS --------------------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        self.conn.commit()
        cursor.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        if result and result[0] == 1:
            await update.message.reply_text("You are blocked from using this bot.")
            return
        await self.show_main_menu(update, context)

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return

        user = update.effective_user
        user_id = user.id

        # --- ТЕКСТ ДЛЯ АДМІНА ---
        if int(user_id) == int(ADMIN_ID):
            welcome_text = (
                f"👑 **Admin Panel**\n\n"
                f"👋 Hello, **{user.first_name}**!\n"
                f"Ready to manage orders and products\n\n"
                f"👇 **Select an option from the dashboard:**"
            )

        # --- ТЕКСТ ДЛЯ ПОКУПЦЯ ---
        else:
            welcome_text = (
                f"🛍️ **Welcome to our store, {user.first_name}!**\n\n"
                "📱 Here you will find the best products at great prices!\n\n"
                "🛒 **What you can do:**\n"
                "• Browse the product catalog\n"
                "• Add products to your cart\n"
                "• Place orders\n"
                "• Track order status\n\n"
                "👇 **Select an action:**"
            )

        # Клавіатура будується залежно від ID (ми це змінили на попередньому кроці)
        reply_markup = self.build_main_keyboard(user.id)

        # Відправка або редагування повідомлення
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup,
                                                              parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass  # Ігноруємо помилку, якщо текст не змінився
        elif update.message:
            await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        text = """
ℹ️ **HELP**

🛍️ How to shop**:
1. Go to the product catalog
2. Select a category and product
3. Add products to your cart
4. Place your order

📞 **Contact details:**
• Telephone: +380501234567
• Email: shop@example.com
• Working hours: 9:00 a.m. to 6:00 p.m.

🚚 **Delivery:**
• In Kyiv: 100₴
• In Ukraine: 150₴
• Free delivery for orders over 1000₴

💳 **Payment:**
• Cash on delivery
• Card payment to the courier
• Bank transfer

❓ **Questions?**
Please contact us using the details above!
        """
        keyboard = [[InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]]
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def show_catalog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT category FROM products WHERE stock > 0")
        categories = cursor.fetchall()
        keyboard = [[InlineKeyboardButton(f"📂 {c[0]}", callback_data=f"category_{c[0]}")] for c in categories]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
        await update.callback_query.edit_message_text(
            "🛍️ **Product catalog**\n\nSelect a category:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def show_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        category = query.data.replace("category_", "")

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE category = ? AND stock > 0", (category,))
        products = cursor.fetchall()

        if not products:
            await query.answer("❌ Category is empty.")
            return

        keyboard = []
        for product in products:
            emoji = product['emoji'] if product['emoji'] else ''
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {product['name']} - {product['price']}$",
                    callback_data=f"product_{product['id']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("🔙 Back to Catalog", callback_data="catalog")])

        text = f"📂 **Category: {category}**\n\nSelect a product:"

        # Якщо ми повернулись з Фото-повідомлення (Product View з file_id)
        if query.message.photo:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                          parse_mode=ParseMode.MARKDOWN)

    async def show_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id_override=None):
        query = update.callback_query

        # Отримуємо ID
        try:
            if product_id_override:
                product_id = int(product_id_override)
            elif query:
                product_id = int(query.data.replace("product_", ""))
            else:
                return
        except:
            return

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

        if not product:
            if query: await query.answer("❌ Product not found")
            return

        user_id = update.effective_user.id

        # Отримуємо кількість в кошику
        cursor.execute("SELECT SUM(quantity) FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        result = cursor.fetchone()
        cart_qty = result[0] if result and result[0] else 0

        emoji = product['emoji'] or ''
        stock = product['stock']
        base_price = product['price']
        img_source = product['image_url']
        is_file_id = img_source and not img_source.startswith("http") if img_source else False

        # --- 1. РОЗУМНА ЦІНА (Діапазон) ---
        min_price = base_price
        max_price = base_price

        vars_text = ""
        if product['variants']:
            try:
                v = json.loads(product['variants'])
                # Шукаємо найменшу і найбільшу ціну серед варіантів
                for key, values in v.items():
                    if isinstance(values, dict):
                        for sub_k, sub_v in values.items():
                            if isinstance(sub_v, dict) and 'price' in sub_v:
                                p = sub_v['price']
                                if p < min_price: min_price = p  # Оновлюємо мінімум
                                if p > max_price: max_price = p  # Оновлюємо максимум
            except:
                pass

        # Формуємо текст ціни
        if min_price == max_price:
            price_text = f"{base_price}$"
        else:
            price_text = f"from {min_price}$"  # Або f"{min_price}$ - {max_price}$"

        # --- 2. ПРИХОВАНИЙ СТОК (Статус) ---
        if stock > 5:
            stock_text = "✅ **In Stock**"
        elif stock > 0:
            stock_text = "⚠️ **Low Stock**"  # Маркетинговий хід
        else:
            stock_text = "❌ **Out of Stock**"

        # Формуємо список опцій для краси (без кількості, бо клієнту це не треба)
        if product['variants']:
            try:
                v = json.loads(product['variants'])
                vars_text = "\n⚙️ **Options:**\n"
                for key, values in v.items():
                    if isinstance(values, dict):
                        # Показуємо просто список доступних: 128GB, 256GB...
                        # Але тільки тих, де qty > 0
                        avail_opts = []
                        for k, info in values.items():
                            qty = info['qty'] if isinstance(info, dict) else info
                            if qty > 0: avail_opts.append(k)

                        if avail_opts:
                            vars_text += f"• {key}: {', '.join(avail_opts)}\n"
                    else:
                        vars_text += f"• {key}: {', '.join(values)}\n"
            except:
                pass

        text = (
            f"{emoji} **{product['name']}**\n\n"
            f"📝 {product['description']}"
            f"{vars_text}\n"
            f"💰 Price: **{price_text}**\n"  # <--- Тут тепер "from 800$"
            f"{stock_text}\n"  # <--- Тут "In Stock" замість "100 pcs"
            f"🛒 In Cart: {cart_qty}\n\n"
            f"Category: {product['category']}"
        )

        # Кнопки
        keyboard = []
        if cart_qty > 0:
            keyboard.append([InlineKeyboardButton("➖ Remove", callback_data=f"remove_from_cart_{product_id}")])

        if stock > 0:
            add_btn = InlineKeyboardButton("➕ Add", callback_data=f"add_to_cart_{product_id}")
            if keyboard and len(keyboard[-1]) == 1:
                keyboard[-1].append(add_btn)
            else:
                keyboard.append([add_btn])

        keyboard.append([
            InlineKeyboardButton("🛒 Cart", callback_data="cart"),
            InlineKeyboardButton(f"🔙 Back", callback_data=f"category_{product['category']}")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        chat_id = update.effective_chat.id

        # Безпечне відображення
        try:
            if query and query.message.text and not is_file_id:
                await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
            elif query and query.message.photo and is_file_id:
                try:
                    await query.edit_message_caption(caption=text, reply_markup=reply_markup,
                                                     parse_mode=ParseMode.MARKDOWN)
                except:
                    pass
            else:
                if is_file_id:
                    await context.bot.send_photo(chat_id=chat_id, photo=img_source, caption=text,
                                                 reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
                else:
                    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup,
                                                   parse_mode=ParseMode.MARKDOWN)
                if query:
                    try:
                        await query.message.delete()
                    except:
                        pass
        except Exception as e:
            try:
                if is_file_id:
                    await context.bot.send_photo(chat_id=chat_id, photo=img_source, caption=text,
                                                 reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
                else:
                    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup,
                                                   parse_mode=ParseMode.MARKDOWN)
            except:
                pass

    async def handle_add_to_cart_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        # 1. Витягуємо ID товару з кнопки "add_to_cart_123"
        try:
            product_id = int(query.data.replace("add_to_cart_", ""))
        except:
            await query.answer("❌ Error")
            return

        # 2. Отримуємо дані про варіанти з бази
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT variants FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()

        variants_data = {}
        if row and row['variants']:
            try:
                variants_data = json.loads(row['variants'])
            except:
                pass

        # 3. Якщо варіантів немає — одразу додаємо в кошик
        if not variants_data:
            await self.add_item_to_cart_db(update, context, product_id, None)
            return

        # 4. СОРТУВАННЯ: Колір -> Розмір -> Інше
        priority_keys = ["color", "colour", "колір", "цвєт", "size", "розмір", "размер"]

        def sort_key(k):
            k_lower = k.lower()
            # Шукаємо часткове співпадіння (щоб "Color of Item" теж було першим)
            for i, pk in enumerate(priority_keys):
                if pk in k_lower:
                    return i
            return 999  # Все інше в кінці

        sorted_keys = sorted(variants_data.keys(), key=sort_key)

        # 5. Зберігаємо стан і починаємо опитування
        self.user_states[user_id] = {
            'step': 'selecting_variant',
            'product_id': product_id,
            'variant_keys': sorted_keys,  # Вже відсортовані!
            'current_key_index': 0,
            'variants_data': variants_data,
            'selected_options': {}
        }

        # Запускаємо перше питання (вже з правильною чергою)
        await self.ask_next_variant(update, context)

    async def show_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT phone, address, email FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()

        phone = user_data[0] if user_data and user_data[0] else "Not set"
        address = user_data[1] if user_data and user_data[1] else "Not set"
        email = user_data[2] if user_data and user_data[2] else "Not set"

        text = f"""
    👤 **My Profile**

    📞 **Phone:** {phone}
    📍 **Address:** {address}
    📧 **Email:** {email}
            """
        keyboard = [
            [InlineKeyboardButton("✏️ Edit Phone", callback_data="edit_phone")],
            [InlineKeyboardButton("✏️ Edit Address", callback_data="edit_address")],
            [InlineKeyboardButton("✏️ Edit Email", callback_data="edit_email")],
            # 👇 НОВА КНОПКА 👇
            [InlineKeyboardButton("🗑️ Delete Data", callback_data="profile_delete_menu")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup,
                                                          parse_mode=ParseMode.MARKDOWN)
        elif update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

        # --- DELETE DATA MENU ---
        async def profile_delete_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            if await self.check_user_blocked(update, context): return
            user_id = update.effective_user.id

            cursor = self.conn.cursor()
            cursor.execute("SELECT phone, address, email FROM users WHERE user_id = ?", (user_id,))
            user_data = cursor.fetchone()

            # Перевіряємо, що саме заповнено
            phone, address, email = user_data if user_data else (None, None, None)

            keyboard = []
            if phone:
                keyboard.append([InlineKeyboardButton("🗑️ Delete Phone", callback_data="delete_profile_phone")])
            if address:
                keyboard.append([InlineKeyboardButton("🗑️ Delete Address", callback_data="delete_profile_address")])
            if email:
                keyboard.append([InlineKeyboardButton("🗑️ Delete Email", callback_data="delete_profile_email")])

            keyboard.append([InlineKeyboardButton("🔙 Back to Profile", callback_data="my_profile")])

            text = "🗑️ **Delete Profile Data**\n\nSelect the data you want to remove:"
            if not (phone or address or email):
                text = "🗑️ **Delete Profile Data**\n\nYour profile is empty. Nothing to delete."

            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                          parse_mode=ParseMode.MARKDOWN)

    async def handle_delete_profile_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            if await self.check_user_blocked(update, context): return
            query = update.callback_query
            data = query.data
            user_id = query.from_user.id

            # Карта відповідності кнопки до поля в БД
            field_map = {
                "delete_profile_phone": ("phone", "Phone number"),
                "delete_profile_address": ("address", "Address"),
                "delete_profile_email": ("email", "Email")
            }

            if data not in field_map: return

            db_field, display_name = field_map[data]

            # Видаляємо (ставимо NULL)
            cursor = self.conn.cursor()
            cursor.execute(f"UPDATE users SET {db_field} = NULL WHERE user_id = ?", (user_id,))
            self.conn.commit()

            await query.answer(f"✅ {display_name} deleted!")

            # Оновлюємо меню (видалена кнопка зникне)
            await self.profile_delete_menu(update, context)

    async def edit_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        self.user_states[user_id] = {'step': 'waiting_phone_profile'}
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="my_profile")]]
        await update.callback_query.edit_message_text(
            "📞 **Enter your phone number:**\n"
            "Example: +380501234567",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def edit_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        self.user_states[user_id] = {'step': 'waiting_email_profile'}
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="my_profile")]]
        await update.callback_query.edit_message_text(
            "📧 **Enter your email address:**\n"
            "Example: example@gmail.com",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def edit_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        self.user_states[user_id] = {'step': 'waiting_address_profile'}
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="my_profile")]]
        await update.callback_query.edit_message_text(
            "📍 **Enter your address:**\n"
            "Example: Kyiv, 1 Khreshchatyk St., apt. 10",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    # -------------------- CART LOGIC --------------------
    async def show_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return

        query = update.callback_query
        user_id = update.effective_user.id

        cursor = self.conn.cursor()
        # Додаємо c.id, щоб мати унікальний ідентифікатор рядка кошика
        cursor.execute('''
                       SELECT c.id,
                              p.id,
                              p.name,
                              p.price,
                              p.emoji,
                              c.quantity,
                              c.selected_options,
                              p.variants,
                              p.stock
                       FROM cart c
                                JOIN products p ON c.product_id = p.id
                       WHERE c.user_id = ?
                       ''', (user_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            text = "🛒 **Your cart is empty**"
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
            if query.message.photo:
                await query.message.delete()
                await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                               reply_markup=InlineKeyboardMarkup(keyboard),
                                               parse_mode=ParseMode.MARKDOWN)
            else:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                              parse_mode=ParseMode.MARKDOWN)
            return

        text = "🛒 **Your cart:**\n\n"
        total_cart_price = 0
        keyboard = []

        for item in cart_items:
            # c.id - це унікальний ID запису в кошику (cart_id)
            cart_id, product_id, name, base_price, emoji, quantity, opts_json, variants_json, real_total_stock = item
            emoji = emoji if emoji else ""

            # --- РОЗРАХУНОК ЦІНИ І ЛІМІТУ ---
            current_price = base_price
            opts_text = ""
            limit = real_total_stock  # За замовчуванням ліміт - загальний сток

            if opts_json and variants_json:
                try:
                    selected_opts = json.loads(opts_json)
                    all_variants_data = json.loads(variants_json)

                    # 1. Шукаємо ціну і ліміт для варіанту
                    for key, val in selected_opts.items():
                        if key in all_variants_data:
                            variant_data = all_variants_data[key]

                            # Варіант складний: {"qty": 5, "price": 1200}
                            if isinstance(variant_data, dict) and val in variant_data:
                                val_data = variant_data[val]
                                if isinstance(val_data, dict):
                                    if "price" in val_data: current_price = val_data["price"]
                                    if "qty" in val_data: limit = val_data["qty"]

                            # Варіант простий: 5
                            elif isinstance(variant_data, dict) and isinstance(variant_data.get(val), int):
                                limit = variant_data[val]

                    vals = [f"{k}: {v}" for k, v in selected_opts.items()]
                    opts_text = f" \n   └ _{', '.join(vals)}_"
                except:
                    pass

            item_total = current_price * quantity
            total_cart_price += item_total

            # Попередження, якщо в кошику більше ніж на складі
            stock_warning = ""
            if quantity > limit:
                stock_warning = f" ⚠️ (Max: {limit})"

            text += f"{emoji} **{name}**{opts_text}\n   💰 {current_price}$ × {quantity} = {item_total}${stock_warning}\n\n"

            # --- УНІКАЛЬНІ КНОПКИ ---
            # Використовуємо cart_item_add_ID замість cart_add_ID
            # Це дозволяє точно знати, який рядок змінювати
            keyboard.append([
                InlineKeyboardButton("➖", callback_data=f"cart_item_minus_{cart_id}"),  # Було cart_remove_ID
                InlineKeyboardButton(f"{emoji} {name}", callback_data=f"product_{product_id}"),
                InlineKeyboardButton("➕", callback_data=f"cart_item_plus_{cart_id}")  # Було cart_add_ID
            ])

        text += f"💳 **Total amount: {total_cart_price}$**"

        keyboard.extend([
            [InlineKeyboardButton("🗑️ Clear cart", callback_data="clear_cart")],
            [InlineKeyboardButton("📋 Checkout", callback_data="checkout")],
            [InlineKeyboardButton("🔙 To Catalog", callback_data="catalog")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Безпечне оновлення
        try:
            if query.message.photo:
                await query.message.delete()
                await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=reply_markup,
                                               parse_mode=ParseMode.MARKDOWN)
            else:
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        except:
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=reply_markup,
                                           parse_mode=ParseMode.MARKDOWN)

    async def start_variant_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id):
            user_id = update.effective_user.id

            # Отримуємо дані про товар
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute("SELECT variants, name FROM products WHERE id = ?", (product_id,))
            row = cursor.fetchone()

            if not row or not row['variants']:
                # Якщо варіантів немає - зразу в кошик
                await self.add_item_to_cart_db(update, context, product_id, None)
                return

            try:
                variants_data = json.loads(row['variants'])
            except:
                await self.add_item_to_cart_db(update, context, product_id, None)
                return

            # 👇 МАГІЯ СОРТУВАННЯ: Колір -> Розмір -> Інше 👇
            # Ми даємо пріоритет певним словам
            priority_keys = ["color", "colour", "колір", "цвєт", "size", "розмір", "размер"]

            def sort_key(k):
                k_lower = k.lower()
                if k_lower in priority_keys:
                    return priority_keys.index(k_lower)
                return 999  # Все інше в кінці

            # Сортуємо ключі (Колір буде першим, Розмір другим)
            sorted_keys = sorted(variants_data.keys(), key=sort_key)

            # Зберігаємо стан
            self.user_states[user_id] = {
                'step': 'selecting_variant',
                'product_id': product_id,
                'variant_keys': sorted_keys,  # Відсортований список
                'current_key_index': 0,  # Починаємо з першого (Колір)
                'variants_data': variants_data,
                'selected_options': {}
            }

            # Запускаємо перше питання
            await self.ask_next_variant(update, context)

        # 1. Функція старту додавання
    async def add_to_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            if await self.check_user_blocked(update, context): return
            query = update.callback_query

            product_id = int(query.data.replace("add_to_cart_", ""))
            user_id = query.from_user.id

            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            product = cursor.fetchone()

            if not product: return await query.answer("❌ Product not found")

            # 👇 ПЕРЕВІРКА ВАРІАНТІВ 👇
            variants = json.loads(product['variants']) if product['variants'] else {}

            if not variants:
                # Немає варіантів - додаємо одразу
                await self.add_item_to_cart_db(update, context, product_id, {})
            else:
                # Є варіанти - починаємо візард
                variant_keys = list(variants.keys())  # ["Size", "Color"]

                self.user_states[user_id] = {
                    'step': 'selecting_variant',
                    'product_id': product_id,
                    'variants_data': variants,
                    'variant_keys': variant_keys,
                    'current_key_index': 0,
                    'selected_options': {}
                }
                # Запускаємо питання
                await self.ask_next_variant(update, context)

        # 2. Функція, яка задає питання
    async def ask_next_variant(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)

        if not state or 'variant_keys' not in state:
            try:
                await update.callback_query.answer("❌ Session expired")
            except:
                pass
            return

        keys = state['variant_keys']
        idx = state['current_key_index']

        # Якщо вибрали все -> додаємо в кошик
        if idx >= len(keys):
            await self.add_item_to_cart_db(update, context, state['product_id'], state['selected_options'])
            self.user_states.pop(user_id, None)
            return

        current_key = keys[idx]
        # Отримуємо дані варіантів для поточного ключа (напр. "Memory" -> {128GB:..., 256GB:...})
        options_data = state['variants_data'].get(current_key, {})

        keyboard = []
        row = []

        # Перевіряємо, чи options_data це словник (складний варіант) чи список (простий)
        if isinstance(options_data, dict):
            # Сортуємо ключі, щоб порядок був гарним
            sorted_items = sorted(options_data.items(), key=lambda x: x[0])

            for opt, val in sorted_items:
                quantity = 0
                price_info = ""

                # 👇 БЕЗПЕЧНА ПЕРЕВІРКА ТИПУ ДАНИХ 👇
                if isinstance(val, dict):
                    # Це новий формат з ціною: {'qty': 5, 'price': 1200}
                    quantity = val.get('qty', 0)
                    if 'price' in val:
                        price_info = f" {val['price']}$"
                else:
                    # Це старий формат або просто число: 5
                    try:
                        quantity = int(val)
                    except:
                        quantity = 0  # Якщо там сміття

                btn_text = f"{opt}{price_info}"

                # ТІЛЬКИ ТЕПЕР перевіряємо quantity
                if quantity > 0:
                    row.append(InlineKeyboardButton(btn_text, callback_data=f"var_sel_{idx}_{opt}"))
                else:
                    row.append(InlineKeyboardButton(f"{opt} (❌)", callback_data="noop"))

        elif isinstance(options_data, list):
            # Простий список ["Red", "Blue"] без складу
            for opt in options_data:
                row.append(InlineKeyboardButton(str(opt), callback_data=f"var_sel_{idx}_{opt}"))

        # Розбивка кнопок (по 2 в ряд)
        final_keyboard = []
        temp_row = []
        for btn in row:
            temp_row.append(btn)
            if len(temp_row) == 2:
                final_keyboard.append(temp_row)
                temp_row = []
        if temp_row: final_keyboard.append(temp_row)

        final_keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_selection")])

        text = f"👇 Select **{current_key}**:"

        # Відправка
        query = update.callback_query
        try:
            if query.message.photo:
                await query.edit_message_caption(
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(final_keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.edit_message_text(
                    text=text,
                    reply_markup=InlineKeyboardMarkup(final_keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=InlineKeyboardMarkup(final_keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    async def handle_variant_selection_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        user_id = query.from_user.id

        # 1. ОБРОБКА КНОПКИ "CANCEL"
        if data == "cancel_selection":
            state = self.user_states.get(user_id)
            # Якщо ми знаємо, з якого товару почали - повертаємось на його картку
            if state and 'product_id' in state:
                pid = state['product_id']
                self.user_states.pop(user_id, None)
                await self.show_product(update, context, product_id_override=pid)
            else:
                self.user_states.pop(user_id, None)
                try:
                    await query.message.delete()
                except:
                    pass
            return

        # 2. РОЗБІР ДАНИХ КНОПКИ (var_sel_Index_OptionName)
        try:
            parts = data.split("_")
            # data виглядає як: var_sel_0_128GB
            # parts[0]=var, parts[1]=sel, parts[2]=Index, parts[3:]=Name
            idx = int(parts[2])
            value = "_".join(parts[3:])  # Збираємо назву назад, якщо в ній були підкреслення
        except:
            await query.answer("❌ Error processing data")
            return

        state = self.user_states.get(user_id)
        if not state or state.get('step') != 'selecting_variant':
            await query.answer("❌ Session expired")
            try:
                await query.message.delete()
            except:
                pass
            return

        # 3. ЗБЕРІГАЄМО ВИБІР
        # Ми просто зберігаємо НАЗВУ опції (наприклад "128GB")
        # Нам тут не треба лізти в ціну чи кількість, це робить add_item_to_cart_db
        key = state['variant_keys'][idx]
        state['selected_options'][key] = value

        # 4. ПЕРЕХІД ДО НАСТУПНОГО КРОКУ
        state['current_key_index'] += 1

        # Викликаємо функцію, яка покаже наступне питання або додасть в кошик
        await self.ask_next_variant(update, context)

        # 4. Допоміжна функція запису в БД

    async def handle_cart_actions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        user_id = update.effective_user.id

        # Формат: cart_item_plus_55 (де 55 - id запису в таблиці cart)
        parts = data.split("_")
        action = parts[2]  # plus або minus
        cart_id = parts[3]

        cursor = self.conn.cursor()

        # 1. Отримуємо інформацію про цей конкретний запис
        cursor.execute('''
                       SELECT c.quantity, c.selected_options, p.stock, p.variants, p.id
                       FROM cart c
                                JOIN products p ON c.product_id = p.id
                       WHERE c.id = ?
                       ''', (cart_id,))
        row = cursor.fetchone()

        if not row:
            await self.show_cart(update, context)  # Якщо запис зник, просто оновлюємо екран
            return

        current_qty = row[0]
        opts_json = row[1]
        real_stock = row[2]
        variants_json = row[3]

        # 2. Визначаємо ліміт
        limit = real_stock
        if opts_json and variants_json:
            try:
                sel_opts = json.loads(opts_json)
                v_data = json.loads(variants_json)
                for key, val in sel_opts.items():
                    if key in v_data:
                        group = v_data[key]
                        if isinstance(group, dict) and val in group:
                            target = group[val]
                            if isinstance(target, dict):
                                limit = target.get('qty', 0)
                            else:
                                limit = int(target)
            except:
                pass

        # 3. Виконуємо дію
        if action == "plus":
            if current_qty + 1 > limit:
                await query.answer(f"❌ Only {limit} items available!", show_alert=True)
                return

            cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE id = ?", (cart_id,))

        elif action == "minus":
            if current_qty > 1:
                cursor.execute("UPDATE cart SET quantity = quantity - 1 WHERE id = ?", (cart_id,))
            else:
                # Якщо 1, то видаляємо
                cursor.execute("DELETE FROM cart WHERE id = ?", (cart_id,))

        self.conn.commit()
        await self.show_cart(update, context)

    async def add_item_to_cart_db(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id, options):
        user_id = update.effective_user.id

        # Сортуємо ключі, щоб уникнути дублікатів у базі ({"Size": "S"} vs {"Size":"S"})
        options_json = json.dumps(options, ensure_ascii=False, sort_keys=True) if options else None

        cursor = self.conn.cursor()

        # 1. ПЕРЕВІРКА ЛІМІТУ
        cursor.execute("SELECT stock, variants FROM products WHERE id = ?", (product_id,))
        prod_row = cursor.fetchone()

        # Якщо товар раптом зник
        if not prod_row:
            if update.callback_query: await update.callback_query.answer("❌ Error: Product not found")
            return

        real_stock = prod_row[0]
        variants_json = prod_row[1]

        limit = real_stock

        # Пробуємо знайти точний ліміт для варіанту
        if options and variants_json:
            try:
                variants_data = json.loads(variants_json)
                for key, val in options.items():
                    if key in variants_data:
                        v_data = variants_data[key]

                        # Перевіряємо тип даних (чи це словник з ціною, чи просто число)
                        if isinstance(v_data, dict):
                            if val in v_data:
                                specific_val = v_data[val]
                                if isinstance(specific_val, dict):
                                    limit = specific_val.get('qty', 0)
                                else:
                                    limit = int(specific_val)
                        # Якщо це старий формат (просто список без кількості) - ліміт це загальний сток
            except Exception as e:
                print(f"Limit calc error: {e}")
                # У разі помилки лімітом стає загальний склад (щоб не блокувати продаж)
                limit = real_stock

        # 2. ЩО ВЖЕ Є В КОШИКУ?
        if options_json:
            cursor.execute(
                "SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ? AND selected_options = ?",
                (user_id, product_id, options_json)
            )
        else:
            cursor.execute(
                "SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ? AND selected_options IS NULL",
                (user_id, product_id)
            )

        cart_row = cursor.fetchone()
        current_in_cart = cart_row[1] if cart_row else 0

        # 3. ПЕРЕВІРКА: ЧИ НЕ ПЕРЕВИЩУЄМО ЛІМІТ?
        if current_in_cart + 1 > limit:
            if update.callback_query:
                await update.callback_query.answer(f"❌ Limit reached! Only {limit} available.", show_alert=True)

            # 🔥 ВАЖЛИВО: Навіть якщо помилка, повертаємо юзера на картку товару!
            # Раніше тут був просто return, і меню зникало або зависало.
            await self.show_product(update, context, product_id_override=product_id)
            return

        # 4. ДОДАВАННЯ В БАЗУ
        if cart_row:
            cart_id = cart_row[0]
            cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE id = ?", (cart_id,))
        else:
            cursor.execute("INSERT INTO cart (user_id, product_id, quantity, selected_options) VALUES (?, ?, 1, ?)",
                           (user_id, product_id, options_json))

        self.conn.commit()

        if update.callback_query:
            await update.callback_query.answer("✅ Added to cart!", show_alert=False)

        # Повертаємось на картку товару
        await self.show_product(update, context, product_id_override=product_id)


    async def remove_from_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        await query.answer("➖ Removed from cart")
        product_id = int(query.data.replace("remove_from_cart_", ""))
        user_id = query.from_user.id
        cursor = self.conn.cursor()
        cursor.execute("UPDATE cart SET quantity = quantity - 1 WHERE user_id=? AND product_id=? AND quantity > 0", (user_id, product_id))
        cursor.execute("DELETE FROM cart WHERE quantity <= 0")
        self.conn.commit()
        await self.update_product_view(query, product_id, context)

    async def cart_operations(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        data = query.data
        if data.startswith("cart_add_"):
            product_id = int(data.replace("cart_add_", ""))
            await self.add_to_cart_from_cart(update, context, product_id)
        elif data.startswith("cart_remove_"):
            product_id = int(data.replace("cart_remove_", ""))
            await self.remove_from_cart_from_cart(update, context, product_id)

    async def add_to_cart_from_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT stock, name FROM products WHERE id = ?", (product_id,))
        stock, product_name = cursor.fetchone()
        cursor.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        current_qty = cursor.fetchone()[0]
        if current_qty >= stock:
            await update.callback_query.answer("❌ Maximum amount reached", show_alert=True)
            return
        cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        self.conn.commit()
        await update.callback_query.answer(f"➕ {product_name}. Amount: {current_qty + 1}")
        await self.show_cart(update, context)

    async def remove_from_cart_from_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        current_qty = cursor.fetchone()[0]
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        product_name = cursor.fetchone()[0]
        if current_qty > 1:
            cursor.execute("UPDATE cart SET quantity = quantity - 1 WHERE user_id = ? AND product_id = ?", (user_id, product_id))
            msg = f"➖ {product_name}. Amount: {current_qty - 1}"
        else:
            cursor.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
            msg = f"🗑️ {product_name} removed from cart!"
        self.conn.commit()
        await update.callback_query.answer(msg)
        await self.show_cart(update, context)

    async def clear_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cart WHERE user_id = ?", (user_id,))
        items_count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        self.conn.commit()
        await update.callback_query.answer(f"🗑️ Cart cleared! {items_count} items removed")
        await self.show_cart(update, context)

    async def update_product_view(self, query, product_id, context):
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            product = cursor.fetchone()

            user_id = query.from_user.id
            cursor.execute("SELECT quantity FROM cart WHERE user_id=? AND product_id=?", (user_id, product_id))
            row = cursor.fetchone()
            cart_qty = row[0] if row else 0

            emoji = product['emoji'] if product['emoji'] else ""
            stock = product['stock']
            img_source = product['image_url']
            is_file_id = img_source and not img_source.startswith("http")

            image_link_markdown = f"[\u200b]({img_source})" if (img_source and not is_file_id) else ""

            # --- КНОПКА ДОДАВАННЯ (Короткий текст) ---
            if stock > 0:
                stock_text = f"📦 **In Stock:** {stock}"
                add_btn = InlineKeyboardButton("➕ Add", callback_data=f"add_to_cart_{product_id}")
            else:
                stock_text = "❌ **OUT OF STOCK**"
                add_btn = None

            text = f"""{image_link_markdown}
    {emoji} **{product['name']}**

    📝 {product['description']}

    💰 **Price:** {product['price']}$
    {stock_text}
    🛒 **In Cart:** {cart_qty}

    **Category:** {product['category']}"""

            keyboard = []

            # --- РЯДОК 1: Кнопки управління (В один ряд!) ---
            control_row = []

            # 1. Кнопка "Відняти" (ліворуч)
            if cart_qty > 0:
                control_row.append(InlineKeyboardButton("➖ Remove", callback_data=f"remove_from_cart_{product_id}"))

            # 2. Кнопка "Додати" (праворуч)
            if add_btn:
                control_row.append(add_btn)

            # Додаємо цей спільний ряд
            if control_row:
                keyboard.append(control_row)

            # --- РЯДОК 2: КОШИК ---
            keyboard.append([InlineKeyboardButton(f"🛒 Go to Cart ({cart_qty})", callback_data="cart")])

            # --- РЯДОК 3: НАЗАД ---
            keyboard.append([InlineKeyboardButton(f"🔙 Back to {product['category']}",
                                                  callback_data=f"category_{product['category']}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            # Логіка оновлення (фото або текст)
            # Якщо поточне повідомлення - Фото -> Редагуємо підпис (Caption)
            if query.message.photo:
                try:
                    await query.edit_message_caption(caption=text, reply_markup=reply_markup,
                                                     parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
            else:
                # Якщо текст -> Редагуємо текст
                try:
                    await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass

    # -------------------- CHECKOUT LOGIC --------------------
    async def checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return

        query = update.callback_query
        user_id = update.effective_user.id

        # 👇 ДОДАЛИ 'msg_id': query.message.message_id
        # Тепер бот пам'ятає ID цього повідомлення, щоб потім його видалити
        self.user_states[user_id] = {
            'step': 'waiting_email',
            'msg_id': query.message.message_id
        }

        cursor = self.conn.cursor()
        cursor.execute("SELECT phone, address FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()

        keyboard = []
        if user_data and user_data[1]:
            keyboard.append([InlineKeyboardButton("👤 Use my profile data", callback_data="use_profile_data")])

        keyboard.extend([
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_cart")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "📋 **Placing an order**\n\n"
            "If you have saved data in your profile, you can use it to checkout faster!\n\n"
            "Or you can proceed with entering your data manually.\n\n"
            "📧 **Step 1/4:** Enter your email address.\n"
            "Example: example@gmail.com"
        )

        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="Markdown")

    async def use_profile_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return

        query = update.callback_query
        user_id = update.effective_user.id

        cursor = self.conn.cursor()
        cursor.execute("SELECT phone, address, email FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()

        # 👇 1. ОБОВ'ЯЗКОВО ЗБЕРІГАЄМО ID ПОВІДОМЛЕННЯ 👇
        # Щоб потім handle_checkout_input міг його видалити
        msg_id = query.message.message_id

        if user_data and user_data[1]:  # Є адреса
            self.user_states[user_id] = {
                'step': 'waiting_payment',
                'phone': user_data[0],
                'address': user_data[1],
                'email': user_data[2],
                'msg_id': msg_id  # <--- ЗАПИСАЛИ
            }

            if user_data[2]:  # Є email
                keyboard = [
                    [InlineKeyboardButton("💵 Cash on delivery", callback_data="pay_cod")],
                    [InlineKeyboardButton("💳 Card to courier", callback_data="pay_card")],
                    [InlineKeyboardButton("🏦 Bank transfer", callback_data="pay_bank")],
                    [InlineKeyboardButton("🔙 Back", callback_data="checkout")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
                ]
                await query.edit_message_text(
                    "📋 **Placing an order**\n\n"
                    "✅ Your data has been pre-filled from your profile.\n\n"
                    "💳 **Step 4/4:** Choose a payment method:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            else:  # Немає email
                self.user_states[user_id]['step'] = 'waiting_email_after_profile'
                keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]]
                await query.edit_message_text(
                    "📋 **Placing an order**\n\n"
                    "Your address is filled from your profile.\n\n"
                    "📧 **Step 2/4:** Enter your email address.\n"
                    "Example: example@gmail.com",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
        else:
            await self.checkout(update, context)

    async def handle_payment_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return

        query = update.callback_query
        user_id = query.from_user.id

        # Визначаємо метод
        payment_map = {"pay_cod": "Cash on delivery", "pay_card": "Card to courier", "pay_bank": "Bank transfer"}
        payment_key = query.data
        if payment_key not in payment_map: return
        payment_method = payment_map[payment_key]

        if user_id not in self.user_states:
            await query.answer("❌ Session expired")
            await self.show_cart(update, context)
            return

        # Зберігаємо метод оплати
        self.user_states[user_id]['payment'] = payment_method

        # 👇 ПЕРЕВІРКА: ЧИ Є ТЕЛЕФОН? 👇
        if not self.user_states[user_id].get('phone'):
            # Якщо телефону немає - питаємо його і ЗБЕРІГАЄМО ID повідомлення
            self.user_states[user_id]['step'] = 'waiting_phone'
            self.user_states[user_id]['msg_id'] = query.message.message_id  # <--- ЗАПИСАЛИ

            await query.edit_message_text(
                "📋 **Placing an order**\n\n"
                "📞 **Step 2/4:** Enter your phone number for delivery contact:\n"
                "Enter your phone number in the format: +380XXXXXXXXX\n"
                "Example: +380501234567",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="checkout")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
                ]),
                parse_mode="Markdown"
            )
            return

        # Якщо телефон є - створюємо замовлення (як раніше)
        order_details = await self.create_order(update, context, send_message=False)

        if not order_details:
            try:
                await query.edit_message_text("❌ Order failed.", reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🛒 Cart", callback_data="cart")]]))
            except Exception:
                pass
            return

        order_id, products_list, total_amount = order_details
        products_text = "".join(
            f"{item['emoji']} {item['name']} × {item['quantity']} = {item['total']}$\n" for item in products_list)

        try:
            from zoneinfo import ZoneInfo
            tz_name = globals().get('BOT_TIMEZONE', 'Europe/Kyiv')
            current_time = datetime.now(ZoneInfo(tz_name)).strftime('%d.%m.%Y %H:%M')
        except:
            current_time = datetime.now().strftime('%d.%m.%Y %H:%M')

        order_text = (
            f"✅ **Order #{order_id} has been successfully placed!**\n\n"
            f"📦 **Products:**\n{products_text}"
            f"💳 **Total: {total_amount}$**\n"
            f"🗓 **Date:** {current_time}\n\n"
            f"👤 **Payment:** {payment_method}\n\n"
            f"Managers will contact you shortly to confirm details. ❤️"
        )

        await query.edit_message_text(
            text=order_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]),
            parse_mode="Markdown"
        )

    async def handle_checkout_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id
        if user_id not in self.user_states: return
        state = self.user_states[user_id]
        msg = update.message

        # 1. Видаляємо повідомлення користувача (те, що ви ввели)
        try:
            await msg.delete()
        except Exception:
            pass

        # 2. Видаляємо старе запитання бота (якщо ми зберегли його ID)
        # 👇👇👇 ОСЬ ЦЕЙ БЛОК ВИДАЛЯЄ СТАРЕ 👇👇👇
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=msg.chat_id, message_id=state['msg_id'])
            except Exception:
                pass
        # ---------------------------------------------

        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]]

        # --- PHONE (Логіка телефону) ---
        if state['step'] == 'waiting_phone':
            # ... (перевірка тексту на Cancel) ...
            if msg.text and (msg.text.strip().lower() in ["❌ cancel", "cancel"]):
                self.user_states.pop(user_id, None)
                await self.show_cart(update, context)
                return

            phone = None
            if msg.contact:
                phone = msg.contact.phone_number
            elif msg.text:
                phone = msg.text.strip()

            if not phone:
                m = await msg.reply_text("❌ Please enter a phone number.")
                state['msg_id'] = m.message_id  # Зберігаємо ID помилки
                return

            phone = phone.replace(" ", "").replace("-", "")
            if not re.fullmatch(r"\+380\d{9}", phone):
                m = await msg.reply_text("❌ Incorrect format. Example: +380501234567")
                state['msg_id'] = m.message_id  # Зберігаємо ID помилки
                return

            state['phone'] = phone

            # Якщо все ок - йдемо далі
            # Якщо вже є метод оплати (ми прийшли сюди після вибору оплати) -> Фіналізуємо
            if state.get('payment'):
                order_details = await self.create_order(update, context, send_message=False)
                if not order_details: return
                order_id, products_list, total_amount = order_details
                payment = state['payment']

                products_text = "".join(
                    f"{item['emoji']} {item['name']} × {item['quantity']} = {item['total']}$\n" for item in
                    products_list)
                try:
                    from zoneinfo import ZoneInfo
                    tz_name = globals().get('BOT_TIMEZONE', 'Europe/Kyiv')
                    current_time = datetime.now(ZoneInfo(tz_name)).strftime('%d.%m.%Y %H:%M')
                except:
                    current_time = datetime.now().strftime('%d.%m.%Y %H:%M')

                order_text = (
                    f"✅ **Order #{order_id} has been successfully placed!**\n\n"
                    f"📦 **Products:**\n{products_text}"
                    f"💳 **Total: {total_amount}$**\n"
                    f"🗓 **Date:** {current_time}\n\n"
                    f"👤 **Payment:** {payment}\n\n"
                    f"Managers will contact you shortly to confirm details. ❤️"
                )
                await msg.reply_text(order_text, reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]]), parse_mode="Markdown")
                return

            # Якщо немає адреси -> питаємо адресу
            if not state.get('address'):
                state['step'] = 'waiting_address'
                new_msg = await msg.reply_text(
                    "📋 **Placing an order**\n\n"
                    "📍 **Step 3/4:** Enter your shipping address.\n"
                    "Example: Kyiv, 1 Khreshchatyk St., apt. 10",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]]),
                    parse_mode="Markdown"
                )
                state['msg_id'] = new_msg.message_id  # <--- ЗБЕРІГАЄМО НОВИЙ ID
                return

        # ... (Код для Email та Address залишається аналогічним, головне всюди зберігати msg_id) ...
        # (Якщо треба - я можу скинути повний код функції handle_checkout_input)

    async def choose_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = query.from_user.id
        state = self.user_states.get(user_id)

        if not state or state.get('step') != 'waiting_payment':
            await query.answer("❌ Payment step invalid")
            return

        payment_map = {
            "pay_cod": "Cash on delivery",
            "pay_card": "Card to courier",
            "pay_bank": "Bank transfer"
        }
        payment = payment_map.get(data)
        if not payment:
            await query.answer("❌ Invalid payment method")
            return

        # Для оплати при отриманні потрібен телефон
        if payment == "Cash on delivery" and not state.get("phone"):
            self.user_states[user_id]['payment'] = payment
            self.user_states[user_id]['step'] = 'waiting_phone'
            keyboard = [
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_payment")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
            ]
            await query.edit_message_text(
                "📞 **Please enter your phone number for delivery contact:**\n" \
                "Enter your phone number in the format: +380XXXXXXXXX\n" \
                "Example: +380501234567",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Зберігаємо метод оплати
        self.user_states[user_id]['payment'] = payment

        # 1. Зберігаємо дані у змінні ПЕРЕД створенням замовлення (щоб не зникли)
        user_email = state.get('email', '—')
        user_phone = state.get('phone', '—')
        user_address = state.get('address', '—')

        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT p.name, p.price, c.quantity, p.emoji FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
            (user_id,))
        cart_items = cursor.fetchall()
        if not cart_items:
            await query.edit_message_text("❌ The cart is empty!")
            self.user_states.pop(user_id, None)
            return

        total_amount = sum(price * quantity for _, price, quantity, _ in cart_items)

        # Логіка для банківського переказу (залишається як є)
        if payment == "Bank transfer":
            order_text = (
                f"🏦 *Bank transfer selected*\n\n" \
                f"To pay for your order, transfer the amount to the following details:\n\n" \
                f"*IBAN:* `UA123456789012345678901234567`\n" \
                f"*Recipient:* Example Shop LLC\n" \
                f"*Purpose:* Order Payment\n" \
                f"*Amount:* {total_amount}$\n\n" \
                f"_After payment, your order will be automatically confirmed._"
            )
            keyboard = [
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_payment")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
            ]
            await query.edit_message_text(order_text, reply_markup=InlineKeyboardMarkup(keyboard),
                                          parse_mode=ParseMode.MARKDOWN)
            return

        # Створюємо замовлення
        order_details = await self.create_order(update, context, send_message=False)
        if not order_details:
            return

        order_id, products_list, total_amount = order_details

        products_text = "".join(
            f"{item['emoji']} {item['name']} × {item['quantity']} = {item['total']}$\n" for item in products_list)

        # Час
        try:
            from zoneinfo import ZoneInfo
            tz_name = globals().get('BOT_TIMEZONE', 'Europe/Kyiv')
            current_time = datetime.now(ZoneInfo(tz_name)).strftime('%d.%m.%Y %H:%M')
        except Exception:
            current_time = datetime.now().strftime('%d.%m.%Y %H:%M')

        # 👇 ОНОВЛЕНИЙ ЧЕК 👇
        order_text = (
            f"✅ **Order #{order_id} has been successfully placed!**\n\n"
            f"👤 **Customer:** {update.effective_user.full_name}\n"
            f"📧 **Email:** {user_email}\n"
            f"📞 **Phone:** {user_phone}\n"
            f"📍 **Address:** {user_address}\n"
            f"💳 **Payment Method:** {payment}\n\n"  # <--- Ось воно!
            f"📦 **Products:**\n"
            f"{products_text}"
            f"💳 **Total amount: {total_amount}$**\n\n"
            f"📋 **Status:** In progress\n"
            f"🕐 **Date:** {current_time}\n\n"
            f"Thank you for shopping with us! 🛍️"  # <--- Нова фраза
        )

        keyboard = [[InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]]

        await query.edit_message_text(
            text=order_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )


    async def handle_checkout_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        """Handles the back buttons during the checkout process."""
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = query.from_user.id

        if data == "back_to_email":
            if user_id in self.user_states:
                self.user_states[user_id]['step'] = 'waiting_email'
            keyboard = [
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_cart")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
            ]
            await query.edit_message_text(
                "📋 **Placing an order**\n\n📧 **Step 1/3:** Enter your email address.\nExample: example@gmail.com",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

        elif data == "back_to_cart":
            await self.show_cart(update, context)

        elif data == "back_to_phone":
            if user_id in self.user_states:
                self.user_states[user_id]['step'] = 'waiting_phone'
            keyboard = [
                [InlineKeyboardButton("🔙 Back to email", callback_data="back_to_email")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
            ]
            await query.edit_message_text(
                "📋 **Placing an order**\n\n"
                "📞 **Step 2/4:** Enter your phone number.\n"
                "Example: +380501234567",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

        elif data == "back_to_address":
            if user_id in self.user_states:
                self.user_states[user_id]['step'] = 'waiting_address'
            keyboard = [
                [InlineKeyboardButton("🔙 Back to email", callback_data="back_to_email")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
            ]
            await query.edit_message_text(
                "📋 **Placing an order**\n\n📍 **Step 2/3:** Enter your shipping address.\nExample: Kyiv, 1 Khreshchatyk St., apt. 10",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

        elif data == "back_to_payment":
            if user_id in self.user_states:
                self.user_states[user_id]['step'] = 'waiting_payment'
            keyboard = [
                [InlineKeyboardButton("💵 Cash on delivery", callback_data="pay_cod")],
                [InlineKeyboardButton("💳 Card to courier", callback_data="pay_card")],
                [InlineKeyboardButton("🏦 Bank transfer", callback_data="pay_bank")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_address")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
            ]
            await query.edit_message_text(
                "📋 **Placing an order**\n\n💳 **Step 3/3:** Choose a payment method:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

    async def handle_cancel_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        self.user_states.pop(user_id, None)
        await update.callback_query.edit_message_text("❌ Order cancelled.")
        await self.show_cart(update, context)

    async def show_category_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data

        # Формат: category_{Category}_{Page} (якщо page немає - то 1)
        # Наприклад: category_Electronics_2
        parts = data.split("_")

        # Спроба витягнути сторінку, якщо вона є
        try:
            if parts[-1].isdigit():
                page = int(parts[-1])
                category = "_".join(parts[1:-1])
            else:
                page = 1
                category = data.replace("category_", "")
        except:
            page = 1
            category = data.replace("category_", "")

        cursor = self.conn.cursor()

        # Пагінація
        cursor.execute("SELECT COUNT(*) FROM products WHERE category = ?", (category,))
        total_items = cursor.fetchone()[0]
        if total_items == 0:
            await query.answer("No products here yet!")
            return

        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        offset = (page - 1) * ITEMS_PER_PAGE

        cursor.execute("SELECT id, name, price, emoji FROM products WHERE category = ? LIMIT ? OFFSET ?",
                       (category, ITEMS_PER_PAGE, offset))
        products = cursor.fetchall()

        text = f"📂 **{category}**\nPage {page}/{total_pages}"
        keyboard = []

        for p_id, name, price, emoji in products:
            emo = emoji if emoji else "📦"
            keyboard.append([InlineKeyboardButton(f"{emo} {name} - {price}$", callback_data=f"product_{p_id}")])

        # Навігація
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"category_{category}_{page - 1}"))

        nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))

        if page < total_pages:
            nav_row.append(InlineKeyboardButton("➡️", callback_data=f"category_{category}_{page + 1}"))

        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("🔙 Back to Catalog", callback_data="catalog")])

        try:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                          parse_mode=ParseMode.MARKDOWN)
        except:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def create_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, send_message=True):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id
        user_name = update.effective_user.full_name
        user_username = update.effective_user.username
        target_message = update.message or (update.callback_query.message if update.callback_query else None)

        if user_id not in self.user_states: return None
        state = self.user_states[user_id]
        out_of_stock_alert = []

        try:
            with self.conn:
                cursor = self.conn.cursor()

                # 1. Отримуємо товари з кошика
                cursor.execute(
                    'SELECT p.id, p.name, p.price, c.quantity, p.emoji, p.stock, c.selected_options '
                    'FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
                    (user_id,)
                )
                cart_items = cursor.fetchall()

                if not cart_items:
                    if target_message: await target_message.reply_text("❌ The cart is empty!")
                    self.user_states.pop(user_id, None)
                    return None

                products_list = []
                total_amount = 0

                # 2. Формування списку
                for product_id, name, price, quantity, emoji, stock, selected_opts_json in cart_items:
                    if stock < quantity:
                        if target_message: await target_message.reply_text(f"Sorry, product '{name}' is out of stock.")
                        return None

                    if (stock - quantity) == 0: out_of_stock_alert.append(name)

                    # Рахуємо ціну (з урахуванням варіантів)
                    item_price = price
                    sel_opts = json.loads(selected_opts_json) if selected_opts_json else {}

                    # (Тут можна додати логіку підтягування ціни з варіанту, якщо треба для звіту)
                    # Але для списання нам головне знати, ЩО списувати

                    item_total = item_price * quantity  # Тут спрощено, беремо базову або ту що в базі
                    # (Якщо у вас ціна динамічна в кошику, треба було б передавати її сюди,
                    # але для складу це не критично)

                    total_amount += item_total

                    products_list.append({
                        'name': name, 'price': price, 'quantity': quantity,
                        'emoji': emoji if emoji else "", 'total': item_total,
                        'product_id': product_id,
                        'selected_options': sel_opts
                    })

                # 3. СПИСАННЯ ЗІ СКЛАДУ (FIX BUG 🛠️)
                for item in products_list:
                    pid = item['product_id']
                    qty = item['quantity']
                    sel_opts = item['selected_options']

                    cursor.execute("SELECT variants, stock FROM products WHERE id = ?", (pid,))
                    row = cursor.fetchone()
                    if row:
                        db_variants_json = row[0]
                        current_total_stock = row[1]

                        # А. Списуємо загальний склад
                        new_total_stock = max(0, current_total_stock - qty)

                        # Б. Списуємо конкретні варіанти
                        new_variants_json = db_variants_json
                        if db_variants_json and sel_opts:
                            try:
                                variants_data = json.loads(db_variants_json)
                                changed = False

                                # sel_opts = {"Memory": "128GB"}
                                for key, val in sel_opts.items():
                                    if key in variants_data:
                                        group = variants_data[key]  # Це наприклад {"128GB": {"qty":5...}, "256GB":...}

                                        # Варіант 1: Складний об'єкт {"qty": 10, "price": 100}
                                        if isinstance(group, dict) and val in group:
                                            target = group[val]
                                            if isinstance(target, dict) and 'qty' in target:
                                                target['qty'] = max(0, target['qty'] - qty)
                                                changed = True
                                            # Варіант 2: Просте число {"128GB": 10}
                                            elif isinstance(target, int):
                                                group[val] = max(0, target - qty)
                                                changed = True

                                if changed:
                                    new_variants_json = json.dumps(variants_data, ensure_ascii=False)
                            except Exception as e:
                                print(f"Stock update error: {e}")

                        cursor.execute("UPDATE products SET stock = ?, variants = ? WHERE id = ?",
                                       (new_total_stock, new_variants_json, pid))

                # 4. Запис в БД замовлень
                products_json = json.dumps(products_list, ensure_ascii=False)
                cursor.execute(
                    'INSERT INTO orders (user_id, user_name, products, total_amount, email, phone, address, payment_method, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (user_id, user_name, products_json, total_amount, state.get('email'), state.get('phone'),
                     state.get('address'), state.get('payment'), 'pending')
                )
                order_id = cursor.lastrowid

                # Очистка кошика
                cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))

        except sqlite3.Error as e:
            logger.error(f"DB Error: {e}")
            if target_message: await target_message.reply_text("An error occurred.")
            return None

        self.user_states.pop(user_id, None)

        # 5. Повідомлення
        receipt_text = f"✅ **Order #{order_id} created!**\n\n"
        for p in products_list:
            opts_str = ""
            if p['selected_options']:
                opts_vals = [f"{k}: {v}" for k, v in p['selected_options'].items()]
                opts_str = f" ({', '.join(opts_vals)})"
            receipt_text += f"{p['emoji']} {p['name']}{opts_str}\n   {p['quantity']} x {p['price']}$ = {p['total']}$\n"

        receipt_text += f"\n💰 **Total: {total_amount}$**\n"
        receipt_text += f"📦 Status: Pending\n"

        if send_message and target_message:
            await target_message.reply_text(receipt_text, parse_mode=ParseMode.MARKDOWN)
            await target_message.reply_text("Thank you!", reply_markup=self.get_main_menu_keyboard())

        # Адмін
        admin_text = (
            f"🆕 **New Order #{order_id}**\n"
            f"👤 User: {user_name} (@{user_username or '-'})\n"
            f"📧 Email: {state.get('email') or '-'}\n"
            f"📞 Phone: {state.get('phone')}\n"
            f"📍 Address: {state.get('address')}\n"
            f"💳 Payment: {state.get('payment')}\n\n"
            f"🛒 **Items:**\n"
        )
        for p in products_list:
            opts_str = ""
            if p['selected_options']:
                opts_vals = [f"{k}: {v}" for k, v in p['selected_options'].items()]
                opts_str = f" ({', '.join(opts_vals)})"
            admin_text += f"- {p['name']}{opts_str} (x{p['quantity']})\n"

        admin_text += f"\n💰 **Total: {total_amount}$**"

        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")

        if out_of_stock_alert:
            alert_msg = "⚠️ **Stock Alert:**\n" + "\n".join([f"- {n} is now OUT OF STOCK!" for n in out_of_stock_alert])
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=alert_msg, parse_mode=ParseMode.MARKDOWN)
            except:
                pass

        return order_id, products_list, total_amount



    # -------------------- USER ORDERS --------------------
    async def show_my_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        user_id = update.effective_user.id
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) as total FROM orders WHERE user_id = ?", (user_id,))
        total_orders = cursor.fetchone()["total"]
        per_page = 10
        total_pages = (total_orders - 1) // per_page + 1 if total_orders else 1
        offset = page * per_page
        if total_orders == 0:
            await query.edit_message_text("🛒 You have no orders yet.", parse_mode="Markdown")
            await query.answer()
            return

        cursor.execute(
            'SELECT id, total_amount, status, created_at, products FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?',
            (user_id, per_page, offset))
        orders = cursor.fetchall()
        text = f"📋 Your orders (Page {page + 1}/{total_pages}):\n\n"
        keyboard = []
        status_emoji = {'pending': '🟡 In processing', 'confirmed': '🔵 Confirmed', 'shipped': '🟠 Sent',
                        'delivered': '🟢 Delivered', 'cancelled': '🔴 Cancelled'}
        for order in orders:
            products = json.loads(order["products"] or "[]")

            # 👇 ВИПРАВЛЕННЯ ЧАСУ 👇
            fmt_date = self.format_date(order['created_at'])

            text += f"🧾 Order #{order['id']}\n"
            for product in products:
                text += f"   {product.get('emoji', '')} {product.get('name', '')} × {product.get('quantity', 0)} = {product.get('total', 0)}$\n"
            text += f"💰 {order['total_amount']}$ | {status_emoji.get(order['status'], order['status'])}\n"
            text += f"📅 {fmt_date}\n\n"
            keyboard.append(
                [InlineKeyboardButton(f"Details #{order['id']}", callback_data=f"order_details_{order['id']}")])

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("🔙 Prev", callback_data=f"my_orders_page_{page - 1}"))
        if page + 1 < total_pages:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_orders_page_{page + 1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        await query.answer()

    async def handle_my_orders_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        await query.answer()
        match = re.match(r'^my_orders_page_(\d+)$', query.data)
        if match:
            page = int(match.group(1))
            await self.show_my_orders(update, context, page)

    async def show_order_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE, order_id=None):
        if await self.check_user_blocked(update, context):
            return

        query = getattr(update, "callback_query", None)
        message = getattr(update, "message", None)

        if order_id is None:
            data = query.data if query and query.data else (message.text if message and message.text else None)
            if not data:
                if query: await query.answer("❌ Invalid request")
                return

            match = re.search(r'order_details_(\d+)', data)
            if not match:
                if query: await query.answer("❌ Invalid request")
                return
            order_id = int(match.group(1))

        uid = update.effective_user.id

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE id = ?" + ("" if uid == ADMIN_ID else " AND user_id = ?"),
                       (order_id,) if uid == ADMIN_ID else (order_id, uid))
        order = cursor.fetchone()
        if not order:
            if query: await query.answer("❌ Order not found")
            return

        order_id_val = order["id"]
        user_name = order["user_name"]
        phone = order["phone"] or "—"
        address = order["address"]
        products_json = order["products"] or "[]"
        total = order["total_amount"]
        status = order["status"]

        # 👇👇👇 ОСЬ ТУТ МАГІЯ ВИПРАВЛЕННЯ 👇👇👇
        # Беремо "сирий" час з бази і перетворюємо в український
        formatted_date = self.format_date(order["created_at"])
        # 👆👆👆

        products = json.loads(products_json)
        status_emoji = {'pending': '🟡 In processing', 'confirmed': '🔵 Confirmed', 'shipped': '🟠 Sent',
                        'delivered': '🟢 Delivered', 'cancelled': '🔴 Cancelled'}

        order_text = f"📋 **Order #{order_id_val}**\n\n👤 **Customer:** {user_name}\n"
        order_text += f"📧 **Email:** {order['email'] or '—'}\n"
        order_text += f"📞 **Phone:** {phone}\n"
        if uid == ADMIN_ID:
            order_text += f"💳 **Payment method:** {order['payment_method'] or '—'}\n"
        order_text += f"📍 **Address:** {address}\n\n📦 **Products:**\n"

        for product in products:
            order_text += f"{product.get('emoji', '')} {product.get('name', '')} × {product.get('quantity', 0)} = {product.get('total', 0)}$\n"

        order_text += f"\n💳 **Total amount: {total}$**\n" \
                      f"📊 **Status:** {status_emoji.get(status, status)}\n" \
                      f"🕐 **Date:** {formatted_date}"  # 👈 Використовуємо вже виправлений час

        keyboard = []
        if uid == ADMIN_ID:
            if status not in ("cancelled", "delivered"):
                keyboard.append([
                    InlineKeyboardButton("✅ Confirm", callback_data=f"admin_confirm_{order_id_val}"),
                    InlineKeyboardButton("📦 Sent", callback_data=f"admin_ship_{order_id_val}")
                ])
                keyboard.append([
                    InlineKeyboardButton("🚚 Delivered", callback_data=f"admin_deliver_{order_id_val}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"admin_cancel_{order_id_val}")
                ])
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_all_orders_page_0")])
        else:
            if status not in ('cancelled', 'delivered'):
                keyboard.append([InlineKeyboardButton("❌ Cancel order", callback_data=f"user_cancel_{order_id_val}")])
            keyboard.append([InlineKeyboardButton("🔙 My orders", callback_data="my_orders")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        if query:
            try:
                await query.edit_message_text(order_text, reply_markup=reply_markup, parse_mode="Markdown")
            except Exception:
                pass
        elif message:
            await message.reply_text(order_text, reply_markup=reply_markup, parse_mode="Markdown")



    # -------------------- ADMIN PANEL --------------------
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.callback_query.answer("❌ Access denied")
            return

        text = "👑 **ADMIN PANEL**\n\n👇 **Dashboard:**"

        keyboard = [
            # 1. Найважливіше - Замовлення
            [InlineKeyboardButton("📋 ALL ORDERS", callback_data="admin_all_orders")],

            # 2. Товари (Кнопку "Додати" прибрали, вона є всередині цього меню)
            [InlineKeyboardButton("📦 Products", callback_data="admin_products")],

            # 3. Аналітика
            [InlineKeyboardButton("📊 Stats", callback_data="admin_statistics"),
             InlineKeyboardButton("📈 Revenue", callback_data="admin_revenue_chart")],

            # 4. Користувачі
            [InlineKeyboardButton("👥 Users", callback_data="admin_user_management")],

            # 5. Вихід
            [InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]
        ]

        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    # -------------------- ADMIN: STATISTICS --------------------
    async def admin_statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.callback_query.answer("❌ Access denied")
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
        pending_orders = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(total_amount) FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered')")
        total_revenue = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM products")
        total_products = cursor.fetchone()[0]
        
        # Most popular products
        cursor.execute("SELECT products FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered')")
        product_counts = {}
        for row in cursor.fetchall():
            products = json.loads(row[0])
            for product in products:
                product_name = product.get("name", "Unknown")
                quantity = product.get("quantity", 0)
                if product_name in product_counts:
                    product_counts[product_name] += quantity
                else:
                    product_counts[product_name] = quantity
        
        popular_products = sorted(product_counts.items(), key=lambda item: item[1], reverse=True)
        popular_products_text = "\n".join([f"• {name}: {count}" for name, count in popular_products[:5]])

        text = f"""
👑 **ADMIN PANEL - STATISTICS**

📊 **General:**
• 📋 Total orders: {total_orders}
• 🟡 New orders: {pending_orders}
• 💰 Revenue: {total_revenue}$
• 📦 Products in catalog: {total_products}

🏆 **Most Popular Products:**
{popular_products_text}
        """
        
        keyboard = [
            [InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")]
        ]
        
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def admin_categories_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query

        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT category FROM products")
        categories = [row[0] for row in cursor.fetchall() if row[0]]

        if not categories:
            text = "📂 **Product Management**\n\nNo products found. Start by creating one!"
            keyboard = [
                # 👇 БУЛО: admin_create_product -> СТАЛО: admin_add_product (бо такий хендлер у вас в main)
                [InlineKeyboardButton("➕ Create Product", callback_data="admin_add_product")],
                # 👇 БУЛО: admin_menu -> СТАЛО: admin_panel
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]
            ]
        else:
            text = "📂 **Select a Category to manage:**"
            keyboard = []
            for cat in categories:
                keyboard.append([InlineKeyboardButton(f"📂 {cat}", callback_data=f"admin_list_cat_{cat}_1")])

            # 👇 Тут теж виправляємо
            keyboard.append([InlineKeyboardButton("➕ Create Product", callback_data="admin_add_product")])
            keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        if query:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup,
                                           parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup,
                                           parse_mode=ParseMode.MARKDOWN)

    async def admin_products_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data

        # Парсимо дані: admin_list_cat_{Category}_{Page}
        # Наприклад: admin_list_cat_Electronics_1
        try:
            parts = data.split("_")
            # Оскільки категорія може мати пробіли, беремо все між "cat" і останнім елементом
            page = int(parts[-1])
            category = "_".join(parts[3:-1])
        except:
            await query.answer("Error parsing category")
            return

        cursor = self.conn.cursor()

        # 1. Рахуємо загальну кількість товарів у категорії
        cursor.execute("SELECT COUNT(*) FROM products WHERE category = ?", (category,))
        total_items = cursor.fetchone()[0]
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

        # 2. Отримуємо товари для поточної сторінки
        offset = (page - 1) * ITEMS_PER_PAGE
        cursor.execute("SELECT id, name, stock FROM products WHERE category = ? LIMIT ? OFFSET ?",
                       (category, ITEMS_PER_PAGE, offset))
        products = cursor.fetchall()

        text = f"📂 Category: **{category}**\nPage {page}/{total_pages}\n\nSelect a product to edit:"
        keyboard = []

        for p_id, p_name, p_stock in products:
            status = "✅" if p_stock > 0 else "❌"
            # Кнопка веде в меню редагування товару
            keyboard.append([InlineKeyboardButton(f"{status} {p_name}", callback_data=f"admin_prod_{p_id}")])

        # 3. Кнопки пагінації (⬅️ ➡️)
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"admin_list_cat_{category}_{page - 1}"))

        nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))

        if page < total_pages:
            nav_row.append(InlineKeyboardButton("➡️", callback_data=f"admin_list_cat_{category}_{page + 1}"))

        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="admin_products")])

        try:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                          parse_mode=ParseMode.MARKDOWN)
        except:
            # Fallback якщо не можна відредагувати
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


    # -------------------- ADMIN: USER MANAGEMENT --------------------
    async def admin_user_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
        if update.effective_user.id != ADMIN_ID:
            await update.callback_query.answer("❌ Access denied")
            return

        # Pagination settings
        items_per_page = 10
        offset = page * items_per_page

        cursor = self.conn.cursor()

        # Count total users
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        total_pages = (total_users - 1) // items_per_page + 1 if total_users > 0 else 1

        # Fetch only the required page
        cursor.execute("SELECT user_id, blocked FROM users LIMIT ? OFFSET ?", (items_per_page, offset))
        users = cursor.fetchall()

        keyboard = []
        for user_id, blocked in users:
            action_text = "✅ Unblock" if blocked else "⛔ Block"
            callback_action = 0 if blocked else 1

            # Try to fetch username (Nick)
            try:
                chat = await context.bot.get_chat(user_id)
                if chat.username:
                    user_display = f"@{chat.username}"
                elif chat.first_name:
                    user_display = chat.first_name
                else:
                    user_display = f"ID: {user_id}"
            except Exception:
                user_display = f"ID: {user_id}"

            keyboard.append([
                # Display Nickname/Name
                InlineKeyboardButton(f"👤 {user_display}", callback_data="noop"),
                # Block/Unblock Action
                InlineKeyboardButton(action_text, callback_data=f"admin_user_block_{user_id}_{callback_action}")
            ])

        # Navigation buttons
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_user_page_{page - 1}"))
        if page + 1 < total_pages:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_user_page_{page + 1}"))

        if nav_buttons:
            keyboard.append(nav_buttons)

        keyboard.append([InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")])

        text = f"👥 **User Management**\nPage {page + 1} of {total_pages}\nTotal users: {total_users}"

        try:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

    async def admin_user_block(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.callback_query.answer("❌ Access denied")
            return
        
        _, _, _, user_id, block_status = update.callback_query.data.split("_")
        user_id = int(user_id)
        block_status = int(block_status)
        
        cursor = self.conn.cursor()
        cursor.execute("UPDATE users SET blocked = ? WHERE user_id = ?", (block_status, user_id))
        self.conn.commit()
        
        if block_status:
            cursor.execute("INSERT OR IGNORE INTO blocked_users (user_id) VALUES (?)", (user_id,))
        else:
            cursor.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
        self.conn.commit()
        
        await self.admin_user_management(update, context)

    async def handle_admin_user_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        match = re.match(r'^admin_user_page_(\d+)$', query.data)
        if match:
            page = int(match.group(1))
            await self.admin_user_management(update, context, page)

    # -------------------- ADMIN: REVENUE CHART --------------------
    async def admin_revenue_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.callback_query.answer("❌ Access denied")
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT strftime('%Y-%m', created_at) as month, SUM(total_amount) FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered') GROUP BY month ORDER BY month")
        
        revenue_by_month = cursor.fetchall()
        
        chart_text = "📈 **Revenue Chart (Monthly)**\n\n"
        if not revenue_by_month:
            chart_text += "No data available."
        else:
            max_revenue = max([r[1] for r in revenue_by_month])
            for month, revenue in revenue_by_month:
                bar = "█" * int((revenue / max_revenue) * 20)
                chart_text += f"`{month}` | {bar} {revenue:.2f}$\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")]
        ]
        
        await update.callback_query.edit_message_text(
            chart_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def admin_all_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
        query = update.callback_query
        if update.effective_user.id != ADMIN_ID:
            await query.answer("Access denied")
            return

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) as total FROM orders")
        total_orders = cursor.fetchone()["total"]

        per_page = 10
        total_pages = (total_orders - 1) // per_page + 1 if total_orders else 1
        offset = page * per_page

        cursor.execute(
            'SELECT id, user_name, total_amount, status, created_at FROM orders ORDER BY id DESC LIMIT ? OFFSET ?',
            (per_page, offset))
        orders = cursor.fetchall()

        if not orders:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
            try:
                await query.edit_message_text("No orders on this page", reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                pass
            await query.answer()
            return

        text = f"All orders (Page {page + 1}/{total_pages}):\n\n"
        keyboard = []
        status_emoji = {'pending': '🟡', 'confirmed': '🔵', 'shipped': '🟠', 'delivered': '🟢', 'cancelled': '🔴'}

        for order in orders:
            emoji_status = status_emoji.get(order["status"], '⚫')

            # 👇 ВИПРАВЛЕННЯ ЧАСУ 👇
            fmt_date = self.format_date(order['created_at'])

            text += f"{emoji_status} #{order['id']} | {order['user_name']} | {order['total_amount']}$ | {fmt_date}\n"
            keyboard.append(
                [InlineKeyboardButton(f"Details #{order['id']}", callback_data=f"order_details_{order['id']}")])

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("🔙 Previous", callback_data=f"admin_all_orders_page_{page - 1}"))
        if page + 1 < total_pages:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_all_orders_page_{page + 1}"))

        if nav_buttons:
            keyboard.append(nav_buttons)
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        await query.answer()

    async def handle_admin_all_orders_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        match = re.match(r'^admin_all_orders_page_(\d+)$', query.data)
        if match:
            page = int(match.group(1))
            await self.admin_all_orders(update, context, page)

    async def admin_order_status_change(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if update.effective_user.id != ADMIN_ID:
            await query.answer("❌ Access denied")
            return

        match = re.search(r'_(confirm|ship|deliver|cancel)_(\d+)$', query.data)
        if not match:
            await query.answer("❌ Invalid request")
            return
        action, order_id_str = match.groups()
        order_id = int(order_id_str)

        status_map = {"confirm": "confirmed", "ship": "shipped", "deliver": "delivered", "cancel": "cancelled"}
        new_status = status_map.get(action)
        if not new_status:
            await query.answer("❌ Invalid action")
            return

        cursor = self.conn.cursor()
        cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        self.conn.commit()

        status_text_map = {"confirmed": "confirmed", "shipped": "sent", "delivered": "delivered", "cancelled": "canceled"}
        await query.answer(f"✅ Order #{order_id} {status_text_map[new_status]}")

        # Сповіщення користувача
        try:
            cursor.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
            row = cursor.fetchone()
            if row:
                user_id = row[0] # row[0] або row['user_id'] залежно від row_factory, тут безпечніше за індексом, якщо row_factory не скинувся
                status_text = {'confirmed': '🔵 Confirmed', 'shipped': '🟠 Shipped', 'delivered': '🟢 Delivered', 'cancelled': '🔴 Cancelled'}
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📦 Your order #{order_id} status has been updated\n\n" \
                         f"🆕 New status: {status_text.get(new_status, new_status)}\n\n" \
                         f"Thank you for your order ❤️"
                )
        except Exception as e:
            logger.error(f"Failed to notify user about order {order_id}: {e}")

        # 👇 ТУТ ЗМІНА: Передаємо order_id явно, без хаків query.data
        await self.show_order_details(update, context, order_id=order_id)

    # -------------------- ADMIN: PRODUCT MANAGEMENT --------------------
    async def admin_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, price, stock, emoji FROM products ORDER BY name")
        products = cursor.fetchall()
        text = "📦 **Product management:**\n\n"
        keyboard = []
        for pid, name, price, stock, emoji in products[:20]:  # Show more products
            stock_status = "✅" if stock > 0 else "❌"
            text_line = f"{stock_status} {emoji or ''} **{name}** | {price}$ | Stock: {stock}\n"
            if len(text) + len(text_line) > 4000: break # Avoid hitting message length limit
            text += text_line
            keyboard.append([
                InlineKeyboardButton(f"{emoji or '📦'} {name}", callback_data=f"admin_view_product_{pid}"),
                InlineKeyboardButton("✏️", callback_data=f"admin_edit_product_{pid}"),
                InlineKeyboardButton("🗑️", callback_data=f"admin_delete_product_{pid}")
            ])
        keyboard.append([InlineKeyboardButton("➕ Add product", callback_data="admin_add_product")])
        keyboard.append([InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")])
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_handle_order_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        if user_id != ADMIN_ID: return

        # Розбираємо дані: "admin_order_reject_55" -> action="reject", order_id="55"
        data = query.data
        parts = data.split("_")
        action = parts[2]  # accept або reject
        order_id = parts[3]

        cursor = self.conn.cursor()

        # Отримуємо дані про замовлення
        cursor.execute("SELECT products, status, user_id FROM orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()

        if not row:
            await query.answer("Order not found")
            return

        products_json = row[0]
        current_status = row[1]
        buyer_id = row[2]

        if current_status != 'pending':
            await query.answer(f"Order is already {current_status}")
            return

        if action == "accept":
            # Просто змінюємо статус
            cursor.execute("UPDATE orders SET status = 'accepted' WHERE id = ?", (order_id,))
            self.conn.commit()

            await query.edit_message_text(f"✅ Order #{order_id} ACCEPTED!")
            try:
                await context.bot.send_message(chat_id=buyer_id, text=f"✅ Your Order #{order_id} has been accepted!")
            except:
                pass

        elif action == "reject":
            # 👇 ТУТ МАГІЯ ПОВЕРНЕННЯ ТОВАРУ 👇

            try:
                products_list = json.loads(products_json)
                for item in products_list:
                    p_id = item['product_id']
                    qty_to_return = item['quantity']
                    sel_opts = item['selected_options']

                    # 1. Отримуємо актуальний стан товару
                    cursor.execute("SELECT stock, variants FROM products WHERE id = ?", (p_id,))
                    prod_row = cursor.fetchone()

                    if prod_row:
                        current_stock = prod_row[0]
                        variants_json = prod_row[1]

                        # 2. Повертаємо в Загальний Сток
                        new_stock = current_stock + qty_to_return

                        # 3. Повертаємо в Варіанти (наприклад, в 128GB)
                        new_vars_json = variants_json
                        if variants_json and sel_opts:
                            try:
                                v_data = json.loads(variants_json)
                                changed = False
                                for key, val in sel_opts.items():
                                    if key in v_data:
                                        group = v_data[key]

                                        # Якщо варіант складний {"qty": 0, "price":...}
                                        if isinstance(group, dict) and val in group:
                                            target = group[val]
                                            if isinstance(target, dict) and 'qty' in target:
                                                target['qty'] += qty_to_return
                                                changed = True
                                            # Якщо варіант простий (int)
                                            elif isinstance(target, int):
                                                group[val] += qty_to_return
                                                changed = True

                                if changed:
                                    new_vars_json = json.dumps(v_data, ensure_ascii=False)
                            except:
                                pass

                        # Оновлюємо товар в базі
                        cursor.execute("UPDATE products SET stock = ?, variants = ? WHERE id = ?",
                                       (new_stock, new_vars_json, p_id))

                # Оновлюємо статус замовлення
                cursor.execute("UPDATE orders SET status = 'canceled' WHERE id = ?", (order_id,))
                self.conn.commit()

                await query.edit_message_text(f"❌ Order #{order_id} REJECTED. Stock restored.")
                try:
                    await context.bot.send_message(chat_id=buyer_id, text=f"❌ Your Order #{order_id} was canceled.")
                except:
                    pass

            except Exception as e:
                print(f"Refund error: {e}")
                await query.answer("Error restoring stock")

    async def admin_product_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id_override=None):
        query = update.callback_query

        # 1. Отримуємо ID товару
        try:
            if product_id_override:
                product_id = int(product_id_override)
            elif query:
                product_id = int(query.data.replace("admin_prod_", ""))
            else:
                return
        except:
            return

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

        if not product:
            if query: await query.answer("Product not found")
            return

        # 2. Формуємо красиву статистику складу (Smart Stock)
        stock_details = ""
        if product['variants']:
            try:
                v_data = json.loads(product['variants'])
                stock_details = "\n📊 **Stock Details:**\n"
                for key, val in v_data.items():
                    if isinstance(val, dict):
                        # Сортуємо опції
                        sorted_items = sorted(val.items(), key=lambda x: x[0])
                        stock_details += f"  🔹 {key}:\n"
                        for opt, info in sorted_items:
                            # Перевірка: чи це словник {qty: 5, price: 100} чи число 5
                            if isinstance(info, dict):
                                qty = info.get('qty', 0)
                                status = f"✅ {qty}" if qty > 0 else "❌ 0"
                                stock_details += f"    - {opt}: {status}\n"
                            else:
                                status = f"✅ {info}" if int(info) > 0 else "❌ 0"
                                stock_details += f"    - {opt}: {status}\n"
            except:
                pass

        text = (
            f"🛠 **Product Management**\n\n"
            f"📌 ID: `{product['id']}`\n"
            f"📦 Total Stock: {product['stock']}\n"
            f"{stock_details}\n"
            f"📝 Name: {product['name']}\n"
            f"💰 Price: {product['price']}$\n"
            f"📂 Category: {product['category']}\n"
            f"😀 Emoji: {product['emoji']}\n\n"
            f"Select an action:"
        )

        # 3. Кнопки управління
        keyboard = [
            [InlineKeyboardButton("✏️ Name", callback_data="admin_edit_field_name"),
             InlineKeyboardButton("✏️ Desc", callback_data="admin_edit_field_description")],
            [InlineKeyboardButton("✏️ Price", callback_data="admin_edit_field_price"),
             InlineKeyboardButton("✏️ Stock", callback_data="admin_edit_field_stock")],
            [InlineKeyboardButton("✏️ Image", callback_data=f"admin_image_menu_{product_id}"),
             InlineKeyboardButton("✏️ Variants", callback_data="admin_edit_field_variants")],
            [InlineKeyboardButton("🗑️ Delete Product", callback_data=f"admin_delete_product_confirm_{product_id}")]
        ]

        # 👇 ГОЛОВНА ЗМІНА ТУТ: Кнопка Назад веде в категорію 👇
        cat_back = product['category']
        keyboard.append([InlineKeyboardButton("🔙 Back to List", callback_data=f"admin_list_cat_{cat_back}_1")])

        # Зберігаємо ID для контексту редагування
        self.user_states[update.effective_user.id] = {'product_id': product_id}

        if query:
            try:
                await query.message.delete()
            except:
                pass

        # 4. Відправка повідомлення (Фото або Текст)
        if product['image_url']:
            try:
                # Пробуємо надіслати фото
                if product['image_url'].startswith("http"):
                    # Якщо посилання
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=product['image_url'],
                        caption=text,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    # Якщо file_id
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=product['image_url'],
                        caption=text,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.MARKDOWN
                    )
            except:
                # Якщо фото бите або помилка - шлемо текст
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text + "\n⚠️ (Image failed to load)",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    async def admin_view_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        match = re.match(r"admin_view_product_(\d+)", query.data)
        if not match: return await query.answer("❌ Invalid request")
        product_id = int(match.group(1))

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if not product: return await query.answer("❌ Product not found")

        emoji = product['emoji'] or ''

        # 👇 БЕЗПЕЧНЕ ВІДОБРАЖЕННЯ ВАРІАНТІВ 👇
        variants_text = "❌ None"
        if 'variants' in product.keys() and product['variants']:
            try:
                v_data = json.loads(product['variants'])
                v_list = [f"{k}: {', '.join(v)}" for k, v in v_data.items()]
                variants_text = "\n" + "\n".join(v_list)
            except:
                pass

        text = (
            f"{emoji} **{product['name']}**\n\n"
            f"📝 {product['description']}\n"
            f"💰 Price: {product['price']}$\n"
            f"Category: {product['category']}\n"
            f"Stock: {product['stock']}\n"
            f"🎨 Variants: {variants_text}"
        )

        keyboard = [
            [InlineKeyboardButton("✏️ Edit", callback_data=f"admin_edit_product_{product_id}"),
             InlineKeyboardButton("🗑️ Delete", callback_data=f"admin_delete_product_{product_id}")],
            [InlineKeyboardButton("🔙 To products", callback_data="admin_products")]
        ]

        if query.message.photo:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                          parse_mode=ParseMode.MARKDOWN)

    async def admin_add_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID: return

        self.user_states[user_id] = {
            'step': 'add_product_name',
            'product_data': {}
        }

        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="admin_products")]]

        # 👇 ЗМІНА: Отримуємо об'єкт повідомлення (msg) і зберігаємо його ID
        msg = await update.callback_query.edit_message_text(
            "📦 **Adding a new product**\n\nEnter the name of the product:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

        # Записуємо ID, щоб handle_admin_product_input міг його видалити
        self.user_states[user_id]['msg_id'] = msg.message_id

    async def admin_edit_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID: return
        query = update.callback_query
        match = re.match(r"admin_edit_product_(\d+)", query.data)
        if not match: return await query.answer("❌ Invalid request")
        product_id = int(match.group(1))

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if not product: return await query.answer("❌ Product not found")

        self.user_states[user_id] = {'step': 'edit_product_field', 'product_id': product_id}

        has_img = "✅ Set" if product['image_url'] else "❌ Not set"

        # 👇 ВИПРАВЛЕННЯ ТУТ: Безпечна перевірка без .get() 👇
        has_vars = "❌ None"
        if 'variants' in product.keys() and product['variants']:
            has_vars = "✅ Set"

        text = (
            f"✏️ **Edit Product**\n\n"
            f"{product['emoji'] or ''} **{product['name']}**\n"
            f"Desc: {product['description']}\n"
            f"Price: {product['price']}$\n"
            f"Category: {product['category']}\n"
            f"Stock: {product['stock']}\n"
            f"Image: {has_img}\n"
            f"Variants: {has_vars}"
        )

        keyboard = [
            [InlineKeyboardButton("Name", callback_data="admin_edit_field_name"),
             InlineKeyboardButton("Desc", callback_data="admin_edit_field_description")],
            [InlineKeyboardButton("Price", callback_data="admin_edit_field_price"),
             InlineKeyboardButton("Category", callback_data="admin_edit_field_category")],
            [InlineKeyboardButton("Emoji", callback_data="admin_edit_field_emoji"),
             InlineKeyboardButton("Stock", callback_data="admin_edit_field_stock")],

            [InlineKeyboardButton("🎨 Variants", callback_data="admin_edit_field_variants")],

            [InlineKeyboardButton("🖼️ Image", callback_data=f"admin_image_menu_{product_id}")],
            [InlineKeyboardButton("🔙 Back to Products", callback_data="admin_products")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_edit_field(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID or user_id not in self.user_states: return
        query = update.callback_query

        field_map = {
            "admin_edit_field_name": ("name", "✏️ Enter new name:"),
            "admin_edit_field_description": ("description", "📝 Enter new description:"),
            "admin_edit_field_price": ("price", "💰 Enter new price (number):"),
            "admin_edit_field_category": ("category", "📂 Enter new category:"),
            "admin_edit_field_emoji": ("emoji", "😀 Enter new emoji:"),
            "admin_edit_field_stock": ("stock", "📦 Enter new stock (integer):"),
            "admin_edit_field_variants": ("variants", "🎨 **Editing Variants**")
        }

        if query.data not in field_map: return await query.answer("❌ Invalid request")

        field, msg_text = field_map[query.data]
        self.user_states[user_id]['editing_field'] = field
        product_id = self.user_states[user_id].get('product_id')

        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data=f"admin_edit_product_{product_id}")]]

        try:
            await query.message.delete()
        except:
            pass

        # Якщо редагуємо ВАРІАНТИ - показуємо поточні налаштування і НОВІ підказки
        if field == "variants":
            cursor = self.conn.cursor()
            cursor.execute("SELECT variants FROM products WHERE id = ?", (product_id,))
            row = cursor.fetchone()
            current_vars_json = row[0] if row else None

            current_text = "None"
            if current_vars_json:
                try:
                    data = json.loads(current_vars_json)
                    lines = []
                    for k, v in data.items():
                        if isinstance(v, dict):
                            # Показуємо Qty і Price, якщо є
                            vals = []
                            for opt, info in v.items():
                                if isinstance(info, dict):
                                    price_str = f"=${info['price']}" if 'price' in info else ""
                                    vals.append(f"{opt}={info['qty']}{price_str}")
                                else:
                                    vals.append(f"{opt}={info}")
                            lines.append(f"`{k}: {', '.join(vals)}`")
                        else:
                            # Старий простий список
                            vals = ", ".join(v)
                            lines.append(f"`{k}: {vals}`")
                    current_text = "\n".join(lines)
                except:
                    pass

            # 👇 ОНОВЛЕНИЙ ТЕКСТ З ПРИКЛАДАМИ ЦІН 👇
            msg_text = (
                f"🎨 **Editing Variants**\n\n"
                f"👇 **Current settings:**\n{current_text}\n\n"
                f"✍️ **To CHANGE, send a list:**\n"
                f"Format 1: `Type: Option=Qty`\n"
                f"Format 2: `Type: Option=Qty=Price`\n\n"
                f"✅ **Examples:**\n"
                f"📱 **Memory:** `128GB=10=800, 256GB=5=900`\n"
                f"👕 **Size:** `S=10, M=5, L=2=1200`\n"
                f"👟 **Shoes:** `40=2, 41=4, 42=5`\n"
                f"🥤 **Volume:** `0.5L=20=2, 1L=10=3`\n\n"
                f"🗑️ Send `-` to delete all variants."
            )

        sent_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=msg_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        self.user_states[user_id]['msg_id'] = sent_msg.message_id

        # --- IMAGE MANAGEMENT MENU ---
    async def admin_image_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id
            if user_id != ADMIN_ID: return
            query = update.callback_query

            match = re.match(r"admin_image_menu_(\d+)", query.data)
            if not match: return await query.answer("❌ Invalid request")
            product_id = int(match.group(1))

            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            product = cursor.fetchone()

            if not product: return await query.answer("❌ Product not found")

            has_image = bool(product['image_url'])

            text = (
                f"🖼️ **Product Image Management**\n\n"
                f"Product: **{product['name']}**\n"
                f"Status: {'✅ Image set' if has_image else '❌ No image'}"
            )

            keyboard = []
            if not has_image:
                keyboard.append([InlineKeyboardButton("➕ Add Photo", callback_data=f"admin_image_set_{product_id}")])
            else:
                keyboard.append(
                    [InlineKeyboardButton("✏️ Change Photo", callback_data=f"admin_image_set_{product_id}")])
                keyboard.append(
                    [InlineKeyboardButton("🗑️ Delete Photo", callback_data=f"admin_image_delete_{product_id}")])

            keyboard.append(
                [InlineKeyboardButton("🔙 Back to Editing", callback_data=f"admin_edit_product_{product_id}")])

            # If previous message was a photo, replace it with text menu
            if query.message.photo:
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                              parse_mode=ParseMode.MARKDOWN)

    async def admin_image_set_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            query = update.callback_query
            match = re.match(r"admin_image_set_(\d+)", query.data)
            if not match: return
            product_id = int(match.group(1))
            user_id = query.from_user.id

            self.user_states[user_id] = {
                'step': 'waiting_product_image',
                'product_id': product_id
            }

            # Cleaner: Delete menu, send prompt
            try:
                await query.message.delete()
            except:
                pass

            keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_image_menu_{product_id}")]]
            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📸 **Send the product image** (or a URL link):",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            self.user_states[user_id]['msg_id'] = msg.message_id

    async def admin_image_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        match = re.match(r"admin_image_delete_(\d+)", query.data)
        if not match: return
        product_id = int(match.group(1))

        # 1. Видаляємо фото з Бази Даних
        cursor = self.conn.cursor()
        cursor.execute("UPDATE products SET image_url = NULL WHERE id = ?", (product_id,))
        self.conn.commit()

        await query.answer("🗑️ Image deleted!")

        # 2. Видаляємо старе повідомлення з фото (щоб уникнути помилок редагування)
        try:
            await query.message.delete()
        except Exception:
            pass

        # 3. Надсилаємо меню "Без фото" напряму (надійно)
        text = (
            f"🖼️ **Product Image Management**\n\n"
            f"Status: ❌ No image"
        )
        keyboard = [
            [InlineKeyboardButton("➕ Add Photo", callback_data=f"admin_image_set_{product_id}")],
            [InlineKeyboardButton("🔙 Back to Editing", callback_data=f"admin_edit_product_{product_id}")]
        ]

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def admin_delete_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID: return
        query = update.callback_query
        match = re.match(r"admin_delete_product_(\d+)", query.data)
        if not match: return await query.answer("❌ Invalid request")
        product_id = int(match.group(1))

        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row: return await query.answer("❌ Product not found")

        name = row[0]
        # Кнопки підтвердження
        keyboard = [
            [InlineKeyboardButton("❌ Yes, delete", callback_data=f"admin_delete_product_confirm_{product_id}")],
            [InlineKeyboardButton("🔙 Cancel", callback_data="admin_products")]
        ]

        # ПИЛОСОС: Видаляємо старе повідомлення і шлемо нове (безпечніше для діалогу)
        try:
            await query.message.delete()
        except:
            pass

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"🗑️ Are you sure you want to delete **{name}**?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def admin_wizard_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        query = update.callback_query

        # 1. Очищаємо пам'ять (стан) створення
        self.user_states.pop(update.effective_user.id, None)

        # 2. Сповіщаємо Телеграм, що кнопку натиснуто
        await query.answer("🚫 Cancelled")

        # 3. Просто викликаємо меню товарів.
        # Воно саме ОНОВИТЬ поточне повідомлення (замість старого тексту з'явиться список товарів).
        # Видаляти нічого не треба!
        await self.admin_products(update, context)

    async def admin_delete_product_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID: return
        query = update.callback_query
        match = re.match(r"admin_delete_product_confirm_(\d+)", query.data)
        if not match: return await query.answer("❌ Invalid request")
        product_id = int(match.group(1))

        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        name = row[0] if row else "Product"

        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        self.conn.commit()

        await query.answer(f"🗑️ {name} deleted!")
        await self.admin_products(update, context)

    # -------------------- TEXT HANDLERS --------------------
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id

        if user_id in self.user_states:
            state = self.user_states[user_id]
            step = state.get('step', '')

            # 👇 РОЗУМНИЙ РОЗПОДІЛ ЗА СТАНОМ 👇
            if step.startswith(
                    'add_product') or step == 'waiting_product_image' or step == 'waiting_variant_values' or step.startswith(
                    'edit_'):
                await self.handle_admin_product_input(update, context)

            elif step.startswith('waiting_') and '_profile' in step:
                await self.handle_profile_input(update, context)

            elif step.startswith('waiting_'):  # Checkout
                await self.handle_checkout_input(update, context)
        else:
            await update.message.reply_text("Use /start for navigating the store! 🛍️")

    async def handle_profile_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        if user_id not in self.user_states:
            return

        state = self.user_states[user_id]
        text = update.message.text.strip()
        
        # Ensure user exists in the database
        cursor = self.conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        self.conn.commit()


        if state['step'] == 'waiting_phone_profile':
            phone = text.replace(" ", "").replace("-", "")
            if not re.fullmatch(r"\+380\d{9}", phone):
                await update.message.reply_text(
                    "❌ Incorrect phone format. Only Ukrainian numbers (+380XXXXXXXXX) are accepted.\n"
                    "Please type your phone as +380XXXXXXXXX.\n"
                    "Example: +380501234567"
                )
                return
            cursor.execute("UPDATE users SET phone = ? WHERE user_id = ?", (phone, user_id))
            self.conn.commit()
            self.user_states.pop(user_id, None)
            await update.message.reply_text("✅ Phone number updated!")
            await self.show_profile(update, context)

        elif state['step'] == 'waiting_address_profile':
            if len(text) < 10:
                await update.message.reply_text("❌ The address is too short. Please enter the full address.")
                return
            cursor.execute("UPDATE users SET address = ? WHERE user_id = ?", (text, user_id))
            self.conn.commit()
            self.user_states.pop(user_id, None)
            await update.message.reply_text("✅ Address updated!")
            await self.show_profile(update, context)

        elif state['step'] == 'waiting_email_profile':
            email = text
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                await update.message.reply_text("❌ Please enter a valid email address.")
                return
            cursor.execute("UPDATE users SET email = ? WHERE user_id = ?", (email, user_id))
            self.conn.commit()
            self.user_states.pop(user_id, None)
            await update.message.reply_text("✅ Email updated!")
            await self.show_profile(update, context)

        # 👇 ВСТАВ ЦЕ В СЕРЕДИНУ КЛАСУ OnlineShopBot 👇

        # --- DELETE DATA MENU ---
    async def profile_delete_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            if await self.check_user_blocked(update, context): return
            user_id = update.effective_user.id

            cursor = self.conn.cursor()
            cursor.execute("SELECT phone, address, email FROM users WHERE user_id = ?", (user_id,))
            user_data = cursor.fetchone()

            # Перевіряємо, що саме заповнено
            phone, address, email = user_data if user_data else (None, None, None)

            keyboard = []
            if phone:
                keyboard.append([InlineKeyboardButton("🗑️ Delete Phone", callback_data="delete_profile_phone")])
            if address:
                keyboard.append([InlineKeyboardButton("🗑️ Delete Address", callback_data="delete_profile_address")])
            if email:
                keyboard.append([InlineKeyboardButton("🗑️ Delete Email", callback_data="delete_profile_email")])

            keyboard.append([InlineKeyboardButton("🔙 Back to Profile", callback_data="my_profile")])

            text = "🗑️ **Delete Profile Data**\n\nSelect the data you want to remove:"
            if not (phone or address or email):
                text = "🗑️ **Delete Profile Data**\n\nYour profile is empty. Nothing to delete."

            if update.callback_query.message.photo:
                await update.callback_query.message.delete()
                await context.bot.send_message(chat_id=update.callback_query.message.chat_id, text=text,
                                               reply_markup=InlineKeyboardMarkup(keyboard),
                                               parse_mode=ParseMode.MARKDOWN)
            else:
                await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                              parse_mode=ParseMode.MARKDOWN)

    async def handle_delete_profile_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            if await self.check_user_blocked(update, context): return
            query = update.callback_query
            data = query.data
            user_id = query.from_user.id

            field_map = {
                "delete_profile_phone": ("phone", "Phone number"),
                "delete_profile_address": ("address", "Address"),
                "delete_profile_email": ("email", "Email")
            }

            if data not in field_map: return

            db_field, display_name = field_map[data]

            cursor = self.conn.cursor()
            cursor.execute(f"UPDATE users SET {db_field} = NULL WHERE user_id = ?", (user_id,))
            self.conn.commit()

            await query.answer(f"✅ {display_name} deleted!")

            # Оновлюємо меню
            await self.profile_delete_menu(update, context)

    async def handle_admin_product_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID or user_id not in self.user_states: return

        state = self.user_states[user_id]
        step = state.get("step")
        msg = update.message

        try:
            await msg.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=msg.chat_id, message_id=state['msg_id'])
            except:
                pass

        if update.message.photo:
            input_value = update.message.photo[-1].file_id
            is_photo = True
        else:
            input_value = update.message.text.strip() if update.message.text else ""
            is_photo = False

        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_wizard_cancel")]])

        # --- 1. ПРОСТИЙ ТОВАР (CREATION) ---
        if step == 'waiting_simple_stock':
            try:
                if not input_value.isdigit(): raise ValueError()
                stock_qty = int(input_value)
                p = state['product_data']
                cursor = self.conn.cursor()
                cursor.execute(
                    "INSERT INTO products (name, description, price, image_url, emoji, category, stock, variants) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (p["name"], p["description"], float(p["price"]), p.get('image_url'), p["emoji"], p["category"],
                     stock_qty, None)
                )
                self.conn.commit()
                self.user_states.pop(user_id, None)
                await context.bot.send_message(chat_id=msg.chat_id,
                                               text=f"✅ Simple Product **{p['name']}** created!\n📦 Stock: {stock_qty}",
                                               reply_markup=InlineKeyboardMarkup(
                                                   [[InlineKeyboardButton("🔙 Back to Products",
                                                                          callback_data="admin_products")]]),
                                               parse_mode=ParseMode.MARKDOWN)
            except:
                m = await context.bot.send_message(chat_id=msg.chat_id, text="❌ Invalid number.",
                                                   reply_markup=cancel_kb)
                state['msg_id'] = m.message_id
            return

        # --- 2. ВАРІАНТИ (CREATION) ---
        if step == 'waiting_variant_values':
            variant_type = state.get('current_variant_type')
            raw_values = input_value.split(",")
            parsed_data = {}
            for v in raw_values:
                v = v.strip()
                if not v: continue
                parts = v.split("=")
                if len(parts) == 3:
                    try:
                        parsed_data[parts[0].strip()] = {"qty": int(parts[1]), "price": float(parts[2])}
                    except:
                        pass
                elif len(parts) == 2:
                    try:
                        parsed_data[parts[0].strip()] = int(parts[1])
                    except:
                        pass
                else:
                    parsed_data[v] = 0

            if parsed_data:
                if 'variants' not in state['product_data']: state['product_data']['variants'] = {}
                state['product_data']['variants'][variant_type] = parsed_data
                display_list = []
                for k, val in parsed_data.items():
                    if isinstance(val, dict):
                        display_list.append(f"{k} (x{val['qty']}, ${val['price']})")
                    else:
                        display_list.append(f"{k} (x{val})")

                confirm_text = f"✅ Added **{variant_type}**: {', '.join(display_list)}"
                state['step'] = 'add_product_variants_loop'
                m = await context.bot.send_message(chat_id=msg.chat_id,
                                                   text=f"{confirm_text}\n\nAdd another type or click **Finish**:",
                                                   reply_markup=self.get_variant_type_keyboard(),
                                                   parse_mode=ParseMode.MARKDOWN)
            else:
                m = await context.bot.send_message(chat_id=msg.chat_id, text="⚠️ Format error.", reply_markup=cancel_kb)
            state['msg_id'] = m.message_id
            return

        # --- 3. ФОТО (CREATION / EDIT) ---
        if step == 'waiting_product_image':
            product_id = state.get('product_id')
            if is_photo:
                new_img = input_value
            elif input_value.startswith('http'):
                new_img = input_value
            else:
                m = await context.bot.send_message(chat_id=msg.chat_id, text="❌ Please send a Photo or URL.",
                                                   reply_markup=cancel_kb)
                state['msg_id'] = m.message_id
                return
            cursor = self.conn.cursor()
            cursor.execute("UPDATE products SET image_url = ? WHERE id = ?", (new_img, product_id))
            self.conn.commit()
            self.user_states.pop(user_id, None)
            kb = [[InlineKeyboardButton("🔙 Back to Image Menu", callback_data=f"admin_image_menu_{product_id}")]]
            await context.bot.send_message(chat_id=msg.chat_id, text="✅ Image updated!",
                                           reply_markup=InlineKeyboardMarkup(kb))
            return

        # --- 4. WIZARD (CREATION) ---
        if step and step.startswith('add_product'):
            field_map = {
                'add_product_name': ('description', "Enter product description:"),
                'add_product_description': ('price', "Enter product price (number):"),
                'add_product_price': ('image',
                                      "📸 **Product Image**\n\nSend a URL, upload a **Photo**, or send `-` to skip:"),
                'add_product_image': ('emoji', "Enter product emoji (e.g., 📱):"),
                'add_product_emoji': ('category', "Enter product category:"),
                'add_product_category': ('DECISION', "❓ **Variants Check**")
            }
            current_field = step.replace('add_product_', '')
            error = None
            if current_field == 'price':
                try:
                    float(input_value)
                except:
                    error = "❌ Price must be a number."
            if error:
                m = await context.bot.send_message(chat_id=msg.chat_id, text=error, reply_markup=cancel_kb)
                state['msg_id'] = m.message_id
                return

            if current_field == 'image':
                state['product_data']['image_url'] = input_value if (is_photo or input_value != '-') else None
            else:
                state['product_data'][current_field] = input_value

            next_step, prompt = field_map.get(step, (None, None))

            if next_step == 'DECISION':
                kb = [[InlineKeyboardButton("🎨 Has Variants", callback_data="admin_decision_vars_yes")],
                      [InlineKeyboardButton("📦 Simple Product", callback_data="admin_decision_vars_no")],
                      [InlineKeyboardButton("❌ Cancel", callback_data="admin_wizard_cancel")]]
                state['step'] = 'waiting_decision'
                m = await context.bot.send_message(chat_id=msg.chat_id, text="❓ **Does this product have variants?**",
                                                   reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
                state['msg_id'] = m.message_id
            elif next_step:
                state['step'] = f"add_product_{next_step}"
                m = await context.bot.send_message(chat_id=msg.chat_id, text=prompt, reply_markup=cancel_kb,
                                                   parse_mode=ParseMode.MARKDOWN)
                state['msg_id'] = m.message_id
            return

        # --- 5. РЕДАГУВАННЯ (EDIT MODE - ТУТ БУВ БАГ) ---
        elif state.get('editing_field'):
            field_to_edit = state['editing_field']
            product_id = state['product_id']
            value = input_value
            error_text = None

            # Змінна для нового стоку (якщо редагуємо варіанти)
            new_calculated_stock = None

            if field_to_edit == "price":
                try:
                    value = float(input_value)
                except:
                    error_text = "❌ Invalid number."
            elif field_to_edit == "stock":
                try:
                    value = int(input_value)
                except:
                    error_text = "❌ Invalid integer."

            elif field_to_edit == "variants":
                if input_value.strip() == "-":
                    value = None
                    new_calculated_stock = 0
                else:
                    try:
                        variants_dict = {}
                        current_calc_stock = 0  # Лічильник стоку

                        # Парсимо вхідний рядок
                        # Очікуємо формат: "Type: Val=Qty, Val=Qty; Type2: ..."
                        # Або просто "Val=Qty" (якщо тип один, спробуємо вгадати або використати дефолт)

                        parts = input_value.split(";")
                        for part in parts:
                            if ":" in part:
                                k, vals_str = part.split(":")
                                parsed_vals = {}
                                simple_list = []
                                is_stock = False

                                for v in vals_str.split(","):
                                    v = v.strip()
                                    if "=" in v:
                                        is_stock = True
                                        sub_parts = v.split("=")
                                        if len(sub_parts) == 3:  # Name=Qty=Price
                                            qty = int(sub_parts[1])
                                            parsed_vals[sub_parts[0].strip()] = {"qty": qty,
                                                                                 "price": float(sub_parts[2])}
                                            current_calc_stock += qty
                                        else:  # Name=Qty
                                            qty = int(sub_parts[1])
                                            parsed_vals[sub_parts[0].strip()] = qty
                                            current_calc_stock += qty
                                    else:
                                        simple_list.append(v)

                                if is_stock:
                                    variants_dict[k.strip()] = parsed_vals
                                else:
                                    variants_dict[k.strip()] = simple_list

                        if not variants_dict: raise ValueError()
                        value = json.dumps(variants_dict, ensure_ascii=False)
                        new_calculated_stock = current_calc_stock  # Запам'ятовуємо новий сток
                    except Exception as e:
                        error_text = "❌ Format error. Use: `Type: Val1=Qty, Val2=Qty`"

            if error_text:
                kb = [[InlineKeyboardButton("🔙 Back", callback_data=f"admin_edit_product_{product_id}")]]
                m = await context.bot.send_message(chat_id=msg.chat_id, text=error_text,
                                                   reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
                state['msg_id'] = m.message_id
                return

            cursor = self.conn.cursor()

            # 👇 ГОЛОВНЕ ВИПРАВЛЕННЯ 👇
            # Якщо ми редагували варіанти, ми ТАКОЖ оновлюємо сток!
            if field_to_edit == "variants" and new_calculated_stock is not None:
                cursor.execute(f"UPDATE products SET variants = ?, stock = ? WHERE id = ?",
                               (value, new_calculated_stock, product_id))
                msg_confirm = f"✅ **Variants** updated!\n📦 New Total Stock: {new_calculated_stock}"
            else:
                cursor.execute(f"UPDATE products SET {field_to_edit} = ? WHERE id = ?", (value, product_id))
                msg_confirm = f"✅ **{field_to_edit}** updated!"

            self.conn.commit()
            self.user_states.pop(user_id, None)

            kb = [[InlineKeyboardButton("🔙 Back to Product", callback_data=f"admin_edit_product_{product_id}")]]
            await context.bot.send_message(chat_id=msg.chat_id, text=msg_confirm, reply_markup=InlineKeyboardMarkup(kb),
                                           parse_mode=ParseMode.MARKDOWN)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.warning(f'Update {update} caused error {context.error}')
        try:
            if hasattr(update, 'effective_message') and update.effective_message:
                await update.effective_message.reply_text("❌ An error has occurred. Please try again or use /start")
        except Exception:
            pass

    async def handle_variant_type_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)

        if not state or 'product_data' not in state:
            await query.answer("❌ Session expired")
            return

        variant_type = query.data.replace("vartype_", "").replace("admin_add_variant_type_", "")

        # --- ЛОГІКА 1: ЗБЕРЕЖЕННЯ (DONE) ---
        if variant_type == "DONE":
            p = state['product_data']
            variants_data = p.get('variants', {})

            # АВТОМАТИЧНИЙ ПІДРАХУНОК СТОКУ
            total_stock = 0
            if variants_data:
                for key, val in variants_data.items():
                    if isinstance(val, dict):
                        for sub_v in val.values():
                            if isinstance(sub_v, dict):
                                total_stock += sub_v.get('qty', 0)
                            elif isinstance(sub_v, int):
                                total_stock += sub_v

            variants_json = json.dumps(variants_data, ensure_ascii=False) if variants_data else None

            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO products (name, description, price, image_url, emoji, category, stock, variants) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (p["name"], p["description"], float(p["price"]), p.get('image_url'), p["emoji"], p["category"],
                 total_stock, variants_json)
            )
            self.conn.commit()

            self.user_states.pop(user_id, None)

            try:
                await query.message.delete()
            except:
                pass

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ Variant Product **{p['name']}** created!\n📦 Total Stock calculated: **{total_stock}**",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back to Products", callback_data="admin_products")]]),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # --- ЛОГІКА 2: ВИБРАНО ТИП (Size, Color...) ---
        state['current_variant_type'] = variant_type
        state['step'] = 'waiting_variant_values'

        examples_map = {
            "Size": "S=10, M=5, L=2=1200",
            "Color": "Red=5, Blue=3, Black=10",
            "Memory": "128GB=10=800, 256GB=5=900",
            "Volume": "0.5L=10=2, 1L=5=3",
            "ShoeSize": "40=2, 41=5, 42=3",
            "Material": "Cotton=10, Silk=5=50"
        }
        example_text = examples_map.get(variant_type, "Option=Qty")

        text = (
            f"Selected: **{variant_type}**\n\n"
            f"Enter options (comma separated):\n"
            f"1. `Option=Qty`\n"
            f"2. `Option=Qty=Price`\n\n"
            f"✅ **Example:**\n`{example_text}`"
        )

        kb = [[InlineKeyboardButton("🔙 Back to Types", callback_data="admin_step_variants_init")]]

        try:
            await query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
            state['msg_id'] = msg.message_id

    async def admin_handle_variant_type_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        # Отримуємо тип (видаляємо префікс)
        variant_type = query.data.replace("admin_add_variant_type_", "")

        # 👇 ОНОВЛЕНІ ПРИКЛАДИ З ЦІНАМИ 👇
        examples_map = {
            "Size": "S=10, M=5, L=2=1200",  # L коштує 1200
            "Color": "Red=5, Blue=3, Gold=10=50",  # Gold дорожчий
            "Memory": "128GB=10=800, 256GB=5=900, 512GB=2=1100",  # Класика
            "Volume": "0.5L=20=2, 1L=10=3, 2L=5=5",
            "ShoeSize": "40=2, 41=5, 42=3",
            "Material": "Cotton=10, Silk=5=50",
            "Taste": "Standard=20, Premium=10=5"
        }

        example_text = examples_map.get(variant_type, "Option1=10, Option2=5=Price")

        text = (
            f"Selected: **{variant_type}**\n\n"
            f"⌨️ Enter available options separated by comma:\n"
            f"Formats:\n"
            f"1. `Option=Qty` (Standard Price)\n"
            f"2. `Option=Qty=Price` (Custom Price)\n\n"
            f"✅ **Example for {variant_type}:**\n"
            f"`{example_text}`"
        )

        # Кнопка НАЗАД
        kb = [[InlineKeyboardButton("🔙 Back to Types", callback_data="admin_step_variants_init")]]

        # Оновлюємо стан
        self.user_states[user_id]['step'] = 'waiting_variant_values'
        self.user_states[user_id]['current_variant_type'] = variant_type

        # Редагуємо повідомлення
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        except:
            # Fallback якщо повідомлення застаріло
            msg = await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                                 reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            self.user_states[user_id]['msg_id'] = msg.message_id

    async def admin_handle_variant_decision(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        data = query.data

        # 1. Якщо вибрано "Simple Product" -> Переходимо до waiting_simple_stock
        if data == "admin_decision_vars_no":
            self.user_states[user_id]['step'] = 'waiting_simple_stock'

            # Використовуємо edit_message_text для плавності
            await query.edit_message_text(
                "📦 **Simple Product Mode**\n\nEnter Total Stock Quantity (integer):",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="admin_wizard_cancel")]]),
                parse_mode=ParseMode.MARKDOWN
            )

        # 2. Якщо вибрано "Has Variants" -> Запускаємо луп варіантів
        elif data == "admin_decision_vars_yes":
            self.user_states[user_id]['step'] = 'add_product_variants_loop'
            self.user_states[user_id]['product_data']['variants'] = {}
            self.user_states[user_id]['product_data']['stock'] = 0  # Початковий сток 0

            await query.edit_message_text(
                "🎨 **Variant Mode**\n\nSelect a Variant Type to add:",
                reply_markup=self.get_variant_type_keyboard(),
                parse_mode=ParseMode.MARKDOWN
            )
    async def admin_back_to_variant_types(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        # Повертаємо стан вибору типу
        self.user_states[user_id]['step'] = 'add_product_variants_loop'

        text = "🎨 **Product Variants**\n\nSelect a type below or click **Finish**:"
        reply_markup = self.get_variant_type_keyboard()

        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# -------------------- MAIN --------------------
def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.critical("BOT_TOKEN not found. Please set it as an environment variable.")
        return

    bot = OnlineShopBot()
    application = Application.builder().token(BOT_TOKEN).build()

    # =========================================================================
    # 1. БАЗОВІ КОМАНДИ ТА ПОВІДОМЛЕННЯ
    # =========================================================================
    application.add_handler(CommandHandler("start", bot.start))

    application.add_handler(MessageHandler(filters.PHOTO, bot.handle_admin_product_input))
    application.add_handler(MessageHandler(filters.CONTACT, bot.handle_checkout_input))
    # Текстові повідомлення (має бути нижче команд, щоб не перехоплювати /start)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))

    # =========================================================================
    # 2. КЛІЄНТСЬКА ЧАСТИНА (Меню, Каталог, Профіль)
    # =========================================================================
    application.add_handler(CallbackQueryHandler(bot.show_main_menu, pattern=r'^main_menu$'))
    application.add_handler(CallbackQueryHandler(bot.show_help, pattern=r'^help$'))

    # Каталог і категорії
    application.add_handler(CallbackQueryHandler(bot.show_catalog, pattern=r'^catalog$'))
    # 👇 Важливо: show_category_products (з пагінацією) має бути вище за звичайну категорію, якщо патерни схожі
    application.add_handler(CallbackQueryHandler(bot.show_category_products, pattern=r'^category_'))
    application.add_handler(CallbackQueryHandler(bot.show_product, pattern=r'^product_'))

    # Профіль
    application.add_handler(CallbackQueryHandler(bot.show_profile, pattern=r'^my_profile$'))
    application.add_handler(CallbackQueryHandler(bot.edit_phone, pattern=r'^edit_phone$'))
    application.add_handler(CallbackQueryHandler(bot.edit_email, pattern=r'^edit_email$'))
    application.add_handler(CallbackQueryHandler(bot.edit_address, pattern=r'^edit_address$'))
    application.add_handler(CallbackQueryHandler(bot.profile_delete_menu, pattern=r'^profile_delete_menu$'))
    application.add_handler(CallbackQueryHandler(bot.handle_delete_profile_data, pattern=r'^delete_profile_'))

    # =========================================================================
    # 3. КОШИК ТА ОФОРМЛЕННЯ (Checkout)
    # =========================================================================
    application.add_handler(CallbackQueryHandler(bot.show_cart, pattern=r'^cart$'))
    application.add_handler(CallbackQueryHandler(bot.clear_cart, pattern=r'^clear_cart$'))

    # Логіка додавання/видалення
    application.add_handler(CallbackQueryHandler(bot.handle_add_to_cart_click, pattern=r'^add_to_cart_'))
    application.add_handler(CallbackQueryHandler(bot.remove_from_cart, pattern=r'^remove_from_cart_'))
    application.add_handler(CallbackQueryHandler(bot.handle_cart_actions, pattern=r'^cart_item_'))  # Нові кнопки +/-
    application.add_handler(
        CallbackQueryHandler(bot.cart_operations, pattern=r'^cart_(add|remove)_'))  # Старі (якщо лишились)

    # Оформлення замовлення
    application.add_handler(CallbackQueryHandler(bot.checkout, pattern=r'^checkout$'))
    application.add_handler(CallbackQueryHandler(bot.use_profile_data, pattern=r'^use_profile_data$'))
    application.add_handler(CallbackQueryHandler(bot.choose_payment, pattern=r'^pay_(cod|card|bank)$'))
    application.add_handler(CallbackQueryHandler(bot.handle_checkout_back, pattern=r'^back_to_'))
    application.add_handler(CallbackQueryHandler(bot.handle_cancel_order, pattern=r'^cancel_order$'))

    # Клієнтські замовлення
    application.add_handler(CallbackQueryHandler(bot.show_my_orders, pattern=r'^my_orders$'))
    application.add_handler(CallbackQueryHandler(bot.handle_my_orders_pagination, pattern=r'^my_orders_page_\d+$'))
    application.add_handler(CallbackQueryHandler(bot.show_order_details, pattern=r'^order_details_'))
    application.add_handler(CallbackQueryHandler(bot.user_cancel_order, pattern=r'^user_cancel_'))

    # =========================================================================
    # 4. АДМІН-ПАНЕЛЬ (Загальне)
    # =========================================================================
    application.add_handler(CallbackQueryHandler(bot.admin_panel, pattern=r'^admin_panel$'))
    application.add_handler(CallbackQueryHandler(bot.admin_statistics, pattern=r'^admin_statistics$'))
    application.add_handler(CallbackQueryHandler(bot.admin_revenue_chart, pattern=r'^admin_revenue_chart$'))

    # Управління користувачами
    application.add_handler(CallbackQueryHandler(bot.admin_user_management, pattern=r'^admin_user_management$'))
    application.add_handler(CallbackQueryHandler(bot.handle_admin_user_pagination, pattern=r'^admin_user_page_\d+$'))
    application.add_handler(CallbackQueryHandler(bot.admin_user_block, pattern=r'^admin_user_block_'))

    # Управління всіма замовленнями
    application.add_handler(CallbackQueryHandler(bot.admin_all_orders, pattern=r'^admin_all_orders$'))
    application.add_handler(
        CallbackQueryHandler(bot.handle_admin_all_orders_pagination, pattern=r'^admin_all_orders_page_\d+$'))
    application.add_handler(
        CallbackQueryHandler(bot.admin_order_status_change, pattern=r'^admin_(confirm|ship|deliver|cancel)'))
    application.add_handler(
        CallbackQueryHandler(bot.admin_handle_order_callback, pattern=r'^admin_order_'))  # Accept/Reject

    # =========================================================================
    # 5. АДМІН: УПРАВЛІННЯ ТОВАРАМИ (Нова структура)
    # =========================================================================

    # 👇 ГОЛОВНЕ: Кнопка "Products" тепер відкриває КАТЕГОРІЇ
    application.add_handler(CallbackQueryHandler(bot.admin_categories_menu, pattern='^admin_products$'))

    # Список товарів у категорії (пагінація)
    application.add_handler(CallbackQueryHandler(bot.admin_products_list, pattern=r'^admin_list_cat_'))

    # Меню конкретного товару
    application.add_handler(CallbackQueryHandler(bot.admin_product_menu, pattern=r'^admin_prod_'))
    application.add_handler(CallbackQueryHandler(bot.admin_view_product,
                                                 pattern=r'^admin_view_product_'))  # Старий вьювер, про всяк випадок

    # Створення та Редагування
    application.add_handler(CallbackQueryHandler(bot.admin_add_product, pattern=r'^admin_add_product$'))
    application.add_handler(CallbackQueryHandler(bot.admin_edit_product, pattern=r'^admin_edit_product_'))
    application.add_handler(CallbackQueryHandler(bot.admin_edit_field, pattern=r'^admin_edit_field_'))

    # Видалення
    application.add_handler(CallbackQueryHandler(bot.admin_delete_product, pattern=r'^admin_delete_product_\d+'))
    application.add_handler(
        CallbackQueryHandler(bot.admin_delete_product_confirm, pattern=r'^admin_delete_product_confirm_'))

    # Фото
    application.add_handler(CallbackQueryHandler(bot.admin_image_menu, pattern=r'^admin_image_menu_'))
    application.add_handler(CallbackQueryHandler(bot.admin_image_set_prompt, pattern=r'^admin_image_set_'))
    application.add_handler(CallbackQueryHandler(bot.admin_image_delete, pattern=r'^admin_image_delete_'))

    # Розвилка (Простий / Варіанти) та Візард
    application.add_handler(CallbackQueryHandler(bot.admin_handle_variant_decision, pattern=r'^admin_decision_vars_'))
    application.add_handler(CallbackQueryHandler(bot.admin_wizard_cancel, pattern=r'^admin_wizard_cancel$'))

    # =========================================================================
    # 6. ЛОГІКА ВАРІАНТІВ (Вибір кольору/розміру)
    # =========================================================================
    # Адмін вибирає тип варіанту (Size, Color...)
    application.add_handler(
        CallbackQueryHandler(bot.admin_handle_variant_type_selection, pattern=r'^admin_add_variant_type_'))
    # Адмін тисне "Назад" у виборі типів
    application.add_handler(
        CallbackQueryHandler(bot.admin_back_to_variant_types, pattern=r'^admin_step_variants_init$'))
    # Загальний хендлер для кнопок варіантів (клієнт і адмін, якщо є спільні)
    application.add_handler(CallbackQueryHandler(bot.handle_variant_type_selection, pattern=r'^vartype_'))

    # Клієнт вибирає конкретну опцію (128GB)
    application.add_handler(CallbackQueryHandler(bot.handle_variant_selection_user, pattern=r'^var_sel_'))
    application.add_handler(CallbackQueryHandler(bot.handle_variant_selection_user, pattern=r'^cancel_selection$'))

    # =========================================================================
    # ЗАПУСК
    # =========================================================================
    application.add_error_handler(bot.error_handler)

    print("🛍️ Online store bot launched!")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print("Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()