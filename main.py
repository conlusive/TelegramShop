import logging
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from dom import BOT_TOKEN, ADMIN_ID, BOT_TIMEZONE, SHIPPING_MODE, PORTMONE_TOKEN
from telegram import LabeledPrice
from telegram.ext import PreCheckoutQueryHandler


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
    def _add_column_if_not_exists(self, cursor, table_name: str, column_name: str, column_type: str):
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]
        if column_name not in columns:
            try:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                print(f"✅ Column '{column_name}' added to table '{table_name}'!")
            except Exception as e:
                print(f"⚠️ Error adding column {column_name} to {table_name}: {e}")

    def init_database(self):
        self.conn = sqlite3.connect('shop.db', check_same_thread=False)
        cursor = self.conn.cursor()

        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS products
                       (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT,
                        price REAL NOT NULL, image_url TEXT, category TEXT, stock INTEGER DEFAULT 0,
                        emoji TEXT, variants TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
                       ''')

        self._add_column_if_not_exists(cursor, "products", "emoji", "TEXT")
        self._add_column_if_not_exists(cursor, "products", "image_url", "TEXT")
        self._add_column_if_not_exists(cursor, "products", "variants", "TEXT")

        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS orders
                       (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, user_name TEXT,
                        full_name TEXT, products TEXT NOT NULL, total_amount REAL NOT NULL,
                        phone TEXT, address TEXT, payment_method TEXT, email TEXT,
                        status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
                       ''')

        self._add_column_if_not_exists(cursor, "orders", "payment_method", "TEXT")
        self._add_column_if_not_exists(cursor, "orders", "email", "TEXT")
        self._add_column_if_not_exists(cursor, "orders", "full_name", "TEXT")

        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS users
                       (user_id INTEGER PRIMARY KEY, phone TEXT, address TEXT, email TEXT,
                        full_name TEXT, blocked INTEGER DEFAULT 0)
                       ''')

        self._add_column_if_not_exists(cursor, "users", "email", "TEXT")
        self._add_column_if_not_exists(cursor, "users", "full_name", "TEXT")
        self._add_column_if_not_exists(cursor, "users", "blocked", "INTEGER DEFAULT 0")

        self.conn.commit()

    def escape_html(self, text):
        if not text: return ""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def generate_receipt(self, order_id, user_name, email, phone, address, payment, products_list, total, date, receipt_format='html'):
        # Автоматичне визначення заголовка адреси на основі SHIPPING_MODE
        if SHIPPING_MODE == 'UKRAINE':
            shipping_label = "📍 Shipping (City/Branch):"
        else:
            shipping_label = "📍 Shipping (City/ZIP):"

        if receipt_format == 'html':
            bold_start, bold_end = "<b>", "</b>"
            escaper = self.escape_html
            product_line_format = "▫️ {emoji} {name}{opts}\n   {quantity} x {price}$ = <b>{total}$</b>\n"
        else:
            bold_start, bold_end = "**", "**"
            escaper = self.escape_md
            product_line_format = "{emoji} {name}{opts}\n   {quantity} x {price}$ = {total}$\n"

        products_text = ""
        for item in products_list:
            opts_str = ""
            if item.get('selected_options'):
                # Відображаємо тільки значення обраних варіантів
                opts_vals = [f"{v}" for k, v in item['selected_options'].items()]
                opts_str = f" ({', '.join(opts_vals)})"

            products_text += product_line_format.format(
                emoji=item.get('emoji', '📦'),
                name=escaper(item.get('name', 'Product')),
                opts=escaper(opts_str),
                quantity=item.get('quantity', 1),
                price=item.get('price', 0),
                total=item.get('total', 0)
            )

        # Формуємо фінальний текст із подвійними відступами
        return (
            f"✅ {bold_start}Order #{order_id} has been successfully placed!{bold_end}\n\n"
            f"👤 {bold_start}Customer:{bold_end} {escaper(user_name)}\n\n"
            f"📧 {bold_start}Email:{bold_end} {escaper(email)}\n\n"
            f"📞 {bold_start}Phone:{bold_end} {escaper(str(phone))}\n\n"
            f"{bold_start}{shipping_label}{bold_end}\n{escaper(address)}\n\n"
            f"💳 {bold_start}Payment Method:{bold_end} {payment}\n\n"
            f"📦 {bold_start}Products:{bold_end}\n{products_text}\n"
            f"💰 {bold_start}Total Amount: {total}${bold_end}\n\n"
            f"📋 {bold_start}Status:{bold_end} In progress\n\n"
            f"🕐 {bold_start}Date:{bold_end} {date}\n\n"
            f"Thank you for shopping with us! 🛍️"
        )


    def escape_md(self, text):

            if not text: return ""

            return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

    def calculate_item_price(self, base_price, variants_json, selected_options_json):

        final_price = base_price

        if not variants_json or not selected_options_json:
            return float(final_price)

        try:
            variants_data = json.loads(variants_json)
            selected_opts = json.loads(selected_options_json)

            for key, val in selected_opts.items():
                if key in variants_data:
                    group = variants_data[key]

                    if isinstance(group, dict) and val in group:
                        option_data = group[val]
                        if isinstance(option_data, dict) and 'price' in option_data:

                            final_price = float(option_data['price'])
        except Exception as e:
            print(f"Error calculating price: {e}")

        return float(final_price)

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

        try:

            if isinstance(date_input, str):

                if "." in date_input:
                    date_input = date_input.split(".")[0]
                dt = datetime.strptime(date_input, "%Y-%m-%d %H:%M:%S")
            else:
                dt = date_input


            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))

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


    def restore_stock(self, order_id):

        cursor = self.conn.cursor()
        cursor.execute("SELECT products FROM orders WHERE id = ?", (order_id,))
        result = cursor.fetchone()

        if result and result[0]:
            try:
                products = json.loads(result[0])
            except:
                return

            for item in products:

                if isinstance(item, str): continue

                product_id = item.get('product_id')
                quantity = item.get('quantity')
                sel_opts = item.get('selected_options', {})

                if not product_id and 'name' in item:
                    cursor.execute("SELECT id FROM products WHERE name = ?", (item['name'],))
                    res = cursor.fetchone()
                    if res: product_id = res[0]

                if product_id and quantity:

                    cursor.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (quantity, product_id))

                    if sel_opts:
                        cursor.execute("SELECT variants FROM products WHERE id = ?", (product_id,))
                        row = cursor.fetchone()
                        if row and row[0]:
                            try:
                                variants_data = json.loads(row[0])
                                changed = False
                                for key, val in sel_opts.items():
                                    if key in variants_data:
                                        group = variants_data[key]

                                        # Варіант 1: Складний
                                        if isinstance(group, dict) and val in group:
                                            target = group[val]
                                            if isinstance(target, dict) and 'qty' in target:
                                                target['qty'] += quantity
                                                changed = True
                                            elif isinstance(target, int):
                                                group[val] += quantity
                                                changed = True

                                if changed:
                                    new_json = json.dumps(variants_data, ensure_ascii=False)
                                    cursor.execute("UPDATE products SET variants = ? WHERE id = ?",
                                                   (new_json, product_id))
                            except Exception as e:
                                print(f"Restore variants error: {e}")

            self.conn.commit()
            logger.info(f"Stock restored for order #{order_id}")


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


        self.restore_stock(order_id)

        cursor.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        self.conn.commit()

        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"🔴 The customer canceled the order. #{order_id}",
                                           parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

        await query.answer("✅ Order canceled")


        await self.show_my_orders(update, context)

    # -------------------- KEYBOARD BUILDER --------------------
    def build_main_keyboard(self, user_id):
        """
        Build the main menu keyboard.
        If Admin: Shows a simplified layout with Admin Panel on top.
        If User: Shows the standard customer layout with cart count.
        """

        try:

            cursor = self.conn.cursor()
            cursor.execute("SELECT SUM(quantity) FROM cart WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            cart_count = result[0] if result and result[0] else 0
        except Exception as e:
            print(f"Error counting cart: {e}")
            cart_count = 0

        cart_text = f"🛒 My cart ({cart_count})" if cart_count > 0 else "🛒 My cart"

        if int(user_id) == int(ADMIN_ID):
            keyboard = [
                [InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")],
                [InlineKeyboardButton("🛍️ Product catalog", callback_data="catalog")],
                [
                    InlineKeyboardButton(cart_text, callback_data="cart"),
                    InlineKeyboardButton("👤 My profile", callback_data="my_profile")
                ]
            ]
            return InlineKeyboardMarkup(keyboard)

        keyboard = [
            [InlineKeyboardButton("🛍️ Product catalog", callback_data="catalog")],
            [InlineKeyboardButton(cart_text, callback_data="cart")],  # <-- ОНОВЛЕНА КНОПКА
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
        safe_name = self.escape_md(user.first_name)

        missing = self.get_profile_completion_status(user_id)
        registration_promo = ""

        if missing and int(user_id) != int(ADMIN_ID):
            # Оновлюємо словник для підтримки 4-х полів
            promo_messages = {
                4: "✨ **Welcome!**\nComplete your profile setup once for instant checkouts! 🚀",
                3: "✨ **Unlock the full experience!**\nComplete your profile details to enjoy one-click orders later. 🚀",
                2: "🔄 **Speed up your shopping!**\nJust two small details left to make your next order instant.",
                1: "🎯 **Almost a Pro!**\nAdd your last detail to finish your setup and save time on every order.",
            }

            missing_labels = []
            if "full_name" in missing: missing_labels.append("👤 Name")
            if "email" in missing: missing_labels.append("📧 Email")

            if "address" in missing:
                # Динамічна мітка адреси залежно від регіону
                label = "📍 Shipping (City/Branch)" if SHIPPING_MODE == 'UKRAINE' else "📍 Shipping (City/ZIP)"
                missing_labels.append(label)

            if "phone" in missing: missing_labels.append("📞 Phone")

            registration_promo = f"\n{promo_messages.get(len(missing), '')}\n"
            registration_promo += f"Missing: *{', '.join(missing_labels)}*\n"
            registration_promo += "────────────────────\n"

        # ... (решта коду welcome_text та відправки повідомлення залишається без змін)
        if int(user_id) == int(ADMIN_ID):
            welcome_text = (
                f"👑 **Admin Dashboard**\n\n"
                f"Welcome back, **{safe_name}**! ⚡️\n"
                f"Everything is under control. What's the plan for today?\n\n"
                f"👇 **Control Center:**"
            )
        else:
            welcome_text = (
                f"👋 **Hi, {safe_name}! Glad to see you!**\n"
                f"{registration_promo}"
                f"Discover our latest deals and premium products! 💎\n\n"
                f"🚀 **What's inside:**\n"
                f"• 🛍️ **Catalog** — Browse and find your favorites\n"
                f"• 🛒 **Cart** — Review and manage your picks\n"
                f"• 📋 **My Orders** — Track your delivery status\n"
                f"• 👤 **Profile** — Manage your fast-checkout data\n\n"
                f"👇 **Where should we start?**"
            )

        reply_markup = self.build_main_keyboard(user_id)

        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup,
                                                              parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
        elif update.message:
            await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return

        # Визначаємо умови залежно від регіону
        if SHIPPING_MODE == 'UKRAINE':
            delivery_info = (
                "• Kyiv: 100₴\n"
                "• Ukraine: 150₴\n"
                "• Free delivery for orders over 1000₴"
            )
            payment_info = (
                "• Cash on delivery\n"
                "• Card payment to courier\n"
                "• Bank transfer"
            )
        else:
            delivery_info = (
                "• Worldwide International Shipping\n"
                "• Carriers: DHL / FedEx / UPS\n"
                "• Rates calculated after order placement"
            )
            payment_info = (
                "• Bank transfer (Full Prepayment required)"
            )

        text = (
            "ℹ️ **HELP & INFORMATION**\n\n"
            "🛍️ **How to shop:**\n"
            "1. Browse our **Catalog**\n"
            "2. Select product and options (Size/Color)\n"
            "3. Add items to **Cart**\n"
            "4. Complete checkout (4 steps)\n\n"
            "📞 **Contact Support:**\n"
            "• Email: shop@example.com\n"
            "• Mon-Fri: 9:00 AM - 6:00 PM\n\n"
            f"🚚 **Delivery ({SHIPPING_MODE}):**\n"
            f"{delivery_info}\n\n"
            "💳 **Payment:**\n"
            f"{payment_info}\n\n"
            "❓ **Questions?**\n"
            "Feel free to message our support for any assistance!"
        )

        keyboard = [[InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]]

        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def show_catalog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query


        page = 1
        if query.data.startswith("catalog_page_"):
            try:
                page = int(query.data.split("_")[-1])
            except:
                page = 1

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT category) FROM products")
        total_items = cursor.fetchone()[0]

        if total_items == 0:
            try:
                await query.edit_message_text("📂 <b>Catalog is empty!</b>", reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]), parse_mode="HTML")
            except:
                pass
            return

        CATS_PER_PAGE = 5
        total_pages = (total_items + CATS_PER_PAGE - 1) // CATS_PER_PAGE
        if page > total_pages: page = total_pages
        if page < 1: page = 1

        offset = (page - 1) * CATS_PER_PAGE

        cursor.execute("SELECT DISTINCT category FROM products ORDER BY category ASC LIMIT ? OFFSET ?",
                       (CATS_PER_PAGE, offset))
        categories = cursor.fetchall()

        text = f"📂 <b>Product Catalog</b>"
        if total_pages > 1: text += f" (Page {page}/{total_pages})"
        text += "\n\nSelect a category 👇"

        keyboard = []
        for (cat_name,) in categories:
            cursor.execute("SELECT emoji FROM products WHERE category = ? LIMIT 1", (cat_name,))
            res = cursor.fetchone()
            emo = res[0] if res and res[0] else "📂"

            keyboard.append([InlineKeyboardButton(f"{emo} {cat_name}", callback_data=f"category_{cat_name}_1_{page}")])

        nav = []
        if page > 1: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"catalog_page_{page - 1}"))
        if page < total_pages: nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"catalog_page_{page + 1}"))
        if nav: keyboard.append(nav)

        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])


        try:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except:

            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    async def show_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id_override=None):
        query = update.callback_query
        user_id = update.effective_user.id

        if product_id_override:
            product_id = product_id_override
            state = self.user_states.get(user_id, {})
            prod_page = state.get('prod_page', 1)
            cat_page = state.get('cat_page', 1)
        else:
            parts = query.data.split('_')
            try:
                if parts[0] == 'product':
                    product_id, prod_page, cat_page = int(parts[1]), int(parts[2]), int(parts[3])
                elif parts[0] == 'prod':
                    product_id, prod_page, cat_page = int(parts[2]), int(parts[3]), int(parts[4])
                else:
                    product_id, prod_page, cat_page = int(parts[1]), 1, 1
            except:
                product_id, prod_page, cat_page = int(parts[1]), 1, 1

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if not product: return

        stock = product['stock']
        stock_status = "✅ <b>In Stock</b>" if stock > 5 else (
            f"⚠️ <b>Low Stock</b> ({stock})" if stock > 0 else "❌ <b>Out of Stock</b>")

        variants_display = ""
        base_price = product['price']
        display_price = f"{base_price}$"

        if product['variants']:
            try:
                v_data = json.loads(product['variants'])

                for v_type, options in v_data.items():
                    opt_list = list(options.keys()) if isinstance(options, dict) else options
                    variants_display += f"\n🔹 <b>{v_type}:</b> {', '.join(map(str, opt_list))}"

                all_prices = []
                for v_type, options in v_data.items():
                    if isinstance(options, dict):
                        for opt, info in options.items():

                            if isinstance(info, dict) and 'price' in info:
                                all_prices.append(float(info['price']))
                            else:
                                all_prices.append(float(base_price))

                if all_prices:
                    min_p = min(all_prices)
                    max_p = max(all_prices)
                    if min_p != max_p:
                        display_price = f"from {min_p}$"
                    else:
                        display_price = f"{min_p}$"

            except Exception as e:
                print(f"Price calc error: {e}")

        cursor.execute("SELECT SUM(quantity) FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        in_cart = cursor.fetchone()[0] or 0

        text = (
            f"{product['emoji'] or '📦'} <b>{self.escape_html(product['name'])}</b>\n\n"
            f"{self.escape_html(product['description'] or 'No description.')}\n"
            f"{variants_display}\n\n"
            f"💰 Price: <b>{display_price}</b>\n"
            f"📦 Status: {stock_status}\n"
            f"🛒 In Cart: <b>{in_cart}</b>"
        )

        keyboard = []
        keyboard.append([
            InlineKeyboardButton("➖", callback_data=f"prod_minus_{product_id}_{prod_page}_{cat_page}"),
            InlineKeyboardButton("➕", callback_data=f"prod_plus_{product_id}_{prod_page}_{cat_page}")
        ])

        cart_btn_text = f"🛒 Cart ({in_cart})" if in_cart > 0 else "🛒 Cart"
        keyboard.append([
            InlineKeyboardButton(cart_btn_text, callback_data="cart"),
            InlineKeyboardButton("🔙 Back", callback_data=f"category_{product['category']}_{prod_page}_{cat_page}")
        ])

        try:
            if product['image_url']:
                await query.message.delete()
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=product['image_url'], caption=text,
                                             reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            else:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except:
            pass

    async def handle_add_to_cart_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        try:
            product_id = int(query.data.replace("add_to_cart_", ""))
        except:
            await query.answer("❌ Error")
            return

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

        if not variants_data:
            await self.add_item_to_cart_db(update, context, product_id, None)
            return

        priority_keys = ["color", "colour", "колір", "цвєт", "size", "розмір", "размер"]

        def sort_key(k):
            k_lower = k.lower()
            for i, pk in enumerate(priority_keys):
                if pk in k_lower:
                    return i
            return 999

        sorted_keys = sorted(variants_data.keys(), key=sort_key)

        self.user_states[user_id] = {
            'step': 'selecting_variant',
            'product_id': product_id,
            'variant_keys': sorted_keys,
            'current_key_index': 0,
            'variants_data': variants_data,
            'selected_options': {}
        }

        await self.ask_next_variant(update, context)

    async def show_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()

        name = user_data[0] if user_data and user_data[0] else "Not set"
        email = user_data[1] if user_data and user_data[1] else "Not set"
        address = user_data[2] if user_data and user_data[2] else "Not set"
        phone = user_data[3] if user_data and user_data[3] else "Not set"

        shipping_label = "Shipping (City/Branch):" if SHIPPING_MODE == 'UKRAINE' else "Shipping (City/ZIP):"

        text = (
            "👤 <b>My Profile</b>\n\n"
            f"<b>Name:</b> {self.escape_html(name)}\n\n"
            f"<b>Email:</b> {self.escape_html(email)}\n\n"
            f"<b>{shipping_label}</b>\n{self.escape_html(address)}\n\n"
            f"<b>Phone:</b> {self.escape_html(phone)}"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit Name", callback_data="edit_full_name")],
            [InlineKeyboardButton("✏️ Edit Email", callback_data="edit_email")],
            [InlineKeyboardButton("✏️ Edit Shipping Info", callback_data="edit_address")],
            [InlineKeyboardButton("✏️ Edit Phone", callback_data="edit_phone")],
            [InlineKeyboardButton("🗑️ Delete Data", callback_data="profile_delete_menu")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ])

        # ВИПРАВЛЕНО: Якщо викликано текстом, надсилаємо НОВЕ повідомлення
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=keyboard, parse_mode="HTML")

    def get_profile_completion_status(self, user_id):
        cursor = self.conn.cursor()
        # Додаємо full_name у вибірку
        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if not row:
            return ["full_name", "email", "address", "phone"]

        full_name, email, address, phone = row
        missing_fields = []

        # Перевіряємо всі 4 обов'язкові поля
        if not full_name: missing_fields.append("full_name")
        if not email: missing_fields.append("email")
        if not address: missing_fields.append("address")
        if not phone: missing_fields.append("phone")

        return missing_fields



    async def handle_delete_profile_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        data = query.data
        user_id = query.from_user.id

        # Розширений список полів для видалення
        field_map = {
            "delete_profile_full_name": ("full_name", "Full Name"),
            "delete_profile_phone": ("phone", "Phone number"),
            "delete_profile_address": ("address", "Address"),
            "delete_profile_email": ("email", "Email")
        }

        if data not in field_map:
            await query.answer("Invalid action")
            return

        db_field, display_name = field_map[data]

        # Оновлення бази даних
        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE users SET {db_field} = NULL WHERE user_id = ?", (user_id,))
        self.conn.commit()

        await query.answer(f"✅ {display_name} deleted!")

        # ПЕРЕВАЖЛИВО: Оновлюємо меню видалення, щоб кнопка зникла
        await self.profile_delete_menu(update, context)

    async def _edit_user_profile_attribute(self, update: Update, context: ContextTypes.DEFAULT_TYPE, field: str,
                                           prompt: str):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id

        self.user_states[user_id] = {
            'step': f'waiting_{field}_profile',
            'msg_id': query.message.message_id
        }

        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="my_profile")]]

        # Використовуємо HTML для підтримки жирного тексту та курсиву
        await query.edit_message_text(
            prompt,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def edit_full_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._edit_user_profile_attribute(
            update, context, "full_name",
            "👤 <b>Enter your Full Name:</b>\n\n"
            "<b>Example:</b> <i>John Doe</i>"
        )

    async def edit_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._edit_user_profile_attribute(
            update, context, "email",
            "📧 <b>Enter your email:</b>\n\n"
            "<b>Example:</b> <i>user@example.com</i>"
        )

    async def edit_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if SHIPPING_MODE == 'UKRAINE':
            prompt = "📍 <b>Enter City, Delivery Service, and Branch #:</b>\n\n<b>Example:</b> <i>Kyiv, Nova Poshta #15</i>"
        else:
            prompt = "📍 <b>Enter Full Address:</b>\n\n<b>Format:</b> Country, City, Street/House, ZIP\n<b>Example:</b> <i>Germany, Berlin, Hauptstraße 10, 10115</i>"
        await self._edit_user_profile_attribute(update, context, "address", prompt)

    async def edit_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        example = "+380501234567" if SHIPPING_MODE == 'UKRAINE' else "+1234567890"
        await self._edit_user_profile_attribute(
            update, context, "phone",
            f"📞 <b>Enter your phone number:</b>\n\n"
            f"<b>Example:</b> <i>{example}</i>"
        )

    # -------------------- CART LOGIC --------------------
    async def show_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        cursor = self.conn.cursor()

        cursor.execute(
            'SELECT c.id, p.name, p.price, c.quantity, p.emoji, c.selected_options, p.variants, p.id '
            'FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
            (user_id,)
        )
        cart_items = cursor.fetchall()

        if not cart_items:
            text = (
                "🛒 **Your cart is empty!**\n\n"
                "Looks like you haven't added anything yet.\n"
                "Check out our catalog to find something cool! 👇"
            )
            keyboard = [
                [InlineKeyboardButton("📂 Go to Catalog", callback_data="catalog")],
                [InlineKeyboardButton("📜 My Orders", callback_data="my_orders")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
            ]

            if query.message.photo:
                await query.message.delete()
                await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                               reply_markup=InlineKeyboardMarkup(keyboard),
                                               parse_mode=ParseMode.MARKDOWN)
            else:
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                              parse_mode=ParseMode.MARKDOWN)
            return

        total_amount = 0
        text = "🛒 **Your Cart:**\n\n"
        keyboard = []

        for row in cart_items:
            cart_id, name, base_price, quantity, emoji, opts_json, variants_json, product_id = row

            real_price = base_price
            try:
                if variants_json and opts_json:
                    v_data = json.loads(variants_json)
                    opts = json.loads(opts_json)
                    for k, v in opts.items():
                        if k in v_data and isinstance(v_data[k], dict) and v in v_data[k]:
                            info = v_data[k][v]
                            if isinstance(info, dict) and 'price' in info:
                                real_price = info['price']
            except:
                pass

            item_total = real_price * quantity
            total_amount += item_total

            opts_str = ""
            if opts_json:
                try:
                    opts = json.loads(opts_json)
                    opts_vals = [f"{v}" for k, v in opts.items()]
                    opts_str = f" ({', '.join(opts_vals)})"
                except:
                    pass

            emo = emoji if emoji else "📦"
            text += f"{emo} **{name}**{opts_str}\n"
            text += f"   {quantity} x {real_price}$ = {item_total}$\n"

            btn_text = f"{name} ({quantity})"
            row_btns = [
                InlineKeyboardButton("➖", callback_data=f"cart_minus_{cart_id}"),
                InlineKeyboardButton(btn_text, callback_data=f"product_{product_id}"),
                InlineKeyboardButton("➕", callback_data=f"cart_plus_{cart_id}")
            ]
            keyboard.append(row_btns)

        text += f"\n💰 **Total: {total_amount}$**"

        keyboard.append([InlineKeyboardButton("✅ Checkout", callback_data="checkout")])

        keyboard.append([
            InlineKeyboardButton("🗑 Clear Cart", callback_data="clear_cart"),
            InlineKeyboardButton("📂 Back to Catalog", callback_data="catalog")
        ])

        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

        if query.message.photo:
            await query.message.delete()
            await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        else:
            try:
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                              parse_mode=ParseMode.MARKDOWN)
            except:
                pass

    async def show_single_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        try:
            product_id = int(query.data.split("_")[-1])
        except:
            return

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()

        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

        if not product:
            await query.answer("❌ Product not found")
            return

        emo = product['emoji'] if product['emoji'] else "📦"

        text = (
            f"{emo} **{product['name']}**\n\n"
            f"📝 {product['description']}\n\n"
            f"💰 Price: **{product['price']}$**\n"
            f"📂 Category: {product['category']}"
        )

        keyboard = [
            [InlineKeyboardButton("🛒 Add One More", callback_data=f"add_to_cart_options_{product['id']}")],
            [InlineKeyboardButton("🔙 Back to Cart", callback_data="my_cart")]
        ]

        try:
            await query.message.delete()
        except:
            pass

        if product['image_url']:
            try:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=product['image_url'],
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:

                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text + "\n⚠️ (Image unavailable)",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    async def handle_cart_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data

        try:
            action, cart_id_str = data.rsplit("_", 1)
            cart_id = int(cart_id_str)
        except:
            return

        cursor = self.conn.cursor()

        cursor.execute("""
                       SELECT c.quantity, c.selected_options, p.stock, p.variants
                       FROM cart c
                                JOIN products p ON c.product_id = p.id
                       WHERE c.id = ?
                       """, (cart_id,))

        row = cursor.fetchone()

        if not row:

            await self.show_cart(update, context)
            return

        current_qty, opts_json, product_stock, variants_json = row

        max_stock = product_stock

        if variants_json and opts_json:
            try:
                v_data = json.loads(variants_json)
                opts = json.loads(opts_json)

                for k, v in opts.items():
                    if k in v_data and isinstance(v_data[k], dict) and v in v_data[k]:
                        info = v_data[k][v]
                        if isinstance(info, dict) and 'qty' in info:

                            max_stock = info['qty']
                            break
            except:
                pass

        if "plus" in action:

            if current_qty < max_stock:
                new_qty = current_qty + 1
                cursor.execute("UPDATE cart SET quantity = ? WHERE id = ?", (new_qty, cart_id))
                self.conn.commit()

                await self.show_cart(update, context)
            else:

                await query.answer(f"❌ Only {max_stock} items left in stock!", show_alert=True)
                return

        elif "minus" in action:
            new_qty = current_qty - 1
            if new_qty > 0:
                cursor.execute("UPDATE cart SET quantity = ? WHERE id = ?", (new_qty, cart_id))
            else:
                cursor.execute("DELETE FROM cart WHERE id = ?", (cart_id,))

            self.conn.commit()
            await self.show_cart(update, context)

    async def handle_product_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        data = query.data

        try:
            parts = data.split("_")


            action_type = parts[1]
            product_id = int(parts[2])

            if len(parts) >= 5:
                prod_page = int(parts[3])
                cat_page = int(parts[4])
                if user_id not in self.user_states: self.user_states[user_id] = {}
                self.user_states[user_id]['prod_page'] = prod_page
                self.user_states[user_id]['cat_page'] = cat_page

        except Exception:
            await query.answer("❌ Error parsing data")
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT stock, variants FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row: return await query.answer("Product not found")

        stock, variants_json = row
        has_variants = False
        if variants_json:
            try:
                if json.loads(variants_json): has_variants = True
            except:
                pass

        if action_type == "plus":
            if has_variants:
                await self.start_variant_selection(update, context, product_id)
                return

            cursor.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
            res = cursor.fetchone()
            current_qty = res[0] if res else 0

            if current_qty < stock:
                if current_qty == 0:
                    cursor.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, 1)",
                                   (user_id, product_id))
                else:
                    cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?",
                                   (user_id, product_id))
            else:
                await query.answer(f"❌ Only {stock} left!", show_alert=True)
                return

        elif action_type == "minus":
            cursor.execute("SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ? LIMIT 1",
                           (user_id, product_id))
            target = cursor.fetchone()
            if target:
                cart_id, qty = target
                if qty > 1:
                    cursor.execute("UPDATE cart SET quantity = quantity - 1 WHERE id = ?", (cart_id,))
                else:
                    cursor.execute("DELETE FROM cart WHERE id = ?", (cart_id,))
            else:
                await query.answer("Cart empty")
                return

        self.conn.commit()


        await self.show_product(update, context, product_id_override=product_id)


    async def start_variant_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id):
            user_id = update.effective_user.id

            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute("SELECT variants, name FROM products WHERE id = ?", (product_id,))
            row = cursor.fetchone()

            if not row or not row['variants']:

                await self.add_item_to_cart_db(update, context, product_id, None)
                return

            try:
                variants_data = json.loads(row['variants'])
            except:
                await self.add_item_to_cart_db(update, context, product_id, None)
                return


            priority_keys = ["color", "colour", "колір", "цвєт", "size", "розмір", "размер"]

            def sort_key(k):
                k_lower = k.lower()
                if k_lower in priority_keys:
                    return priority_keys.index(k_lower)
                return 999
            sorted_keys = sorted(variants_data.keys(), key=sort_key)

            self.user_states[user_id] = {
                'step': 'selecting_variant',
                'product_id': product_id,
                'variant_keys': sorted_keys,
                'current_key_index': 0,
                'variants_data': variants_data,
                'selected_options': {}
            }

            await self.ask_next_variant(update, context)

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

            variants = json.loads(product['variants']) if product['variants'] else {}

            if not variants:

                await self.add_item_to_cart_db(update, context, product_id, {})
            else:

                variant_keys = list(variants.keys())

                self.user_states[user_id] = {
                    'step': 'selecting_variant',
                    'product_id': product_id,
                    'variants_data': variants,
                    'variant_keys': variant_keys,
                    'current_key_index': 0,
                    'selected_options': {}
                }

                await self.ask_next_variant(update, context)

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

        if idx >= len(keys):
            await self.add_item_to_cart_db(update, context, state['product_id'], state['selected_options'])
            self.user_states.pop(user_id, None)
            return

        current_key = keys[idx]

        options_data = state['variants_data'].get(current_key, {})

        keyboard = []
        row = []

        if isinstance(options_data, dict):
            sorted_items = sorted(options_data.items(), key=lambda x: x[0])

            for opt, val in sorted_items:
                quantity = 0
                price_info = ""

                if isinstance(val, dict):

                    quantity = val.get('qty', 0)
                    if 'price' in val:
                        price_info = f" {val['price']}$"
                else:

                    try:
                        quantity = int(val)
                    except:
                        quantity = 0

                btn_text = f"{opt}{price_info}"


                if quantity > 0:
                    row.append(InlineKeyboardButton(btn_text, callback_data=f"var_sel_{idx}_{opt}"))
                else:
                    row.append(InlineKeyboardButton(f"{opt} (❌)", callback_data="noop"))

        elif isinstance(options_data, list):

            for opt in options_data:
                row.append(InlineKeyboardButton(str(opt), callback_data=f"var_sel_{idx}_{opt}"))

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

        if data == "cancel_selection":
            state = self.user_states.get(user_id)

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

        try:
            parts = data.split("_")

            idx = int(parts[2])
            value = "_".join(parts[3:])
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

        key = state['variant_keys'][idx]
        state['selected_options'][key] = value
        state['current_key_index'] += 1

        await self.ask_next_variant(update, context)

    async def handle_cart_actions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        user_id = update.effective_user.id
        parts = data.split("_")
        action = parts[2]
        cart_id = parts[3]
        cursor = self.conn.cursor()
        cursor.execute('''
                       SELECT c.quantity, c.selected_options, p.stock, p.variants, p.id
                       FROM cart c
                                JOIN products p ON c.product_id = p.id
                       WHERE c.id = ?
                       ''', (cart_id,))
        row = cursor.fetchone()

        if not row:
            await self.show_cart(update, context)
            return

        current_qty = row[0]
        opts_json = row[1]
        real_stock = row[2]
        variants_json = row[3]

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

        if action == "plus":
            if current_qty + 1 > limit:
                await query.answer(f"❌ Only {limit} items available!", show_alert=True)
                return

            cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE id = ?", (cart_id,))

        elif action == "minus":
            if current_qty > 1:
                cursor.execute("UPDATE cart SET quantity = quantity - 1 WHERE id = ?", (cart_id,))
            else:

                cursor.execute("DELETE FROM cart WHERE id = ?", (cart_id,))

        self.conn.commit()
        await self.show_cart(update, context)

    async def add_item_to_cart_db(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id, options):
        user_id = update.effective_user.id

        options_json = json.dumps(options, ensure_ascii=False, sort_keys=True) if options else None

        cursor = self.conn.cursor()
        cursor.execute("SELECT stock, variants FROM products WHERE id = ?", (product_id,))
        prod_row = cursor.fetchone()


        if not prod_row:
            if update.callback_query: await update.callback_query.answer("❌ Error: Product not found")
            return

        real_stock = prod_row[0]
        variants_json = prod_row[1]

        limit = real_stock

        if options and variants_json:
            try:
                variants_data = json.loads(variants_json)
                for key, val in options.items():
                    if key in variants_data:
                        v_data = variants_data[key]


                        if isinstance(v_data, dict):
                            if val in v_data:
                                specific_val = v_data[val]
                                if isinstance(specific_val, dict):
                                    limit = specific_val.get('qty', 0)
                                else:
                                    limit = int(specific_val)

            except Exception as e:
                print(f"Limit calc error: {e}")

                limit = real_stock


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

        if current_in_cart + 1 > limit:
            if update.callback_query:
                await update.callback_query.answer(f"❌ Limit reached! Only {limit} available.", show_alert=True)

            await self.show_product(update, context, product_id_override=product_id)
            return

        if cart_row:
            cart_id = cart_row[0]
            cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE id = ?", (cart_id,))
        else:
            cursor.execute("INSERT INTO cart (user_id, product_id, quantity, selected_options) VALUES (?, ?, 1, ?)",
                           (user_id, product_id, options_json))

        self.conn.commit()

        if update.callback_query:
            await update.callback_query.answer("✅ Added to cart!", show_alert=False)

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

            control_row = []

            if cart_qty > 0:
                control_row.append(InlineKeyboardButton("➖ Remove", callback_data=f"remove_from_cart_{product_id}"))

            if add_btn:
                control_row.append(add_btn)

            if control_row:
                keyboard.append(control_row)

            keyboard.append([InlineKeyboardButton(f"🛒 Go to Cart ({cart_qty})", callback_data="cart")])
            keyboard.append([InlineKeyboardButton(f"🔙 Back to {product['category']}",
                                                  callback_data=f"category_{product['category']}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            if query.message.photo:
                try:
                    await query.edit_message_caption(caption=text, reply_markup=reply_markup,
                                                     parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
            else:

                try:
                    await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass

    # -------------------- CHECKOUT LOGIC --------------------
    async def checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id

        cursor = self.conn.cursor()
        cursor.execute("SELECT product_id FROM cart WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            await query.answer("Your cart is empty")
            return

        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()
        has_data = user_data and any(user_data)

        self.user_states[user_id] = {'step': 'waiting_full_name'}

        keyboard = []
        if has_data:
            keyboard.append([InlineKeyboardButton("👤 Use my Profile Data", callback_data="use_profile_data")])
        keyboard.append([InlineKeyboardButton("🔙 Back to Cart", callback_data="cart")])
        keyboard.append([InlineKeyboardButton("❌ Cancel Order", callback_data="cancel_order")])

        text = (
            f"📋 <b>Step 1/4: Full Name</b>\n\n"
            "Please enter the recipient's full name:\n\n"
            "<b>Example:</b> <i>John Doe</i>"
        )

        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        self.user_states[user_id]['msg_id'] = query.message.message_id

    async def _request_missing_checkout_data(self, update, context, state, chat_id):
        phone = state.get('phone')
        address = state.get('address')
        email = state.get('email')


        if not email:
            state['step'] = 'waiting_email_checkout_flow'
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]]

            loaded_info = []
            if phone: loaded_info.append(f"📞 Phone: {self.escape_html(phone)}")
            if address: loaded_info.append(f"📍 Address: {self.escape_html(address)}")

            text = (
                f"📋 <b>Placing an order</b>\n\n"
                f"✅ <b>Loaded from profile:</b>\n" + "\n".join(loaded_info) + "\n\n"
                f"📧 <b>Step 1/3:</b> Enter your email address.\n"
                f"Example: example@gmail.com"
            )

            try:
                await update.callback_query.message.delete()
            except:
                pass

            m = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                               parse_mode="HTML")
            state['msg_id'] = m.message_id
            return True


        if not address:
            state['step'] = 'waiting_address'
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]]

            loaded_info = []
            if phone: loaded_info.append(f"📞 Phone: {self.escape_html(phone)}")
            if email: loaded_info.append(f"📧 Email: {self.escape_html(email)}")

            text = (
                f"📋 <b>Placing an order</b>\n\n"
                f"✅ <b>Loaded from profile:</b>\n" + "\n".join(loaded_info) + "\n\n"
                f"📍 <b>Step 2/3:</b> Enter your shipping address.\n"
                f"Example: Kyiv, Main St. 1, apt 5"
            )
            try:
                await update.callback_query.message.delete()
            except:
                pass

            m = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                               parse_mode="HTML")
            state['msg_id'] = m.message_id
            return True

        return False


    async def use_profile_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id
        chat_id = query.message.chat_id

        await query.answer("Loading profile data...")

        cursor = self.conn.cursor()
        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()

        if not user_data:
            await self.checkout(update, context)
            return

        full_name, email, address, phone = user_data

        # Записуємо дані та ставимо прапорець from_profile
        self.user_states[user_id] = {
            'full_name': full_name,
            'email': email,
            'address': address,
            'phone': phone,
            'msg_id': query.message.message_id,
            'from_profile': True  # ВКАЗУЄМО, ЩО ЦЕ З ПРОФІЛЮ
        }

        # Перевірка на заповненість і перехід далі
        if full_name and email and address and phone:
            try: await query.message.delete()
            except: pass
            await self.show_order_summary(context, chat_id, user_id)
        else:
            await self.continue_checkout_flow(update, context)

    async def continue_checkout_flow(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states[user_id]
        chat_id = update.effective_chat.id
        total_steps = "4"

        # Якщо ми щойно виправили одне поле через "✏️ Edit", повертаємо в Summary
        if state.get('is_editing_single'):
            state['is_editing_single'] = False
            await self.show_order_summary(context, chat_id, user_id)
            return

        from_profile = state.get('from_profile', False)
        header = "📋 <b>Checkout</b> (Profile data loaded)\n\n" if from_profile else "📋 <b>Order Checkout</b>\n\n"

        # Визначаємо, який наступний крок потрібно показати
        if not state.get('full_name'):
            state['step'] = 'waiting_full_name'
            text = header + f"👤 <b>Step 1/{total_steps}: Full Name</b>\n\nPlease enter the recipient's full name:\n\n<b>Example:</b> <i>John Doe</i>"
            back_callback = "cart"
        elif not state.get('email'):
            state['step'] = 'waiting_email'
            text = header + f"📧 <b>Step 2/{total_steps}: Email Address</b>\n\nPlease enter your email:\n\n<b>Example:</b> <i>user@gmail.com</i>"
            back_callback = "back_to_name"
        elif not state.get('address'):
            state['step'] = 'waiting_shipping'
            if SHIPPING_MODE == 'UKRAINE':
                text = header + f"📍 <b>Step 3/{total_steps}: Shipping Info</b>\n\nEnter City and Nova Poshta Branch:\n\n<b>Example:</b> <i>Kyiv, Nova Poshta #15</i>"
            else:
                text = header + f"📍 <b>Step 3/{total_steps}: Shipping Info</b>\n\nEnter Full Address (Country, City, ZIP):\n\n<b>Example:</b> <i>Germany, Berlin, Hauptstraße 10, 10115</i>"
            back_callback = "back_to_email"
        elif not state.get('phone'):
            state['step'] = 'waiting_phone'
            example = "+380501234567" if SHIPPING_MODE == 'UKRAINE' else "+1234567890"
            text = header + f"📱 <b>Step 4/{total_steps}: Phone Number</b>\n\nEnter phone with country code:\n\n<b>Example:</b> <i>{example}</i>"
            back_callback = "back_to_shipping"
        else:
            # Якщо все заповнено, показуємо підсумок
            await self.show_order_summary(context, chat_id, user_id)
            return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data=back_callback)],
            [InlineKeyboardButton("❌ Cancel Order", callback_data="cancel_order")]
        ])

        # Логіка "пилососа": якщо це не натискання кнопки, а ввід тексту - видаляємо старе повідомлення бота
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            if 'msg_id' in state:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
                except Exception:
                    pass
            m = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
            state['msg_id'] = m.message_id


    async def handle_payment_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return

        query = update.callback_query
        user_id = query.from_user.id

        payment_map = {"pay_cod": "Cash on delivery", "pay_card": "Card to courier", "pay_bank": "Bank transfer"}
        payment_key = query.data
        if payment_key not in payment_map: return
        payment_method = payment_map[payment_key]

        if user_id not in self.user_states:
            await query.answer("❌ Session expired")
            await self.show_cart(update, context)
            return

        self.user_states[user_id]['payment'] = payment_method

        if not self.user_states[user_id].get('phone'):
            self.user_states[user_id]['step'] = 'waiting_phone'
            self.user_states[user_id]['msg_id'] = query.message.message_id

            await query.edit_message_text(
                "📋 **Placing an order**\n\n"
                "📞 **Step 4/4:** Enter your phone number for delivery contact:\n"
                "Enter your phone number in the format: +380XXXXXXXXX\n"
                "Example: +380501234567",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="checkout")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]
                ]),
                parse_mode="Markdown"
            )
            return

        # ВИПРАВЛЕНО: встановлено True для надсилання сповіщення адміну
        order_details = await self.create_order(update, context, send_message=True)

        if not order_details:
            try:
                await query.edit_message_text("❌ Order failed.", reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🛒 Cart", callback_data="cart")]]))
            except Exception:
                pass
            return

        order_id, products_list, total_amount = order_details
        products_text = "".join(f"▫️ {item['emoji']} {item['name']} × {item['quantity']} = {item['total']}$\n" for item in products_list)

        try:
            from zoneinfo import ZoneInfo
            tz_name = globals().get('BOT_TIMEZONE', 'Europe/Kyiv')
            current_time = datetime.now(ZoneInfo(tz_name)).strftime('%d.%m.%Y %H:%M')
        except:
            current_time = datetime.now().strftime('%d.%m.%Y %H:%M')

        order_text = (
            f"✅ **Order #{order_id} has been successfully placed!**\n\n"
            f"📦 **Products:**\n{products_text}\n"
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
        chat_id = msg.chat_id

        try: await msg.delete()
        except: pass
        if 'msg_id' in state:
            try: await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except: pass

        # --- Крок 1: ПІБ ---
        if state['step'] == 'waiting_full_name':
            name = msg.text.strip()
            if len(name.split()) < 2:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cart")], [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]])
                m = await context.bot.send_message(chat_id=chat_id, text="❌ <b>Invalid Name (Step 1/4)</b>\n\nPlease provide First and Last name.", reply_markup=kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            state['full_name'] = name
            await self.continue_checkout_flow(update, context)

        # --- Крок 2: Email ---
        elif state['step'] == 'waiting_email':
            email = msg.text.strip()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_name")], [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]])
                m = await context.bot.send_message(chat_id=chat_id, text="❌ <b>Invalid Email (Step 2/4)\n\nExample: example@gmail.com</b>", reply_markup=kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            state['email'] = email
            await self.continue_checkout_flow(update, context)

        # --- Крок 3: Адреса (Shipping) ---
        elif state['step'] == 'waiting_shipping':
            address = msg.text.strip()
            is_valid = True
            if SHIPPING_MODE == 'UKRAINE':
                comma_parts = [p.strip() for p in address.split(',') if p.strip()]
                space_parts = [p.strip() for p in address.split() if p.strip()]
                if not (len(comma_parts) >= 2 or len(space_parts) >= 3): is_valid = False
            else:
                # Перевірка на 3 коми для міжнародного формату
                if address.count(',') < 3: is_valid = False

            if not is_valid:
                error_msg = "❌ <b>Invalid Address (Step 3/4)</b>\n\n"
                if SHIPPING_MODE == 'UKRAINE': error_msg += "Format: Kyiv, Nova Poshta #15"
                else: error_msg += "Format: Country, City, Street, ZIP\n\nExample: <i>Germany, Berlin, Hauptstraße 10, 10115</i>"
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_email")], [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]])
                m = await context.bot.send_message(chat_id=chat_id, text=error_msg, reply_markup=kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            state['address'] = address
            await self.continue_checkout_flow(update, context)

        # --- Крок 4: Телефон ---
        elif state['step'] == 'waiting_phone':
            phone = msg.text.strip()
            is_valid = (re.fullmatch(r"^\+380\d{9}$", phone) if SHIPPING_MODE == 'UKRAINE' else re.fullmatch(r"^\+\d{10,15}$", phone))
            if not is_valid:
                example = "+380501234567" if SHIPPING_MODE == 'UKRAINE' else "+441234567890"
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_shipping")], [InlineKeyboardButton("❌ Cancel", callback_data="cancel_order")]])
                m = await context.bot.send_message(chat_id=chat_id, text=f"❌ <b>Invalid Phone (Step 4/4)</b>\n\nExample: <i>{example}</i>", reply_markup=kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            state['phone'] = phone
            await self.show_order_summary(context, chat_id, user_id)

    async def send_payment_keyboard(self, context, chat_id, user_id):
        self.user_states[user_id]['step'] = 'waiting_payment'
        keyboard = []

        # Логіка для України: Готівка + Карта кур'єру + Онлайн
        if SHIPPING_MODE == 'UKRAINE':
            keyboard.append([InlineKeyboardButton("💵 Готівка при отриманні", callback_data="pay_cod")])
            keyboard.append([InlineKeyboardButton("💳 Картою кур'єру", callback_data="pay_card")])
            keyboard.append([InlineKeyboardButton("📱 Оплатити онлайн (Apple Pay)", callback_data="pay_online")])
        else:
            # Для всього світу тільки онлайн через Stripe
            keyboard.append([InlineKeyboardButton("💳 Card / Apple Pay (Stripe)", callback_data="pay_online")])

        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="confirm_details_back")])
        keyboard.append([InlineKeyboardButton("❌ Скасувати замовлення", callback_data="cancel_order")])

        text = "💳 <b>Останній крок: Спосіб оплати</b>\n\nОберіть, як вам зручно оплатити замовлення:"
        m = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                           parse_mode="HTML")
        self.user_states[user_id]['msg_id'] = m.message_id

    async def choose_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = query.from_user.id
        state = self.user_states.get(user_id)
        if not state: return

        cursor = self.conn.cursor()
        cursor.execute('SELECT p.price, c.quantity, p.variants, c.selected_options FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?', (user_id,))
        total_amount = sum(self.calculate_item_price(p, v, o) * q for p, q, v, o in cursor.fetchall())

        if data == "pay_online":
            # ВИДАЛЯЄМО повідомлення з вибором оплати, щоб не "смітити"
            await query.message.delete()
            await self.send_invoice(update, context, total_amount)
        elif data == "pay_card":
            await self.finalize_order(update, context, "Картою кур'єру", total_amount)
        else:
            await self.finalize_order(update, context, "Готівка (накладений платіж)", total_amount)

    async def send_invoice(self, update: Update, context: ContextTypes.DEFAULT_TYPE, total_amount):
        from dom import STRIPE_TOKEN, PORTMONE_TOKEN
        user_id = update.effective_user.id

        # Determine token and currency based on region
        token = PORTMONE_TOKEN if SHIPPING_MODE == 'UKRAINE' else STRIPE_TOKEN
        currency = "UAH" if SHIPPING_MODE == 'UKRAINE' else "USD"

        # MANDATORY: One button MUST have pay=True when using custom reply_markup
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💳 Pay {total_amount} {currency}", pay=True)],
            [InlineKeyboardButton("🔙 Back to payment selection", callback_data="back_to_payment")]
        ])

        invoice_msg = await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title="Order Payment #QuickShop",
            description="Payment for selected items in your cart",
            payload=f"order_{user_id}",
            provider_token=token,
            currency=currency,
            prices=[LabeledPrice("Total Amount", int(total_amount * 100))],
            start_parameter="shop-payment",
            reply_markup=keyboard,
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False
        )

        # Store invoice ID to delete it later and avoid chat clutter
        if user_id in self.user_states:
            self.user_states[user_id]['invoice_msg_id'] = invoice_msg.message_id

    async def precheckout_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.pre_checkout_query
        user_id = query.from_user.id
        # Важливо: фінальна перевірка складу перед списанням грошей
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT c.quantity, p.name, p.stock FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?",
            (user_id,))
        for qty, name, stock in cursor.fetchall():
            if stock < qty:
                return await query.answer(ok=False, error_message=f"Sorry, {name} is already sold out!")
        await query.answer(ok=True)

    async def successful_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)

        # 1. Clean up: Remove the invoice message so only the receipt remains
        if state and 'invoice_msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=state['invoice_msg_id'])
            except Exception:
                pass

        # 2. Get payment info
        payment_info = update.message.successful_payment
        total_amount = payment_info.total_amount / 100

        # 3. Finalize order and send the receipt (the receipt text is generated in finalize_order)
        await self.finalize_order(update, context, "Online Card Payment", total_amount)

    async def handle_checkout_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id
        data = query.data
        state = self.user_states.get(user_id)
        if not state: return

        # Словник для швидкого визначення поля та його поточної назви
        edit_map = {
            "edit_check_name": ("full_name", "Name", "waiting_full_name"),
            "edit_check_email": ("email", "Email", "waiting_email"),
            "edit_check_address": ("address", "Shipping Address", "waiting_shipping"),
            "edit_check_phone": ("phone", "Phone Number", "waiting_phone")
        }

        if data in edit_map:
            field_key, display_name, next_step = edit_map[data]
            current_val = state.get(field_key, "Not set")
            state['step'] = next_step
            state['is_editing_single'] = True # Прапорець, щоб повернутись саме в Summary

            text = (
                f"✏️ <b>Editing {display_name}</b>\n\n"
                f"<b>Current value:</b> <code>{self.escape_html(current_val)}</code>\n\n"
                f"Please enter the new value below:"
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel Editing", callback_data="confirm_details_back")]])
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
            return

        # Стара логіка для кнопки Back (якщо залишилась десь у лінійному потоці)
        if data == "back_to_payment":
            try: await query.message.delete()
            except: pass
            await self.send_payment_keyboard(context, query.message.chat_id, user_id)

    async def handle_cancel_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        user_id = update.effective_user.id
        self.user_states.pop(user_id, None)
        await update.callback_query.edit_message_text("❌ Order cancelled.")
        await self.show_cart(update, context)

    async def show_order_summary(self, context, chat_id, user_id):
        state = self.user_states[user_id]
        state['step'] = 'waiting_confirmation'

        summary_text = (
            "🔍 <b>Confirm your details:</b>\n\n"
            f"👤 <b>Name:</b> {self.escape_html(state['full_name'])}\n"
            f"📧 <b>Email:</b> {self.escape_html(state['email'])}\n"
            f"📍 <b>Shipping:</b> {self.escape_html(state['address'])}\n"
            f"📱 <b>Phone:</b> {self.escape_html(state['phone'])}\n\n"
            "─────────────────────────\n"
            "Click ✏️ next to a field to change it, or ✅ to proceed."
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit Name", callback_data="edit_check_name"),
             InlineKeyboardButton("✏️ Edit Email", callback_data="edit_check_email")],
            [InlineKeyboardButton("✏️ Edit Shipping", callback_data="edit_check_address"),
             InlineKeyboardButton("✏️ Edit Phone", callback_data="edit_check_phone")],
            [InlineKeyboardButton("✅ Everything is correct", callback_data="confirm_details")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data="cancel_order")]
        ])

        if 'msg_id' in state:
            try: await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except: pass

        m = await context.bot.send_message(chat_id=chat_id, text=summary_text, reply_markup=keyboard, parse_mode="HTML")
        state['msg_id'] = m.message_id

    async def show_category_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data

        parts = data.split("_")
        cat_page = 1
        prod_page = 1
        category = ""


        try:
            if len(parts) >= 4 and parts[-1].isdigit() and parts[-2].isdigit():
                cat_page = int(parts[-1])
                prod_page = int(parts[-2])
                category = "_".join(parts[1:-2])
            elif len(parts) >= 3 and parts[-1].isdigit():
                prod_page = int(parts[-1])
                category = "_".join(parts[1:-1])
            else:
                category = data.replace("category_", "")
        except:
            category = data.replace("category_", "")

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products WHERE category = ?", (category,))
        total_items = cursor.fetchone()[0]

        if total_items == 0:
            await query.answer("No products here yet!")
            return

        ITEMS_PER_PAGE = 5
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        offset = (prod_page - 1) * ITEMS_PER_PAGE

        cursor.execute("SELECT id, name, price, emoji, variants FROM products WHERE category = ? LIMIT ? OFFSET ?",
                       (category, ITEMS_PER_PAGE, offset))
        products = cursor.fetchall()

        text = f"📂 <b>{self.escape_html(category)}</b>\nPage {prod_page}/{total_pages}"
        keyboard = []

        for p_id, name, base_price, emoji, variants_json in products:
            emo = emoji if emoji else "📦"

            all_prices = []
            if variants_json:
                try:
                    v_data = json.loads(variants_json)
                    for key, val in v_data.items():
                        if isinstance(val, dict):
                            for opt, info in val.items():
                                if isinstance(info, dict) and 'price' in info:
                                    all_prices.append(float(info['price']))
                                else:
                                    if base_price > 0: all_prices.append(base_price)
                except:
                    pass

            if not all_prices: all_prices.append(base_price)
            min_p = min(all_prices)
            max_p = max(all_prices)
            price_str = f"from {min_p}$" if min_p != max_p else f"{min_p}$"

            keyboard.append([InlineKeyboardButton(f"{emo} {name} - {price_str}",
                                                  callback_data=f"product_{p_id}_{prod_page}_{cat_page}")])

        nav = []
        if prod_page > 1:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"category_{category}_{prod_page - 1}_{cat_page}"))
        if prod_page < total_pages:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"category_{category}_{prod_page + 1}_{cat_page}"))
        if nav: keyboard.append(nav)

        keyboard.append([InlineKeyboardButton("🔙 Back to Catalog", callback_data=f"catalog_page_{cat_page}")])

        try:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    async def create_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, send_message=True):
        user = update.effective_user
        user_id = user.id
        state = self.user_states.get(user_id, {})

        cursor = self.conn.cursor()
        cursor.execute("SELECT product_id, quantity, selected_options FROM cart WHERE user_id = ?", (user_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            return None

        total_amount = 0
        products_details = []
        products_text_list = []

        for prod_id, qty, opts_json in cart_items:
            cursor.execute("SELECT name, price, emoji, variants, stock FROM products WHERE id = ?", (prod_id,))
            prod = cursor.fetchone()
            if prod:
                name, base_price, emoji, variants_json, current_stock = prod
                selected_opts = json.loads(opts_json) if opts_json else {}

                price = self.calculate_item_price(base_price, variants_json, opts_json)
                item_total = price * qty
                total_amount += item_total

                opts_str = f" ({', '.join(selected_opts.values())})" if selected_opts else ""
                products_details.append({
                    'name': name, 'quantity': qty, 'price': price,
                    'total': item_total, 'emoji': emoji,
                    'selected_options': selected_opts, 'product_id': prod_id
                })
                products_text_list.append(f"{emoji or '📦'} {name}{opts_str} x{qty}")
                cursor.execute("UPDATE products SET stock = ? WHERE id = ?", (max(0, current_stock - qty), prod_id))

        full_name = state.get('full_name', '')
        phone = state.get('phone', '')
        address = state.get('address', '')
        email = state.get('email', '')
        payment_method = state.get('payment', 'Unknown')

        cursor.execute('''
                       INSERT INTO orders (user_id, user_name, full_name, products, total_amount, phone, address,
                                           payment_method, email)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ''', (user_id, user.full_name, full_name, json.dumps(products_details, ensure_ascii=False),
                             total_amount, phone, address, payment_method, email))

        order_id = cursor.lastrowid
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        self.conn.commit()

        # --- СПОВІЩЕННЯ АДМІНІСТРАТОРІВ (АНГЛІЙСЬКОЮ) ---
        if send_message:
            items_str = "\n".join([f"▫️ {item}" for item in products_text_list])

            if SHIPPING_MODE == 'UKRAINE':
                region_header = "🇺🇦 NEW ORDER - UKRAINE"
                address_label = "📍 Shipping (City/Branch):"
            else:
                region_header = "🌎 NEW ORDER - INTERNATIONAL"
                address_label = "📍 Shipping (City/ZIP):"

            admin_text = (
                f"🔔 <b>{region_header} #{order_id}</b>\n\n"
                f"👤 <b>Customer:</b> {self.escape_html(full_name)}\n\n"
                f"📧 <b>Email:</b> {self.escape_html(email)}\n\n"
                f"📞 <b>Phone:</b> {self.escape_html(str(phone))}\n\n"
                f"<b>{address_label}</b>\n{self.escape_html(address)}\n\n"
                f"💳 <b>Payment Method:</b> {payment_method}\n\n"
                f"📦 <b>Products:</b>\n{items_str}\n\n"
                f"💰 <b>Total Amount: {total_amount}$</b>"
            )

            # Обробка ADMIN_ID як списку або одного числа
            admins = ADMIN_ID if isinstance(ADMIN_ID, list) else [ADMIN_ID]
            for admin in admins:
                try:
                    await context.bot.send_message(chat_id=admin, text=admin_text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin}: {e}")

        return order_id, products_details, total_amount

    async def finalize_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payment_method,
                             pre_calc_total=None):
        user = update.effective_user
        user_id = user.id

        # 1. Отримуємо стан або створюємо, якщо його немає
        if user_id not in self.user_states:
            self.user_states[user_id] = {}
        state = self.user_states[user_id]

        # 2. ВАЖЛИВО: Записуємо спосіб оплати в пам'ять, щоб create_order його побачив
        state['payment'] = payment_method

        # 3. Створюємо замовлення (тепер у базі та в адміна не буде "Unknown")
        result = await self.create_order(update, context, send_message=True)

        if not result:
            msg = "⚠️ Error: Order failed. Cart might be empty."
            if update.callback_query:
                await update.callback_query.answer(msg)
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
            return

        order_id, products_list, total_amount = result

        # Беремо дані з анкети для фінального чека
        user_name = state.get('full_name', user.full_name)
        user_email = state.get('email', '—')
        user_phone = state.get('phone', '—')
        user_address = state.get('address', '—')

        current_time = datetime.now(ZoneInfo(BOT_TIMEZONE)).strftime('%d.%m.%Y %H:%M')

        # Генеруємо чек
        receipt = self.generate_receipt(order_id, user_name, user_email, user_phone, user_address, payment_method,
                                        products_list, total_amount, current_time, receipt_format='html')

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]])

        if update.callback_query:
            await update.callback_query.edit_message_text(text=receipt, reply_markup=kb, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=receipt, reply_markup=kb,
                                           parse_mode="HTML")

        # Видаляємо дані тільки ПІСЛЯ того, як все успішно створено
        if user_id in self.user_states:
            del self.user_states[user_id]

    # -------------------- USER ORDERS --------------------
    async def show_my_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) as total FROM orders WHERE user_id = ?", (user_id,))
        total_orders = cursor.fetchone()["total"]

        if total_orders == 0:
            keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
            await query.edit_message_text(
                "🛒 <b>You have no orders yet.</b>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        per_page = 5
        total_pages = (total_orders - 1) // per_page + 1
        offset = page * per_page

        cursor.execute(
            'SELECT id, total_amount, status, created_at, products FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?',
            (user_id, per_page, offset))
        orders = cursor.fetchall()

        text = f"📋 <b>Your orders</b> (Page {page + 1}/{total_pages}):\n"
        text += "➖➖➖➖➖➖➖➖➖➖➖➖➖➖\n\n"

        keyboard = []
        status_emoji_map = {
            'pending': '🟡 Processing',
            'confirmed': '🔵 Confirmed',
            'shipped': '🟠 Sent',
            'delivered': '🟢 Delivered',
            'cancelled': '🔴 Cancelled'
        }

        for order in orders:
            raw_products = order["products"]
            product_display_list = []
            try:
                products_data = json.loads(raw_products)
                for p in products_data:
                    p_emoji = p.get('emoji', '📦')
                    p_name = re.sub(r'\s*\(?x\d+\)?\)*$', '', str(p.get('name', 'Product')))
                    product_display_list.append(f"{p_emoji} {p_name}")
            except:
                product_display_list.append("📦 Order Items")

            products_str = ", ".join(product_display_list)
            if len(products_str) > 35: products_str = products_str[:32] + "..."

            status_text = status_emoji_map.get(order['status'], order['status'])
            fmt_date = self.format_date(order['created_at'])

            text += f"📋 <b>Order #{order['id']}</b>\n"
            text += f"   {self.escape_html(products_str)}\n"
            text += f"💰 <b>{order['total_amount']}$</b> | {status_text}\n"
            text += f"🗓 {fmt_date}\n"
            text += "➖➖➖➖➖➖➖➖➖➖➖➖➖➖\n\n"

            keyboard.append([InlineKeyboardButton(f"📄 Details #{order['id']}",
                                                  callback_data=f"order_details_{order['id']}_{page}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"my_orders_page_{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_orders_page_{page + 1}"))

        if nav:
            keyboard.append(nav)

        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    async def handle_my_orders_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        await query.answer()
        match = re.match(r'^my_orders_page_(\d+)$', query.data)
        if match:
            page = int(match.group(1))
            await self.show_my_orders(update, context, page)

    async def show_order_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE, order_id=None,
                                 origin_page=0):
        if await self.check_user_blocked(update, context): return

        query = getattr(update, "callback_query", None)
        user_id = update.effective_user.id


        if order_id is None:
            if query:
                data = query.data
                match = re.search(r'_(\d+)(?:_(\d+))?$', data)
                if match:
                    order_id = int(match.group(1))
                    if match.group(2):
                        origin_page = int(match.group(2))
                else:
                    return

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()


        if int(user_id) == int(ADMIN_ID):
            cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        else:
            cursor.execute("SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id))

        order = cursor.fetchone()
        if not order:
            if query: await query.answer("❌ Order not found")
            return

        products_text = ""
        try:
            products_list = json.loads(order["products"])
            for p in products_list:
                if isinstance(p, str): raise ValueError()


                name = p.get('name', 'Unknown')

                name = re.sub(r'\s*\(?x\d+\)?\)*$', '', str(name))

                emoji = p.get('emoji', '📦')
                qty = p.get('quantity', 1)
                price_total = p.get('total', 0)

                opts = p.get('selected_options', {})
                opts_str = f" ({', '.join([str(v) for v in opts.values()])})" if opts else ""

                products_text += f"{emoji} {self.escape_html(name)}{self.escape_html(opts_str)} x{qty} = <b>{price_total}$</b>\n"
        except:

            raw = order["products"]
            if raw:
                for line in str(raw).split('\n'):
                    if line.strip():
                        products_text += f"📦 {self.escape_html(line)}\n"
            else:
                products_text = "📦 Items info unavailable\n"


        status_map = {'pending': '🟡 Processing', 'confirmed': '🔵 Confirmed', 'shipped': '🟠 Sent',
                      'delivered': '🟢 Delivered', 'cancelled': '🔴 Cancelled'}
        status_display = status_map.get(order['status'], order['status'])
        fmt_date = self.format_date(order['created_at'])
        pay_method = order['payment_method'] or '—'

        text = (
            f"📋 <b>Order #{order['id']}</b>\n\n"
            f"👤 <b>Customer:</b> {self.escape_html(order['user_name'])}\n"
            f"📧 <b>Email:</b> {self.escape_html(order['email'] or '—')}\n"
            f"📞 <b>Phone:</b> {self.escape_html(order['phone'] or '—')}\n"
            f"📍 <b>Address:</b> {self.escape_html(order['address'])}\n"
            f"💳 <b>Payment:</b> {self.escape_html(pay_method)}\n\n"
            f"📦 <b>Products:</b>\n{products_text}\n"
            f"💰 <b>Total: {order['total_amount']}$</b>\n\n"
            f"📊 <b>Status:</b> {status_display}\n"
            f"🕐 <b>Date:</b> {fmt_date}"
        )

        keyboard = []
        is_final = order['status'] in ('cancelled', 'delivered')

        if int(user_id) == int(ADMIN_ID):

            if not is_final:
                keyboard.append([
                    InlineKeyboardButton("🔵 Confirm", callback_data=f"admin_confirm_{order_id}_{origin_page}"),
                    InlineKeyboardButton("🟠 Sent", callback_data=f"admin_ship_{order_id}_{origin_page}")
                ])
                keyboard.append([
                    InlineKeyboardButton("🟢 Delivered", callback_data=f"admin_deliver_{order_id}_{origin_page}"),
                    InlineKeyboardButton("🔴 Cancel", callback_data=f"admin_cancel_{order_id}_{origin_page}")
                ])

            keyboard.append(
                [InlineKeyboardButton("🔙 Back to All Orders", callback_data=f"admin_all_orders_page_{origin_page}")])
        else:

            if not is_final:
                keyboard.append([InlineKeyboardButton("❌ Cancel Order", callback_data=f"user_cancel_{order_id}")])
            keyboard.append([InlineKeyboardButton("🔙 Back to list", callback_data=f"my_orders_page_{origin_page}")])

        if query:
            try:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            except Exception:

                await query.message.delete()
                await context.bot.send_message(query.message.chat_id, text, reply_markup=InlineKeyboardMarkup(keyboard),
                                               parse_mode="HTML")
        else:
            await context.bot.send_message(update.effective_chat.id, text, reply_markup=InlineKeyboardMarkup(keyboard),
                                           parse_mode="HTML")

    # -------------------- ADMIN PANEL --------------------
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.callback_query.answer("❌ Access denied")
            return

        text = "👑 **ADMIN PANEL**\n\n👇 **Dashboard:**"

        keyboard = [
            [InlineKeyboardButton("📋 ALL ORDERS", callback_data="admin_all_orders")],
            [InlineKeyboardButton("📦 Products", callback_data="admin_products")],
            [InlineKeyboardButton("📊 Stats", callback_data="admin_statistics"),
             InlineKeyboardButton("📈 Revenue", callback_data="admin_revenue_chart")],
            [InlineKeyboardButton("👥 Users", callback_data="admin_user_management")],
            [InlineKeyboardButton("🔙 Main menu", callback_data="main_menu")]
        ]

        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    # -------------------- ADMIN: STATISTICS --------------------
    async def admin_statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) != int(ADMIN_ID):
            await update.callback_query.answer("❌ Access denied")
            return

        cursor = self.conn.cursor()


        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
        pending_orders = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(total_amount) FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered')")
        total_revenue = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT products FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered')")
        product_sales = {}

        for (products_json,) in cursor.fetchall():
            try:
                products_list = json.loads(products_json)
                for item in products_list:

                    name = item.get('name', 'Unknown')
                    qty = item.get('quantity', 0)
                    product_sales[name] = product_sales.get(name, 0) + qty
            except:
                continue


        sorted_sales = sorted(product_sales.items(), key=lambda x: x[1], reverse=True)

        top_5 = sorted_sales[:5]
        top_text = "\n".join([f"🔥 {name}: {qty} pcs" for name, qty in top_5]) if top_5 else "No data"

        bottom_5 = sorted_sales[-5:] if len(sorted_sales) > 0 else []
        bottom_text = "\n".join([f"🧊 {name}: {qty} pcs" for name, qty in bottom_5]) if bottom_5 else "No data"


        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM orders")
        active_buyers = cursor.fetchone()[0]

        text = (
            f"👑 <b>ADMIN STATISTICS</b>\n\n"
            f"💰 <b>Total Revenue:</b> {total_revenue}$\n"
            f"📦 <b>Total Orders:</b> {total_orders} (🟡 {pending_orders} new)\n"
            f"👥 <b>Users:</b> {total_users} ({active_buyers} active buyers)\n\n"
            f"🏆 <b>Top 5 Best Sellers:</b>\n{top_text}\n\n"
            f"📉 <b>Bottom 5 Sellers:</b>\n{bottom_text}\n\n"
            f"<i>*Statistics based on confirmed/delivered orders</i>"
        )

        keyboard = [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")]]

        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def admin_categories_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query

        page = 1
        if query and query.data.startswith("admin_cat_page_"):
            try:
                page = int(query.data.split("_")[-1])
            except:
                page = 1

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT category) FROM products")
        total_items = cursor.fetchone()[0]

        CATS_PER_PAGE = 5
        total_pages = (total_items + CATS_PER_PAGE - 1) // CATS_PER_PAGE

        if page > total_pages: page = total_pages
        if page < 1: page = 1

        offset = (page - 1) * CATS_PER_PAGE

        cursor.execute(
            "SELECT DISTINCT category FROM products ORDER BY category ASC LIMIT ? OFFSET ?",
            (CATS_PER_PAGE, offset)
        )
        categories = cursor.fetchall()

        text = f"🛠 **Product Management**"
        if total_pages > 1:
            text += f" (Page {page}/{total_pages})"
        text += "\nSelect a category to edit items:"

        keyboard = []

        for (cat_name,) in categories:
            cursor.execute("SELECT COUNT(*) FROM products WHERE category = ?", (cat_name,))
            count = cursor.fetchone()[0]
            keyboard.append(
                [InlineKeyboardButton(f"📂 {cat_name} ({count})", callback_data=f"admin_list_cat_{cat_name}_1")])

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_cat_page_{page - 1}"))

        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_cat_page_{page + 1}"))

        if nav_row:
            keyboard.append(nav_row)

        keyboard.append([InlineKeyboardButton("➕ Add Product", callback_data="admin_add_product")])

        keyboard.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")])

        if query:
            try:
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                              parse_mode=ParseMode.MARKDOWN)
            except:
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                               reply_markup=InlineKeyboardMarkup(keyboard),
                                               parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_products_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE, category_override=None):
        query = update.callback_query
        data = query.data

        if category_override:
            category = category_override
            page = 1
        else:
            try:
                parts = data.split("_")
                page = int(parts[-1])
                category = "_".join(parts[3:-1])
            except:
                await query.answer("Error parsing category")
                return

        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM products WHERE category = ?", (category,))
        total_items = cursor.fetchone()[0]

        if total_items == 0:
            await self.admin_categories_menu(update, context)
            return

        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        offset = (page - 1) * ITEMS_PER_PAGE
        cursor.execute("SELECT id, name, stock FROM products WHERE category = ? LIMIT ? OFFSET ?",
                       (category, ITEMS_PER_PAGE, offset))
        products = cursor.fetchall()

        text = f"📂 Category: **{category}**\nPage {page}/{total_pages}\n\nSelect a product to edit:"
        keyboard = []

        for p_id, p_name, p_stock in products:
            status = "✅" if p_stock > 0 else "❌"

            keyboard.append([InlineKeyboardButton(f"{status} {p_name}", callback_data=f"admin_prod_{p_id}_{page}")])

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_list_cat_{category}_{page - 1}"))

        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_list_cat_{category}_{page + 1}"))

        if nav_row:
            keyboard.append(nav_row)

        keyboard.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="admin_products")])

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

    # -------------------- ADMIN: USER MANAGEMENT --------------------
    async def admin_user_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
        if update.effective_user.id != ADMIN_ID:
            await update.callback_query.answer("❌ Access denied")
            return


        items_per_page = 10
        offset = page * items_per_page

        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        total_pages = (total_users - 1) // items_per_page + 1 if total_users > 0 else 1

        cursor.execute("SELECT user_id, blocked FROM users LIMIT ? OFFSET ?", (items_per_page, offset))
        users = cursor.fetchall()

        keyboard = []
        for user_id, blocked in users:
            action_text = "✅ Unblock" if blocked else "⛔ Block"
            callback_action = 0 if blocked else 1

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

                InlineKeyboardButton(f"👤 {user_display}", callback_data="noop"),
                InlineKeyboardButton(action_text, callback_data=f"admin_user_block_{user_id}_{callback_action}")
            ])

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
    async def admin_revenue(self, update: Update, context: ContextTypes.DEFAULT_TYPE, period: str = "all"):
        if int(update.effective_user.id) != int(ADMIN_ID):
            await update.callback_query.answer("❌ Access denied")
            return

        query = update.callback_query

        period_sql = ""
        label = "all time"

        if period == "today":
            period_sql = " AND created_at >= date('now', 'localtime')"
            label = "today"
        elif period == "week":
            period_sql = " AND created_at >= date('now', '-7 days')"
            label = "last 7 days"
        elif period == "month":
            period_sql = " AND created_at >= date('now', '-30 days')"
            label = "last 30 days"

        cursor = self.conn.cursor()

        cursor.execute(
            f"SELECT SUM(total_amount), COUNT(id) FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered'){period_sql}")
        res = cursor.fetchone()
        total_rev = res[0] or 0
        total_orders = res[1] or 0

        avg_check = round(total_rev / total_orders, 2) if total_orders > 0 else 0
        cursor.execute(f"SELECT SUM(total_amount) FROM orders WHERE status = 'pending'{period_sql}")
        pending_rev = cursor.fetchone()[0] or 0

        text = (
            f"💰 <b>FINANCIAL REPORT</b> ({label.upper()})\n\n"
            f"💵 <b>Total Revenue:</b> {total_rev}$\n"
            f"💳 <b>Average Check:</b> {avg_check}$\n"
            f"📦 <b>Sales Count:</b> {total_orders}\n\n"
            f"⏳ <b>Pending Revenue:</b> {pending_rev}$\n"
            f"<i>*Only confirmed/delivered orders are included</i>"
        )

        keyboard = [
            [
                InlineKeyboardButton("📅 Today", callback_data="rev_today"),
                InlineKeyboardButton("📅 Week", callback_data="rev_week"),
                InlineKeyboardButton("📅 Month", callback_data="rev_month")
            ],
            [InlineKeyboardButton("📊 All Time", callback_data="rev_all")],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")]
        ]

        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass

    async def handle_revenue_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        period = query.data.replace("rev_", "")
        await self.admin_revenue(update, context, period=period)

    async def admin_all_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
        query = update.callback_query
        user_id = update.effective_user.id

        admins = ADMIN_ID if isinstance(ADMIN_ID, list) else [ADMIN_ID]
        if int(user_id) not in [int(aid) for aid in admins]:
            await query.answer("Access denied: You are not an admin.", show_alert=True)
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
            await query.edit_message_text("No orders found in database.", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        text = f"<b>📦 All Orders (Page {page + 1}/{total_pages}):</b>\n\n"
        keyboard = []
        status_emoji = {'pending': '🟡', 'confirmed': '🔵', 'shipped': '🟠', 'delivered': '🟢', 'cancelled': '🔴'}

        for order in orders:
            emoji = status_emoji.get(order["status"], '⚪')
            fmt_date = self.format_date(order['created_at'])
            text += f"{emoji} <code>#{order['id']}</code> | {order['user_name']} | {order['total_amount']}$ | {fmt_date}\n"
            keyboard.append(
                [InlineKeyboardButton(f"Details #{order['id']}", callback_data=f"order_details_{order['id']}_{page}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_all_orders_page_{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_all_orders_page_{page + 1}"))

        if nav: keyboard.append(nav)
        keyboard.append([InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
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

        if int(update.effective_user.id) != int(ADMIN_ID):
            await query.answer("❌ Access denied")
            return

        match = re.search(r'admin_(confirm|ship|deliver|cancel)_(\d+)(?:_(\d+))?', query.data)
        if not match:
            await query.answer("❌ Error parsing data")
            return

        action = match.group(1)
        order_id = int(match.group(2))
        origin_page = int(match.group(3)) if match.group(3) else 0

        status_map = {
            "confirm": "confirmed",
            "ship": "shipped",
            "deliver": "delivered",
            "cancel": "cancelled"
        }
        new_status = status_map.get(action)

        cursor = self.conn.cursor()
        cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        self.conn.commit()

        await query.answer(f"✅ Status updated: {new_status}")


        try:

            cursor.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
            row = cursor.fetchone()
            if row:
                buyer_id = row[0]

                status_text_map = {
                    'confirmed': '🔵 Confirmed',
                    'shipped': '🟠 Sent',
                    'delivered': '🟢 Delivered',
                    'cancelled': '🔴 Cancelled'
                }
                display_status = status_text_map.get(new_status, new_status)

                await context.bot.send_message(
                    chat_id=buyer_id,
                    text=f"📦 <b>Order #{order_id} update</b>\n\n"
                         f"🆕 New status: <b>{display_status}</b>\n\n"
                         f"Thank you for shopping with us! ❤️",
                    parse_mode="HTML"
                )
        except Exception as e:
            print(f"⚠️ Failed to notify user: {e}")


        await self.show_order_details(update, context, order_id=order_id, origin_page=origin_page)

    # -------------------- ADMIN: PRODUCT MANAGEMENT --------------------
    async def admin_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, price, stock, emoji FROM products ORDER BY name")
        products = cursor.fetchall()
        text = "📦 **Product management:**\n\n"
        keyboard = []
        for pid, name, price, stock, emoji in products[:20]:
            stock_status = "✅" if stock > 0 else "❌"
            text_line = f"{stock_status} {emoji or ''} **{name}** | {price}$ | Stock: {stock}\n"
            if len(text) + len(text_line) > 4000: break
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

        data = query.data
        parts = data.split("_")
        action = parts[2]
        order_id = parts[3]

        cursor = self.conn.cursor()

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

            cursor.execute("UPDATE orders SET status = 'accepted' WHERE id = ?", (order_id,))
            self.conn.commit()

            await query.edit_message_text(f"✅ Order #{order_id} ACCEPTED!")
            try:
                await context.bot.send_message(chat_id=buyer_id, text=f"✅ Your Order #{order_id} has been accepted!")
            except:
                pass

        elif action == "reject":


            try:
                products_list = json.loads(products_json)
                for item in products_list:
                    p_id = item['product_id']
                    qty_to_return = item['quantity']
                    sel_opts = item['selected_options']

                    cursor.execute("SELECT stock, variants FROM products WHERE id = ?", (p_id,))
                    prod_row = cursor.fetchone()

                    if prod_row:
                        current_stock = prod_row[0]
                        variants_json = prod_row[1]
                        new_stock = current_stock + qty_to_return
                        new_vars_json = variants_json
                        if variants_json and sel_opts:
                            try:
                                v_data = json.loads(variants_json)
                                changed = False
                                for key, val in sel_opts.items():
                                    if key in v_data:
                                        group = v_data[key]

                                        if isinstance(group, dict) and val in group:
                                            target = group[val]
                                            if isinstance(target, dict) and 'qty' in target:
                                                target['qty'] += qty_to_return
                                                changed = True

                                            elif isinstance(target, int):
                                                group[val] += qty_to_return
                                                changed = True

                                if changed:
                                    new_vars_json = json.dumps(v_data, ensure_ascii=False)
                            except:
                                pass

                        cursor.execute("UPDATE products SET stock = ?, variants = ? WHERE id = ?",
                                       (new_stock, new_vars_json, p_id))


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

        origin_page = 1

        try:
            if product_id_override:
                product_id = int(product_id_override)
            elif query:

                data = query.data
                parts = data.split("_")

                if len(parts) >= 4:
                    product_id = int(parts[2])
                    origin_page = int(parts[3])
                else:
                    product_id = int(data.replace("admin_prod_", ""))
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

        self.user_states[update.effective_user.id] = {
            'product_id': product_id,
            'step': 'edit_product_menu'
        }

        stock_details = ""
        base_price = product['price']
        all_prices = []

        if product['variants']:
            try:
                v_data = json.loads(product['variants'])
                stock_details = "\n📊 **Stock Details:**\n"

                for key, val in v_data.items():
                    if isinstance(val, dict):
                        sorted_items = sorted(val.items(), key=lambda x: x[0])
                        stock_details += f"  🔹 {key}:\n"

                        for opt, info in sorted_items:
                            qty = 0
                            price_str = ""

                            if isinstance(info, dict):
                                qty = info.get('qty', 0)
                                if 'price' in info:
                                    p_val = float(info['price'])
                                    all_prices.append(p_val)
                                    price_str = f" ({p_val}$)"
                                else:
                                    if base_price > 0: all_prices.append(base_price)
                            else:
                                qty = int(info)
                                if base_price > 0: all_prices.append(base_price)

                            status = f"✅ {qty}" if qty > 0 else "❌ 0"
                            stock_details += f"    - {opt}: {status}{price_str}\n"
            except:
                pass

        if not all_prices:
            all_prices.append(base_price)

        min_p = min(all_prices)
        max_p = max(all_prices)

        if min_p != max_p:
            display_price = f"from {min_p}$"
        else:
            display_price = f"{min_p}$"


        text = (
            f"🛠 **Product Management**\n\n"
            f"📌 ID: `{product['id']}`\n"
            f"📦 Total Stock: {product['stock']}\n"
            f"{stock_details}\n"
            f"📝 Name: {product['name']}\n"
            f"📄 Desc: {product['description']}\n"
            f"💰 Price: {display_price}\n"
            f"📂 Category: {product['category']}\n"
            f"😀 Emoji: {product['emoji']}\n\n"
            f"Select an action:"
        )

        keyboard = [
            [InlineKeyboardButton("✏️ Name", callback_data="admin_edit_field_name"),
             InlineKeyboardButton("✏️ Desc", callback_data="admin_edit_field_description")],

            [InlineKeyboardButton("✏️ Price", callback_data="admin_edit_field_price"),
             InlineKeyboardButton("✏️ Stock", callback_data="admin_edit_field_stock")],

            [InlineKeyboardButton("✏️ Category", callback_data="admin_edit_field_category"),
             InlineKeyboardButton("✏️ Emoji", callback_data="admin_edit_field_emoji")],

            [InlineKeyboardButton("✏️ Image", callback_data=f"admin_image_menu_{product_id}"),
             InlineKeyboardButton("✏️ Variants", callback_data="admin_edit_field_variants")],

            [InlineKeyboardButton("🗑️ Delete Product", callback_data=f"admin_delete_product_confirm_{product_id}")]
        ]

        cat_back = product['category']

        keyboard.append(
            [InlineKeyboardButton("🔙 Back to List", callback_data=f"admin_list_cat_{cat_back}_{origin_page}")])

        if query:
            try:
                await query.message.delete()
            except:
                pass

        sent_photo = False
        if product['image_url']:
            try:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=product['image_url'],
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
                sent_photo = True
            except Exception:
                pass

        if not sent_photo:
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

        msg = await update.callback_query.edit_message_text(
            "📦 **Adding a new product**\n\nEnter the name of the product:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )


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


        if field == "category":
            keyboard = self.get_existing_categories_keyboard()
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data=f"admin_prod_{product_id}")]])

        try:
            await query.message.delete()
        except:
            pass


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
                            vals = []
                            for opt, info in v.items():
                                if isinstance(info, dict):
                                    price_str = f"=${info['price']}" if 'price' in info else ""
                                    vals.append(f"{opt}={info['qty']}{price_str}")
                                else:
                                    vals.append(f"{opt}={info}")
                            lines.append(f"`{k}: {', '.join(vals)}`")
                        else:
                            vals = ", ".join(v)
                            lines.append(f"`{k}: {vals}`")
                    current_text = "\n".join(lines)
                except:
                    pass

            msg_text = (
                f"🎨 **Editing Variants**\n\n"
                f"👇 **Current settings:**\n{current_text}\n\n"
                f"✍️ **To CHANGE, send a list:**\n"
                f"`Type: Option=Qty` or `Type: Option=Qty=Price`\n\n"
                f"Example: `Color: Red=10, Blue=5=900`\n"
                f"🗑️ Send `-` to delete all variants."
            )

        sent_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=msg_text,
            reply_markup=keyboard,
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
            keyboard.append([InlineKeyboardButton("✏️ Change Photo", callback_data=f"admin_image_set_{product_id}")])
            keyboard.append([InlineKeyboardButton("🗑️ Delete Photo", callback_data=f"admin_image_delete_{product_id}")])

        keyboard.append([InlineKeyboardButton("🔙 Back to Editing", callback_data=f"admin_prod_{product_id}")])

        if query.message.photo:
            try: await query.message.delete()
            except: pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

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

        cursor = self.conn.cursor()
        cursor.execute("UPDATE products SET image_url = NULL WHERE id = ?", (product_id,))
        self.conn.commit()

        await query.answer("🗑️ Image deleted!")

        try:
            await query.message.delete()
        except Exception:
            pass

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

        keyboard = [
            [InlineKeyboardButton("❌ Yes, delete", callback_data=f"admin_delete_product_confirm_{product_id}")],
            [InlineKeyboardButton("🔙 Cancel", callback_data="admin_products")]
        ]

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

        self.user_states.pop(update.effective_user.id, None)

        await query.answer("🚫 Cancelled")

        await self.admin_categories_menu(update, context)

    async def admin_delete_product_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID: return
        query = update.callback_query
        match = re.match(r"admin_delete_product_confirm_(\d+)", query.data)
        if not match: return await query.answer("❌ Invalid request")
        product_id = int(match.group(1))

        cursor = self.conn.cursor()

        cursor.execute("SELECT name, category FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()

        if not row:
            await query.answer("❌ Product already deleted")
            await self.admin_categories_menu(update, context)
            return

        name = row[0]
        category_to_return = row[1]


        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        self.conn.commit()

        await query.answer(f"🗑️ {name} deleted!")


        await self.admin_products_list(update, context, category_override=category_to_return)

    # -------------------- TEXT HANDLERS --------------------
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id

        if user_id in self.user_states:
            state = self.user_states[user_id]
            step = state.get('step', '')

            if (step.startswith('add_product') or
                    step.startswith('edit_') or
                    step.startswith('waiting_simple_') or
                    step.startswith('waiting_var_') or
                    step == 'waiting_product_image' or
                    step == 'waiting_variant_values' or
                    step == 'waiting_type_decision'):

                await self.handle_admin_product_input(update, context)


            elif step.startswith('waiting_') and '_profile' in step:
                await self.handle_profile_input(update, context)


            elif step.startswith('waiting_'):
                await self.handle_checkout_input(update, context)
        else:
            await update.message.reply_text("Use /start for navigating the store! 🛍️")

    async def handle_profile_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state: return

        text = update.message.text.strip()
        msg = update.message
        chat_id = msg.chat_id

        try: await msg.delete()
        except: pass
        if 'msg_id' in state:
            try: await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except: pass

        cursor = self.conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        error_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="my_profile")]])

        # --- ПІБ ---
        if state['step'] == 'waiting_full_name_profile':
            if len(text.split()) < 2:
                m = await context.bot.send_message(chat_id=chat_id, text="❌ <b>Invalid Name</b>\n\nEnter First and Last name.", reply_markup=error_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            cursor.execute("UPDATE users SET full_name = ? WHERE user_id = ?", (text, user_id))

        # --- Email ---
        elif state['step'] == 'waiting_email_profile':
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
                m = await context.bot.send_message(chat_id=chat_id, text="❌ <b>Invalid Email Format</b>", reply_markup=error_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            cursor.execute("UPDATE users SET email = ? WHERE user_id = ?", (text, user_id))

        # --- Телефон (Виправлено перевірку) ---
        elif state['step'] == 'waiting_phone_profile':
            is_valid = (re.fullmatch(r"^\+380\d{9}$", text) if SHIPPING_MODE == 'UKRAINE' else re.fullmatch(r"^\+\d{10,15}$", text))
            if not is_valid:
                example = "+380501234567" if SHIPPING_MODE == 'UKRAINE' else "+441234567890"
                m = await context.bot.send_message(chat_id=chat_id, text=f"❌ <b>Invalid Phone</b>\n\nExample: {example}", reply_markup=error_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            cursor.execute("UPDATE users SET phone = ? WHERE user_id = ?", (text, user_id))

        # --- Адреса (Оновлено перевірку та приклад) ---
        elif state['step'] == 'waiting_address_profile':
            is_valid = True
            if SHIPPING_MODE == 'UKRAINE':
                if text.count(',') < 1 and len(text.split()) < 3: is_valid = False
            else:
                if text.count(',') < 3: is_valid = False

            if not is_valid:
                example = "Kyiv, Nova Poshta #15" if SHIPPING_MODE == 'UKRAINE' else "Germany, Berlin, Hauptstraße 10, 10115"
                m = await context.bot.send_message(chat_id=chat_id, text=f"❌ <b>Address too short</b>\n\nExample: <i>{example}</i>", reply_markup=error_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            cursor.execute("UPDATE users SET address = ? WHERE user_id = ?", (text, user_id))

        self.conn.commit()
        self.user_states.pop(user_id, None)
        await self.show_profile(update, context)

    async def profile_delete_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id

        # Отримуємо всі дані профілю
        cursor = self.conn.cursor()
        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        # Якщо даних взагалі немає
        if not row:
            await query.answer("Your profile is already empty")
            await self.show_profile(update, context)
            return

        full_name, email, address, phone = row
        keyboard = []

        # Додаємо кнопки тільки для тих даних, які існують у базі
        if full_name:
            keyboard.append([InlineKeyboardButton("🗑️ Delete Name", callback_data="delete_profile_full_name")])
        if email:
            keyboard.append([InlineKeyboardButton("🗑️ Delete Email", callback_data="delete_profile_email")])

        if address:
            # Адаптивна назва залежно від регіону
            shipping_label = "🗑️ Delete Shipping (City/Branch)" if SHIPPING_MODE == 'UKRAINE' else "🗑️ Delete Shipping (City/ZIP)"
            keyboard.append([InlineKeyboardButton(shipping_label, callback_data="delete_profile_address")])

        if phone:
            keyboard.append([InlineKeyboardButton("🗑️ Delete Phone", callback_data="delete_profile_phone")])

        keyboard.append([InlineKeyboardButton("🔙 Back to Profile", callback_data="my_profile")])

        text = "🗑️ <b>Delete Personal Data</b>\n\nSelect the information you want to remove from your profile:"

        # Видаляємо старе повідомлення, щоб уникнути помилок при зміні типів (текст/фото)
        try:
            await query.message.delete()
        except:
            pass

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

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

        # --- Додавання нового товару (Wizard) ---
        if step == 'add_product_name':
            state['product_data']['name'] = input_value
            state['step'] = 'add_product_description'
            m = await context.bot.send_message(chat_id=msg.chat_id, text="📝 Enter description:", reply_markup=cancel_kb)
            state['msg_id'] = m.message_id
            return

        if step == 'add_product_description':
            state['product_data']['description'] = input_value
            kb = [[InlineKeyboardButton("📦 Simple Product", callback_data="admin_decision_vars_no")],
                  [InlineKeyboardButton("🎨 Has Variants", callback_data="admin_decision_vars_yes")],
                  [InlineKeyboardButton("❌ Cancel", callback_data="admin_wizard_cancel")]]
            state['step'] = 'waiting_type_decision'
            m = await context.bot.send_message(chat_id=msg.chat_id, text="❓ **Choose Product Type:**",
                                               reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            state['msg_id'] = m.message_id
            return

        # ... (проміжні кроки створення пропускаю, вони стандартні)

        # --- РЕДАГУВАННЯ ПОЛІВ (ВИПРАВЛЕНО БАГ З ВАРІАНТАМИ) ---
        if state.get('editing_field'):
            field_to_edit = state['editing_field']
            product_id = state['product_id']
            value = input_value
            error_text = None
            new_calculated_stock = None

            if field_to_edit == "price":
                try:
                    value = float(input_value.replace("$", "").strip())
                except:
                    error_text = "❌ Invalid price."
            elif field_to_edit == "stock":
                try:
                    value = int(input_value)
                except:
                    error_text = "❌ Invalid stock number."
            elif field_to_edit == "variants":
                if input_value.strip() == "-":
                    value = None
                    new_calculated_stock = 0
                else:
                    try:
                        variants_dict = {}
                        total_qty = 0
                        # Розбиваємо за ";" (якщо кілька типів одночасно)
                        parts = input_value.split(";")
                        for part in parts:
                            part = part.strip()
                            if not part: continue

                            # ВИПРАВЛЕННЯ: Тепер категорія (напр. Size:) необов'язкова
                            if ":" in part:
                                v_type, v_opts = part.split(":", 1)
                                v_type = v_type.strip()
                            else:
                                v_type = "Variant"  # Стандартне ім'я, якщо ви ввели просто "XS=10=20"
                                v_opts = part

                            opts_map = {}
                            for opt_pair in v_opts.split(","):
                                p = opt_pair.strip().split("=")
                                if not p[0]: continue
                                opt_name = p[0].strip()

                                if len(p) == 3:  # Формат: XS=10=20 (Ім'я=К-сть=Ціна)
                                    q = int(p[1])
                                    pr = float(p[2].replace("$", ""))
                                    opts_map[opt_name] = {"qty": q, "price": pr}
                                    total_qty += q
                                elif len(p) == 2:  # Формат: XS=10 (Ім'я=К-сть)
                                    q = int(p[1])
                                    opts_map[opt_name] = q
                                    total_qty += q
                                else:
                                    opts_map[opt_name] = 0

                            if opts_map:
                                variants_dict[v_type] = opts_map

                        if not variants_dict: raise ValueError("Empty")
                        value = json.dumps(variants_dict, ensure_ascii=False)
                        new_calculated_stock = total_qty
                    except:
                        error_text = "❌ <b>Format Error!</b>\n\nUse: <code>XS=10=20, S=5</code>\nOr: <code>Size: XS=10=20</code>"

            if error_text:
                kb = [[InlineKeyboardButton("🔙 Back", callback_data=f"admin_prod_{product_id}")]]
                m = await context.bot.send_message(chat_id=msg.chat_id, text=error_text,
                                                   reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
                state['msg_id'] = m.message_id
                return

            cursor = self.conn.cursor()
            # Оновлюємо базу: якщо це варіанти, автоматично оновлюємо і загальний Stock
            if field_to_edit == "variants" and new_calculated_stock is not None:
                cursor.execute("UPDATE products SET variants = ?, stock = ? WHERE id = ?",
                               (value, new_calculated_stock, product_id))
                confirm_msg = f"✅ Variants updated!\n📦 New Total Stock: <b>{new_calculated_stock}</b>"
            else:
                cursor.execute(f"UPDATE products SET {field_to_edit} = ? WHERE id = ?", (value, product_id))
                confirm_msg = f"✅ {field_to_edit.capitalize()} updated!"

            self.conn.commit()
            self.user_states.pop(user_id, None)
            await context.bot.send_message(chat_id=msg.chat_id, text=confirm_msg, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back to Product", callback_data=f"admin_prod_{product_id}")]]),
                                           parse_mode="HTML")
            return

        # --- Оновлення фото (waiting_product_image) ---
        if step == 'waiting_product_image':
            pid = state.get('product_id')
            new_img = input_value if (is_photo or input_value.startswith('http')) else None
            cursor = self.conn.cursor()
            cursor.execute("UPDATE products SET image_url = ? WHERE id = ?", (new_img, pid))
            self.conn.commit()
            self.user_states.pop(user_id, None)
            await context.bot.send_message(chat_id=msg.chat_id, text="✅ Image updated!",
                                           reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",
                                                                                                    callback_data=f"admin_image_menu_{pid}")]]))
            return

    async def handle_edit_field_input(self, update, context, state, input_value, msg):
        field_to_edit = state['editing_field']
        product_id = state['product_id']
        value = input_value
        error_text = None
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
                    current_calc_stock = 0
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
                                    if len(sub_parts) == 3:
                                        qty = int(sub_parts[1])
                                        parsed_vals[sub_parts[0].strip()] = {"qty": qty, "price": float(sub_parts[2])}
                                        current_calc_stock += qty
                                    else:
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
                    new_calculated_stock = current_calc_stock
                except:
                    error_text = "❌ Format error."

        if error_text:
            kb = [[InlineKeyboardButton("🔙 Back", callback_data=f"admin_edit_product_{product_id}")]]
            m = await context.bot.send_message(chat_id=msg.chat_id, text=error_text,
                                               reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            state['msg_id'] = m.message_id
            return

        cursor = self.conn.cursor()

        if field_to_edit == "variants" and new_calculated_stock is not None:
            cursor.execute(f"UPDATE products SET variants = ?, stock = ? WHERE id = ?",
                           (value, new_calculated_stock, product_id))
            msg_confirm = f"✅ **Variants** updated!\n📦 New Total Stock: {new_calculated_stock}"
        else:
            cursor.execute(f"UPDATE products SET {field_to_edit} = ? WHERE id = ?", (value, product_id))
            msg_confirm = f"✅ **{field_to_edit}** updated!"

        self.conn.commit()
        self.user_states.pop(update.effective_user.id, None)

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

        if variant_type == "DONE":
            p = state['product_data']
            variants_data = p.get('variants', {})

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

        variant_type = query.data.replace("admin_add_variant_type_", "")

        examples_map = {
            "Size": "S=10, M=5, L=2=1200",
            "Color": "Red=5, Blue=3, Gold=10=50",
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

        kb = [[InlineKeyboardButton("🔙 Back to Types", callback_data="admin_step_variants_init")]]

        self.user_states[user_id]['step'] = 'waiting_variant_values'
        self.user_states[user_id]['current_variant_type'] = variant_type

        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        except:

            msg = await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                                 reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            self.user_states[user_id]['msg_id'] = msg.message_id

    async def admin_handle_variant_decision(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        data = query.data

        try:
            await query.message.delete()
        except:
            pass

        if data == "admin_decision_vars_no":
            self.user_states[user_id]['step'] = 'waiting_simple_price'

            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_wizard_cancel")]])

            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="💰 **Simple Product**\n\nEnter the **Price** (number):",
                reply_markup=cancel_kb,
                parse_mode=ParseMode.MARKDOWN
            )
            self.user_states[user_id]['msg_id'] = msg.message_id

        elif data == "admin_decision_vars_yes":
            self.user_states[user_id]['step'] = 'waiting_var_image'

            self.user_states[user_id]['product_data']['variants'] = {}
            self.user_states[user_id]['product_data']['stock'] = 0
            self.user_states[user_id]['product_data']['price'] = 0

            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_wizard_cancel")]])

            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🎨 **Variant Product**\n\n📸 Send a **Photo** (or URL, or `-` to skip):",
                reply_markup=cancel_kb,
                parse_mode=ParseMode.MARKDOWN
            )
            self.user_states[user_id]['msg_id'] = msg.message_id

    async def admin_back_to_variant_types(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        self.user_states[user_id]['step'] = 'add_product_variants_loop'

        text = "🎨 **Product Variants**\n\nSelect a type below or click **Finish**:"
        reply_markup = self.get_variant_type_keyboard()

        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    def get_existing_categories_keyboard(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT category FROM products")
        categories = [row[0] for row in cursor.fetchall() if row[0]]

        keyboard = []
        for i in range(0, len(categories), 2):
            row = []
            for cat in categories[i:i + 2]:
                row.append(InlineKeyboardButton(cat, callback_data=f"admin_set_cat_{cat}"))
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="admin_wizard_cancel")])
        return InlineKeyboardMarkup(keyboard)

    async def admin_handle_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        if user_id != ADMIN_ID or user_id not in self.user_states:
            await query.answer("❌ Session expired")
            return

        category = query.data.replace("admin_set_cat_", "")
        state = self.user_states[user_id]

        await query.answer(f"Selected: {category}")


        if state.get('editing_field') == 'category':
            product_id = state.get('product_id')
            cursor = self.conn.cursor()
            cursor.execute("UPDATE products SET category = ? WHERE id = ?", (category, product_id))
            self.conn.commit()

            self.user_states.pop(user_id, None)

            try:
                await query.message.delete()
            except:
                pass

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ Category updated to **{category}**!",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back to Product", callback_data=f"admin_prod_{product_id}")]])
            )
            return

        step = state.get('step')
        try:
            await query.message.delete()
        except:
            pass

        if step == 'waiting_simple_category':

            p = state['product_data']
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO products (name, description, price, image_url, emoji, category, stock, variants) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (p["name"], p["description"], p["price"], p.get('image_url'), p["emoji"], category, p["stock"], None))
            self.conn.commit()
            self.user_states.pop(user_id, None)
            await context.bot.send_message(chat_id=query.message.chat_id, text="✅ Product Created!",
                                           reply_markup=InlineKeyboardMarkup(
                                               [[InlineKeyboardButton("🔙 Back to Products",
                                                                      callback_data="admin_products")]]))

        elif step == 'waiting_var_category':

            state['product_data']['category'] = category
            state['step'] = 'add_product_variants_loop'
            m = await context.bot.send_message(chat_id=query.message.chat_id, text="🎨 Setup Variants:",
                                               reply_markup=self.get_variant_type_keyboard())
            state['msg_id'] = m.message_id

    async def handle_checkout_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id
        data = query.data

        # 1. Підтвердження даних (з Summary до вибору оплати)
        if data == "confirm_details":
            try:
                await query.message.delete()
            except:
                pass
            await self.send_payment_keyboard(context, query.message.chat_id, user_id)

        # 2. Назад з вибору оплати до перевірки даних (Summary)
        elif data == "confirm_details_back":
            try:
                await query.message.delete()
            except:
                pass
            await self.show_order_summary(context, query.message.chat_id, user_id)

        # 3. НАЗАД з реквізитів (карти/банку) до вибору способу оплати
        elif data == "back_to_payment":
            try:
                await query.message.delete()
            except:
                pass
            # Повертаємо користувача до вибору кнопок: Cash, Card, Bank
            await self.send_payment_keyboard(context, query.message.chat_id, user_id)

        await query.answer()

    async def show_bank_payment_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        text = (
            "🏦 <b>Bank Transfer (IBAN)</b>\n\n"
            "Please use the following details for the transfer:\n\n"
            "<b>Recipient:</b> John Doe Shop\n"
            "<b>IBAN:</b> <code>UA12345678901234567890123456</code>\n"
            "<b>Purpose:</b> Order Payment\n\n"
            "────────────────────\n\n"
            "Please send a confirmation screenshot to our support after the transfer."
        )
        # Тут використовується ваша клавіатура з кнопкою back_to_payment
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

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

    # Обробка медіа та контактів
    application.add_handler(MessageHandler(filters.PHOTO, bot.handle_admin_product_input))
    application.add_handler(MessageHandler(filters.CONTACT, bot.handle_checkout_input))

    # Загальний текстовий обробник (має бути останнім серед MessageHandler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))

    # =========================================================================
    # 2. КЛІЄНТСЬКА ЧАСТИНА (Меню, Каталог, Профіль)
    # =========================================================================
    application.add_handler(CallbackQueryHandler(bot.show_main_menu, pattern=r'^main_menu$'))
    application.add_handler(CallbackQueryHandler(bot.show_help, pattern=r'^help$'))

    # Каталог та категорії
    application.add_handler(CallbackQueryHandler(bot.show_catalog, pattern=r'^catalog(_page_\d+)?$'))
    application.add_handler(CallbackQueryHandler(bot.show_category_products, pattern=r'^category_'))
    application.add_handler(CallbackQueryHandler(bot.show_product, pattern=r'^product_'))
    application.add_handler(CallbackQueryHandler(bot.handle_product_action, pattern=r'^prod_(plus|minus)_'))

    # Профіль користувача
    application.add_handler(CallbackQueryHandler(bot.show_profile, pattern=r'^my_profile$'))
    application.add_handler(CallbackQueryHandler(bot.edit_phone, pattern=r'^edit_phone$'))
    application.add_handler(CallbackQueryHandler(bot.edit_email, pattern=r'^edit_email$'))
    application.add_handler(CallbackQueryHandler(bot.edit_address, pattern=r'^edit_address$'))
    application.add_handler(CallbackQueryHandler(bot.profile_delete_menu, pattern=r'^profile_delete_menu$'))
    application.add_handler(CallbackQueryHandler(bot.handle_delete_profile_data, pattern=r'^delete_profile_'))
    application.add_handler(CallbackQueryHandler(bot.edit_full_name, pattern=r'^edit_full_name$'))

    # =========================================================================
    # 3. КОШИК ТА ОФОРМЛЕННЯ (Checkout)
    # =========================================================================
    application.add_handler(CallbackQueryHandler(bot.show_cart, pattern=r'^(cart|my_cart)$'))
    application.add_handler(CallbackQueryHandler(bot.clear_cart, pattern=r'^clear_cart$'))
    application.add_handler(CallbackQueryHandler(bot.handle_cart_update, pattern=r'^cart_(plus|minus)_'))
    application.add_handler(CallbackQueryHandler(bot.handle_add_to_cart_click, pattern=r'^add_to_cart_'))
    application.add_handler(CallbackQueryHandler(bot.remove_from_cart, pattern=r'^remove_from_cart_'))
    application.add_handler(CallbackQueryHandler(bot.handle_cart_actions, pattern=r'^cart_item_'))
    application.add_handler(PreCheckoutQueryHandler(bot.precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, bot.successful_payment_callback))

    # Логіка замовлення (Checkout flow)
    application.add_handler(CallbackQueryHandler(bot.checkout, pattern=r'^checkout$'))
    application.add_handler(CallbackQueryHandler(bot.use_profile_data, pattern=r'^use_profile_data$'))

    # ПІДТВЕРДЖЕННЯ (має бути ПЕРЕД загальним back_to_)
    application.add_handler(
        CallbackQueryHandler(bot.handle_checkout_confirm, pattern=r'^(confirm_details|confirm_details_back)$'))

    # Оплата
    application.add_handler(CallbackQueryHandler(bot.choose_payment, pattern=r'^pay_(cod|card|bank|online)$'))

    # Універсальний обробник кнопок "Назад"
    application.add_handler(CallbackQueryHandler(bot.handle_checkout_back, pattern=r'^back_to_'))
    application.add_handler(CallbackQueryHandler(bot.handle_cancel_order, pattern=r'^cancel_order$'))
    application.add_handler(CallbackQueryHandler(bot.handle_checkout_back, pattern=r'^back_to_|edit_'))


    # Мої замовлення
    application.add_handler(CallbackQueryHandler(bot.show_my_orders, pattern=r'^my_orders$'))
    application.add_handler(CallbackQueryHandler(bot.handle_my_orders_pagination, pattern=r'^my_orders_page_\d+$'))
    application.add_handler(CallbackQueryHandler(bot.show_order_details, pattern=r'^order_details_'))
    application.add_handler(CallbackQueryHandler(bot.user_cancel_order, pattern=r'^user_cancel_'))

    # =========================================================================
    # 4. АДМІН-ПАНЕЛЬ
    # =========================================================================
    application.add_handler(CallbackQueryHandler(bot.admin_panel, pattern=r'^admin_panel$'))
    application.add_handler(CallbackQueryHandler(bot.admin_statistics, pattern=r'^admin_statistics$'))
    application.add_handler(CallbackQueryHandler(bot.admin_revenue, pattern=r'^admin_revenue_chart$'))
    application.add_handler(CallbackQueryHandler(bot.handle_revenue_period, pattern=r'^rev_'))

    # Управління користувачами
    application.add_handler(CallbackQueryHandler(bot.admin_user_management, pattern=r'^admin_user_management$'))
    application.add_handler(CallbackQueryHandler(bot.handle_admin_user_pagination, pattern=r'^admin_user_page_\d+$'))
    application.add_handler(CallbackQueryHandler(bot.admin_user_block, pattern=r'^admin_user_block_'))

    # Управління замовленнями (Адмін)
    application.add_handler(CallbackQueryHandler(bot.admin_all_orders, pattern=r'^admin_all_orders$'))
    application.add_handler(
        CallbackQueryHandler(bot.handle_admin_all_orders_pagination, pattern=r'^admin_all_orders_page_\d+$'))
    application.add_handler(
        CallbackQueryHandler(bot.admin_order_status_change, pattern=r'^admin_(confirm|ship|deliver|cancel)'))
    application.add_handler(CallbackQueryHandler(bot.admin_handle_order_callback, pattern=r'^admin_order_'))

    # Управління товарами (Адмін)
    application.add_handler(
        CallbackQueryHandler(bot.admin_categories_menu, pattern=r'^admin_products$|^admin_cat_page_'))
    application.add_handler(CallbackQueryHandler(bot.admin_products_list, pattern=r'^admin_list_cat_'))
    application.add_handler(CallbackQueryHandler(bot.admin_handle_category_selection, pattern=r'^admin_set_cat_'))
    application.add_handler(CallbackQueryHandler(bot.admin_product_menu, pattern=r'^admin_prod_'))
    application.add_handler(CallbackQueryHandler(bot.admin_view_product, pattern=r'^admin_view_product_'))
    application.add_handler(CallbackQueryHandler(bot.admin_add_product, pattern=r'^admin_add_product$'))
    application.add_handler(CallbackQueryHandler(bot.admin_edit_product, pattern=r'^admin_edit_product_'))
    application.add_handler(CallbackQueryHandler(bot.admin_edit_field, pattern=r'^admin_edit_field_'))
    application.add_handler(CallbackQueryHandler(bot.admin_delete_product, pattern=r'^admin_delete_product_\d+'))
    application.add_handler(
        CallbackQueryHandler(bot.admin_delete_product_confirm, pattern=r'^admin_delete_product_confirm_'))

    # Фото та варіанти (Адмін)
    application.add_handler(CallbackQueryHandler(bot.admin_image_menu, pattern=r'^admin_image_menu_'))
    application.add_handler(CallbackQueryHandler(bot.admin_image_set_prompt, pattern=r'^admin_image_set_'))
    application.add_handler(CallbackQueryHandler(bot.admin_image_delete, pattern=r'^admin_image_delete_'))
    application.add_handler(CallbackQueryHandler(bot.admin_handle_variant_decision, pattern=r'^admin_decision_vars_'))
    application.add_handler(CallbackQueryHandler(bot.admin_wizard_cancel, pattern=r'^admin_wizard_cancel$'))
    application.add_handler(
        CallbackQueryHandler(bot.admin_handle_variant_type_selection, pattern=r'^admin_add_variant_type_'))
    application.add_handler(
        CallbackQueryHandler(bot.admin_back_to_variant_types, pattern=r'^admin_step_variants_init$'))

    # Логіка варіантів (Клієнт)
    application.add_handler(CallbackQueryHandler(bot.handle_variant_type_selection, pattern=r'^vartype_'))
    application.add_handler(
        CallbackQueryHandler(bot.handle_variant_selection_user, pattern=r'^var_sel_|^cancel_selection$'))

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