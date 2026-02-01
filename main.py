import logging
import json
import sqlite3
from datetime import datetime
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
BOT_TOKEN = "8383113616:AAE4CfMMLjkBRxDZYrrWffVY20B-vWvfPKQ"
ADMIN_ID = 8027188846


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

    # -------------------- KEYBOARD BUILDER --------------------
    def build_main_keyboard(self, user_id):
        """
        Build the main menu keyboard, adding Admin panel button if admin.
        """
        keyboard = [
            [InlineKeyboardButton("🛍️ Product catalog", callback_data="catalog")],
            [InlineKeyboardButton("🛒 My cart", callback_data="cart")],
            [InlineKeyboardButton("📋 My orders", callback_data="my_orders")]
        ]
        # Always check admin strictly as int
        if int(user_id) == int(ADMIN_ID):
            keyboard.append([InlineKeyboardButton("👑 Admin panel", callback_data="admin_panel")])
        keyboard.append([InlineKeyboardButton("ℹ️ Help", callback_data="help")])
        return InlineKeyboardMarkup(keyboard)

    # -------------------- USER-FACING SCREENS --------------------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_main_menu(update, context)

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Display the main menu. Admin panel button is always visible for the admin.
        """
        user = update.effective_user
        logger.info(f"Displaying main menu for user.id={user.id}, ADMIN_ID={ADMIN_ID}")

        welcome_text = f"🛍️ **Welcome to our store, {user.first_name}!**\n\n" \
                       "📱 Here you will find the best products at great prices!\n\n" \
                       "🛒 **What you can do:**\n" \
                       "• Browse the product catalog\n" \
                       "• Add products to your cart\n" \
                       "• Place orders\n" \
                       "• Track order status\n\n" \
                       "👇 **Select an action:**"

        reply_markup = self.build_main_keyboard(user.id)

        # Send or edit message
        if update.callback_query:
            await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        elif update.message:
            await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

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
        category = update.callback_query.data.replace("category_", "")
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

💰 **Price:** {product['price']}$\n📦 **In stock:** {product['stock']} items
🛒 **In the cart:** {cart_qty} items

**Category:** {product['category']} """
        keyboard = [[InlineKeyboardButton("➕ Add to cart", callback_data=f"add_to_cart_{product_id}")]]
        if cart_qty > 0:
            keyboard.append([InlineKeyboardButton("➖ Remove from cart", callback_data=f"remove_from_cart_{product_id}")])
        keyboard.extend([
            [InlineKeyboardButton("🔙 To the category", callback_data=f"category_{product['category']}")],
            [InlineKeyboardButton("🛒 My cart", callback_data="cart")]
        ])
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # -------------------- CART LOGIC --------------------
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
                [InlineKeyboardButton("🛍️ Product catalog", callback_data="catalog")],
                [InlineKeyboardButton("📋 My orders", callback_data="my_orders")],
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
            ]
            if int(user_id) == int(ADMIN_ID):
                keyboard.append([InlineKeyboardButton("👑 Admin panel", callback_data="admin_panel")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.edit_message_text(
                "🛒 **Your cart is empty**\n\nAdd items from the catalog!",
                reply_markup=reply_markup,
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
            [InlineKeyboardButton("🔙 To the catalog", callback_data="catalog")],
            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
        ])
        if int(user_id) == int(ADMIN_ID):
            keyboard.append([InlineKeyboardButton("👑 Admin panel", callback_data="admin_panel")])
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

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
            cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        else:
            cursor.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, 1)", (user_id, product_id))
        self.conn.commit()
        await self.update_product_view(query, product_id, context)
        await query.answer("✅ Item added to cart")

    async def remove_from_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        query = update.callback_query
        data = query.data
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
        cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?", (user_id, product_id))
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
            cursor.execute("UPDATE cart SET quantity = quantity - 1 WHERE user_id = ? AND product_id = ?", (user_id, product_id))
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
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # -------------------- CHECKOUT LOGIC --------------------
    async def checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.user_states[user_id] = {'step': 'waiting_email'}
        keyboard = [
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_cart")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.reply_text(
            "📋 **Placing an order**\n\n" \
            "📧 **Step 1/3:** Enter your email address.\n" \
            "Example: example@gmail.com",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    async def handle_checkout_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_states:
            return
        state = self.user_states[user_id]
        msg = update.message
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if state['step'] == 'waiting_email':
            email = msg.text.strip()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                await msg.reply_text("❌ Please enter a valid email address.")
                return
            state['email'] = email
            state['step'] = 'waiting_address'
            await msg.reply_text(
                "📋 **Placing an order**\n\n" \
                "📍 **Step 2/3:** Enter your shipping address.\n" \
                "Example: Kyiv, 1 Khreshchatyk St., apt. 10",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_email")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
                ]),
                parse_mode="Markdown"
            )
            return

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
            state['step'] = 'waiting_payment'
            keyboard = [
                [InlineKeyboardButton("💵 Cash on delivery", callback_data="pay_cod")],
                [InlineKeyboardButton("💳 Card to courier", callback_data="pay_card")],
                [InlineKeyboardButton("🏦 Bank transfer", callback_data="pay_bank")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_address")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
            ]
            await msg.reply_text(
                "📋 **Placing an order**\n\n" \
                "💳 **Step 3/3:** Choose a payment method:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

        elif state['step'] == 'waiting_phone':
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
                    "❌ Please send your phone number using the button or enter it in format: +380XXXXXXXXX (13 digits).\n" \
                    "Example: +380501234567",
                    reply_markup=reply_markup
                )
                return
            phone = phone.replace(" ", "").replace("-", "")
            if not re.fullmatch(r"\+380\d{9}", phone):
                await msg.reply_text(
                    "❌ Incorrect phone format. Only Ukrainian numbers (+380XXXXXXXXX) are accepted.\n" \
                    "Please use the button or type your phone as +380XXXXXXXXX.\n" \
                    "Example: +380501234567",
                    reply_markup=reply_markup
                )
                return
            state['phone'] = phone
            await msg.reply_text("✅ **Order data confirmed!**", parse_mode="Markdown")
            await self.create_order(update, context)

    async def choose_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        # For Cash on delivery, require phone before confirming
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
                parse_mode="Markdown"
            )
            return

        # For Card and Bank, set payment in state
        self.user_states[user_id]['payment'] = payment

        cursor = self.conn.cursor()
        cursor.execute('SELECT p.name, p.price, c.quantity, p.emoji FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?', (user_id,))
        cart_items = cursor.fetchall()
        if not cart_items:
            await query.edit_message_text("❌ The cart is empty!")
            self.user_states.pop(user_id, None)
            return

        total_amount = sum(price * quantity for _, price, quantity, _ in cart_items)

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
            await query.edit_message_text(order_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return

        # Card to courier or Cash on delivery (with phone already present): create order and send confirmation
        order_id = await self.create_order(update, context, send_message=False)
        products_text = "".join(f"{emoji} {name} × {quantity} = {price*quantity}$\n" for name, price, quantity, emoji in cart_items)
        order_text = (
            f"💳 {payment} selected\n\n" \
            f"You will be able to pay by {payment.lower()} upon delivery.\n\n" \
            f"✅ **Order #{order_id} has been successfully placed!**\n\n" \
            f"👤 **Customer:** {update.effective_user.full_name}\n" \
            f"📧 **Email:** {state['email']}\n" \
            f"📞 **Phone:** {state.get('phone', '—')}\n" \
            f"📍 **Address:** {state['address']}\n" \
            f"💳 **Payment:** {payment}\n\n" \
            f"📦 **Products:**\n" \
            f"{products_text}" \
            f"💳 **Total amount: {total_amount}$**\n\n" \
            f"📋 **Status:** In progress\n" \
            f"🕐 **Date:** {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n" \
            f"We will contact you shortly!"
        )
        await query.edit_message_text(order_text, parse_mode="Markdown")
        keyboard = [[InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]]
        await query.message.reply_text("You can return to the main menu:", reply_markup=InlineKeyboardMarkup(keyboard))
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        self.conn.commit()
        self.user_states.pop(user_id, None)

    async def handle_checkout_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        user_id = update.effective_user.id
        self.user_states.pop(user_id, None)
        await update.callback_query.edit_message_text("❌ Order cancelled.")
        await self.show_cart(update, context)

    async def create_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, send_message=True):
        user_id = update.effective_user.id
        user_name = update.effective_user.full_name
        if user_id not in self.user_states:
            return None
        state = self.user_states[user_id]
        cursor = self.conn.cursor()
        cursor.execute('SELECT p.name, p.price, c.quantity, p.emoji FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?', (user_id,))
        cart_items = cursor.fetchall()
        target_message = update.message or (update.callback_query.message if update.callback_query else None)
        if not cart_items:
            if target_message:
                await target_message.reply_text("❌ The cart is empty!")
            self.user_states.pop(user_id, None)
            return None

        products_list = []
        total_amount = 0
        for name, price, quantity, emoji in cart_items:
            item_total = price * quantity
            total_amount += item_total
            products_list.append({'name': name, 'price': price, 'quantity': quantity, 'emoji': emoji if emoji else "", 'total': item_total})
        products_json = json.dumps(products_list, ensure_ascii=False)

        cursor.execute(
            'INSERT INTO orders (user_id, user_name, products, total_amount, email, phone, address, payment_method, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (user_id, user_name, products_json, total_amount, state.get('email'), state.get('phone'), state.get('address'), state.get('payment'), 'pending')
        )
        order_id = cursor.lastrowid
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        self.conn.commit()
        self.user_states.pop(user_id, None)

        if send_message:
            products_text = "".join(f"{item['emoji']} {item['name']} × {item['quantity']} = {item['total']}$\n" for item in products_list)
            order_text = f"""
✅ **Order #{order_id} has been successfully placed!**

👤 **Customer:** {user_name}
📧 **Email:** {state['email']}
📞 **Phone:** {state.get('phone', '—')}
📍 **Address:** {state['address']}
💳 **Payment:** {state.get('payment')}

📦 **Products:**
{products_text}
💳 **Total amount: {total_amount}$**

📋 **Status:** In progress
🕐 **Date:** {datetime.now().strftime('%d.%m.%Y %H:%M')}

We will contact you shortly!
            """
            keyboard = [[InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]]
            if target_message:
                await target_message.reply_text(order_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

        if ADMIN_ID != user_id:
            admin_products_text = "".join(f"{item['emoji']} {item['name']} × {item['quantity']} = {item['total']}$\n" for item in products_list)
            admin_text = f"""
🔔 **NEW ORDER #{order_id}**

👤 **Customer:** {user_name} (ID: {user_id})
📧 **Email:** {state['email']}
📞 **Phone:** {state.get('phone', '—')}
📍 **Address:** {state['address']}
💳 **Payment:** {state.get('payment')}

📦 **Products:**
{admin_products_text}
💳 **Total amount: {total_amount}$**"""
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.warning(f"Error sending to admin: {e}")
        return order_id

    # -------------------- USER ORDERS --------------------
    async def show_my_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
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

        cursor.execute('SELECT id, total_amount, status, created_at, products FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?', (user_id, per_page, offset))
        orders = cursor.fetchall()
        text = f"📋 Your orders (Page {page + 1}/{total_pages}):\n\n"
        keyboard = []
        status_emoji = {'pending': '🟡 In processing', 'confirmed': '🔵 Confirmed', 'shipped': '🟠 Sent', 'delivered': '🟢 Delivered', 'cancelled': '🔴 Cancelled'}
        for order in orders:
            products = json.loads(order["products"] or "[]")
            text += f"🧾 Order #{order['id']}\n"
            for product in products:
                text += f"   {product.get('emoji', '')} {product.get('name', '')} × {product.get('quantity', 0)} = {product.get('total', 0)}$\n"
            text += f"💰 {order['total_amount']}$ | {status_emoji.get(order['status'], order['status'])}\n"
            text += f"📅 {order['created_at'][:16]}\n\n"
            keyboard.append([InlineKeyboardButton(f"Details #{order['id']}", callback_data=f"order_details_{order['id']}")])

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
        query = update.callback_query
        await query.answer()
        match = re.match(r'^my_orders_page_(\d+)$', query.data)
        if match:
            page = int(match.group(1))
            await self.show_my_orders(update, context, page)

    async def show_order_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Fetch and display order details for both admin and regular users."""
        query = getattr(update, "callback_query", None)
        message = getattr(update, "message", None)
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
        cursor.execute("SELECT * FROM orders WHERE id = ?" + ("" if uid == ADMIN_ID else " AND user_id = ?"), (order_id,) if uid == ADMIN_ID else (order_id, uid))
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
        created_at = order["created_at"]
        products = json.loads(products_json)

        status_emoji = {'pending': '🟡 In processing', 'confirmed': '🔵 Confirmed', 'shipped': '🟠 Sent', 'delivered': '🟢 Delivered', 'cancelled': '🔴 Cancelled'}

        order_text = f"📋 **Order #{order_id_val}**\n\n👤 **Customer:** {user_name}\n"
        if uid == ADMIN_ID:
            order_text += f"📧 **Email:** {order['email'] or '—'}\n"
            order_text += f"📞 **Phone:** {phone}\n"
            order_text += f"💳 **Payment method:** {order['payment_method'] or '—'}\n"
        else:
             order_text += f"📞 **Phone:** {phone}\n"
        order_text += f"📍 **Address:** {address}\n\n📦 **Products:**\n"

        for product in products:
            order_text += f"{product.get('emoji', '')} {product.get('name', '')} × {product.get('quantity', 0)} = {product.get('total', 0)}$\n"
        order_text += f"\n💳 **Total amount: {total}$**\n" \
                      f"📊 **Status:** {status_emoji.get(status, status)}\n" \
                      f"🕐 **Date:** {created_at[:16]}"

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
            await query.edit_message_text(order_text, reply_markup=reply_markup, parse_mode="Markdown")
        elif message:
            await message.reply_text(order_text, reply_markup=reply_markup, parse_mode="Markdown")

    async def user_cancel_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        cursor.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        self.conn.commit()

        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"🔴 The customer canceled the order. #{order_id}", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

        await query.answer("✅ Order canceled")
        await self.show_main_menu(update, context)

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
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, user_name, total_amount, created_at, products FROM orders WHERE status = 'pending' ORDER BY created_at DESC")
        orders = cursor.fetchall()
        if not orders:
            keyboard = [[InlineKeyboardButton("🔙 Admin panel", callback_data="admin_panel")]]
            await update.callback_query.edit_message_text("📋 **No new orders**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            return

        text = "📋 **New orders:**\n\n"
        keyboard = []
        for order in orders:
            products = json.loads(order["products"] or "[]")
            product_line = ""
            if products:
                first_product = products[0]
                name = first_product.get('name', 'Unknown')
                product_line = f"| {name}"
            text += f"🧾 **#{order['id']}** {product_line}\n💰 {order['total_amount']}$ | {order['created_at'][:16]}\n\n"
            keyboard.append([InlineKeyboardButton(f"📋 Review #{order['id']}", callback_data=f"order_details_{order['id']}")])

        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

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
        cursor.execute('SELECT id, user_name, total_amount, status, created_at FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?', (per_page, offset))
        orders = cursor.fetchall()
        if not orders:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
            await query.edit_message_text("No orders on this page", reply_markup=InlineKeyboardMarkup(keyboard))
            await query.answer()
            return

        text = f"All orders (Page {page + 1}/{total_pages}):\n\n"
        keyboard = []
        status_emoji = {'pending': '🟡', 'confirmed': '🔵', 'shipped': '🟠', 'delivered': '🟢', 'cancelled': '🔴'}
        for order in orders:
            emoji_status = status_emoji.get(order["status"], '⚫')
            text += f"{emoji_status} #{order['id']} | {order['user_name']} | {order['total_amount']}$ | {order['created_at'][:16]}\n"
            keyboard.append([InlineKeyboardButton(f"Details #{order['id']}", callback_data=f"order_details_{order['id']}")])

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

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        self.conn.commit()

        status_text_map = {"confirmed": "confirmed", "shipped": "sent", "delivered": "delivered", "cancelled": "canceled"}
        await query.answer(f"✅ Order #{order_id} {status_text_map[new_status]}")

        # Notify user
        try:
            cursor.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
            user_id = cursor.fetchone()['user_id']
            status_text = {'confirmed': '🔵 Confirmed', 'shipped': '🟠 Shipped', 'delivered': '🟢 Delivered', 'cancelled': '🔴 Cancelled'}
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📦 Your order #{order_id} status has been updated\n\n" \
                     f"🆕 New status: {status_text.get(new_status, new_status)}\n\n" \
                     f"Thank you for your order ❤️"
            )
        except Exception as e:
            logger.error(f"Failed to notify user about order {order_id}: {e}")

        # Refresh the order details view for the admin
        await self.show_order_details(update, context)

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
        text = f"✏️ **Editing a product:**\n\n{product['emoji'] or ''} **{product['name']}**\nDescription: {product['description']}\nPrice: {product['price']}$\nCategory: {product['category']}\nStock: {product['stock']}"
        keyboard = [
            [InlineKeyboardButton("Title", callback_data="admin_edit_field_name"), InlineKeyboardButton("Description", callback_data="admin_edit_field_description")],
            [InlineKeyboardButton("Price", callback_data="admin_edit_field_price"), InlineKeyboardButton("Category", callback_data="admin_edit_field_category")],
            [InlineKeyboardButton("Emoji", callback_data="admin_edit_field_emoji"), InlineKeyboardButton("Stock", callback_data="admin_edit_field_stock")],
            [InlineKeyboardButton("🔙 To products", callback_data="admin_products")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_edit_field(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID or user_id not in self.user_states: return
        state = self.user_states[user_id]
        if state.get('step') != 'edit_product_field': return
        query = update.callback_query

        field_map = {
            "admin_edit_field_name": ("name", "Enter a new product name:"),
            "admin_edit_field_description": ("description", "Enter a new product description:"),
            "admin_edit_field_price": ("price", "Enter the new price of the item (as a number):"),
            "admin_edit_field_category": ("category", "Enter a new product category:"),
            "admin_edit_field_emoji": ("emoji", "Enter a new emoji for the product:"),
            "admin_edit_field_stock": ("stock", "Enter the new quantity in stock (as a number):")
        }
        if query.data not in field_map: return await query.answer("❌ Invalid request")

        field, msg = field_map[query.data]
        state['editing_field'] = field
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data=f"admin_edit_product_{state['product_id']}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

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
        keyboard = [
            [InlineKeyboardButton("❌ Yes, delete", callback_data=f"admin_delete_product_confirm_{product_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_products")]
        ]
        await query.edit_message_text(f"Are you sure you want to delete the product? **{name}**?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

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
        if not row: return await query.answer("❌ Product not found")

        name = row[0]
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        self.conn.commit()
        await query.answer(f"🗑️ Product {name} has been removed")
        await self.admin_products(update, context)

    # -------------------- TEXT HANDLERS --------------------
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in self.user_states:
            # Delegate to specific handlers based on state
            await self.handle_admin_product_input(update, context)
            await self.handle_checkout_input(update, context)
        else:
            # Fallback for messages outside of a specific flow
            await update.message.reply_text("Use /start for navigating the store! 🛍️")

    async def handle_admin_product_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID or user_id not in self.user_states: return

        state = self.user_states[user_id]
        step = state.get("step")
        text = update.message.text.strip()

        # Handle text input for adding a new product
        if step and step.startswith('add_product'):
            field_map = {
                'add_product_name': ('description', "Enter product description:"),
                'add_product_description': ('price', "Enter the product price (as a number):"),
                'add_product_price': ('emoji', "Enter an emoji for the product (e.g., 📱):"),
                'add_product_emoji': ('category', "Enter the product category:"),
                'add_product_category': ('stock', "Enter the quantity in stock (as a number):"),
                'add_product_stock': (None, "Saving product...")
            }
            current_field = step.replace('add_product_', '')
            state['product_data'][current_field] = text

            if current_field == 'price':
                try: float(text)
                except ValueError: return await update.message.reply_text("❌ The price must be a number. Please try again.")
            if current_field == 'stock':
                try: int(text)
                except ValueError: return await update.message.reply_text("❌ The stock must be an integer. Please try again.")

            next_step, prompt = field_map.get(step, (None, None))
            if next_step:
                state['step'] = f"add_product_{next_step}"
                await update.message.reply_text(prompt)
            else: # Last step, save product
                data = state['product_data']
                cursor = self.conn.cursor()
                cursor.execute(
                    "INSERT INTO products (name, description, price, emoji, category, stock) VALUES (?, ?, ?, ?, ?, ?)",
                    (data["name"], data["description"], float(data["price"]), data["emoji"], data["category"], int(data["stock"]))
                )
                self.conn.commit()
                await update.message.reply_text(f"✅ Product **{data['name']}** successfully added!")
                self.user_states.pop(user_id, None)
                # This part is tricky as we don't have a callback_query to call admin_products
                # A simple message is sent instead.
                await update.message.reply_text("You can manage products from the admin panel.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 Admin panel", callback_data="admin_panel")]]))

        # Handle text input for editing an existing product
        elif state.get('editing_field'):
            field_to_edit = state['editing_field']
            product_id = state['product_id']
            value = text

            if field_to_edit == "price":
                try: value = float(text)
                except ValueError: return await update.message.reply_text("❌ Enter a valid number for price.")
            elif field_to_edit == "stock":
                try: value = int(text)
                except ValueError: return await update.message.reply_text("❌ Enter a valid integer for stock.")

            cursor = self.conn.cursor()
            cursor.execute(f"UPDATE products SET {field_to_edit} = ? WHERE id = ?", (value, product_id))
            self.conn.commit()
            await update.message.reply_text(f"✅ Product's {field_to_edit} updated successfully!")
            self.user_states.pop(user_id, None)
            # This part is also tricky, we simulate a callback_query to refresh the view
            class FakeQuery:
                def __init__(self, data): self.data = data
            update.callback_query = FakeQuery(data=f"admin_edit_product_{product_id}")
            await self.admin_edit_product(update, context)

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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))
    application.add_handler(MessageHandler(filters.CONTACT, bot.handle_checkout_input))

    # --- CALLBACK QUERY HANDLERS ---
    application.add_handler(CallbackQueryHandler(bot.show_main_menu, pattern=r'^main_menu$'))
    application.add_handler(CallbackQueryHandler(bot.show_catalog, pattern=r'^catalog$'))
    application.add_handler(CallbackQueryHandler(bot.show_category, pattern=r'^category_'))
    application.add_handler(CallbackQueryHandler(bot.show_product, pattern=r'^product_'))
    application.add_handler(CallbackQueryHandler(bot.show_help, pattern=r'^help$'))
    application.add_handler(CallbackQueryHandler(bot.show_cart, pattern=r'^cart$'))
    application.add_handler(CallbackQueryHandler(bot.add_to_cart, pattern=r'^add_to_cart_'))
    application.add_handler(CallbackQueryHandler(bot.remove_from_cart, pattern=r'^remove_from_cart_'))
    application.add_handler(CallbackQueryHandler(bot.cart_operations, pattern=r'^cart_(add|remove)_'))
    application.add_handler(CallbackQueryHandler(bot.clear_cart, pattern=r'^clear_cart$'))
    application.add_handler(CallbackQueryHandler(bot.checkout, pattern=r'^checkout$'))
    application.add_handler(CallbackQueryHandler(bot.choose_payment, pattern=r'^pay_(cod|card|bank)$'))
    application.add_handler(CallbackQueryHandler(bot.handle_checkout_back, pattern=r'^back_to_'))
    application.add_handler(CallbackQueryHandler(bot.handle_cancel_order, pattern=r'^cancel_order$'))
    application.add_handler(CallbackQueryHandler(bot.show_my_orders, pattern=r'^my_orders$'))
    application.add_handler(CallbackQueryHandler(bot.handle_my_orders_pagination, pattern=r'^my_orders_page_\d+$'))
    application.add_handler(CallbackQueryHandler(bot.show_order_details, pattern=r'^order_details_'))
    application.add_handler(CallbackQueryHandler(bot.user_cancel_order, pattern=r'^user_cancel_'))
    application.add_handler(CallbackQueryHandler(bot.admin_panel, pattern=r'^admin_panel$'))
    application.add_handler(CallbackQueryHandler(bot.admin_orders, pattern=r'^admin_orders$'))
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

    # --- ERROR HANDLER ---
    application.add_error_handler(bot.error_handler)

    print("🛍️ Online store bot launched!")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print("Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()