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


class OnlineShopBot:
    def __init__(self):
        self.init_database()
        self.user_states = {}

    # -------------------- DATABASE --------------------
    def init_database(self):
        self.conn = sqlite3.connect('shop.db', check_same_thread=False)
        cursor = self.conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                image_url TEXT,
                category TEXT,
                stock INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute("PRAGMA table_info(products)")
        columns = [row[1] for row in cursor.fetchall()]
        if "emoji" not in columns:
            try:
                cursor.execute("ALTER TABLE products ADD COLUMN emoji TEXT")
            except Exception as e:
                logger.warning(f"Could not add emoji column: {e}")

        if "image_url" not in columns:
            try:
                cursor.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
                logger.info("Added image_url column to products table")
            except Exception as e:
                logger.warning(f"Could not add image_url column: {e}")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                products TEXT NOT NULL,
                total_amount REAL NOT NULL,
                phone TEXT,
                address TEXT,
                payment_method TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # --- DB migration: add payment_method column if it does not exist ---
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT")
            self.conn.commit()
        except Exception:
            pass

        # --- DB migration: add email column if it does not exist ---
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN email TEXT")
            self.conn.commit()
        except Exception:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cart (
                user_id INTEGER,
                product_id INTEGER,
                quantity INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, product_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                phone TEXT,
                address TEXT,
                email TEXT,
                blocked INTEGER DEFAULT 0
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocked_users (
                user_id INTEGER PRIMARY KEY,
                blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # --- DB migration: add email column if it does not exist ---
        try:
            cursor.execute("PRAGMA table_info(users)")
            columns = [row[1] for row in cursor.fetchall()]
            if "email" not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")
                self.conn.commit()
        except Exception:
            pass

        # --- DB migration: add blocked column if it does not exist ---
        try:
            cursor.execute("PRAGMA table_info(users)")
            columns = [row[1] for row in cursor.fetchall()]
            if "blocked" not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
                self.conn.commit()
        except Exception:
            pass

        cursor.execute("SELECT COUNT(*) FROM products")
        if cursor.fetchone()[0] == 0:
            demo_products = [
                ("iPhone 15 Pro", "New iPhone 15 Pro 256GB Excellent condition, warranty.", 1000.0, None, "📱", "Electronics", 5),
                ("MacBook Air M2", "MacBook Air 13 with M2 chip, 8GB RAM, 256GB SSD", 1500.0, None, "💻", "Electronics", 3),
                ("Delonghi coffee maker", "Automatic coffee maker for true coffee lovers", 450.0, None, "☕", "Home appliances", 2),
                ("Fitness bracelet", "Smart bracelet with health monitoring", 55.0, None, "⌚", "Sports", 15),
                ("Bluetooth column", "Portable speaker with excellent sound quality", 75.0, None, "🔊", "Accessories", 8)
            ]
            cursor.executemany('''
                INSERT INTO products (name, description, price, image_url, emoji, category, stock)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', demo_products)

        self.conn.commit()

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
            """Повертає товари на склад, якщо замовлення скасовано."""
            cursor = self.conn.cursor()
            cursor.execute("SELECT products FROM orders WHERE id = ?", (order_id,))
            result = cursor.fetchone()

            if result and result[0]:
                products = json.loads(result[0])
                for item in products:
                    product_id = item.get('product_id')
                    quantity = item.get('quantity')
                    if product_id and quantity:
                        cursor.execute(
                            "UPDATE products SET stock = stock + ? WHERE id = ?",
                            (quantity, product_id)
                        )
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

    async def show_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            if await self.check_user_blocked(update, context): return

            query = update.callback_query
            product_id = int(query.data.replace("product_", ""))

            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            product = cursor.fetchone()

            if not product: return await query.answer("❌ Product not found")

            user_id = update.effective_user.id
            cursor.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
            cart_item = cursor.fetchone()
            cart_qty = cart_item[0] if cart_item else 0

            emoji = product['emoji'] if product['emoji'] else ''
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

            # Додаємо цей спільний ряд (вони стануть поруч)
            if control_row:
                keyboard.append(control_row)

            # --- РЯДОК 2: КОШИК ---
            keyboard.append([InlineKeyboardButton(f"🛒 Go to Cart ({cart_qty})", callback_data="cart")])

            # --- РЯДОК 3: НАЗАД ---
            keyboard.append([InlineKeyboardButton(f"🔙 Back to {product['category']}",
                                                  callback_data=f"category_{product['category']}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            # Логіка відправки
            if is_file_id:
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=img_source,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                if query.message.photo:
                    try:
                        await query.message.delete()
                    except:
                        pass
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

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
        if await self.check_user_blocked(update, context):
            return

        query = update.callback_query
        user_id = update.effective_user.id

        cursor = self.conn.cursor()
        cursor.execute('''
                       SELECT p.id, p.name, p.price, p.emoji, c.quantity
                       FROM cart c
                                JOIN products p ON c.product_id = p.id
                       WHERE c.user_id = ?
                       ''', (user_id,))
        cart_items = cursor.fetchall()

        # --- ЯКЩО КОШИК ПОРОЖНІЙ ---
        if not cart_items:
            keyboard = [
                [InlineKeyboardButton("🛍️ Product catalog", callback_data="catalog")],
                [InlineKeyboardButton("📋 My orders", callback_data="my_orders")],
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
            ]
            if int(user_id) == int(ADMIN_ID):
                keyboard.append([InlineKeyboardButton("👑 Admin panel", callback_data="admin_panel")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            text = "🛒 **Your cart is empty**\n\nAdd items from the catalog!"

            # 👇 ВИПРАВЛЕННЯ 1: Видаляємо фото, якщо воно є
            if query.message.photo:
                await query.message.delete()
                await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=reply_markup,
                                               parse_mode=ParseMode.MARKDOWN)
            else:
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
            return

        # --- ЯКЩО В КОШИКУ ЩОСЬ Є ---
        text = "🛒 **Your cart:**\n\n"
        total = 0
        keyboard = []
        for item in cart_items:
            product_id, name, price, emoji, quantity = item
            emoji = emoji if emoji else ""
            item_total = price * quantity
            total += item_total
            text += f"{emoji} **{name}**\n   💰 {price}$ × {quantity} = {item_total}$\n\n"
            keyboard.append([
                InlineKeyboardButton("➖", callback_data=f"cart_remove_{product_id}"),
                InlineKeyboardButton(f"{emoji} {name} ({quantity})", callback_data=f"product_{product_id}"),
                InlineKeyboardButton("➕", callback_data=f"cart_add_{product_id}")
            ])

        text += f"💳 **Total amount: {total}$**"

        keyboard.extend([
            [InlineKeyboardButton("🗑️ Clear cart", callback_data="clear_cart")],
            [InlineKeyboardButton("📋 Checkout", callback_data="checkout")],
            [InlineKeyboardButton("🔙 To Catalog", callback_data="catalog")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ])
        if int(user_id) == int(ADMIN_ID):
            keyboard.append([InlineKeyboardButton("👑 Admin panel", callback_data="admin_panel")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # 👇 ВИПРАВЛЕННЯ 2: Те ж саме для повного кошика
        if query.message.photo:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def add_to_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        product_id = int(query.data.replace("add_to_cart_", ""))
        user_id = query.from_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT stock FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row:
            await query.answer("❌ Product not found")
            return
        stock = row[0]
        cursor.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        cart_row = cursor.fetchone()
        current_qty = cart_row[0] if cart_row else 0
        if current_qty >= stock:
            await query.answer(f"❌ Available quantity: {stock} items")
            return
        if cart_row:
            cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        else:
            cursor.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, 1)", (user_id, product_id))
        self.conn.commit()
        await self.update_product_view(query, product_id, context)
        await query.answer("✅ Item added to cart")

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

    async def create_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, send_message=True):
            if await self.check_user_blocked(update, context):
                return
            user_id = update.effective_user.id
            user_name = update.effective_user.full_name
            target_message = update.message or (update.callback_query.message if update.callback_query else None)

            if user_id not in self.user_states:
                return None
            state = self.user_states[user_id]

            # Список для товарів, що закінчилися
            out_of_stock_alert = []

            try:
                with self.conn:
                    cursor = self.conn.cursor()

                    # 1. Отримуємо товари
                    cursor.execute(
                        'SELECT p.id, p.name, p.price, c.quantity, p.emoji, p.stock '
                        'FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
                        (user_id,)
                    )
                    cart_items = cursor.fetchall()

                    if not cart_items:
                        if target_message:
                            await target_message.reply_text("❌ The cart is empty!")
                        self.user_states.pop(user_id, None)
                        return None

                    products_list = []
                    total_amount = 0

                    # 2. Перевіряємо та рахуємо
                    for product_id, name, price, quantity, emoji, stock in cart_items:
                        if stock < quantity:
                            if target_message:
                                await target_message.reply_text(f"Sorry, product '{name}' is out of stock.")
                            return None

                            # 👇 ПЕРЕВІРКА: Чи закінчиться товар після цієї покупки?
                        if (stock - quantity) == 0:
                            out_of_stock_alert.append(name)

                        item_total = price * quantity
                        total_amount += item_total
                        products_list.append({
                            'name': name, 'price': price, 'quantity': quantity,
                            'emoji': emoji if emoji else "", 'total': item_total,
                            'product_id': product_id
                        })

                    # 3. Списуємо зі складу
                    for item in products_list:
                        cursor.execute(
                            "UPDATE products SET stock = stock - ? WHERE id = ?",
                            (item['quantity'], item['product_id'])
                        )

                    # 4. Створюємо замовлення
                    products_json_list = products_list
                    products_json = json.dumps(products_json_list, ensure_ascii=False)

                    cursor.execute(
                        'INSERT INTO orders (user_id, user_name, products, total_amount, email, phone, address, payment_method, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (user_id, user_name, products_json, total_amount, state.get('email'), state.get('phone'),
                         state.get('address'), state.get('payment'), 'pending')
                    )
                    order_id = cursor.lastrowid

                    # 5. Чистимо кошик
                    cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))

            except sqlite3.Error as e:
                logger.error(f"Database transaction failed: {e}")
                if target_message:
                    await target_message.reply_text("An error occurred. Please try again.")
                return None

            self.user_states.pop(user_id, None)

            # Час
            try:
                from zoneinfo import ZoneInfo
                tz_name = globals().get('BOT_TIMEZONE', 'Europe/Kyiv')
                current_time = datetime.now(ZoneInfo(tz_name)).strftime('%d.%m.%Y %H:%M')
            except Exception:
                current_time = datetime.now().strftime('%d.%m.%Y %H:%M')

            # Сповіщення адміна про НОВЕ ЗАМОВЛЕННЯ
            if ADMIN_ID != user_id:
                admin_products_text = "".join(
                    f"{item['emoji']} {item['name']} × {item['quantity']} = {item['total']}$\n" for item in
                    products_list)

                admin_text = f"""
    🔔 **NEW ORDER #{order_id}**

    👤 **Customer:** {user_name} (ID: {user_id})
    📧 **Email:** {state.get('email', '—')}
    📞 **Phone:** {state.get('phone', '—')}
    📍 **Address:** {state.get('address', '—')}
    💳 **Payment:** {state.get('payment', '—')}

    📦 **Products:**
    {admin_products_text}
    💳 **Total amount: {total_amount}$**
    🕐 **Date:** {current_time}"""

                try:
                    await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Error sending to admin: {e}")

            # 👇👇👇 НОВЕ: СПОВІЩЕННЯ ПРО ЗАКІНЧЕННЯ ТОВАРУ 👇👇👇
            if out_of_stock_alert:
                alert_text = "⚠️ **STOCK ALERT** ⚠️\n\nThe following items have reached 0 stock:\n\n"
                for item_name in out_of_stock_alert:
                    alert_text += f"❌ **{item_name}**\n"

                alert_text += "\n⚙️ Please update the stock in Admin Panel."

                try:
                    # Надсилаємо окреме повідомлення адміну
                    await context.bot.send_message(chat_id=ADMIN_ID, text=alert_text, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Error sending stock alert: {e}")
            # 👆👆👆

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
        text = f"{emoji} **{product['name']}**\n\n📝 {product['description']}\n💰 Price: {product['price']}$\nCategory: {product['category']}\nStock: {product['stock']}"
        keyboard = [
            [InlineKeyboardButton("✏️ Edit", callback_data=f"admin_edit_product_{product_id}"),
             InlineKeyboardButton("🗑️ Delete", callback_data=f"admin_delete_product_{product_id}")],
            [InlineKeyboardButton("🔙 To products", callback_data="admin_products")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_add_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID: return
        self.user_states[user_id] = {'step': 'add_product_name', 'product_data': {}}
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="admin_products")]]
        await update.callback_query.edit_message_text(
            "📦 **Adding a new product**\n\nEnter the name of the product:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

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

        text = (
            f"✏️ **Edit Product**\n\n"
            f"{product['emoji'] or ''} **{product['name']}**\n"
            f"Desc: {product['description']}\n"
            f"Price: {product['price']}$\n"
            f"Category: {product['category']}\n"
            f"Stock: {product['stock']}\n"
            f"Image: {has_img}"
        )

        keyboard = [
            [InlineKeyboardButton("Name", callback_data="admin_edit_field_name"),
             InlineKeyboardButton("Desc", callback_data="admin_edit_field_description")],
            [InlineKeyboardButton("Price", callback_data="admin_edit_field_price"),
             InlineKeyboardButton("Category", callback_data="admin_edit_field_category")],
            [InlineKeyboardButton("Emoji", callback_data="admin_edit_field_emoji"),
             InlineKeyboardButton("Stock", callback_data="admin_edit_field_stock")],
            # 👇 BUTTON LEADS TO IMAGE MENU 👇
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
            "admin_edit_field_image_url": ("image_url", "📸 **Manage Image**...")
        }

        if query.data not in field_map: return await query.answer("❌ Invalid request")

        field, msg_text = field_map[query.data]
        self.user_states[user_id]['editing_field'] = field

        # Кнопка скасування
        keyboard = [[InlineKeyboardButton("❌ Cancel",
                                          callback_data=f"admin_edit_product_{self.user_states[user_id]['product_id']}")]]

        # 👇 ГОЛОВНА ЗМІНА: Видаляємо старе меню, шлемо нове питання і зберігаємо ID
        try:
            await query.message.delete()  # Видаляємо старе меню
        except Exception:
            pass

        sent_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=msg_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

        # Зберігаємо ID повідомлення, щоб "пилосос" потім його прибрав
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
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        if user_id in self.user_states:
            # Delegate to specific handlers based on state
            await self.handle_admin_product_input(update, context)
            await self.handle_checkout_input(update, context)
            await self.handle_profile_input(update, context)
        else:
            # Fallback for messages outside of a specific flow
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

        # 👇 1. CLEANER: Delete user message
        try:
            await msg.delete()
        except Exception:
            pass

        # 👇 2. CLEANER: Delete bot's old question
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=msg.chat_id, message_id=state['msg_id'])
            except Exception:
                pass

        # Check input type
        if update.message.photo:
            input_value = update.message.photo[-1].file_id
            is_photo = True
        else:
            input_value = update.message.text.strip()
            is_photo = False

        # --- 1. IMAGE UPLOAD ---
        if step == 'waiting_product_image':
            product_id = state['product_id']
            if is_photo:
                new_image = input_value
            elif input_value.startswith('http'):
                new_image = input_value
            else:
                m = await context.bot.send_message(chat_id=msg.chat_id, text="❌ Please send a Photo or a URL.")
                state['msg_id'] = m.message_id
                return

            cursor = self.conn.cursor()
            cursor.execute("UPDATE products SET image_url = ? WHERE id = ?", (new_image, product_id))
            self.conn.commit()

            self.user_states.pop(user_id, None)

            # Success + Back Button
            keyboard = [[InlineKeyboardButton("🔙 Back to Image Menu", callback_data=f"admin_image_menu_{product_id}")]]
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text="✅ **Image successfully updated!**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # --- 2. FIELD EDITING (Price, Name...) ---
        elif state.get('editing_field'):
            field_to_edit = state['editing_field']
            product_id = state['product_id']
            value = input_value

            error_text = None
            if field_to_edit == "price":
                try:
                    value = float(input_value)
                except ValueError:
                    error_text = "❌ Invalid number. Enter price (e.g. 100.5):"
            elif field_to_edit == "stock":
                try:
                    value = int(input_value)
                except ValueError:
                    error_text = "❌ Invalid integer. Enter quantity (e.g. 10):"

            if error_text:
                m = await context.bot.send_message(chat_id=msg.chat_id, text=error_text)
                state['msg_id'] = m.message_id
                return

            cursor = self.conn.cursor()
            cursor.execute(f"UPDATE products SET {field_to_edit} = ? WHERE id = ?", (value, product_id))
            self.conn.commit()

            self.user_states.pop(user_id, None)

            # Success + Back Button
            keyboard = [[InlineKeyboardButton("🔙 Back to Editing", callback_data=f"admin_edit_product_{product_id}")]]

            field_display = field_to_edit.capitalize()
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=f"✅ **{field_display}** updated to `{value}`\n\n👇 Continue editing:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

        # --- 3. WIZARD (Shortened for brevity, logic is the same) ---
        elif step and step.startswith('add_product'):
            # (Тут твій старий код для додавання товарів, він працював)
            # Тільки не забудь додати переклади англійською, якщо вони там українською
            pass

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.warning(f'Update {update} caused error {context.error}')
        try:
            if hasattr(update, 'effective_message') and update.effective_message:
                await update.effective_message.reply_text("❌ An error has occurred. Please try again or use /start")
        except Exception:
            pass

# -------------------- MAIN --------------------
def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.critical("BOT_TOKEN not found. Please set it as an environment variable.")
        return

    bot = OnlineShopBot()
    application = Application.builder().token(BOT_TOKEN).build()

    # --- COMMAND HANDLERS ---
    application.add_handler(CommandHandler("start", bot.start))

    # --- MESSAGE HANDLERS ---
    application.add_handler(MessageHandler(filters.PHOTO, bot.handle_admin_product_input))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))
    application.add_handler(MessageHandler(filters.CONTACT, bot.handle_checkout_input))

    # --- CALLBACK QUERY HANDLERS ---
    application.add_handler(CallbackQueryHandler(bot.show_main_menu, pattern=r'^main_menu$'))
    application.add_handler(CallbackQueryHandler(bot.show_catalog, pattern=r'^catalog$'))
    application.add_handler(CallbackQueryHandler(bot.show_category, pattern=r'^category_'))
    application.add_handler(CallbackQueryHandler(bot.show_product, pattern=r'^product_'))
    application.add_handler(CallbackQueryHandler(bot.show_profile, pattern=r'^my_profile$'))
    application.add_handler(CallbackQueryHandler(bot.edit_phone, pattern=r'^edit_phone$'))
    application.add_handler(CallbackQueryHandler(bot.edit_email, pattern=r'^edit_email$'))
    application.add_handler(CallbackQueryHandler(bot.edit_address, pattern=r'^edit_address$'))
    application.add_handler(CallbackQueryHandler(bot.show_help, pattern=r'^help$'))
    application.add_handler(CallbackQueryHandler(bot.show_cart, pattern=r'^cart$'))
    application.add_handler(CallbackQueryHandler(bot.add_to_cart, pattern=r'^add_to_cart_'))
    application.add_handler(CallbackQueryHandler(bot.remove_from_cart, pattern=r'^remove_from_cart_'))
    application.add_handler(CallbackQueryHandler(bot.cart_operations, pattern=r'^cart_(add|remove)_'))
    application.add_handler(CallbackQueryHandler(bot.clear_cart, pattern=r'^clear_cart$'))
    application.add_handler(CallbackQueryHandler(bot.checkout, pattern=r'^checkout$'))
    application.add_handler(CallbackQueryHandler(bot.use_profile_data, pattern=r'^use_profile_data$'))
    application.add_handler(CallbackQueryHandler(bot.choose_payment, pattern=r'^pay_(cod|card|bank)$'))
    application.add_handler(CallbackQueryHandler(bot.handle_checkout_back, pattern=r'^back_to_'))
    application.add_handler(CallbackQueryHandler(bot.handle_cancel_order, pattern=r'^cancel_order$'))
    application.add_handler(CallbackQueryHandler(bot.show_my_orders, pattern=r'^my_orders$'))
    application.add_handler(CallbackQueryHandler(bot.handle_my_orders_pagination, pattern=r'^my_orders_page_\d+$'))
    application.add_handler(CallbackQueryHandler(bot.show_order_details, pattern=r'^order_details_'))
    application.add_handler(CallbackQueryHandler(bot.user_cancel_order, pattern=r'^user_cancel_'))
    application.add_handler(CallbackQueryHandler(bot.admin_panel, pattern=r'^admin_panel$'))
    application.add_handler(CallbackQueryHandler(bot.admin_statistics, pattern=r'^admin_statistics$'))
    application.add_handler(CallbackQueryHandler(bot.admin_user_management, pattern=r'^admin_user_management$'))
    application.add_handler(CallbackQueryHandler(bot.admin_user_block, pattern=r'^admin_user_block_'))
    application.add_handler(CallbackQueryHandler(bot.admin_revenue_chart, pattern=r'^admin_revenue_chart$'))
    application.add_handler(CallbackQueryHandler(bot.admin_all_orders, pattern=r'^admin_all_orders$'))
    application.add_handler(CallbackQueryHandler(bot.handle_admin_all_orders_pagination, pattern=r'^admin_all_orders_page_\d+$'))
    application.add_handler(CallbackQueryHandler(bot.admin_order_status_change, pattern=r'^admin_(confirm|ship|deliver|cancel)'))
    application.add_handler(CallbackQueryHandler(bot.admin_products, pattern=r'^admin_products$'))
    application.add_handler(CallbackQueryHandler(bot.admin_add_product, pattern=r'^admin_add_product$'))
    application.add_handler(CallbackQueryHandler(bot.admin_view_product, pattern=r'^admin_view_product_'))
    application.add_handler(CallbackQueryHandler(bot.admin_edit_product, pattern=r'^admin_edit_product_'))
    application.add_handler(CallbackQueryHandler(bot.admin_edit_field, pattern=r'^admin_edit_field_'))
    application.add_handler(CallbackQueryHandler(bot.admin_delete_product, pattern=r'^admin_delete_product_'))
    application.add_handler(CallbackQueryHandler(bot.admin_delete_product_confirm, pattern=r'^admin_delete_product_confirm_'))
    application.add_handler(CallbackQueryHandler(bot.handle_admin_user_pagination, pattern=r'^admin_user_page_\d+$'))
    application.add_handler(CallbackQueryHandler(bot.admin_image_menu, pattern=r'^admin_image_menu_'))
    application.add_handler(CallbackQueryHandler(bot.admin_image_set_prompt, pattern=r'^admin_image_set_'))
    application.add_handler(CallbackQueryHandler(bot.admin_image_delete, pattern=r'^admin_image_delete_'))
    application.add_handler(CallbackQueryHandler(bot.profile_delete_menu, pattern=r'^profile_delete_menu$'))
    application.add_handler(CallbackQueryHandler(bot.handle_delete_profile_data, pattern=r'^delete_profile_(phone|address|email)$'))

    # --- ERROR HANDLER ---
    application.add_error_handler(bot.error_handler)

    print("🛍️ Online store bot launched!")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print("Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()