import logging
import json
import sqlite3
from datetime import datetime
import os
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

# -------------------- LOGGING --------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- SETTINGS --------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "8383113616:AAE4CfMMLjkBRxDZYrrWffVY20B-vWvfPKQ")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8027188846"))

PHONE_RE = re.compile(r"^\+\d{10,15}$")


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

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                products TEXT NOT NULL,
                total_amount REAL NOT NULL,
                phone TEXT,
                address TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cart (
                user_id INTEGER,
                product_id INTEGER,
                quantity INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, product_id)
            )
        ''')

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

    # -------------------- SCREENS --------------------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        welcome_text = f"""
🛍️ **Welcome to our store, {user.first_name}!**

📱 Here you will find the best products at great prices!

🛒 **What you can do:**
• Browse the product catalog
• Add products to your cart
• Place orders
• Track order status

👇 **Select an action:**
        """
        keyboard = [
            [InlineKeyboardButton("🛍️ Product catalog", callback_data="catalog")],
            [InlineKeyboardButton("🛒 My cart", callback_data="cart")],
            [InlineKeyboardButton("📋 My orders", callback_data="my_orders")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
        ]
        if user.id == ADMIN_ID:
            keyboard.insert(-1, [InlineKeyboardButton("👑 Admin panel", callback_data="admin_panel")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.message:
            await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def show_catalog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        import sqlite3
        category = update.callback_query.data.replace("category_", "")
        # Use sqlite3.Row for named columns
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE category = ? AND stock > 0", (category,))
        products = cursor.fetchall()
        if not products:
            await update.callback_query.answer("❌ There are no products in this category.")
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
        keyboard.append([InlineKeyboardButton("🔙 To the catalog", callback_data="catalog")])
        await update.callback_query.edit_message_text(
            f"📂 **Category: {category}**\n\nSelect a product:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def show_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        product_id = int(update.callback_query.data.replace("product_", ""))
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if not product:
            await update.callback_query.answer("❌ Product not found")
            return
        user_id = update.effective_user.id
        cursor.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        cart_item = cursor.fetchone()
        cart_qty = cart_item[0] if cart_item else 0
        emoji = product['emoji'] if product['emoji'] else ''
        text = f"""
{emoji} **{product['name']}**

📝 {product['description']}

💰 **Price:** {product['price']}$
📦 **In stock:** {product['stock']} items
🛒 **In the cart:** {cart_qty} items

**Category:** {product['category']}
        """
        keyboard = [[InlineKeyboardButton("➕ Add to cart", callback_data=f"add_to_cart_{product_id}")]]
        if cart_qty > 0:
            keyboard.append([InlineKeyboardButton("➖ Remove from cart", callback_data=f"remove_from_cart_{product_id}")])
        keyboard.extend([
            [InlineKeyboardButton("🔙 To the category", callback_data=f"category_{product['category']}")],
            [InlineKeyboardButton("🛒 My cart", callback_data="cart")]
        ])
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # -------------------- CART --------------------
    async def add_to_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            cursor.execute(
                "UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?",
                (user_id, product_id)
            )
        else:
            cursor.execute(
                "INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, 1)",
                (user_id, product_id)
            )
        self.conn.commit()
        await self.update_product_view(query, product_id, context)
        await query.answer("✅ Item added to cart")

    async def remove_from_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer("➖ Removed from cart")

        product_id = int(query.data.replace("remove_from_cart_", ""))
        user_id = query.from_user.id

        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE cart SET quantity = quantity - 1
            WHERE user_id=? AND product_id=? AND quantity > 0
        """, (user_id, product_id))
        cursor.execute("DELETE FROM cart WHERE quantity <= 0")
        self.conn.commit()

        await self.update_product_view(query, product_id, context)


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
        text = (
            f"{emoji} {product['name']}\n\n"
            f"📝 {product['description']}\n\n"
            f"💰 Price: {product['price']}$\n"
            f"📦 In stock: {product['stock']} items\n"
            f"🛒 In the cart: {cart_qty} items\n\n"
            f"Category: {product['category']}"
        )

        keyboard = [
            [
                InlineKeyboardButton("➖ Remove from cart", callback_data=f"remove_from_cart_{product_id}") if cart_qty > 0 else None,
                InlineKeyboardButton("➕ Add to cart", callback_data=f"add_to_cart_{product_id}")
            ],
            [InlineKeyboardButton("🔙 To category", callback_data=f"category_{product['category']}")],
            [InlineKeyboardButton("🛒 My cart", callback_data="cart")]
        ]
        keyboard = [[btn for btn in row if btn is not None] for row in keyboard if any(row)]

        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    async def show_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT p.id, p.name, p.price, p.emoji, c.quantity
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        ''', (user_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            keyboard = [
                [InlineKeyboardButton("🛍️ To the catalog", callback_data="catalog")],
                [InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]
            ]
            await update.callback_query.edit_message_text(
                "🛒 **Your cart is empty**\n\nAdd items from the catalog!",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

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
            [InlineKeyboardButton("🗑️ Clear the cart", callback_data="clear_cart")],
            [InlineKeyboardButton("📋 Make an order", callback_data="checkout")],
            [InlineKeyboardButton("🔙 To the catalog", callback_data="catalog")]
        ])
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def cart_operations(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = update.callback_query.data
        if data.startswith("cart_add_"):
            product_id = int(data.replace("cart_add_", ""))
            await self.add_to_cart_from_cart(update, context, product_id)
        elif data.startswith("cart_remove_"):
            product_id = int(data.replace("cart_remove_", ""))
            await self.remove_from_cart_from_cart(update, context, product_id)

    async def add_to_cart_from_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT stock, name FROM products WHERE id = ?", (product_id,))
        stock, product_name = cursor.fetchone()
        cursor.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        current_qty = cursor.fetchone()[0]
        if current_qty >= stock:
            await update.callback_query.answer("❌ Maximum amount reached", show_alert=True)
            return
        cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?",
                       (user_id, product_id))
        self.conn.commit()
        await update.callback_query.answer(f"➕ {product_name}. Amount: {current_qty + 1}")
        await self.show_cart(update, context)

    async def remove_from_cart_from_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        current_qty = cursor.fetchone()[0]
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        product_name = cursor.fetchone()[0]
        if current_qty > 1:
            cursor.execute("UPDATE cart SET quantity = quantity - 1 WHERE user_id = ? AND product_id = ?",
                           (user_id, product_id))
            msg = f"➖ {product_name}. Amount: {current_qty - 1}"
        else:
            cursor.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
            msg = f"🗑️ {product_name} removed from cart!"
        self.conn.commit()
        await update.callback_query.answer(msg)
        await self.show_cart(update, context)

    async def clear_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cart WHERE user_id = ?", (user_id,))
        items_count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        self.conn.commit()
        await update.callback_query.answer(f"🗑️ Cart cleared! {items_count} items removed")
        await self.show_cart(update, context)

    # -------------------- PLACING AN ORDER --------------------
    async def checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.user_states[user_id] = {'step': 'waiting_phone'}
        keyboard = [
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.reply_text(
            "📋 **Placing an order**\n\n"
            "📞 **Step 1/2:** Please share your phone number using the button below, "
            "or type it manually in the format: +380XXXXXXXXX (13 digits, only Ukrainian numbers allowed).\n\n"
            "We will use your number for contact and delivery.\n\n"
            "Example: +380501234567",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    async def handle_checkout_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_states:
            return
        state = self.user_states[user_id]
        msg = update.message
        keyboard = [
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if state['step'] == 'waiting_phone':
            if msg.text and (msg.text.strip().lower() in ["❌ cancel", "cancel"]):
                self.user_states.pop(user_id, None)
                await msg.reply_text("❌ Order cancelled.", reply_markup=None)
                await self.show_cart(update, context)
                return
            phone = None
            if msg.contact and msg.contact.phone_number:
                phone = msg.contact.phone_number
            elif msg.text:
                phone = msg.text.strip()
            else:
                await msg.reply_text(
                    "❌ Please send your phone number using the button or enter it in format: +380XXXXXXXXX (13 digits).\n"
                    "Example: +380501234567",
                    reply_markup=reply_markup
                )
                return
            phone = phone.replace(" ", "").replace("-", "")
            if not re.fullmatch(r"\+380\d{9}", phone):
                await msg.reply_text(
                    "❌ Incorrect phone format. Only Ukrainian numbers (+380XXXXXXXXX) are accepted.\n"
                    "Please use the button or type your phone as +380XXXXXXXXX.\n"
                    "Example: +380501234567",
                    reply_markup=reply_markup
                )
                return
            state['phone'] = phone
            state['step'] = 'waiting_address'
            await msg.reply_text(
                "📋 **Placing an order**\n\n"
                "📍 **Step 2/2:** Enter your shipping address.\n"
                "Example: Kyiv, 1 Khreshchatyk St., apt. 10",
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        elif state['step'] == 'waiting_address':
            text = msg.text.strip()
            if text.lower() in ["❌ cancel", "cancel"]:
                self.user_states.pop(user_id, None)
                await msg.reply_text("❌ Order cancelled.", reply_markup=None)
                await self.show_cart(update, context)
                return
            if len(text) < 10:
                await msg.reply_text("❌ The address is too short. Please enter the full address.", reply_markup=reply_markup)
                return
            state['address'] = text
            await msg.reply_text("✅ Address received. Processing your order...", reply_markup=None)
            await self.create_order(update, context)
    async def handle_cancel_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.user_states.pop(user_id, None)
        await update.callback_query.edit_message_text("❌ Order cancelled.")

    async def create_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user_name = update.effective_user.full_name
        if user_id not in self.user_states:
            return
        state = self.user_states[user_id]
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT p.name, p.price, c.quantity, p.emoji
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        ''', (user_id,))
        cart_items = cursor.fetchall()
        if not cart_items:
            await update.message.reply_text("❌ The cart is empty!")
            self.user_states.pop(user_id, None)
            return
        products_list = []
        total_amount = 0
        for name, price, quantity, emoji in cart_items:
            item_total = price * quantity
            total_amount += item_total
            products_list.append({
                'name': name,
                'price': price,
                'quantity': quantity,
                'emoji': emoji if emoji else "",
                'total': item_total
            })
        products_json = json.dumps(products_list, ensure_ascii=False)
        cursor.execute('''
            INSERT INTO orders (user_id, user_name, products, total_amount, phone, address, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
        ''', (user_id, user_name, products_json, total_amount, state['phone'], state['address']))
        order_id = cursor.lastrowid
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        self.conn.commit()
        self.user_states.pop(user_id, None)

        order_text = f"""
✅ **Order #{order_id} has been successfully placed!**

👤 **Customer:** {user_name}
📞 **Phone:** {state['phone']}
📍 **Address:** {state['address']}

📦 **Products:**
        """
        for item in products_list:
            order_text += f"{item['emoji']} {item['name']} × {item['quantity']} = {item['total']}$\n"
        order_text += f"""
💳 **Total amount: {total_amount}$**

📋 **Status:** In progress
🕐 **Date:** {datetime.now().strftime('%d.%m.%Y %H:%M')}

We will contact you shortly!
        """
        keyboard = [[InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]]
        await update.message.reply_text(order_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

        if ADMIN_ID != user_id:
            admin_text = f"""
🔔 **NEW ORDER #{order_id}**

👤 **Customer:** {user_name} (ID: {user_id})
📞 **Phone:** {state['phone']}
📍 **Address:** {state['address']}

📦 **Products:**
            """
            for item in products_list:
                admin_text += f"{item['emoji']} {item['name']} × {item['quantity']} = {item['total']}$\n"
            admin_text += f"\n💳 **Total amount: {total_amount}$**"
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.warning(f"Error sending to admin: {e}")

    # -------------------- ORDER (USER/ADMIN) --------------------
    async def show_my_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        import json
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, total_amount, status, created_at, products
            FROM orders 
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 10
        ''', (user_id,))
        orders = cursor.fetchall()
        if not orders:
            keyboard = [[InlineKeyboardButton("🛍️ To the catalog", callback_data="catalog")],
                        [InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]]
            await update.callback_query.edit_message_text(
                "📋 **You don't have any orders yet**\n\nPlace your first order from our catalog!",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return
        text = "📋 **Your orders:**\n\n"
        keyboard = []
        status_emoji = {
            'pending': '🟡 In processing',
            'confirmed': '🔵 Confirmed',
            'shipped': '🟠 Sent',
            'delivered': '🟢 Delivered',
            'cancelled': '🔴 Cancelled'
        }
        for order in orders:
            order_id, total, status, created_at, products_json = order
            status_text = status_emoji.get(status, status)
            text += f"🧾 **Order #{order_id}**\n"
            try:
                products = json.loads(products_json)
            except Exception:
                products = []
            for product in products:
                emoji = product.get('emoji', '')
                name = product.get('name', '')
                qty = product.get('quantity', 0)
                total_price = product.get('total', 0)
                text += f"   {emoji} {name} × {qty} = {total_price}$\n"
            text += f"💰 {total}$ | {status_text}\n"
            text += f"📅 {created_at[:16]}\n\n"
            keyboard.append([InlineKeyboardButton(f"📋 Details #{order_id}", callback_data=f"order_details_{order_id}")])
        keyboard.append([InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")])
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def show_order_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        message = getattr(update, "message", None)
        data = None
        if query and query.data:
            data = query.data
        elif message and message.text:
            data = message.text
        else:
            if query:
                await query.answer("❌ Invalid request")
            return
        match = re.search(r'order_details_(\d+)', data)
        if not match:
            if query:
                await query.answer("❌ Invalid request")
            return
        order_id = int(match.group(1))
        uid = update.effective_user.id
        cursor = self.conn.cursor()
        if uid == ADMIN_ID:
            cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        else:
            cursor.execute("SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, uid))
        order = cursor.fetchone()
        if not order:
            if query:
                await query.answer("❌ Order not found")
            return
        oid, user_id, user_name, products_json, total, phone, address, status, created_at = order
        products = json.loads(products_json)
        status_emoji = {
            'pending': '🟡 In processing',
            'confirmed': '🔵 Confirmed',
            'shipped': '🟠 Sent',
            'delivered': '🟢 Delivered',
            'cancelled': '🔴 Cancelled'
        }
        order_text = f"""
📋 **Order #{order_id}**

👤 **Customer:** {user_name}
📞 **Phone:** {phone}
📍 **Address:** {address}

📦 **Products:**
"""
        for product in products:
            order_text += f"{product['emoji']} {product['name']} × {product['quantity']} = {product['total']}$\n"
        order_text += f"""
💳 **Total amount: {total}$**
📊 **Status:** {status_emoji.get(status, status)}
🕐 **Date:** {created_at[:16]}
        """
        keyboard = []
        if uid == ADMIN_ID:
            keyboard.extend([
                [InlineKeyboardButton("✅ Confirm", callback_data=f"admin_confirm_{order_id}"), InlineKeyboardButton("📦 Sent", callback_data=f"admin_ship_{order_id}")],
                [InlineKeyboardButton("🚚 Delivered", callback_data=f"admin_deliver_{order_id}"), InlineKeyboardButton("❌ Cancel", callback_data=f"admin_cancel_{order_id}")],
                [InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")]
            ])
        else:
            if status not in ('cancelled', 'delivered'):
                keyboard.append([InlineKeyboardButton("❌ Cancel order", callback_data=f"user_cancel_{order_id}")])
            keyboard.append([InlineKeyboardButton("🔙 My orders", callback_data="my_orders")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        if query:
            await query.edit_message_text(order_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        elif message:
            await message.reply_text(order_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    # -------------------- ADMIN PANEL --------------------
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        text = f"""
👑 **ADMIN PANEL**

📊 **Statistics:**
• 📋 Total orders: {total_orders}
• 🟡 New orders: {pending_orders}
• 💰 Revenue: {total_revenue}$
• 📦 Products in catalog: {total_products}

👇 **Select an action:**
        """
        keyboard = [
            [InlineKeyboardButton("📋 New orders", callback_data="admin_orders")],
            [InlineKeyboardButton("📦 Commodity management", callback_data="admin_products")],
            [InlineKeyboardButton("📊 All orders", callback_data="admin_all_orders")],
            [InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]
        ]
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            return

        import json
        import sqlite3

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, user_name, total_amount, created_at, products
            FROM orders 
            WHERE status = 'pending'
            ORDER BY created_at DESC
        ''')
        orders = cursor.fetchall()
        if not orders:
            keyboard = [[InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")]]
            await update.callback_query.edit_message_text("📋 **No new orders**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            return
        text = "📋 **New orders:**\n\n"
        keyboard = []
        cursor.execute("SELECT id, name, emoji FROM products")
        product_map = {row["id"]: {"name": row["name"], "emoji": row["emoji"] or ""} for row in cursor.fetchall()}
        for order in orders:
            order_id, user_name, total, created_at, products_json = order
            main_product_name = None
            try:
                products = json.loads(products_json)
            except Exception:
                products = []
            if products:
                first_product = products[0]
                name = first_product.get('name')
                if not name or name == "None":
                    pid = first_product.get('id')
                    prodinfo = product_map.get(pid) if pid is not None else None
                    name = prodinfo['name'] if prodinfo and 'name' in prodinfo else None
                main_product_name = name if name else None
            if not main_product_name:
                main_product_name = user_name if user_name else "Unknown"
            text += f"🧾 **#{order_id}** | {main_product_name}\n💰 {total}$ | {created_at[:16]}\n"
            for product in products:
                name = product.get('name', '') or ''
                emoji = product.get('emoji', '') or ''
                if (not name or name == "None") and product.get('id') is not None:
                    prodinfo = product_map.get(product.get('id'))
                    if prodinfo:
                        name = prodinfo.get("name", name)
                        emoji = prodinfo.get("emoji", emoji)
                if not name:
                    name = "Unknown"
                qty = product.get('quantity', 0)
                total_price = product.get('total', 0)
                text += f"   {emoji} {name} × {qty} = {total_price}$\n"
            text += "\n"
            keyboard.append([InlineKeyboardButton(f"📋 Review #{order_id}", callback_data=f"order_details_{order_id}")])
        keyboard.append([InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")])
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_all_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            return
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, user_name, total_amount, status, created_at, products
            FROM orders 
            ORDER BY created_at DESC
            LIMIT 20
        ''')
        orders = cursor.fetchall()
        text = "📊 **All orders (last 20):**\n\n"
        keyboard = []
        status_emoji = {
            'pending': '🟡',
            'confirmed': '🔵',
            'shipped': '🟠',
            'delivered': '🟢',
            'cancelled': '🔴'
        }
        for order in orders:
            order_id, user_name, total, status, created_at, products_json = order
            emoji_status = status_emoji.get(status, '⚫')
            text += f"{emoji_status} **#{order_id}** | {user_name} | {total}$\n"
            try:
                products = json.loads(products_json)
            except Exception as e:
                products = []
            for product in products:
                emoji = product.get('emoji', '')
                name = product.get('name', '')
                qty = product.get('quantity', 0)
                total_price = product.get('total', 0)
                text += f"   {emoji} {name} × {qty} = {total_price}$\n"
            text += "\n"
            keyboard.append([InlineKeyboardButton(f"📋 #{order_id}", callback_data=f"order_details_{order_id}")])
        keyboard.append([InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")])
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )


    # -------------------- ADMIN: product management --------------------
    async def admin_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):

        if update.effective_user.id != ADMIN_ID:
            return
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, price, stock, emoji, category FROM products ORDER BY name")
        products = cursor.fetchall()
        text = "📦 **Product management:**\n\n"
        keyboard = []
        for product in products[:10]:
            pid, name, price, stock, emoji, category = product
            stock_status = "✅" if stock > 0 else "❌"
            text += f"{stock_status} {emoji} **{name}** | {price}$ | Stock: {stock}\n"
            btns = [
                InlineKeyboardButton("✏️", callback_data=f"admin_edit_product_{pid}"),
                InlineKeyboardButton("🗑️", callback_data=f"admin_delete_product_{pid}")
            ]
            keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"admin_view_product_{pid}")] + btns)
        keyboard.append([InlineKeyboardButton("➕ Add product", callback_data="admin_add_product")])
        keyboard.append([InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")])
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
        )

    async def admin_add_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            return
        self.user_states[user_id] = {'step': 'add_product_name'}
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            "📦 **Adding a new product**\n\nEnter the name of the product:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

    async def admin_edit_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            return

        data = update.callback_query.data
        match = re.match(r"admin_edit_product_(\d+)", data)
        if not match:
            await update.callback_query.answer("❌ Invalid request")
            return
        product_id = int(match.group(1))
        cursor = self.conn.cursor()
        cursor.execute("SELECT name, description, price, emoji, category, stock FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row:
            await update.callback_query.answer("❌  Product not found")
            return
        name, description, price, emoji, category, stock = row
        self.user_states[user_id] = {
            'step': 'edit_product_field',
            'product_id': product_id,
            'name': name,
            'description': description,
            'price': price,
            'emoji': emoji,
            'category': category,
            'stock': stock
        }

        text = f"✏️ **Editing a product:**\n\n{emoji} **{name}**\nDescription: {description}\nPrice: {price}$\nCategory: {category}\nStock: {stock}"
        keyboard = [
            [InlineKeyboardButton("Title", callback_data="admin_edit_field_name"),
             InlineKeyboardButton("Description", callback_data="admin_edit_field_description")],
            [InlineKeyboardButton("Price", callback_data="admin_edit_field_price"),
             InlineKeyboardButton("Category", callback_data="admin_edit_field_category")],
            [InlineKeyboardButton("Emoji", callback_data="admin_edit_field_emoji"),
             InlineKeyboardButton("Stock", callback_data="admin_edit_field_stock")],
            [InlineKeyboardButton("💾 Save changes", callback_data="admin_save_product")],
            [InlineKeyboardButton("🔙 To products", callback_data="admin_products")]
        ]
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_delete_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            return
        data = update.callback_query.data
        match = re.match(r"admin_delete_product_(\d+)", data)
        if not match:
            await update.callback_query.answer("❌ Invalid request")
            return
        product_id = int(match.group(1))
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row:
            await update.callback_query.answer("❌ Product not found")
            return
        name = row[0]
        keyboard = [
            [InlineKeyboardButton("❌ Yes, delete", callback_data=f"admin_delete_product_confirm_{product_id}")],
            [InlineKeyboardButton("🔙 Cancel", callback_data="admin_products")]
        ]
        await update.callback_query.edit_message_text(
            f"Are you sure you want to delete the product? **{name}**?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
        )

    async def admin_delete_product_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            return
        data = update.callback_query.data
        match = re.match(r"admin_delete_product_confirm_(\d+)", data)
        if not match:
            await update.callback_query.answer("❌ Invalid request")
            return
        product_id = int(match.group(1))
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row:
            await update.callback_query.answer("❌ Product not found")
            return
        name = row[0]
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        self.conn.commit()
        await update.callback_query.answer(f"🗑️ Product {name} has been removed")
        await self.admin_products(update, context)

    async def admin_view_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = update.callback_query.data
        match = re.match(r"admin_view_product_(\d+)", data)
        if not match:
            await update.callback_query.answer("❌ Invalid request")
            return
        product_id = int(match.group(1))
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if not product:
            await update.callback_query.answer("❌ Product not found")
            return
        emoji = product['emoji'] if product['emoji'] else ''
        text = f"{emoji} **{product['name']}**\n\n📝 {product['description']}\n💰 Price: {product['price']}$\nCategory: {product['category']}\nStock: {product['stock']}"
        keyboard = [
            [InlineKeyboardButton("✏️ Edit", callback_data=f"admin_edit_product_{product_id}"),
             InlineKeyboardButton("🗑️ Delete", callback_data=f"admin_delete_product_{product_id}")],
            [InlineKeyboardButton("🔙 To products", callback_data="admin_products")]
        ]
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def handle_admin_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("❌ Access denied.")
            return

        if user_id not in self.user_states:
            await update.message.reply_text("❌ No active action. Use /admin_panel")
            return

        state = self.user_states[user_id]
        product_id = state.get("product_id")
        field = state.get("field")
        if not product_id or not field:
            await update.message.reply_text("❌ No product selected for editing.")
            return

        text = update.message.text.strip()
        if text.lower() in ["/cancel", "cancel", "❌"]:
            self.user_states.pop(user_id, None)
            await update.message.reply_text("❌ Action cancelled.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")]
            ]))
            return

        cursor = self.conn.cursor()
        try:
            if field == "price":
                try:
                    value = float(text)
                except ValueError:
                    await update.message.reply_text("❌ Enter a valid number for price.")
                    return
                cursor.execute("UPDATE products SET price = ? WHERE id = ?", (value, product_id))
            elif field == "stock":
                try:
                    value = int(text)
                except ValueError:
                    await update.message.reply_text("❌ Enter a valid integer for stock.")
                    return
                cursor.execute("UPDATE products SET stock = ? WHERE id = ?", (value, product_id))
            elif field in ["name", "description", "category", "image_url"]:
                cursor.execute(f"UPDATE products SET {field} = ? WHERE id = ?", (text, product_id))
            else:
                await update.message.reply_text("❌ Invalid field.")
                return

            self.conn.commit()

            cursor.execute("SELECT id, name, description, price, emoji, category, stock FROM products WHERE id = ?", (product_id,))
            product = cursor.fetchone()

            product_text = f"""✅ Product updated successfully!

🆔 ID: {product[0]}
{product[4]} Name: {product[1]}
📝 Description: {product[2]}
💰 Price: {product[3]}$
📂 Category: {product[5]}
📦 Stock: {product[6]}
"""

            keyboard = [
                [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")],
                [InlineKeyboardButton("✏️ Edit this product again", callback_data=f"admin_edit_product_{product_id}")]
            ]

            self.user_states.pop(user_id, None)
            await update.message.reply_text(product_text, reply_markup=InlineKeyboardMarkup(keyboard),
                                            parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def handle_admin_product_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_states:
            return
        state = self.user_states[user_id]
        text = update.message.text.strip()

        if text.strip().lower() in ["❌ cancel", "cancel"]:
            self.user_states.pop(user_id, None)
            msg_id = state.get("message_id")
            chat_id = state.get("chat_id")
            if msg_id and chat_id:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="❌ Addition canceled.",
                    reply_markup=None,
                    parse_mode=ParseMode.MARKDOWN
                )
            class FakeCQ:
                def __init__(self, chat_id, message_id, from_user):
                    self.message = type("msg", (), {"chat_id": chat_id, "message_id": message_id})
                    self.from_user = from_user
            fake_update = type("Upd", (), {"callback_query": FakeCQ(chat_id, msg_id, update.effective_user), "effective_user": update.effective_user})
            await self.admin_products(fake_update, context)
            return

        if state.get("step", "").startswith("edit_product_"):
            product_id = state.get("product_id")
            field = state.get("field")
            if not product_id or not field:
                await update.message.reply_text("❌ No product selected for editing.")
                return
            cursor = self.conn.cursor()
            value = text
            try:
                if field == "price":
                    try:
                        value = float(text)
                    except ValueError:
                        await update.message.reply_text(
                            "❌ Enter a valid number for price.\n\n❌ Cancel",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
                        )
                        return
                    cursor.execute("UPDATE products SET price = ? WHERE id = ?", (value, product_id))
                elif field == "stock":
                    try:
                        value = int(text)
                    except ValueError:
                        await update.message.reply_text(
                            "❌ Enter a valid integer for stock.\n\n❌ Cancel",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
                        )
                        return
                    cursor.execute("UPDATE products SET stock = ? WHERE id = ?", (value, product_id))
                elif field in ["name", "description", "category", "image_url"]:
                    cursor.execute(f"UPDATE products SET {field} = ? WHERE id = ?", (value, product_id))
                elif field == "emoji":
                    cursor.execute("UPDATE products SET emoji = ? WHERE id = ?", (value, product_id))
                else:
                    await update.message.reply_text(
                        "❌ Invalid field.\n\n❌ Cancel",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
                    )
                    return

                self.conn.commit()
                cursor.execute("SELECT id, name, description, price, emoji, category, stock FROM products WHERE id = ?", (product_id,))
                product = cursor.fetchone()
                self.user_states.pop(user_id, None)
                product_text = f"""✅ Product updated successfully!

🆔 ID: {product[0]}
{product[4]} Name: {product[1]}
📝 Description: {product[2]}
💰 Price: {product[3]}$
📂 Category: {product[5]}
📦 Stock: {product[6]}
"""
                keyboard = [
                    [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")],
                    [InlineKeyboardButton("✏️ Edit this product again", callback_data=f"admin_edit_product_{product_id}")]
                ]
                await update.message.reply_text(
                    product_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Error: {e}")
            return

        def build_product_add_text(state, current_step, error_msg=None):
            t = "📦 **Adding a new product**\n\n"
            t += f"**Title:** {state.get('name', '—')}\n"
            t += f"**Description:** {state.get('description', '—')}\n"
            t += f"**Price:** {str(state.get('price'))+'$' if 'price' in state else '—'}\n"
            t += f"**Emoji:** {state.get('emoji', '—')}\n"
            t += f"**Category:** {state.get('category', '—')}\n"
            t += f"**Stock:** {state.get('stock', '—')}\n\n"
            if error_msg:
                t += f"❌ {error_msg}\n"
            prompts = {
                'add_product_name': "Enter product name:",
                'add_product_description': "Enter product description:",
                'add_product_price': "Enter the product price (as a number):",
                'add_product_emoji': "Enter an emoji for the product (for example, 📱):",
                'add_product_category': "Enter the product category:",
                'add_product_stock': "Enter the quantity in stock (as a number):",
            }
            if current_step in prompts:
                t += prompts[current_step]
            return t

        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg_id = state.get("message_id")
        chat_id = state.get("chat_id")
        step = state.get("step")

        async def send_or_edit(text, reply_markup=None):
            if msg_id and chat_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

        if step == "add_product_name":
            state["name"] = text
            state["step"] = "add_product_description"
            msg = build_product_add_text(state, "add_product_description")
            await send_or_edit(msg, reply_markup)
            return
        if step == "add_product_description":
            state["description"] = text
            state["step"] = "add_product_price"
            msg = build_product_add_text(state, "add_product_price")
            await send_or_edit(msg, reply_markup)
            return
        if step == "add_product_price":
            try:
                price = float(text)
                state["price"] = price
                state["step"] = "add_product_emoji"
                msg = build_product_add_text(state, "add_product_emoji")
                await send_or_edit(msg, reply_markup)
            except Exception:
                msg = build_product_add_text(state, "add_product_price", error_msg="The price must be a number. Please try again.:")
                await send_or_edit(msg, reply_markup)
            return
        if step == "add_product_emoji":
            state["emoji"] = text
            state["step"] = "add_product_category"
            msg = build_product_add_text(state, "add_product_category")
            await send_or_edit(msg, reply_markup)
            return
        if step == "add_product_category":
            state["category"] = text
            state["step"] = "add_product_stock"
            msg = build_product_add_text(state, "add_product_stock")
            await send_or_edit(msg, reply_markup)
            return
        if step == "add_product_stock":
            try:
                stock = int(text)
                state["stock"] = stock
            except Exception:
                msg = build_product_add_text(state, "add_product_stock", error_msg="The value must be a number. Please try again:")
                await send_or_edit(msg, reply_markup)
                return
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO products (name, description, price, emoji, category, stock) VALUES (?, ?, ?, ?, ?, ?)",
                (state["name"], state["description"], state["price"], state["emoji"], state["category"], state["stock"])
            )
            self.conn.commit()
            msg = f"✅ Product **{state['name']}** successfully added!"
            keyboard = [[InlineKeyboardButton("🔙 Return to product management", callback_data="admin_products")]]
            if msg_id and chat_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=msg,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    await update.message.reply_text(
                        msg,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.MARKDOWN
                    )
            else:
                await update.message.reply_text(
                    msg,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
            self.user_states.pop(user_id, None)
            return

    async def admin_edit_field(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_states:
            return
        state = self.user_states[user_id]
        data = update.callback_query.data
        field_map = {
            "admin_edit_field_name": ("name", "Enter a new product name:"),
            "admin_edit_field_description": ("description", "Enter a new product description:"),
            "admin_edit_field_price": ("price", "Enter the new price of the item (as a number):"),
            "admin_edit_field_category": ("category", "Enter a new product category:"),
            "admin_edit_field_emoji": ("emoji", "Enter a new emoji for the product:"),
            "admin_edit_field_stock": ("stock", "Enter the new quantity in stock (as a number):")
        }
        if data not in field_map:
            await update.callback_query.answer("❌ Invalid request")
            return
        field, msg = field_map[data]
        state['field'] = field
        state['step'] = f"edit_product_{field}"
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    async def admin_save_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):

        user_id = update.effective_user.id
        if user_id not in self.user_states:
            return
        state = self.user_states[user_id]
        product_id = state['product_id']
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE products SET name=?, description=?, price=?, image_url=?, category=?, stock=?
            WHERE id=?
        ''', (state['name'], state['description'], state['price'], state['emoji'], state['category'], state['stock'], product_id))
        self.conn.commit()
        await update.callback_query.answer("✅ Changes saved")
        self.user_states.pop(user_id, None)
        await self.admin_products(update, context)

    async def admin_order_status_change(self, update: Update, context: ContextTypes.DEFAULT_TYPE):

        query = update.callback_query
        if update.effective_user.id != ADMIN_ID:
            await query.answer("❌ Access denied")
            return

        data = query.data

        match = re.search(r'_(\d+)$', data)
        if not match:
            await query.answer("❌ Invalid request")
            return
        order_id = int(match.group(1))

        status_map = {
            "admin_confirm": ("confirmed", "confirmed"),
            "admin_ship": ("shipped", "sent"),
            "admin_deliver": ("delivered", "delivered"),
            "admin_cancel": ("cancelled", "canceled"),
        }
        action_match = re.match(r"^(admin_confirm|admin_ship|admin_deliver|admin_cancel)_\d+$", data)
        if not action_match:
            await query.answer("❌ Invalid request")
            return
        action = action_match.group(1)
        if action not in status_map:
            return
        new_status, status_text = status_map[action]

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        order = cursor.fetchone()
        if not order:
            await query.answer("❌ Order not found")
            return

        cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        self.conn.commit()
        await query.answer(f"✅ Order #{order_id} {status_text}")

        oid, user_id, user_name, products_json, total, phone, address, status, created_at = order

        status = new_status
        products = json.loads(products_json)
        status_emoji = {
            'pending': '🟡 In processing',
            'confirmed': '🔵 Confirmed',
            'shipped': '🟠 Sent',
            'delivered': '🟢 Delivered',
            'cancelled': '🔴 Cancelled'
        }
        order_text = f"""
📋 **Order #{order_id}**

👤 **Customer:** {user_name}
📞 **Phone:** {phone}
📍 **Address:** {address}

📦 **Products:**
"""
        for product in products:
            order_text += f"{product['emoji']} {product['name']} × {product['quantity']} = {product['total']}$\n"
        order_text += f"""
💳 **Total amount: {total}$**
📊 **Status:** {status_emoji.get(status, status)}
🕐 **Date:** {created_at[:16]}
        """
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data=f"admin_confirm_{order_id}"),
             InlineKeyboardButton("📦 Sent", callback_data=f"admin_ship_{order_id}")],
            [InlineKeyboardButton("🚚 Delivered", callback_data=f"admin_deliver_{order_id}"),
             InlineKeyboardButton("❌ Cancel", callback_data=f"admin_cancel_{order_id}")],
            [InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(order_text, reply_markup=reply_markup, parse_mode="Markdown")

    async def user_cancel_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):

        query = update.callback_query
        data = query.data

        match = re.match(r"user_cancel_(\d+)", data)
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

        cursor.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        self.conn.commit()

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🔴 The customer canceled the order. #{order_id}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

        await query.answer("✅ Order canceled")
        await self.show_order_details(update, context)

    # -------------------- CONVERSATIONS/HELP --------------------
    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    # -------------------- DISPATCHERS --------------------
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        if data == "main_menu":
            await self.start(update, context)
        elif data == "catalog":
            await self.show_catalog(update, context)
        elif data == "cart":
            await self.show_cart(update, context)
        elif data == "my_orders":
            await self.show_my_orders(update, context)
        elif data == "help":
            await self.show_help(update, context)
        elif data.startswith("category_"):
            await self.show_category(update, context)
        elif data.startswith("product_"):
            await self.show_product(update, context)
        elif data.startswith("add_to_cart_"):
            await self.add_to_cart(update, context)
        elif data.startswith("remove_from_cart_"):
            await self.remove_from_cart(update, context)
        elif data.startswith("cart_add_") or data.startswith("cart_remove_"):
            await self.cart_operations(update, context)
        elif data == "cancel":
            user_id = update.effective_user.id
            if user_id in self.user_states:
                self.user_states.pop(user_id)
            await self.admin_products(update, context)
        elif data == "cancel_order":
            await self.handle_cancel_order(update, context)
        elif data == "clear_cart":
            await self.clear_cart(update, context)
        elif data == "checkout":
            await self.checkout(update, context)
        elif data.startswith("order_details_"):
            await self.show_order_details(update, context)
        elif data.startswith("user_cancel_"):
            await self.user_cancel_order(update, context)
        elif data == "admin_panel":
            await self.admin_panel(update, context)
        elif data == "admin_orders":
            await self.admin_orders(update, context)
        elif data == "admin_all_orders":
            await self.admin_all_orders(update, context)
        elif data == "admin_products":
            await self.admin_products(update, context)
        elif data == "admin_add_product":
            await self.admin_add_product(update, context)
        elif data.startswith("admin_edit_product_"):
            await self.admin_edit_product(update, context)
        elif data.startswith("admin_delete_product_confirm_"):
            await self.admin_delete_product_confirm(update, context)
        elif data.startswith("admin_delete_product_"):
            await self.admin_delete_product(update, context)
        elif data.startswith("admin_view_product_"):
            await self.admin_view_product(update, context)
        elif data in (
            "admin_edit_field_name",
            "admin_edit_field_description",
            "admin_edit_field_price",
            "admin_edit_field_category",
            "admin_edit_field_emoji",
            "admin_edit_field_stock"
        ):
            await self.admin_edit_field(update, context)
        elif data == "admin_save_product":
            await self.admin_save_product(update, context)
        elif data.startswith("admin_confirm_") or data.startswith("admin_ship_") or data.startswith("admin_deliver_") or data.startswith("admin_cancel_"):
            await self.admin_order_status_change(update, context)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id

        if user_id in self.user_states:
            state = self.user_states[user_id]
            if state.get('step', '').startswith('add_product') or state.get('step', '').startswith('edit_product_'):
                await self.handle_admin_product_input(update, context)
                return
            if state.get('field') and state.get('product_id'):
                await self.handle_admin_product_input(update, context)
                return
            await self.handle_checkout_input(update, context)
            return
        text = update.message.text.lower()
        if any(word in text for word in ["привіт", "hello", "hi"]):
            await update.message.reply_text("Hi! 👋 Use /start to start shopping!")
        elif any(word in text for word in ["catalog", "goods", "shop"]):
            await update.message.reply_text("Go to the product catalog:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛍️ Product catalog", callback_data="catalog")]]))
        elif any(word in text for word in ["cart", "cart"]):
            await update.message.reply_text("Go to your shopping cart:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 My cart", callback_data="cart")]]))
        elif any(word in text for word in ["assistance, help, support"]):
            await update.message.reply_text("Get help:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ℹ️ Help", callback_data="help")]]))
        else:
            await update.message.reply_text("Use /start for navigating the store! 🛍️")

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
        print("❌ Error: Install BOT_TOKEN!")
        print("1. Create a bot via @BotFather in Telegram")
        print("2. Get a token")
        print("3. Export: export BOT_TOKEN=\"<TOKEN>\"")
        print("4. Export your ADMIN_ID: export ADMIN_ID=\"123456789\"")
        return

    bot = OnlineShopBot()
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CallbackQueryHandler(bot.button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))
    application.add_error_handler(bot.error_handler)

    print("🛍️ Online store bot launched!")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print("📊 Database: shop.db")
    print("Press Ctrl+C to stop.")

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
