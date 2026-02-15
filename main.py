import logging
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from dom import (
    BOT_TOKEN, ADMIN_ID, BOT_TIMEZONE, SHIPPING_MODE,
    DB_NAME, SHOP_NAME, CURRENCY_SYMBOL, STORE_MESSAGES,
    SUPPORT_USER, CHANNEL_LINK, PAYMENT_TOKENS, CURRENCY_CODE
)
from telegram import LabeledPrice
from telegram.ext import PreCheckoutQueryHandler
from strings import STRINGS
from dom import *
import time


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

    def get_text(self, key, **kwargs):
        lang = SHIPPING_MODE if SHIPPING_MODE in STRINGS else 'INTERNATIONAL'
        kwargs.setdefault('currency_symbol', CURRENCY_SYMBOL)
        return STRINGS[lang].get(key, f"_{key}_").format(**kwargs)

    # -------------------- DATABASE --------------------
    def _add_column_if_not_exists(self, cursor, table_name: str, column_name: str, column_type: str):
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]
        if column_name not in columns:
            try:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                print(self.get_text('column_added', column_name=column_name, table_name=table_name))
            except Exception as e:
                print(self.get_text('error_adding_column', column_name=column_name, table_name=table_name, e=e))

    def init_database(self):
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = self.conn.cursor()

        # Таблиця товарів
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS products
                       (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT,
                        price REAL NOT NULL, image_url TEXT, category TEXT, stock INTEGER DEFAULT 0,
                        emoji TEXT, variants TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
                       ''')

        # ТАБЛИЦЯ КОШИКА (додано)
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS cart
                       (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, 
                        product_id INTEGER NOT NULL, quantity INTEGER DEFAULT 1, 
                        selected_options TEXT,
                        FOREIGN KEY(product_id) REFERENCES products(id))
                       ''')

        # Таблиця замовлень
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS orders
                       (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, user_name TEXT,
                        full_name TEXT, products TEXT NOT NULL, total_amount REAL NOT NULL,
                        phone TEXT, address TEXT, payment_method TEXT, email TEXT,
                        status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
                       ''')

        # Таблиця користувачів
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS users
                       (user_id INTEGER PRIMARY KEY, phone TEXT, address TEXT, email TEXT,
                        full_name TEXT, blocked INTEGER DEFAULT 0)
                       ''')

        # Перевірка та додавання колонок (для сумісності)
        self._add_column_if_not_exists(cursor, "products", "emoji", "TEXT")
        self._add_column_if_not_exists(cursor, "products", "image_url", "TEXT")
        self._add_column_if_not_exists(cursor, "products", "variants", "TEXT")
        self._add_column_if_not_exists(cursor, "orders", "payment_method", "TEXT")
        self._add_column_if_not_exists(cursor, "orders", "email", "TEXT")
        self._add_column_if_not_exists(cursor, "orders", "full_name", "TEXT")
        self._add_column_if_not_exists(cursor, "users", "email", "TEXT")
        self._add_column_if_not_exists(cursor, "users", "full_name", "TEXT")
        self._add_column_if_not_exists(cursor, "users", "blocked", "INTEGER DEFAULT 0")

        self.conn.commit()

    def escape_html(self, text):
        if not text: return ""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # main.py
    def generate_receipt(self, order_id, user_name, email, phone, address, payment, products_list, total, date,
                         receipt_format='html'):
        from dom import SHIPPING_MODE, CURRENCY_SYMBOL

        # Визначаємо коротку мітку (без зайвих знаків, бо emoji додамо в шаблоні)
        shipping_label = self.get_text('shipping_label_ukraine') if SHIPPING_MODE == 'UKRAINE' else self.get_text(
            'shipping_label_international')

        if receipt_format == 'html':
            bold_start, bold_end = "<b>", "</b>"
            escaper = self.escape_html
            product_line_format = "▫️ {emoji} {name}{opts}\n   {quantity} x {price}{symbol} = <b>{total}{symbol}</b>\n"
        else:
            bold_start, bold_end = "**", "**"
            escaper = self.escape_md
            product_line_format = "{emoji} {name}{opts}\n   {quantity} x {price}{symbol} = {total}{symbol}\n"

        products_text = ""
        for item in products_list:
            opts_str = ""
            if item.get('selected_options'):
                opts_vals = [f"{v}" for k, v in item['selected_options'].items()]
                opts_str = f" ({', '.join(opts_vals)})"

            products_text += product_line_format.format(
                emoji=item.get('emoji', '📦'),
                name=escaper(item.get('name', self.get_text('product'))),
                opts=escaper(opts_str),
                quantity=item.get('quantity', 1),
                price=item.get('price', 0),
                total=item.get('total', 0),
                symbol=CURRENCY_SYMBOL
            )

        return self.get_text(
            'receipt',
            bold_start=bold_start,
            bold_end=bold_end,
            order_id=order_id,
            user_name=escaper(user_name),
            email=escaper(email),
            phone=escaper(str(phone)),
            shipping_label=shipping_label,
            address=escaper(address),
            payment=payment,
            products_text=products_text,
            total=total,
            date=date,
            symbol=CURRENCY_SYMBOL
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
            print(self.get_text('price_calculation_error', e=e))

        return float(final_price)

    def get_variant_type_keyboard(self):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(self.get_text('size_variant'), callback_data="vartype_Size"),
             InlineKeyboardButton(self.get_text('color_variant'), callback_data="vartype_Color")],
            [InlineKeyboardButton(self.get_text('memory_variant'), callback_data="vartype_Memory"),
             InlineKeyboardButton(self.get_text('volume_variant'), callback_data="vartype_Volume")],
            [InlineKeyboardButton(self.get_text('weight_variant'), callback_data="vartype_Weight"),
             InlineKeyboardButton(self.get_text('shoe_size_variant'), callback_data="vartype_ShoeSize")],
            [InlineKeyboardButton(self.get_text('finish_skip_variant'), callback_data="vartype_DONE")],
            [InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_wizard_cancel")]
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
                await update.callback_query.answer(self.get_text('user_blocked_inline'), show_alert=True)
            elif update.message:
                await update.message.reply_text(self.get_text('user_blocked'))
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
            await query.answer(self.get_text('invalid_request'))
            return
        order_id = int(match.group(1))
        uid = query.from_user.id

        cursor = self.conn.cursor()
        cursor.execute("SELECT status FROM orders WHERE id = ? AND user_id = ?", (order_id, uid))
        row = cursor.fetchone()

        if not row:
            await query.answer(self.get_text('invalid_request'))
            return

        status = row[0]
        if status in ('cancelled', 'delivered'):
            await query.answer(self.get_text('order_already_delivered_or_canceled'))
            return


        self.restore_stock(order_id)

        cursor.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        self.conn.commit()

        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=self.get_text('customer_canceled_order', order_id=order_id),
                                           parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

        await query.answer(self.get_text('order_canceled'))


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

        cart_text = self.get_text('my_cart_count', cart_count=cart_count) if cart_count > 0 else self.get_text('my_cart')

        if int(user_id) == int(ADMIN_ID):
            keyboard = [
                [InlineKeyboardButton(self.get_text('admin_panel_button'), callback_data="admin_panel")],
                [InlineKeyboardButton(self.get_text('product_catalog_button'), callback_data="catalog")],
                [
                    InlineKeyboardButton(cart_text, callback_data="cart"),
                    InlineKeyboardButton(self.get_text('my_profile_button'), callback_data="my_profile")
                ]
            ]
            return InlineKeyboardMarkup(keyboard)

        keyboard = [
            [InlineKeyboardButton(self.get_text('product_catalog_button'), callback_data="catalog")],
            [InlineKeyboardButton(cart_text, callback_data="cart")],
            [InlineKeyboardButton(self.get_text('my_orders_button'), callback_data="my_orders")],
            [InlineKeyboardButton(self.get_text('my_profile_button'), callback_data="my_profile")],
            [InlineKeyboardButton(self.get_text('help_button'), callback_data="help")]
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
            await update.message.reply_text(self.get_text('user_blocked'))
            return
        await self.show_main_menu(update, context)

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return

        user = update.effective_user
        user_id = user.id
        safe_name = self.escape_html(user.first_name)

        if int(user_id) == int(ADMIN_ID):
            welcome_text = self.get_text('admin_welcome', safe_name=safe_name)
        else:
            # Отримуємо базовий текст із dom.py
            base_welcome = STORE_MESSAGES[SHIPPING_MODE]['welcome'].format(shop_name=SHOP_NAME)

            # Перевіряємо профіль
            missing = self.get_profile_completion_status(user_id)
            if missing:
                promo = self.get_text(f'welcome_promo_{len(missing)}')
                labels = []
                if "full_name" in missing: labels.append(self.get_text('missing_name'))
                if "email" in missing: labels.append(self.get_text('missing_email'))
                if "address" in missing:
                    key = 'missing_address_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'missing_address_international'
                    labels.append(self.get_text(key))
                if "phone" in missing: labels.append(self.get_text('missing_phone'))

                promo_block = self.get_text('missing_fields_info', promo=promo, missing_labels=', '.join(labels))

                # ЛОГІКА ПЕРЕМІЩЕННЯ: розрізаємо текст по подвійному переносу рядка
                parts = base_welcome.split("\n\n", 1)
                if len(parts) > 1:
                    # Вставляємо ПРОМІЖ вітанням та описом
                    welcome_text = f"{parts[0]}\n{promo_block}\n\n{parts[1]}"
                else:
                    # Якщо тексту мало, просто додаємо зверху
                    welcome_text = f"{promo_block}\n\n{base_welcome}"
            else:
                welcome_text = base_welcome

        reply_markup = self.build_main_keyboard(user_id)

        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup,
                                                              parse_mode=ParseMode.HTML)
            except Exception:
                pass
        elif update.message:
            await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return

        text = STORE_MESSAGES[SHIPPING_MODE]['help'].format(
            shop_name=SHOP_NAME,
            support=SUPPORT_USER,
            channel=CHANNEL_LINK
        )

        keyboard = [[InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]]

        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
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
                await query.edit_message_text(self.get_text('catalog_empty'), reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]]), parse_mode="HTML")
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

        text = self.get_text('product_catalog')
        if total_pages > 1: text += self.get_text('page_indicator', page=page, total_pages=total_pages)
        text += self.get_text('select_category')

        keyboard = []
        for (cat_name,) in categories:
            cursor.execute("SELECT emoji FROM products WHERE category = ? LIMIT 1", (cat_name,))
            res = cursor.fetchone()
            emo = res[0] if res and res[0] else "📂"

            keyboard.append([InlineKeyboardButton(f"{emo} {cat_name}", callback_data=f"category_{cat_name}_1_{page}")])

        nav = []
        if page > 1: nav.append(InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"catalog_page_{page - 1}"))
        if page < total_pages: nav.append(InlineKeyboardButton(self.get_text('next_button'), callback_data=f"catalog_page_{page + 1}"))
        if nav: keyboard.append(nav)

        keyboard.append([InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")])


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

        # --- 🛠️ РОЗРАХУНОК ЦІНИ ---
        all_prices = []
        if product['variants']:
            try:
                v_data = json.loads(product['variants'])
                for v_type, options in v_data.items():
                    for opt, info in options.items():
                        # Витягуємо ціну з варіанту
                        p = float(info['price']) if isinstance(info, dict) else float(info)
                        if p > 0: all_prices.append(p)
            except: pass

        if all_prices:
            # Для варіантів ЗАВЖДИ додаємо "від / from"
            min_p = min(all_prices)
            display_price = self.get_text('price_from', price=min_p).replace('$', CURRENCY_SYMBOL)
        else:
            # Для звичайних товарів — просто ціна
            display_price = f"{product['price']}{CURRENCY_SYMBOL}"

        stock = product['stock']
        stock_status = self.get_text('in_stock') if stock > 5 else (
            self.get_text('low_stock', stock=stock) if stock > 0 else self.get_text('out_of_stock'))

        cursor.execute("SELECT SUM(quantity) FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        in_cart = cursor.fetchone()[0] or 0

        variants_display = ""
        if product['variants']:
            try:
                v_data = json.loads(product['variants'])
                for v_type, options in v_data.items():
                    opt_list = list(options.keys())
                    variants_display += self.get_text('variant_display', v_type=v_type, opt_list=', '.join(map(str, opt_list)))
            except: pass

        text = (
            f"{product['emoji'] or '📦'} <b>{self.escape_html(product['name'])}</b>\n\n"
            f"{self.escape_html(product['description'] or self.get_text('no_description'))}\n"
            f"{variants_display}\n\n"
            f"{self.get_text('product_details', display_price=display_price, stock_status=stock_status, in_cart=in_cart)}"
        )

        keyboard = [
            [InlineKeyboardButton(self.get_text('minus_button'), callback_data=f"prod_minus_{product_id}_{prod_page}_{cat_page}"),
             InlineKeyboardButton(self.get_text('plus_button'), callback_data=f"prod_plus_{product_id}_{prod_page}_{cat_page}")],
            [InlineKeyboardButton(self.get_text('cart_button_count', in_cart=in_cart) if in_cart > 0 else self.get_text('cart_button'), callback_data="cart"),
             InlineKeyboardButton(self.get_text('back_button'), callback_data=f"category_{product['category']}_{prod_page}_{cat_page}")]
        ]

        try:
            if product['image_url']:
                await query.message.delete()
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=product['image_url'], caption=text,
                                             reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            else:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except: pass

    async def handle_add_to_cart_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        try:
            product_id = int(query.data.replace("add_to_cart_", ""))
        except:
            await query.answer(self.get_text('error'))
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

        priority_keys = [
            self.get_text('color_variant_key'), self.get_text('colour_variant_key'),
            self.get_text('color_variant_key_uk'), self.get_text('color_variant_key_ru'),
            self.get_text('size_variant_key'), self.get_text('size_variant_key_uk'),
            self.get_text('size_variant_key_ru')
        ]

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

        name = user_data[0] if user_data and user_data[0] else self.get_text('not_set')
        email = user_data[1] if user_data and user_data[1] else self.get_text('not_set')
        address = user_data[2] if user_data and user_data[2] else self.get_text('not_set')
        phone = user_data[3] if user_data and user_data[3] else self.get_text('not_set')

        shipping_label = self.get_text('shipping_label_profile_ukraine') if SHIPPING_MODE == 'UKRAINE' else self.get_text('shipping_label_profile_international')

        text = self.get_text('profile_details', name=self.escape_html(name), email=self.escape_html(email), shipping_label=shipping_label, address=self.escape_html(address), phone=self.escape_html(phone))

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(self.get_text('edit_name_button'), callback_data="edit_full_name")],
            [InlineKeyboardButton(self.get_text('edit_email_button'), callback_data="edit_email")],
            [InlineKeyboardButton(self.get_text('edit_shipping_info_button'), callback_data="edit_address")],
            [InlineKeyboardButton(self.get_text('edit_phone_button'), callback_data="edit_phone")],
            [InlineKeyboardButton(self.get_text('delete_data_button'), callback_data="profile_delete_menu")],
            [InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]
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

        field_map = {
            "delete_profile_full_name": ("full_name", self.get_text('missing_name')),
            "delete_profile_phone": ("phone", self.get_text('missing_phone')),
            "delete_profile_address": ("address", self.get_text('missing_address_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'missing_address_international')),
            "delete_profile_email": ("email", self.get_text('missing_email'))
        }

        if data not in field_map:
            await query.answer(self.get_text('invalid_action'))
            return

        db_field, display_name = field_map[data]

        # Оновлення бази даних
        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE users SET {db_field} = NULL WHERE user_id = ?", (user_id,))
        self.conn.commit()

        await query.answer(self.get_text('data_deleted', display_name=display_name))

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

        keyboard = [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="my_profile")]]

        # Використовуємо HTML для підтримки жирного тексту та курсиву
        await query.edit_message_text(
            prompt,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def edit_full_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._edit_user_profile_attribute(
            update, context, "full_name",
            self.get_text('enter_full_name')
        )

    async def edit_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._edit_user_profile_attribute(
            update, context, "email",
            self.get_text('enter_email')
        )

    async def edit_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if SHIPPING_MODE == 'UKRAINE':
            prompt = self.get_text('enter_address_ukraine')
        else:
            prompt = self.get_text('enter_address_international')
        await self._edit_user_profile_attribute(update, context, "address", prompt)

    async def edit_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        example = "+380501234567" if SHIPPING_MODE == 'UKRAINE' else "+1234567890"
        await self._edit_user_profile_attribute(
            update, context, "phone",
            self.get_text('enter_phone', example=example)
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

        # --- СЦЕНАРІЙ: КОШИК ПОРОЖНІЙ ---
        if not cart_items:
            text = STORE_MESSAGES[SHIPPING_MODE]['cart_empty']
            keyboard = [
                [InlineKeyboardButton(self.get_text('go_to_catalog_button'), callback_data="catalog")],
                [InlineKeyboardButton(self.get_text('my_orders_button_2'), callback_data="my_orders")],
                [InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]
            ]

            if query.message.photo:
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                               reply_markup=InlineKeyboardMarkup(keyboard),
                                               parse_mode=ParseMode.HTML)
            else:
                try:
                    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                  parse_mode=ParseMode.HTML)
                except Exception:
                    # Якщо повідомлення було видалено (наприклад, скасований інвойс)
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=text,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.HTML
                    )
            return

        # --- СЦЕНАРІЙ: КОШИК З ТОВАРАМИ ---
        total_amount = 0
        text = self.get_text('cart_header')
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
            text += f"{emo} <b>{self.escape_html(name)}</b>{self.escape_html(opts_str)}\n"
            text += f"   {quantity} x {real_price}{CURRENCY_SYMBOL} = {item_total}{CURRENCY_SYMBOL}\n"

            btn_text = f"{name} ({quantity})"
            row_btns = [
                InlineKeyboardButton("➖", callback_data=f"cart_minus_{cart_id}"),
                InlineKeyboardButton(btn_text, callback_data=f"product_{product_id}"),
                InlineKeyboardButton("➕", callback_data=f"cart_plus_{cart_id}")
            ]
            keyboard.append(row_btns)

        # Додаємо роздільну лінію перед підсумком та сам підсумок

        text += self.get_text('cart_total', total_amount=total_amount).replace('$', CURRENCY_SYMBOL)

        keyboard.append([InlineKeyboardButton(self.get_text('checkout_button'), callback_data="checkout")])

        keyboard.append([
            InlineKeyboardButton(self.get_text('clear_cart_button'), callback_data="clear_cart"),
            InlineKeyboardButton(self.get_text('back_to_catalog_button'), callback_data="catalog")
        ])

        keyboard.append([InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")])

        if query.message.photo:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            try:
                # Намагаємося змінити існуюче повідомлення
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                              parse_mode=ParseMode.HTML)
            except Exception:
                # Якщо змінити не вдалося (бо інвойс був видалений), надсилаємо нове
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )

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
            await query.answer(self.get_text('product_not_found'))
            return

        emo = product['emoji'] if product['emoji'] else "📦"

        text = (
            f"{emo} <b>{self.escape_html(product['name'])}</b>\n\n"
            f"📝 {self.escape_html(product['description'])}\n\n"
            f"💰 Price: <b>{product['price']}{CURRENCY_SYMBOL}</b>\n"
            f"📂 Category: {self.escape_html(product['category'])}"
        )

        keyboard = [
            [InlineKeyboardButton(self.get_text('add_one_more_button'), callback_data=f"add_to_cart_options_{product['id']}")],
            [InlineKeyboardButton(self.get_text('back_to_cart_button'), callback_data="my_cart")]
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
                    parse_mode=ParseMode.HTML
                )
            except Exception:

                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text + self.get_text('image_unavailable'),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
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
                # ВИПРАВЛЕНО: використовуємо limit замість max_stock
                await query.answer(self.get_text('stock_limit', limit=max_stock), show_alert=True)
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
            await query.answer(self.get_text('error_parsing_data'))
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT stock, variants FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row: return await query.answer(self.get_text('product_not_found'))

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
                # ВИПРАВЛЕНО: використовуємо limit замість stock
                await query.answer(self.get_text('stock_limit', limit=stock), show_alert=True)
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
                await query.answer(self.get_text('cart_empty_2'))
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


            priority_keys = [
                self.get_text('color_variant_key'), self.get_text('colour_variant_key'),
                self.get_text('color_variant_key_uk'), self.get_text('color_variant_key_ru'),
                self.get_text('size_variant_key'), self.get_text('size_variant_key_uk'),
                self.get_text('size_variant_key_ru')
            ]

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

            if not product: return await query.answer(self.get_text('product_not_found'))

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
                await update.callback_query.answer(self.get_text('session_expired'))
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
                        price_info = f" {val['price']}{CURRENCY_SYMBOL}"
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

        final_keyboard.append([InlineKeyboardButton(self.get_text('cancel_button'), callback_data="cancel_selection")])

        text = self.get_text('select_variant', current_key=current_key)

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
            await query.answer(self.get_text('error_parsing_data'))
            return

        state = self.user_states.get(user_id)
        if not state or state.get('step') != 'selecting_variant':
            await query.answer(self.get_text('session_expired'))
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
                # ТУТ ПРАВИЛЬНО: вже використовується limit
                await query.answer(self.get_text('stock_limit', limit=limit), show_alert=True)
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
            if update.callback_query: await update.callback_query.answer(self.get_text('product_not_found'))
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
                await update.callback_query.answer(self.get_text('limit_reached', limit=limit), show_alert=True)

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
            await update.callback_query.answer(self.get_text('added_to_cart'), show_alert=False)

        await self.show_product(update, context, product_id_override=product_id)


    async def remove_from_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        await query.answer(self.get_text('removed_from_cart'))
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
            await update.callback_query.answer(self.get_text('maximum_amount_reached'), show_alert=True)
            return
        cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        self.conn.commit()
        await update.callback_query.answer(self.get_text('added_amount', product_name=product_name, current_qty=current_qty + 1))
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
            msg = self.get_text('removed_amount', product_name=product_name, current_qty=current_qty - 1)
        else:
            cursor.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
            msg = self.get_text('product_removed_from_cart', product_name=product_name)
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
        await update.callback_query.answer(self.get_text('cart_cleared', items_count=items_count))
        await self.show_cart(update, context)

    async def update_product_view(self, query, product_id, context):
        user_id = query.from_user.id
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()

        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if not product: return

        cursor.execute("SELECT SUM(quantity) FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        in_cart = cursor.fetchone()[0] or 0

        # --- 🛠️ ТАКА Ж ЛОГІКА ЦІНИ ---
        all_prices = []
        if product['variants']:
            try:
                v_data = json.loads(product['variants'])
                for v_type, options in v_data.items():
                    for opt, info in options.items():
                        p = float(info['price']) if isinstance(info, dict) else float(info)
                        if p > 0: all_prices.append(p)
            except:
                pass

        if all_prices:
            min_p = min(all_prices)
            display_price = self.get_text('price_from', price=min_p).replace('$', CURRENCY_SYMBOL)
        else:
            display_price = f"{product['price']}{CURRENCY_SYMBOL}"

        stock = product['stock']
        stock_status = self.get_text('in_stock') if stock > 5 else (
            self.get_text('low_stock', stock=stock) if stock > 0 else self.get_text('out_of_stock'))

        variants_display = ""
        if product['variants']:
            try:
                v_data = json.loads(product['variants'])
                for v_type, options in v_data.items():
                    opt_list = list(options.keys())
                    variants_display += self.get_text('variant_display', v_type=v_type,
                                                      opt_list=', '.join(map(str, opt_list)))
            except:
                pass

        text = (
            f"{product['emoji'] or '📦'} <b>{self.escape_html(product['name'])}</b>\n\n"
            f"{self.escape_html(product['description'] or self.get_text('no_description'))}\n"
            f"{variants_display}\n\n"
            f"{self.get_text('product_details', display_price=display_price, stock_status=stock_status, in_cart=in_cart)}"
        )

        state = self.user_states.get(user_id, {})
        prod_page = state.get('prod_page', 1)
        cat_page = state.get('cat_page', 1)

        keyboard = [
            [InlineKeyboardButton(self.get_text('minus_button'),
                                  callback_data=f"prod_minus_{product_id}_{prod_page}_{cat_page}"),
             InlineKeyboardButton(self.get_text('plus_button'),
                                  callback_data=f"prod_plus_{product_id}_{prod_page}_{cat_page}")],
            [InlineKeyboardButton(
                self.get_text('cart_button_count', in_cart=in_cart) if in_cart > 0 else self.get_text('cart_button'),
                callback_data="cart"),
             InlineKeyboardButton(self.get_text('back_button'),
                                  callback_data=f"category_{product['category']}_{prod_page}_{cat_page}")]
        ]

        try:
            if product['image_url']:
                await query.edit_message_caption(caption=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                 parse_mode="HTML")
            else:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except:
            pass

    # -------------------- CHECKOUT LOGIC --------------------
    async def checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id

        cursor = self.conn.cursor()
        cursor.execute("SELECT product_id FROM cart WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            await query.answer(self.get_text('cart_empty_3'))
            return

        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()
        has_data = user_data and any(user_data)

        self.user_states[user_id] = {'step': 'waiting_full_name'}

        keyboard = []
        if has_data:
            keyboard.append([InlineKeyboardButton(self.get_text('use_profile_data_button'), callback_data="use_profile_data")])
        keyboard.append([InlineKeyboardButton(self.get_text('back_to_cart_button'), callback_data="cart")])
        keyboard.append([InlineKeyboardButton(self.get_text('cancel_order_button'), callback_data="cancel_order")])

        text = self.get_text('checkout_step_1')

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

        await query.answer(self.get_text('loading_profile_data'))

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

        # Якщо юзер виправив одне поле, одразу повертаємо його до перевірки всіх даних
        if state.get('is_editing_single'):
            state['is_editing_single'] = False
            await self.show_order_summary(context, chat_id, user_id)
            return

        from_profile = state.get('from_profile', False)
        header = self.get_text('checkout_profile_loaded_header') if from_profile else self.get_text('checkout_header')

        if not state.get('full_name'):
            state['step'] = 'waiting_full_name'
            text = header + self.get_text('checkout_step_1_of_4', total_steps=total_steps)
            back_callback = "cart"
        elif not state.get('email'):
            state['step'] = 'waiting_email'
            text = header + self.get_text('checkout_step_2_of_4', total_steps=total_steps)
            back_callback = "back_to_name"
        elif not state.get('address'):
            state['step'] = 'waiting_shipping'
            if SHIPPING_MODE == 'UKRAINE':
                text = header + self.get_text('checkout_step_3_of_4_ukraine', total_steps=total_steps)
            else:
                text = header + self.get_text('checkout_step_3_of_4_international', total_steps=total_steps)
            back_callback = "back_to_email"
        elif not state.get('phone'):
            state['step'] = 'waiting_phone'
            example = "+380501234567" if SHIPPING_MODE == 'UKRAINE' else "+1234567890"
            text = header + self.get_text('checkout_step_4_of_4', total_steps=total_steps, example=example)
            back_callback = "back_to_shipping"
        else:
            await self.show_order_summary(context, chat_id, user_id)
            return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(self.get_text('back_button_2'), callback_data=back_callback)],
            [InlineKeyboardButton(self.get_text('cancel_order_button'), callback_data="cancel_order")]
        ])

        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            # Видаляємо старе повідомлення бота, щоб чат не засмічувався
            if 'msg_id' in state:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
                except Exception:
                    pass
            m = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
            state['msg_id'] = m.message_id


    async def handle_payment_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        payment_key = query.data

        if payment_key == 'pay_cod':
            await self.process_final_order(update, context, payment_method="cod")
        elif payment_key == 'pay_card':
            await self.process_final_order(update, context, payment_method="card_manual")
        elif payment_key == 'pay_bank':
            await self.show_bank_payment_info(update, context)
        elif payment_key == 'pay_online':
            # Передаємо ТІЛЬКИ ці три параметри
            await self.send_invoice(update.effective_chat.id, user_id, context)
            return
        elif payment_key == 'confirm_details_back':
            await self.show_order_summary(context, update.effective_chat.id, user_id)
        elif payment_key == 'cancel_order':
            await self.cancel_order(update, context)

    async def handle_checkout_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id
        if user_id not in self.user_states: return
        state = self.user_states[user_id]
        msg = update.message
        chat_id = msg.chat_id

        # "Пилосос": видаляємо повідомлення юзера та попереднє повідомлення бота
        try:
            await msg.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        async def send_error(error_key, back_cb):
            err_text = self.get_text(error_key)
            btns = [
                [InlineKeyboardButton(self.get_text('back_button_2'), callback_data=back_cb)],
                [InlineKeyboardButton(self.get_text('cancel_order_button'), callback_data="cancel_order")]
            ]
            m = await context.bot.send_message(chat_id=chat_id, text=err_text,
                                               reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")
            state['msg_id'] = m.message_id

        is_edit = state.get('is_editing_single', False)

        # --- Крок 1: ПІБ ---
        if state['step'] == 'waiting_full_name':
            name = msg.text.strip()
            if len(name.split()) < 2:
                back = "confirm_details_back" if is_edit else "cart"
                await send_error('err_invalid_name', back)
                return
            state['full_name'] = name
            await self.continue_checkout_flow(update, context)

        # --- Крок 2: Email ---
        elif state['step'] == 'waiting_email':
            email = msg.text.strip()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                back = "confirm_details_back" if is_edit else "back_to_name"
                await send_error('err_invalid_email', back)
                return
            state['email'] = email
            await self.continue_checkout_flow(update, context)

        # --- Крок 3: АДРЕСА (Оновлена перевірка) ---
        elif state['step'] == 'waiting_shipping':
            address = msg.text.strip()
            is_valid = True
            
            if SHIPPING_MODE == 'UKRAINE':
                if len(address.split()) < 2:
                    is_valid = False
            else:
                parts = [p.strip() for p in address.split(',')]
                if len(parts) < 4 or not any(char.isdigit() for char in parts[-1]):
                    is_valid = False

            if not is_valid:
                back = "confirm_details_back" if is_edit else "back_to_email"
                await send_error('err_invalid_address', back)
                return

            state['address'] = address
            await self.continue_checkout_flow(update, context)

        # --- Крок 4: Телефон ---
        elif state['step'] == 'waiting_phone':
            phone = msg.text.strip()
            valid = (
                re.fullmatch(r"^\+380\d{9}$", phone) if SHIPPING_MODE == 'UKRAINE' else re.fullmatch(r"^\+\d{10,15}$",
                                                                                                     phone))
            if not valid:
                back = "confirm_details_back" if is_edit else "back_to_shipping"
                await send_error('err_invalid_phone', back)
                return
            state['phone'] = phone
            await self.show_order_summary(context, chat_id, user_id)

    async def send_payment_keyboard(self, context, chat_id, user_id):
        self.user_states[user_id]['step'] = 'waiting_payment'
        keyboard = []

        if SHIPPING_MODE == 'UKRAINE':
            keyboard.append([InlineKeyboardButton("💵 Готівка при отриманні", callback_data="pay_cod")])
            keyboard.append([InlineKeyboardButton("💳 Картою кур'єру", callback_data="pay_card")])
            keyboard.append([InlineKeyboardButton("📱 Оплатити онлайн (Apple Pay)", callback_data="pay_online")])
            back_text = "🔙 Назад"
            cancel_text = "❌ Скасувати замовлення"
            main_text = "💳 <b>Останній крок: Спосіб оплати</b>\n\nОберіть, як вам зручно оплатити замовлення:"
        else:
            keyboard.append([InlineKeyboardButton("💳 Card / Apple Pay", callback_data="pay_online")])
            back_text = "🔙 Back"
            cancel_text = "❌ Cancel Order"
            main_text = "💳 <b>Final Step: Payment Method</b>\n\nChoose how you want to pay for your order:"

        keyboard.append([InlineKeyboardButton(back_text, callback_data="confirm_details_back")])
        keyboard.append([InlineKeyboardButton(cancel_text, callback_data="cancel_order")])

        # --- ПИЛОСОС: Спочатку пробуємо відредагувати існуюче повідомлення ---
        try:
            msg_id = self.user_states[user_id].get('msg_id')
            if msg_id:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=main_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
            else:
                raise Exception("No msg_id")
        except Exception:
            m = await context.bot.send_message(
                chat_id=chat_id,
                text=main_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            self.user_states[user_id]['msg_id'] = m.message_id

    async def notify_admin_new_order(self, context, order_id, full_name, email, phone, address, payment_method,
                                     products_list, total_amount):
        try:
            # Визначаємо мову заголовків залежно від SHIPPING_MODE
            region_header = self.get_text(
                'new_order_notification_ukraine') if SHIPPING_MODE == 'UKRAINE' else self.get_text(
                'new_order_notification_international')
            address_label = self.get_text(
                'delivery_notification_ukraine') if SHIPPING_MODE == 'UKRAINE' else self.get_text(
                'delivery_notification_international')
            pay_label = self.get_text('payment_notification_ukraine') if SHIPPING_MODE == 'UKRAINE' else self.get_text(
                'payment_notification_international')

            items_str = ""
            for item in products_list:
                opts_str = ""
                # Формуємо рядок обраних варіантів (наприклад: 1L, Red)
                if item.get('selected_options'):
                    opts_vals = [f"{v}" for k, v in item['selected_options'].items()]
                    opts_str = f" ({', '.join(opts_vals)})"

                # --- 🛠️ ВИПРАВЛЕНО: Додано ціну до кожного товару ---
                # Формат: ▫️ Емодзі Назва (Варіант) x Кількість - Ціна₴
                item_price = item.get('price', 0)
                items_str += f"▫️ {item['emoji']} {item['name']}{opts_str} x {item['quantity']} - <b>{item_price}{CURRENCY_SYMBOL}</b>\n"

            # Формуємо повний текст повідомлення за шаблоном з strings.py
            text = self.get_text('admin_new_order_notification',
                                 region_header=region_header,
                                 order_id=order_id,
                                 full_name=full_name,
                                 email=email,
                                 phone=phone,
                                 address_label=address_label,
                                 address=address,
                                 pay_label=pay_label,
                                 payment_method=payment_method,
                                 items_str=items_str,
                                 total_amount=total_amount)

            # Відправляємо адміну
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=text,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(self.get_text('failed_to_notify_admin', admin=ADMIN_ID, e=e))

    # main.py
    async def create_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, send_message=False,
                           payment_method_override=None):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id, {})
        user_name = update.effective_user.full_name

        cursor = self.conn.cursor()

        # 1. Отримуємо товари з кошика
        cursor.execute(
            'SELECT c.id, p.name, p.price, c.quantity, p.emoji, c.selected_options, p.variants, p.id, p.stock FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
            (user_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            return None

        products_list = []
        total_amount = 0

        for row in cart_items:
            cart_id, name, base_price, quantity, emoji, opts_json, variants_json, product_id, current_stock = row
            real_price = self.calculate_item_price(base_price, variants_json, opts_json)
            item_total = real_price * quantity
            total_amount += item_total

            sel_opts = json.loads(opts_json) if opts_json else {}
            products_list.append({
                "product_id": product_id, "name": name, "price": real_price,
                "quantity": quantity, "total": item_total, "emoji": emoji,
                "selected_options": sel_opts
            })

            # --- 🛠 ВИПРАВЛЕНО: СПИСУЄМО ЗІ СКЛАДУ (Загальний + Варіанти) ---
            # Отримуємо свіжі дані з бази, бо в кошику може бути кілька варіантів одного товару
            cursor.execute("SELECT stock, variants FROM products WHERE id = ?", (product_id,))
            p_row = cursor.fetchone()

            if p_row:
                p_stock, p_variants_json = p_row
                new_stock = max(0, p_stock - quantity)
                new_variants_json = p_variants_json

                # Якщо у товару є варіанти, списуємо конкретний варіант
                if p_variants_json and sel_opts:
                    try:
                        v_data = json.loads(p_variants_json)
                        changed = False
                        for key, val in sel_opts.items():
                            if key in v_data:
                                group = v_data[key]
                                if isinstance(group, dict) and val in group:
                                    target = group[val]
                                    if isinstance(target, dict) and 'qty' in target:
                                        target['qty'] = max(0, target['qty'] - quantity)
                                        changed = True
                                    elif isinstance(target, int):
                                        group[val] = max(0, group[val] - quantity)
                                        changed = True
                        if changed:
                            new_variants_json = json.dumps(v_data, ensure_ascii=False)
                    except Exception as e:
                        print(f"Error deducting variants stock: {e}")

                # Оновлюємо товар у базі
                cursor.execute("UPDATE products SET stock = ?, variants = ? WHERE id = ?",
                               (new_stock, new_variants_json, product_id))

        # 3. ЗБЕРІГАЄМО ЗАМОВЛЕННЯ
        full_name = state.get('full_name', user_name)
        email = state.get('email', '—')
        address = state.get('address', '—')
        phone = state.get('phone', '—')
        payment_method = payment_method_override or state.get('payment', 'Unknown')

        cursor.execute(
            "INSERT INTO orders (user_id, user_name, full_name, products, total_amount, phone, address, payment_method, email) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, user_name, full_name, json.dumps(products_list, ensure_ascii=False), total_amount, phone, address,
             payment_method, email)
        )
        order_id = cursor.lastrowid

        # 4. ОЧИЩАЄМО КОШИК
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))

        # 5. ПРИМУСОВО ЗБЕРІГАЄМО ВСІ ЗМІНИ
        self.conn.commit()

        if send_message:
            await self.notify_admin_new_order(context, order_id, full_name, email, phone, address, payment_method,
                                              products_list, total_amount)

        return order_id, products_list, total_amount

    async def choose_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        state = self.user_states.get(user_id)
        if not state: return

        if query.data == "pay_online":
            try:
                await query.message.delete()
            except:
                pass
            # ПЕРЕДАЄМО ТІЛЬКИ ЦІ ТРИ АРГУМЕНТИ
            await self.send_invoice(update.effective_chat.id, user_id, context)
            return

        # Для інших способів оплати логіка залишається (розрахунок суми для finalize_order)
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT p.price, c.quantity, p.variants, c.selected_options FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
            (user_id,))
        total_amount = sum(self.calculate_item_price(p, v, o) * q for p, q, v, o in cursor.fetchall())

        if query.data == "pay_card":
            method_name = self.get_text('method_card_courier')
            await self.finalize_order(update, context, method_name, total_amount)
        else:
            method_name = self.get_text('method_cod')
            await self.finalize_order(update, context, method_name, total_amount)

    async def send_invoice(self, chat_id, user_id, context: ContextTypes.DEFAULT_TYPE):
        try:
            # 1. Отримуємо дані з кошика
            cursor = self.conn.cursor()
            cursor.execute(
                'SELECT p.price, c.quantity, p.variants, c.selected_options FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
                (user_id,)
            )
            cart_data = cursor.fetchall()

            if not cart_data:
                await context.bot.send_message(chat_id=chat_id, text=self.get_text('cart_empty_3'))
                return

            # Рахуємо суму
            total_amount = sum(self.calculate_item_price(p, v, o) * q for p, q, v, o in cart_data)
            telegram_amount = int(total_amount * 100)
            prices = [LabeledPrice(self.get_text('invoice_label'), telegram_amount)]

            # Опис
            description_template = self.get_text('invoice_desc')
            description = (
                f"{description_template}\n"
                f"💰 До сплати: {total_amount} {CURRENCY_SYMBOL}"
            )

            invoice_title = self.get_text('invoice_title', shop_name=SHOP_NAME)

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(self.get_text('pay_button_text'), pay=True)],
                [InlineKeyboardButton(self.get_text('back_button_2'), callback_data="back_to_payment")],
                [InlineKeyboardButton(self.get_text('cancel_order_button'), callback_data="cancel_order")]
            ])

            # --- ВИПРАВЛЕННЯ: Відправляємо інвойс і зберігаємо його в змінну `m` ---
            m = await context.bot.send_invoice(
                chat_id=chat_id,
                title=invoice_title,
                description=description,
                payload=f"order_{user_id}_{int(time.time())}",
                provider_token=PAYMENT_TOKENS['PORTMONE'],
                currency=CURRENCY_CODE,
                prices=prices,
                start_parameter="test-payment",
                is_flexible=False,
                reply_markup=keyboard
            )

            # Зберігаємо ID інвойсу, щоб бот знав, що саме видаляти після оплати
            self.user_states[user_id]['invoice_msg_id'] = m.message_id

        except Exception as e:
            logger.error(f"❌ Error in send_invoice: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Помилка при створенні платежу. Спробуйте інший спосіб оплати або зверніться до підтримки."
            )

    # main.py
    async def precheckout_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.pre_checkout_query
        user_id = query.from_user.id

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT c.quantity, p.name, p.stock FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?",
            (user_id,))

        for qty, name, stock in cursor.fetchall():
            if stock < qty:
                # Використовуємо ключ stock_out_error із strings.py
                return await query.answer(ok=False, error_message=self.get_text('stock_out_error', name=name))

        await query.answer(ok=True)

    async def successful_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_states:
            self.user_states[user_id] = {'step': 'completed'}
        state = self.user_states[user_id]

        try: await update.message.delete()
        except: pass

        if 'invoice_msg_id' in state:
            try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=state['invoice_msg_id'])
            except: pass

        payment_info = update.message.successful_payment
        total_amount = payment_info.total_amount / 100

        # --- 🛠 ВИПРАВЛЕНО: Використовуємо локалізований текст замість "Online Card Payment" ---
        method_name = self.get_text('method_online_card')
        await self.finalize_order(update, context, method_name, total_amount)

    async def handle_checkout_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id
        data = query.data
        state = self.user_states.get(user_id)
        if not state: return

        # Мапа для швидкого редагування: (state_key, назва, наступний крок, приклад)
        edit_map = {
            "edit_check_name": ("full_name", self.get_text('summary_name_label'), "waiting_full_name", "<i>John Doe</i>"),
            "edit_check_email": ("email", self.get_text('summary_email_label'), "waiting_email", "<i>user@gmail.com</i>"),
            "edit_check_address": ("address", self.get_text('summary_address_label'), "waiting_shipping", "<i>Kyiv, NP #15</i>" if SHIPPING_MODE == 'UKRAINE' else "<i>Germany, Berlin, Hauptstraße 10, 10115</i>"),
            "edit_check_phone": ("phone", self.get_text('summary_phone_label'), "waiting_phone", "<i>+380...</i>" if SHIPPING_MODE == 'UKRAINE' else "<i>+1234567890</i>")
        }

        if data in edit_map:
            field_key, display_name, next_step, example = edit_map[data]
            current_val = state.get(field_key, self.get_text('not_set'))
            state['step'] = next_step
            state['is_editing_single'] = True # Мітка для повернення в Summary після вводу

            text = (
                f"<b>{self.get_text('edit_field_title', field=display_name)}</b>\n\n"
                f"<b>{self.get_text('current_value_label')}</b> <code>{self.escape_html(current_val)}</code>\n\n"
                f"<b>{self.get_text('example_label')}</b> {example}\n\n"
                f"{self.get_text('enter_new_value_prompt')}"
            )
            # Кнопка веде назад до підсумку замовлення, а не в профіль!
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(self.get_text('back_to_summary_btn'), callback_data="confirm_details_back")]])
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
            return

        # Стандартна логіка кнопок "Назад" (лінійний потік)
        if data == "back_to_name": state['full_name'] = None
        elif data == "back_to_email": state['email'] = None
        elif data == "back_to_shipping": state['address'] = None
        elif data == "back_to_phone_input": state['phone'] = None
        elif data == "back_to_payment":
            try: await query.message.delete()
            except: pass
            await self.send_payment_keyboard(context, query.message.chat_id, user_id)
            return

        await self.continue_checkout_flow(update, context)

    async def handle_cancel_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context):
            return
        query = update.callback_query
        user_id = update.effective_user.id
        self.user_states.pop(user_id, None)

        await query.answer(self.get_text('order_cancelled_2'))

        # ВИПРАВЛЕННЯ: Видаляємо повідомлення ТІЛЬКИ якщо це інвойс.
        # Якщо це звичайний текст, show_cart його плавно відредагує
        if getattr(query.message, 'invoice', None):
            try:
                await query.message.delete()
            except Exception:
                pass

        await self.show_cart(update, context)

    async def show_order_summary(self, context, chat_id, user_id):
        state = self.user_states[user_id]
        state['step'] = 'waiting_confirmation'
        state['is_editing_single'] = False

        full_name = self.escape_html(state.get('full_name', ''))
        email = self.escape_html(state.get('email', ''))
        address = self.escape_html(state.get('address', ''))
        phone = self.escape_html(state.get('phone', ''))

        missing = self.get_profile_completion_status(user_id)
        promo_header = ""
        separator = "<code>────────────────────</code>"

        if missing:
            promo_text = self.get_text(f'welcome_promo_{len(missing)}')
            labels = []
            if "full_name" in missing: labels.append(self.get_text('missing_name'))
            if "email" in missing: labels.append(self.get_text('missing_email'))
            if "address" in missing:
                key = 'missing_address_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'missing_address_international'
                labels.append(self.get_text(key))
            if "phone" in missing: labels.append(self.get_text('missing_phone'))

            promo_header = f"{promo_text}\n⚠️ <b>Заповніть ці дані:</b> {', '.join(labels)}\n{separator}\n\n"

        base_summary = self.get_text('confirm_details', full_name=full_name, email=email, address=address, phone=phone)
        summary_text = f"{promo_header}{base_summary}"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(self.get_text('summary_edit_name_btn'), callback_data="edit_check_name"),
                InlineKeyboardButton(self.get_text('summary_edit_email_btn'), callback_data="edit_check_email"),
            ],
            [
                InlineKeyboardButton(self.get_text('summary_edit_address_btn'), callback_data="edit_check_address"),
                InlineKeyboardButton(self.get_text('summary_edit_phone_btn'), callback_data="edit_check_phone"),
            ],
            [InlineKeyboardButton(self.get_text('summary_confirm_btn'), callback_data="confirm_details")],
            [InlineKeyboardButton(self.get_text('cancel_order_button'), callback_data="cancel_order")]
        ])

        # --- ПИЛОСОС: Спочатку пробуємо відредагувати існуюче повідомлення ---
        try:
            msg_id = state.get('msg_id')
            if msg_id:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=summary_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            else:
                raise Exception("No msg_id")
        except Exception:
            m = await context.bot.send_message(
                chat_id=chat_id,
                text=summary_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
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
            await query.answer(self.get_text('no_products_yet'))
            return

        ITEMS_PER_PAGE = 5
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        offset = (prod_page - 1) * ITEMS_PER_PAGE

        cursor.execute("SELECT id, name, price, emoji, variants FROM products WHERE category = ? LIMIT ? OFFSET ?",
                       (category, ITEMS_PER_PAGE, offset))
        products = cursor.fetchall()

        text = self.get_text('category_header', category=self.escape_html(category), prod_page=prod_page,
                             total_pages=total_pages)
        keyboard = []

        for p_id, name, base_price, emoji, variants_json in products:
            emo = emoji if emoji else "📦"

            # --- ЛОГІКА ЦІНИ ДЛЯ СПИСКУ ---
            display_price = f"{base_price}{CURRENCY_SYMBOL}"

            if variants_json:
                try:
                    v_data = json.loads(variants_json)
                    all_prices = []
                    for v_type, options in v_data.items():
                        if isinstance(options, dict):
                            for opt, info in options.items():
                                if isinstance(info, dict) and 'price' in info and float(info['price']) > 0:
                                    all_prices.append(float(info['price']))

                    if all_prices:
                        min_p = min(all_prices)
                        display_price = self.get_text('price_from', price=min_p).replace('$', CURRENCY_SYMBOL)
                except:
                    pass

            keyboard.append([InlineKeyboardButton(f"{emo} {name} - {display_price}",
                                                  callback_data=f"product_{p_id}_{prod_page}_{cat_page}")])

        nav = []
        if prod_page > 1:
            nav.append(InlineKeyboardButton(self.get_text('prev_button'),
                                            callback_data=f"category_{category}_{prod_page - 1}_{cat_page}"))
        if prod_page < total_pages:
            nav.append(InlineKeyboardButton(self.get_text('next_button'),
                                            callback_data=f"category_{category}_{prod_page + 1}_{cat_page}"))
        if nav: keyboard.append(nav)

        keyboard.append(
            [InlineKeyboardButton(self.get_text('back_to_catalog_button_2'), callback_data=f"catalog_page_{cat_page}")])

        try:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    async def finalize_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payment_method,
                             pre_calc_total=None):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        query = update.callback_query

        # Якщо state загубився (наприклад, довга оплата), створюємо мінімальний
        if user_id not in self.user_states:
            self.user_states[user_id] = {}
        state = self.user_states[user_id]

        # Встановлюємо спосіб оплати для бази даних
        state['payment'] = payment_method

        # Викликаємо створення замовлення (тут відбувається списування складу та DELETE FROM cart)
        result = await self.create_order(update, context, send_message=True)

        if not result:
            # Якщо кошик раптом порожній
            msg = self.get_text('order_failed_cart_empty')
            await context.bot.send_message(chat_id=chat_id, text=msg)
            return

        order_id, products_list, total_amount = result

        # Формуємо текст чека (беремо заголовок з dom.py та деталі з strings.py)
        from dom import STORE_MESSAGES, SHIPPING_MODE, BOT_TIMEZONE, CURRENCY_SYMBOL
        success_header = STORE_MESSAGES[SHIPPING_MODE]['order_success'].format(order_id=order_id)

        current_time = datetime.now(ZoneInfo(BOT_TIMEZONE)).strftime('%d.%m.%Y %H:%M')

        # Генеруємо детальний список (ПІБ, Товари, Ціна)
        details = self.generate_receipt(
            order_id,
            state.get('full_name', update.effective_user.full_name),
            state.get('email', '—'),
            state.get('phone', '—'),
            state.get('address', '—'),
            payment_method,
            products_list,
            total_amount,
            current_time,
            receipt_format='html'
        )

        final_text = f"{success_header}\n\n{details}"
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]])

        # --- ПИЛОСОС ДЛЯ ЧЕКУ ---
        if query:
            try:
                # Намагаємося плавно змінити меню вибору оплати на фінальний чек
                await query.edit_message_text(
                    text=final_text,
                    reply_markup=kb,
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                # Якщо змінити не вийшло, м'яко видаляємо старе і надсилаємо нове
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=final_text,
                    reply_markup=kb,
                    parse_mode=ParseMode.HTML
                )
        else:
            # Для успішної онлайн-оплати (це не CallbackQuery, а SUCCESSFUL_PAYMENT)
            await context.bot.send_message(
                chat_id=chat_id,
                text=final_text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )

        # ВАЖЛИВО: Очищуємо state тільки в самому кінці, коли замовлення вже в базі і чек показано
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
            keyboard = [[InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]]
            await query.edit_message_text(
                self.get_text('no_orders_yet'),
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

        text = self.get_text('your_orders_header', page=page + 1, total_pages=total_pages)

        keyboard = []
        status_emoji_map = {
            'pending': self.get_text('status_pending'),
            'confirmed': self.get_text('status_confirmed'),
            'shipped': self.get_text('status_shipped'),
            'delivered': self.get_text('status_delivered'),
            'cancelled': self.get_text('status_cancelled')
        }

        for order in orders:
            raw_products = order["products"]
            product_display_list = []
            try:
                products_data = json.loads(raw_products)
                for p in products_data:
                    p_emoji = p.get('emoji', '📦')
                    p_name = re.sub(r'\s*\(?x\d+\)?\)*$', '', str(p.get('name', self.get_text('product'))))
                    product_display_list.append(f"{p_emoji} {p_name}")
            except:
                product_display_list.append(self.get_text('order_items'))

            products_str = ", ".join(product_display_list)
            if len(products_str) > 35: products_str = products_str[:32] + "..."

            status_text = status_emoji_map.get(order['status'], order['status'])
            fmt_date = self.format_date(order['created_at'])

            text += self.get_text('order_summary_line', order_id=order['id'], products_str=self.escape_html(products_str), total_amount=order['total_amount'], status_text=status_text, date=fmt_date)

            keyboard.append([InlineKeyboardButton(self.get_text('details_button', order_id=order['id']),
                                                  callback_data=f"order_details_{order['id']}_{page}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"my_orders_page_{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton(self.get_text('next_button'), callback_data=f"my_orders_page_{page + 1}"))

        if nav:
            keyboard.append(nav)

        keyboard.append([InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")])

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
            if query: await query.answer(self.get_text('order_not_found'))
            return

        products_text = ""
        try:
            products_list = json.loads(order["products"])
            for p in products_list:
                if isinstance(p, str): raise ValueError()


                name = p.get('name', self.get_text('unknown'))

                name = re.sub(r'\s*\(?x\d+\)?\)*$', '', str(name))

                emoji = p.get('emoji', '📦')
                qty = p.get('quantity', 1)
                price_total = p.get('total', 0)

                opts = p.get('selected_options', {})
                opts_str = f" ({', '.join([str(v) for v in opts.values()])})" if opts else ""

                products_text += f"{emoji} {self.escape_html(name)}{self.escape_html(opts_str)} x{qty} = <b>{price_total}{CURRENCY_SYMBOL}</b>\n"
        except:

            raw = order["products"]
            if raw:
                for line in str(raw).split('\n'):
                    if line.strip():
                        products_text += f"📦 {self.escape_html(line)}\n"
            else:
                products_text = self.get_text('items_info_unavailable')


        status_map = {
            'pending': self.get_text('status_pending'),
            'confirmed': self.get_text('status_confirmed'),
            'shipped': self.get_text('status_shipped'),
            'delivered': self.get_text('status_delivered'),
            'cancelled': self.get_text('status_cancelled')
        }
        status_display = status_map.get(order['status'], order['status'])
        fmt_date = self.format_date(order['created_at'])
        pay_method = order['payment_method'] or '—'

        text = self.get_text(
            'order_details_text',
            order_id=order['id'],
            user_name=self.escape_html(order['user_name']),
            email=self.escape_html(order['email'] or '—'),
            phone=self.escape_html(order['phone'] or '—'),
            address=self.escape_html(order['address']),
            payment_method=self.escape_html(pay_method),
            products_text=products_text,
            total_amount=order['total_amount'],
            status_display=status_display,
            date=fmt_date
        )

        keyboard = []
        is_final = order['status'] in ('cancelled', 'delivered')

        if int(user_id) == int(ADMIN_ID):

            if not is_final:
                keyboard.append([
                    InlineKeyboardButton(self.get_text('confirm_button'), callback_data=f"admin_confirm_{order['id']}_{origin_page}"),
                    InlineKeyboardButton(self.get_text('sent_button'), callback_data=f"admin_ship_{order['id']}_{origin_page}")
                ])
                keyboard.append([
                    InlineKeyboardButton(self.get_text('delivered_button'), callback_data=f"admin_deliver_{order['id']}_{origin_page}"),
                    InlineKeyboardButton(self.get_text('cancel_button_2'), callback_data=f"admin_cancel_{order['id']}_{origin_page}")
                ])

            keyboard.append(
                [InlineKeyboardButton(self.get_text('back_to_all_orders_button'), callback_data=f"admin_all_orders_page_{origin_page}")])
        else:

            if not is_final:
                keyboard.append([InlineKeyboardButton(self.get_text('cancel_order_button_3'), callback_data=f"user_cancel_{order['id']}")])
            keyboard.append([InlineKeyboardButton(self.get_text('back_to_list_button'), callback_data=f"my_orders_page_{origin_page}")])

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
            await update.callback_query.answer(self.get_text('access_denied'))
            return

        text = self.get_text('admin_panel_header')

        keyboard = [
            [InlineKeyboardButton(self.get_text('all_orders_button'), callback_data="admin_all_orders")],
            [InlineKeyboardButton(self.get_text('products_button'), callback_data="admin_products")],
            [InlineKeyboardButton(self.get_text('stats_button'), callback_data="admin_statistics"),
             InlineKeyboardButton(self.get_text('revenue_button'), callback_data="admin_revenue_chart")],
            [InlineKeyboardButton(self.get_text('users_button'), callback_data="admin_user_management")],
            [InlineKeyboardButton(self.get_text('main_menu_button_3'), callback_data="main_menu")]
        ]

        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    # -------------------- ADMIN: STATISTICS --------------------
    async def admin_statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) != int(ADMIN_ID):
            await update.callback_query.answer(self.get_text('access_denied'))
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

                    name = item.get('name', self.get_text('unknown'))
                    qty = item.get('quantity', 0)
                    product_sales[name] = product_sales.get(name, 0) + qty
            except:
                continue


        sorted_sales = sorted(product_sales.items(), key=lambda x: x[1], reverse=True)

        top_5 = sorted_sales[:5]
        top_text = "\n".join([f"🔥 {name}: {qty} pcs" for name, qty in top_5]) if top_5 else self.get_text('no_data')

        bottom_5 = sorted_sales[-5:] if len(sorted_sales) > 0 else []
        bottom_text = "\n".join([f"🧊 {name}: {qty} pcs" for name, qty in bottom_5]) if bottom_5 else self.get_text('no_data')


        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM orders")
        active_buyers = cursor.fetchone()[0]

        text = self.get_text(
            'admin_stats_text',
            total_revenue=total_revenue,
            total_orders=total_orders,
            pending_orders=pending_orders,
            total_users=total_users,
            active_buyers=active_buyers,
            top_text=top_text,
            bottom_text=bottom_text
        )

        keyboard = [[InlineKeyboardButton(self.get_text('back_to_admin_panel_button'), callback_data="admin_panel")]]

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

        text = self.get_text('product_management_header')
        if total_pages > 1:
            text += self.get_text('page_indicator_2', page=page, total_pages=total_pages)
        text += self.get_text('select_category_to_edit')

        keyboard = []

        for (cat_name,) in categories:
            cursor.execute("SELECT COUNT(*) FROM products WHERE category = ?", (cat_name,))
            count = cursor.fetchone()[0]
            keyboard.append(
                [InlineKeyboardButton(self.get_text('category_button_count', cat_name=cat_name, count=count), callback_data=f"admin_list_cat_{cat_name}_1")])

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"admin_cat_page_{page - 1}"))

        if page < total_pages:
            nav_row.append(InlineKeyboardButton(self.get_text('next_button'), callback_data=f"admin_cat_page_{page + 1}"))

        if nav_row:
            keyboard.append(nav_row)

        keyboard.append([InlineKeyboardButton(self.get_text('add_product_button'), callback_data="admin_add_product")])

        keyboard.append([InlineKeyboardButton(self.get_text('back_to_admin_panel_button'), callback_data="admin_panel")])

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
                await query.answer(self.get_text('error_parsing_category'))
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

        text = self.get_text('category_header_2', category=category, page=page, total_pages=total_pages)
        keyboard = []

        for p_id, p_name, p_stock in products:
            status = "✅" if p_stock > 0 else "❌"

            keyboard.append([InlineKeyboardButton(f"{status} {p_name}", callback_data=f"admin_prod_{p_id}_{page}")])

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"admin_list_cat_{category}_{page - 1}"))

        if page < total_pages:
            nav_row.append(InlineKeyboardButton(self.get_text('next_button'), callback_data=f"admin_list_cat_{category}_{page + 1}"))

        if nav_row:
            keyboard.append(nav_row)

        keyboard.append([InlineKeyboardButton(self.get_text('back_to_categories_button'), callback_data="admin_products")])

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
            await update.callback_query.answer(self.get_text('access_denied'))
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
            action_text = self.get_text('unblock_button') if blocked else self.get_text('block_button')
            callback_action = 0 if blocked else 1

            try:
                chat = await context.bot.get_chat(user_id)
                if chat.username:
                    user_display = f"@{chat.username}"
                elif chat.first_name:
                    user_display = chat.first_name
                else:
                    user_display = self.get_text('user_id', user_id=user_id)
            except Exception:
                user_display = self.get_text('user_id', user_id=user_id)

            keyboard.append([

                InlineKeyboardButton(self.get_text('user_display', user_display=user_display), callback_data="noop"),
                InlineKeyboardButton(action_text, callback_data=f"admin_user_block_{user_id}_{callback_action}")
            ])

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"admin_user_page_{page - 1}"))
        if page + 1 < total_pages:
            nav_buttons.append(InlineKeyboardButton(self.get_text('next_button'), callback_data=f"admin_user_page_{page + 1}"))

        if nav_buttons:
            keyboard.append(nav_buttons)

        keyboard.append([InlineKeyboardButton(self.get_text('admin_panel_button_2'), callback_data="admin_panel")])

        text = self.get_text('user_management_header', page=page + 1, total_pages=total_pages, total_users=total_users)

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
            await update.callback_query.answer(self.get_text('access_denied'))
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
            await update.callback_query.answer(self.get_text('access_denied'))
            return

        query = update.callback_query

        period_sql = ""
        label = self.get_text('all_time')

        if period == "today":
            period_sql = " AND created_at >= date('now', 'localtime')"
            label = self.get_text('today')
        elif period == "week":
            period_sql = " AND created_at >= date('now', '-7 days')"
            label = self.get_text('last_7_days')
        elif period == "month":
            period_sql = " AND created_at >= date('now', '-30 days')"
            label = self.get_text('last_30_days')

        cursor = self.conn.cursor()

        cursor.execute(
            f"SELECT SUM(total_amount), COUNT(id) FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered'){period_sql}")
        res = cursor.fetchone()
        total_rev = res[0] or 0
        total_orders = res[1] or 0

        avg_check = round(total_rev / total_orders, 2) if total_orders > 0 else 0
        cursor.execute(f"SELECT SUM(total_amount) FROM orders WHERE status = 'pending'{period_sql}")
        pending_rev = cursor.fetchone()[0] or 0

        text = self.get_text(
            'financial_report',
            label=label.upper(),
            total_rev=total_rev,
            avg_check=avg_check,
            total_orders=total_orders,
            pending_rev=pending_rev
        )

        keyboard = [
            [
                InlineKeyboardButton(self.get_text('today_button'), callback_data="rev_today"),
                InlineKeyboardButton(self.get_text('week_button'), callback_data="rev_week"),
                InlineKeyboardButton(self.get_text('month_button'), callback_data="rev_month")
            ],
            [InlineKeyboardButton(self.get_text('all_time_button'), callback_data="rev_all")],
            [InlineKeyboardButton(self.get_text('back_to_admin_panel_button'), callback_data="admin_panel")]
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
            await query.answer(self.get_text('access_denied_not_admin'), show_alert=True)
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
            keyboard = [[InlineKeyboardButton(self.get_text('back_button_2'), callback_data="admin_panel")]]
            await query.edit_message_text(self.get_text('no_orders_in_db'), reply_markup=InlineKeyboardMarkup(keyboard))
            return

        text = self.get_text('all_orders_header', page=page + 1, total_pages=total_pages)
        keyboard = []
        status_emoji = {'pending': '🟡', 'confirmed': '🔵', 'shipped': '🟠', 'delivered': '🟢', 'cancelled': '🔴'}

        for order in orders:
            emoji = status_emoji.get(order["status"], '⚪')
            fmt_date = self.format_date(order['created_at'])
            text += f"{emoji} <code>#{order['id']}</code> | {order['user_name']} | {order['total_amount']}{CURRENCY_SYMBOL} | {fmt_date}\n"
            keyboard.append(
                [InlineKeyboardButton(self.get_text('details_button_2', order_id=order['id']), callback_data=f"order_details_{order['id']}_{page}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"admin_all_orders_page_{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton(self.get_text('next_button'), callback_data=f"admin_all_orders_page_{page + 1}"))

        if nav: keyboard.append(nav)
        keyboard.append([InlineKeyboardButton(self.get_text('back_to_admin_button'), callback_data="admin_panel")])

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
            await query.answer(self.get_text('access_denied'))
            return

        match = re.search(r'admin_(confirm|ship|deliver|cancel)_(\d+)(?:_(\d+))?', query.data)
        if not match:
            await query.answer(self.get_text('error_parsing_data'))
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

        # --- ВИПРАВЛЕННЯ: Повертаємо сток, якщо адмін скасовує замовлення ---
        if new_status == 'cancelled':
            # Перевіряємо поточний статус, щоб не повернути сток двічі
            cursor = self.conn.cursor()
            cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
            row = cursor.fetchone()
            if row and row[0] != 'cancelled':
                self.restore_stock(order_id)
        # --------------------------------------------------------------------

        cursor = self.conn.cursor()
        cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        self.conn.commit()

        await query.answer(self.get_text('status_updated', new_status=new_status))

        try:
            cursor.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
            row = cursor.fetchone()
            if row:
                buyer_id = row[0]
                status_text_map = {
                    'confirmed': self.get_text('status_confirmed'),
                    'shipped': self.get_text('status_shipped'),
                    'delivered': self.get_text('status_delivered'),
                    'cancelled': self.get_text('status_cancelled')
                }
                display_status = status_text_map.get(new_status, new_status)

                # Тексти сповіщень залежно від режиму
                if SHIPPING_MODE == 'UKRAINE':
                    msg_text = self.get_text('order_update_notification_ukraine', order_id=order_id, display_status=display_status)
                else:
                    msg_text = self.get_text('order_update_notification_international', order_id=order_id, display_status=display_status)

                await context.bot.send_message(chat_id=buyer_id, text=msg_text, parse_mode="HTML")
        except Exception as e:
            print(self.get_text('failed_to_notify_user', e=e))

        await self.show_order_details(update, context, order_id=order_id, origin_page=origin_page)

    # -------------------- ADMIN: PRODUCT MANAGEMENT --------------------
    async def admin_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, price, stock, emoji FROM products ORDER BY name")
        products = cursor.fetchall()
        text = self.get_text('product_management_header_2')
        keyboard = []
        for pid, name, price, stock, emoji in products[:20]:
            stock_status = "✅" if stock > 0 else "❌"
            text_line = f"{stock_status} {emoji or ''} **{name}** | {price}{CURRENCY_SYMBOL} | {self.get_text('stock')}: {stock}\n"
            if len(text) + len(text_line) > 4000: break
            text += text_line
            keyboard.append([
                InlineKeyboardButton(f"{emoji or '📦'} {name}", callback_data=f"admin_view_product_{pid}"),
                InlineKeyboardButton(self.get_text('edit_button'), callback_data=f"admin_edit_product_{pid}"),
                InlineKeyboardButton(self.get_text('delete_button'), callback_data=f"admin_delete_product_{pid}")
            ])
        keyboard.append([InlineKeyboardButton(self.get_text('add_product_button_2'), callback_data="admin_add_product")])
        keyboard.append([InlineKeyboardButton(self.get_text('admin_panel_button_3'), callback_data="admin_panel")])
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_handle_order_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        # Перевірка прав адміністратора
        if user_id != ADMIN_ID:
            return

        data = query.data
        parts = data.split("_")
        action = parts[2]
        order_id = int(parts[3])

        cursor = self.conn.cursor()
        # Отримуємо дані замовлення
        cursor.execute("SELECT status, user_id FROM orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()

        if not row:
            await query.answer(self.get_text('order_not_found_2'))
            return

        current_status = row[0]
        buyer_id = row[1]

        # Не дозволяємо міняти статус, якщо замовлення вже не в черзі (pending)
        if current_status != 'pending':
            await query.answer(self.get_text('order_already_status', current_status=current_status))
            return

        if action == "accept":
            # Оновлюємо статус на "Підтверджено"
            cursor.execute("UPDATE orders SET status = 'confirmed' WHERE id = ?", (order_id,))
            self.conn.commit()

            await query.edit_message_text(self.get_text('order_accepted', order_id=order_id))

            # Сповіщаємо покупця про підтвердження
            try:
                status_text = self.get_text('status_confirmed')
                msg_text = self.get_text('order_update_notification_ukraine', order_id=order_id,
                                         display_status=status_text) if SHIPPING_MODE == 'UKRAINE' else self.get_text(
                    'order_update_notification_international', order_id=order_id, display_status=status_text)
                await context.bot.send_message(chat_id=buyer_id, text=msg_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to notify buyer: {e}")

        elif action == "reject":
            # --- 🛠 ВИПРАВЛЕНО: Використовуємо універсальний метод повернення товару ---
            # Це автоматично оновить і загальний stock, і JSON-варіанти
            self.restore_stock(order_id)

            # Оновлюємо статус замовлення на "Скасовано"
            cursor.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
            self.conn.commit()

            await query.edit_message_text(self.get_text('order_rejected', order_id=order_id))

            # Сповіщаємо покупця про скасування
            try:
                status_text = self.get_text('status_cancelled')
                msg_text = self.get_text('order_update_notification_ukraine', order_id=order_id,
                                         display_status=status_text) if SHIPPING_MODE == 'UKRAINE' else self.get_text(
                    'order_update_notification_international', order_id=order_id, display_status=status_text)
                await context.bot.send_message(chat_id=buyer_id, text=msg_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to notify buyer: {e}")

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
            if query: await query.answer(self.get_text('product_not_found_2'))
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
                stock_details = self.get_text('admin_stock_details_header')

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
                                    price_str = f" ({p_val}{CURRENCY_SYMBOL})"
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
            display_price = self.get_text('price_from', price=min_p)
        else:
            display_price = f"{min_p}{CURRENCY_SYMBOL}"


        text = self.get_text(
            'product_management_details',
            product_id=product['id'],
            stock=product['stock'],
            stock_details=stock_details,
            name=product['name'],
            description=product['description'],
            display_price=display_price,
            category=product['category'],
            emoji=product['emoji']
        )

        keyboard = [
            [InlineKeyboardButton(self.get_text('edit_name_button_3'),
                                  callback_data=f"admin_edit_field_name_{product_id}"),
             InlineKeyboardButton(self.get_text('edit_desc_button'),
                                  callback_data=f"admin_edit_field_description_{product_id}")],

            [InlineKeyboardButton(self.get_text('edit_price_button'),
                                  callback_data=f"admin_edit_field_price_{product_id}"),
             InlineKeyboardButton(self.get_text('edit_stock_button'),
                                  callback_data=f"admin_edit_field_stock_{product_id}")],

            [InlineKeyboardButton(self.get_text('edit_category_button'),
                                  callback_data=f"admin_edit_field_category_{product_id}"),
             InlineKeyboardButton(self.get_text('edit_emoji_button'),
                                  callback_data=f"admin_edit_field_emoji_{product_id}")],

            [InlineKeyboardButton(self.get_text('edit_image_button'), callback_data=f"admin_image_menu_{product_id}"),
             InlineKeyboardButton(self.get_text('edit_variants_button'),
                                  callback_data=f"admin_edit_field_variants_{product_id}")],

            [InlineKeyboardButton(self.get_text('delete_product_button'),
                                  callback_data=f"admin_delete_product_confirm_{product_id}")]
        ]

        cat_back = product['category']

        keyboard.append(
            [InlineKeyboardButton(self.get_text('back_to_list_button_2'), callback_data=f"admin_list_cat_{cat_back}_{origin_page}")])

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
        # Шукаємо ID в будь-якому місці рядка
        match = re.search(r"(\d+)$", query.data)
        if not match:
            return await query.answer(self.get_text('invalid_request_2'))

        product_id = int(match.group(1))

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

        if not product:
            return await query.answer(self.get_text('product_not_found_2'))

        text = (
            f"🛠 <b>{self.get_text('admin_control_title')}</b>\n\n"
            f"📌 <b>ID:</b> <code>{product['id']}</code>\n"
            f"📦 <b>Сток:</b> {product['stock']}\n\n"
            f"📝 <b>Назва:</b> {product['name']}\n"
            f"📄 <b>Опис:</b> {product['description']}\n"
            f"💰 <b>Ціна:</b> {product['price']}{CURRENCY_SYMBOL}\n"
            f"📂 <b>Категорія:</b> {product['category']}\n"
            f"😀 <b>Емодзі:</b> {product['emoji']}\n\n"
            f"Виберіть дію:"
        )

        # Кожна кнопка РЕДАГУВАННЯ тепер має однаковий префікс 'admin_edit_field_'
        keyboard = [
            [
                InlineKeyboardButton("✏️ Назва", callback_data=f"admin_edit_field_name_{product_id}"),
                InlineKeyboardButton("✏️ Опис", callback_data=f"admin_edit_field_description_{product_id}")
            ],
            [
                InlineKeyboardButton("✏️ Ціна", callback_data=f"admin_edit_field_price_{product_id}"),
                InlineKeyboardButton("✏️ Сток", callback_data=f"admin_edit_field_stock_{product_id}")
            ],
            [
                InlineKeyboardButton("✏️ Категорія", callback_data=f"admin_edit_field_category_{product_id}"),
                InlineKeyboardButton("✏️ Емодзі", callback_data=f"admin_edit_field_emoji_{product_id}")
            ],
            [
                InlineKeyboardButton("🖼 Зображення", callback_data=f"admin_image_menu_{product_id}"),
                InlineKeyboardButton("⚙️ Варіанти", callback_data=f"admin_edit_field_variants_{product_id}")
            ],
            [InlineKeyboardButton("🗑️ Видалити товар", callback_data=f"admin_delete_product_confirm_{product_id}")],
            [InlineKeyboardButton("🔙 Назад", callback_data=f"admin_list_cat_{product['category']}_1")]
        ]

        await self.safe_edit_or_send(update, context, text, InlineKeyboardMarkup(keyboard))

    async def admin_add_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID: return

        self.user_states[user_id] = {
            'step': 'add_product_name',
            'product_data': {}
        }

        keyboard = [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_products")]]

        msg = await update.callback_query.edit_message_text(
            self.get_text('adding_new_product_name'),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )


        self.user_states[user_id]['msg_id'] = msg.message_id

    async def admin_edit_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        # Витягуємо ID товару з callback_data (наприклад, admin_edit_product_15)
        match = re.search(r"admin_edit_product_(\d+)", query.data)
        if not match:
            return await query.answer(self.get_text('invalid_request_2'))

        product_id = int(match.group(1))

        # Отримуємо поточну назву товару для відображення в заголовку
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        product_name = product[0] if product else ""

        # Клавіатура з кнопками редагування кожного поля.
        # Формат callback_data МАЄ бути: admin_edit_field_{field}_{id}
        keyboard = [
            [InlineKeyboardButton(self.get_text('edit_name'), callback_data=f"admin_edit_field_name_{product_id}")],
            [InlineKeyboardButton(self.get_text('edit_description'),
                                  callback_data=f"admin_edit_field_description_{product_id}")],
            [InlineKeyboardButton(self.get_text('edit_price'), callback_data=f"admin_edit_field_price_{product_id}")],
            [InlineKeyboardButton(self.get_text('edit_category'),
                                  callback_data=f"admin_edit_field_category_{product_id}")],
            [InlineKeyboardButton(self.get_text('edit_emoji'), callback_data=f"admin_edit_field_emoji_{product_id}")],
            [InlineKeyboardButton(self.get_text('edit_stock'), callback_data=f"admin_edit_field_stock_{product_id}")],
            [InlineKeyboardButton(self.get_text('edit_image'), callback_data=f"admin_edit_field_image_{product_id}")],
            [InlineKeyboardButton(self.get_text('back_button'), callback_data=f"admin_prod_{product_id}")]
        ]

        text = f"⚙️ <b>{self.get_text('edit_product_title')}</b>\n\n🏷 <i>{product_name}</i>"

        try:
            await query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            # Якщо повідомлення неможливо відредагувати, надсилаємо нове
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )

    async def admin_edit_field(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        data = query.data

        # Витягуємо поле та ID товару з callback_data
        match = re.search(r"admin_edit_field_(.+)_(\d+)$", data)
        if not match:
            return await query.answer(self.get_text('invalid_request_2'))

        field = match.group(1)
        product_id = int(match.group(2))

        # Дістаємо товар з бази для відображення поточного значення
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

        if not product:
            return await query.answer(self.get_text('product_not_found_2'))

        # Якщо ми редагуємо ВАРІАНТИ
        if field == "variants":
            current_text = self.get_text('none')
            if product['variants']:
                try:
                    v_data = json.loads(product['variants'])
                    lines = []
                    for v_type, options in v_data.items():
                        opt_parts = [f"{opt}={info['qty'] if isinstance(info, dict) else info}" for opt, info in
                                     options.items()]
                        lines.append(f"{v_type}: {', '.join(opt_parts)}")
                    current_text = "<code>" + "\n".join(lines) + "</code>"
                except:
                    current_text = f"<code>{product['variants']}</code>"

            # Викликаємо нашу нову локалізовану інструкцію
            msg_text = self.get_text('editing_variants_instructions', current_text=current_text)

        # Якщо ми редагуємо будь-яке інше поле (Ціна, Опис тощо)
        else:
            current_val = product[field] if product[field] is not None else self.get_text('not_set')

            field_names = {
                "name": self.get_text('summary_name_label'),
                "description": self.get_text('desc'),
                "price": self.get_text('price'),
                "stock": self.get_text('stock'),
                "emoji": self.get_text('emoji'),
                "category": self.get_text('category')
            }
            display_name = field_names.get(field, field.capitalize())

            msg_text = (
                f"<b>{self.get_text('edit_field_title', field=display_name)}</b>\n\n"
                f"<b>{self.get_text('current_value_label')}</b> <code>{current_val}</code>\n\n"
                f"{self.get_text('enter_new_value_prompt')}"
            )

        # Зберігаємо стан для "пилососа" та обробки вводу
        self.user_states[user_id] = {'step': 'edit_product_field', 'product_id': product_id, 'field': field}

        keyboard = [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data=f"admin_prod_{product_id}")]]

        await query.message.delete()
        sent_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=msg_text,
            reply_markup=InlineKeyboardMarkup(
                keyboard) if field != "category" else self.get_existing_categories_keyboard(product_id=product_id),
            parse_mode="HTML"
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

        if not product: return await query.answer(self.get_text('product_not_found_2'))

        has_image = bool(product['image_url'])

        text = self.get_text(
            'product_image_management',
            product_name=product['name'],
            status=self.get_text('admin_img_status_set') if has_image else self.get_text('admin_img_status_none')
        )

        keyboard = []
        if not has_image:
            keyboard.append([InlineKeyboardButton(self.get_text('add_photo_button'), callback_data=f"admin_image_set_{product_id}")])
        else:
            keyboard.append([InlineKeyboardButton(self.get_text('change_photo_button'), callback_data=f"admin_image_set_{product_id}")])
            keyboard.append([InlineKeyboardButton(self.get_text('delete_photo_button'), callback_data=f"admin_image_delete_{product_id}")])

        keyboard.append([InlineKeyboardButton(self.get_text('back_to_editing_button'), callback_data=f"admin_prod_{product_id}")])

        if query.message.photo:
            try: await query.message.delete()
            except: pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

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

            keyboard = [[InlineKeyboardButton(self.get_text('cancel_button_3'), callback_data=f"admin_image_menu_{product_id}")]]
            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=self.get_text('send_product_image'),
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

        await query.answer(self.get_text('image_deleted'))

        try:
            await query.message.delete()
        except Exception:
            pass

        text = self.get_text('product_image_management_no_image')
        keyboard = [
            [InlineKeyboardButton(self.get_text('add_photo_button'), callback_data=f"admin_image_set_{product_id}")],
            [InlineKeyboardButton(self.get_text('back_to_editing_button'), callback_data=f"admin_edit_product_{product_id}")]
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
        if not match: return await query.answer(self.get_text('invalid_request_2'))
        product_id = int(match.group(1))

        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row: return await query.answer(self.get_text('product_not_found_2'))

        name = row[0]

        keyboard = [
            [InlineKeyboardButton(self.get_text('yes_delete_button'), callback_data=f"admin_delete_product_confirm_{product_id}")],
            [InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_products")]
        ]

        try:
            await query.message.delete()
        except:
            pass

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=self.get_text('confirm_delete_product', name=name),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    async def admin_wizard_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        query = update.callback_query

        self.user_states.pop(update.effective_user.id, None)

        await query.answer(self.get_text('cancelled'))

        await self.admin_categories_menu(update, context)

    async def admin_delete_product_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID: return
        query = update.callback_query
        match = re.match(r"admin_delete_product_confirm_(\d+)", query.data)
        if not match: return await query.answer(self.get_text('invalid_request_2'))
        product_id = int(match.group(1))

        cursor = self.conn.cursor()

        cursor.execute("SELECT name, category FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()

        if not row:
            await query.answer(self.get_text('product_already_deleted'))
            await self.admin_categories_menu(update, context)
            return

        name = row[0]
        category_to_return = row[1]


        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        self.conn.commit()

        await query.answer(self.get_text('product_deleted', name=name))


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
            await update.message.reply_text(self.get_text('use_start'))

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
        error_kb = InlineKeyboardMarkup([[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="my_profile")]])

        # --- ПІБ ---
        if state['step'] == 'waiting_full_name_profile':
            if len(text.split()) < 2:
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('err_invalid_name'), reply_markup=error_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            cursor.execute("UPDATE users SET full_name = ? WHERE user_id = ?", (text, user_id))

        # --- Email ---
        elif state['step'] == 'waiting_email_profile':
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('err_invalid_email'), reply_markup=error_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return
            cursor.execute("UPDATE users SET email = ? WHERE user_id = ?", (text, user_id))

        # --- Телефон (Виправлено перевірку) ---
        elif state['step'] == 'waiting_phone_profile':
            is_valid = (re.fullmatch(r"^\+380\d{9}$", text) if SHIPPING_MODE == 'UKRAINE' else re.fullmatch(r"^\+\d{10,15}$", text))
            if not is_valid:
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('err_invalid_phone'), reply_markup=error_kb, parse_mode="HTML")
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
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('err_invalid_address'), reply_markup=error_kb, parse_mode="HTML")
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

        cursor = self.conn.cursor()
        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if not row:
            await query.answer(self.get_text('session_expired'))
            await self.show_profile(update, context)
            return

        full_name, email, address, phone = row
        keyboard = []

        # Використовуємо локалізовані кнопки
        if full_name:
            keyboard.append([InlineKeyboardButton(self.get_text('delete_name_btn'), callback_data="delete_profile_full_name")])
        if email:
            keyboard.append([InlineKeyboardButton(self.get_text('delete_email_btn'), callback_data="delete_profile_email")])
        if address:
            label_key = 'delete_shipping_ukr_btn' if SHIPPING_MODE == 'UKRAINE' else 'delete_shipping_int_btn'
            keyboard.append([InlineKeyboardButton(self.get_text(label_key), callback_data="delete_profile_address")])
        if phone:
            keyboard.append([InlineKeyboardButton(self.get_text('delete_phone_btn'), callback_data="delete_profile_phone")])

        keyboard.append([InlineKeyboardButton(self.get_text('back_to_profile_btn'), callback_data="my_profile")])

        try:
            await query.message.delete()
        except:
            pass

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=self.get_text('profile_delete_title'),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    async def handle_edit_field_input(self, update, context, state, input_value, msg):
        user_id = update.effective_user.id
        product_id = state.get('product_id')
        field = state.get('field')
        chat_id = update.effective_chat.id

        # --- 🧹 ПИЛОСОС ---
        try:
            await update.message.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        async def send_error(text, kb):
            new_msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
            state['msg_id'] = new_msg.message_id

        cancel_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(self.get_text('cancel_button'), callback_data=f"admin_prod_{product_id}")
        ]])

        cursor = self.conn.cursor()
        value = input_value
        new_total_stock = None  # Для синхронізації

        # 1. Валідація чисел
        if field in ['price', 'stock']:
            try:
                clean_text = str(input_value).replace('$', '').replace(' ', '').replace(',', '.').strip()
                value = float(clean_text) if field == 'price' else int(clean_text)
            except ValueError:
                example_val = "1250" if field == 'price' else "10"
                await send_error(self.get_text('err_invalid_number', val=input_value, ex_val=example_val), cancel_kb)
                return

        # 2. Валідація ВАРІАНТІВ + РОЗРАХУНОК СТОКУ
        elif field == 'variants':
            try:
                if ":" in str(input_value):
                    v_type_part, options_part = str(input_value).split(":", 1)
                    v_type = v_type_part.strip()
                    options_list = options_part.split(",")
                    v_data = {v_type: {}}

                    calculated_stock = 0
                    for opt in options_list:
                        parts = opt.strip().split("=")
                        opt_name = parts[0].strip()
                        qty = int(parts[1].strip()) if len(parts) > 1 else 0
                        price = float(parts[2].strip()) if len(parts) > 2 else 0
                        v_data[v_type][opt_name] = {"qty": qty, "price": price}
                        calculated_stock += qty  # Додаємо до загального залишку

                    value = json.dumps(v_data, ensure_ascii=False)
                    new_total_stock = calculated_stock
                else:
                    raise ValueError()
            except Exception:
                await send_error(self.get_text('err_variant_format', val=input_value), cancel_kb)
                return
        else:
            value = input_value

        # 3. ОНОВЛЕННЯ БД (Синхронізовано)
        try:
            if new_total_stock is not None:
                # Оновлюємо і варіанти, і загальний сток одним запитом
                cursor.execute("UPDATE products SET variants = ?, stock = ? WHERE id = ?",
                               (value, new_total_stock, product_id))
            else:
                cursor.execute(f"UPDATE products SET {field} = ? WHERE id = ?", (value, product_id))
            self.conn.commit()
        except Exception as e:
            await send_error(f"❌ DB Error: {e}", cancel_kb)
            return

        # 4. УСПІХ
        self.user_states.pop(user_id, None)
        field_names = {"name": self.get_text('summary_name_label'), "price": self.get_text('price'),
                       "stock": self.get_text('stock'), "variants": self.get_text('variants')}
        display_field = field_names.get(field, str(field).capitalize())

        await context.bot.send_message(
            chat_id=chat_id,
            text=self.get_text('status_updated', new_status=f"<b>{display_field}</b>"),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.get_text('back_button_3'), callback_data=f"admin_prod_{product_id}")]]),
            parse_mode="HTML"
        )

    async def handle_admin_product_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID or user_id not in self.user_states:
            return

        state = self.user_states[user_id]
        step = state.get("step")
        msg = update.message
        chat_id = msg.chat_id

        # --- 🧹 ПИЛОСОС (Видаляємо повідомлення адміна та попередню інструкцію бота) ---
        try:
            await msg.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        # Отримання значення: фото або текст
        if update.message.photo:
            input_value = update.message.photo[-1].file_id
            is_photo = True
        else:
            input_value = update.message.text.strip() if update.message.text else ""
            is_photo = False

        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_wizard_cancel")]])

        # --- 1. ОБРОБКА РЕДАГУВАННЯ ПОЛЯ (для існуючих товарів) ---
        if step == 'edit_product_field':
            await self.handle_edit_field_input(update, context, state, input_value, msg)
            return

        # --- 2. ОБРОБКА ОНОВЛЕННЯ ФОТО (меню зображень) ---
        if step == 'waiting_product_image':
            img = input_value if (is_photo or input_value.startswith('http')) else None
            if img or input_value == '-':
                cursor = self.conn.cursor()
                cursor.execute("UPDATE products SET image_url = ? WHERE id = ?", (None if input_value == '-' else img, state.get('product_id')))
                self.conn.commit()
                self.user_states.pop(user_id, None)
                kb = [[InlineKeyboardButton(self.get_text('back_button_3'), callback_data=f"admin_prod_{state.get('product_id')}")]]
                await context.bot.send_message(chat_id=chat_id, text=self.get_text('admin_img_status_set'), reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
            else:
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('error_photo_required'), reply_markup=cancel_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
            return

        # --- 3. КРОКИ ДОДАВАННЯ НОВОГО ТОВАРУ ---
        if step == 'add_product_name':
            state['product_data'] = {'name': input_value}
            state['step'] = 'add_product_description'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('enter_product_description'), reply_markup=cancel_kb, parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'add_product_description':
            state['product_data']['description'] = input_value
            kb = [[InlineKeyboardButton(self.get_text('simple_product_button'), callback_data="admin_decision_vars_no")],
                  [InlineKeyboardButton(self.get_text('has_variants_button'), callback_data="admin_decision_vars_yes")],
                  [InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_wizard_cancel")]]
            state['step'] = 'waiting_type_decision'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('choose_product_type'), reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_simple_price':
            try:
                clean_text = input_value.replace('$', '').replace(' ', '').replace(',', '.').strip()
                state['product_data']['price'] = float(clean_text)
                state['step'] = 'waiting_simple_stock'
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('admin_wizard_simple_stock'), reply_markup=cancel_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
            except:
                error_msg = self.get_text('err_invalid_number', val=input_value, ex_val="1250")
                m = await context.bot.send_message(chat_id=chat_id, text=error_msg, reply_markup=cancel_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id

        elif step == 'waiting_simple_stock':
            try:
                state['product_data']['stock'] = int(input_value)
                state['step'] = 'waiting_simple_category'
                # Використовуємо клавіатуру з існуючими категоріями
                m = await context.bot.send_message(
                    chat_id=chat_id,
                    text=self.get_text('enter_category'),
                    reply_markup=self.get_existing_categories_keyboard(),
                    parse_mode="HTML"
                )
                state['msg_id'] = m.message_id
            except:
                error_msg = self.get_text('err_invalid_number', val=input_value, ex_val="10")
                m = await context.bot.send_message(chat_id=chat_id, text=error_msg, reply_markup=cancel_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id

        elif step == 'waiting_simple_category':
            state['product_data']['category'] = input_value
            state['step'] = 'waiting_simple_emoji'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('enter_emoji'), reply_markup=cancel_kb, parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_simple_emoji':
            state['product_data']['emoji'] = input_value
            state['step'] = 'waiting_simple_image'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('admin_wizard_variant_photo'), reply_markup=cancel_kb, parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_simple_image' or step == 'waiting_var_image':
            img = input_value if (is_photo or input_value.startswith('http')) else None
            if not img and input_value != '-':
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('error_photo_required'), reply_markup=cancel_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return

            if step == 'waiting_simple_image':
                p = state['product_data']
                cursor = self.conn.cursor()
                cursor.execute("INSERT INTO products (name, description, price, image_url, category, stock, emoji) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (p['name'], p['description'], p['price'], img if input_value != '-' else None, p['category'], p['stock'], p['emoji']))
                self.conn.commit()
                self.user_states.pop(user_id, None)
                await context.bot.send_message(chat_id=chat_id, text=self.get_text('product_created', name=p['name']), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(self.get_text('back_button_3'), callback_data="admin_products")]]), parse_mode="HTML")
            else:
                state['product_data']['image_url'] = img if input_value != '-' else None
                state['step'] = 'waiting_var_category'
                m = await context.bot.send_message(
                    chat_id=chat_id,
                    text=self.get_text('enter_category'),
                    reply_markup=self.get_existing_categories_keyboard(),
                    parse_mode="HTML"
                )
                state['msg_id'] = m.message_id

        elif step == 'waiting_var_category':
            state['product_data']['category'] = input_value
            state['step'] = 'waiting_var_emoji'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('enter_emoji'), reply_markup=cancel_kb, parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_var_emoji':
            state['product_data']['emoji'] = input_value
            state['step'] = 'add_product_variants_loop'
            await self.show_variant_type_selection(context, chat_id, user_id)

        elif step == 'waiting_variant_values':
            await self.process_variant_values_input(update, context)

    async def show_variant_type_selection(self, context, chat_id, user_id, status_msg="", edit_query=None):
        state = self.user_states[user_id]
        variants = state['product_data'].get('variants', {})

        added_info = ""
        if variants:
            v_type_raw = list(variants.keys())[0]
            # Перекладаємо назву типу (Size -> Розмір)
            v_type_localized = self.get_text(f'type_{v_type_raw}')
            if v_type_localized == f"_type_{v_type_raw}_": v_type_localized = v_type_raw

            options_list = ', '.join(variants[v_type_raw].keys())
            # У обох цих методах замініть рядок формування added_info:
            added_info = (
                f"{self.get_text('active_variant_label')}{v_type_localized} (<i>{options_list}</i>)\n"
                f"────────────────────\n\n"
            )
        header = self.get_text('status_message', status_msg=status_msg) if status_msg else ""
        # ПЕРЕДАЄМО added_info, щоб не було KeyError
        text = f"{header}{self.get_text('admin_wizard_variant_title', added_info=added_info)}"

        if edit_query:
            try:
                await edit_query.edit_message_text(text, reply_markup=self.get_variant_type_keyboard(),
                                                   parse_mode="HTML")
                return
            except Exception:
                pass

        m = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=self.get_variant_type_keyboard(),
                                           parse_mode="HTML")
        state['msg_id'] = m.message_id

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.warning(f'Update {update} caused error {context.error}')
        try:
            if hasattr(update, 'effective_message') and update.effective_message:
                await update.effective_message.reply_text(self.get_text('error_handler'))
        except Exception:
            pass

    async def handle_variant_type_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state:
            return

        # Отримуємо тип варіанту з даних кнопки (наприклад, Size, Color)
        data = query.data.replace("vartype_", "")

        # Якщо адмін натиснув "Готово"
        if data == "DONE":
            p = state.get('product_data', {})
            vars_data = p.get('variants', {})

            # Перевірка, чи були додані варіанти взагалі
            if not vars_data:
                await query.answer(self.get_text('add_variants_before_finishing'), show_alert=True)
                return

            # Розрахунок загального залишку на основі всіх доданих варіантів
            total_stock = 0
            for v_type in vars_data:
                for opt in vars_data[v_type].values():
                    total_stock += opt.get('qty', 0) if isinstance(opt, dict) else opt

            # Збереження товару в базу даних з ціною 0.0 (ціна буде братися з варіантів)
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO products (name, description, price, image_url, emoji, category, stock, variants) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (p.get("name"), p.get("description"), 0.0, p.get("image_url"), p.get("emoji", "📦"),
                 p.get("category"), total_stock, json.dumps(vars_data, ensure_ascii=False))
            )
            self.conn.commit()

            # Очищуємо стан користувача
            self.user_states.pop(user_id, None)

            # Видаляємо меню вибору та шлемо повідомлення про успіх
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=self.get_text('product_created_2', name=p.get('name')),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(self.get_text('back_button_3'), callback_data="admin_products")]]),
                parse_mode="HTML"
            )
            return

        # Якщо вибрано конкретний тип варіанту (Size, Color тощо)
        state['current_variant_type'] = data
        state['step'] = 'waiting_variant_values'

        # 1. Локалізуємо назву типу (наприклад, Size -> Розмір) за допомогою ключів type_
        v_type_localized = self.get_text(f'type_{data}')
        if v_type_localized == f"_type_{data}_":
            v_type_localized = data

        # 2. Отримуємо локалізований приклад для цього типу
        ex = self.get_text(f'admin_ex_{data}')
        if ex == f"_admin_ex_{data}_":
            ex = self.get_text('admin_ex_default')

        # 3. Формуємо текст запиту через шаблон variant_input_prompt
        text = self.get_text('variant_input_prompt', v_type=v_type_localized, example=ex)

        kb = [
            [
                InlineKeyboardButton(self.get_text('back_to_types_btn'), callback_data="admin_step_variants_init"),
                InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_wizard_cancel")

            ]
        ]

        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    async def admin_handle_variant_type_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        variant_type_raw = query.data.replace("admin_add_variant_type_", "")

        v_type_localized = self.get_text(f'type_{variant_type_raw}')
        if v_type_localized == f"_type_{variant_type_raw}_": v_type_localized = variant_type_raw

        ex = self.get_text(f'admin_ex_{variant_type_raw}')
        if ex == f"_admin_ex_{variant_type_raw}_": ex = self.get_text('admin_ex_default')

        text = self.get_text('variant_input_prompt', v_type=v_type_localized, example=ex)
        kb = [[InlineKeyboardButton(self.get_text('back_to_types'), callback_data="admin_step_variants_init")]]

        if user_id not in self.user_states: self.user_states[user_id] = {}
        self.user_states[user_id]['step'] = 'waiting_variant_values'
        self.user_states[user_id]['current_variant_type'] = variant_type_raw

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    async def admin_handle_variant_decision(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            query = update.callback_query
            user_id = update.effective_user.id
            data = query.data

            # Видаляємо попереднє повідомлення для чистоти інтерфейсу
            try:
                await query.message.delete()
            except:
                pass

            # Перевірка наявності стану
            if user_id not in self.user_states:
                self.user_states[user_id] = {'product_data': {}}

            if data == "admin_decision_vars_no":
                # Якщо товар без варіантів — переходимо до вводу ціни
                self.user_states[user_id]['step'] = 'waiting_simple_price'
                text = self.get_text('admin_wizard_simple_price')
            else:
                # Якщо товар з варіантами — спочатку питаємо фото
                self.user_states[user_id]['step'] = 'waiting_var_image'
                self.user_states[user_id]['product_data']['variants'] = {}
                text = self.get_text('admin_wizard_variant_photo')

            cancel_kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_wizard_cancel")]])

            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=cancel_kb,
                parse_mode="HTML"
            )
            self.user_states[user_id]['msg_id'] = msg.message_id

    async def admin_back_to_variant_types(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        if user_id not in self.user_states:
            return await query.answer(self.get_text('session_expired'))

        state = self.user_states[user_id]
        state['step'] = 'add_product_variants_loop'

        # Отримуємо дані про вже додані варіанти
        variants = state.get('product_data', {}).get('variants', {})
        # Шукайте цей рядок у методах роботи з варіантами (приблизно 4160 та 4349 рядки):
        added_info = (
            f"{self.get_text('active_variant_label')}{v_type_localized} (<i>{options_list}</i>)\n"
            f"────────────────────"
        )

        if variants:
            # Беремо перший ключ (тип варіанту, наприклад 'color')
            v_type_raw = list(variants.keys())[0]
            # Пробуємо локалізувати назву типу
            v_type_localized = self.get_text(f'type_{v_type_raw}')
            if v_type_localized == f"_type_{v_type_raw}_":
                v_type_localized = v_type_raw

            # Формуємо список значень (наприклад: Red, Blue)
            options_list = ', '.join(variants[v_type_raw].keys())

            # ВИПРАВЛЕНО: Використовуємо моноширинну стабільну лінію
            added_info = (
                f"{self.get_text('active_variant_label')}{v_type_localized} (<i>{options_list}</i>)\n"
                f"────────────────────"
            )

        # Формуємо текст повідомлення
        text = self.get_text('admin_wizard_variant_title', added_info=added_info)
        reply_markup = self.get_variant_type_keyboard()

        # Оновлюємо інтерфейс
        try:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except:
            # Якщо повідомлення не змінилося, просто ігноруємо помилку
            await query.answer()

    def get_existing_categories_keyboard(self, product_id=None):
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT category FROM products")
        categories = [row[0] for row in cursor.fetchall() if row[0]]

        keyboard = []
        for i in range(0, len(categories), 2):
            row = []
            for cat in categories[i:i + 2]:
                row.append(InlineKeyboardButton(cat, callback_data=f"admin_set_cat_{cat}"))
            keyboard.append(row)

        # ВИПРАВЛЕНО: Якщо є product_id, повертаємо до редагування товару
        cancel_callback = f"admin_prod_{product_id}" if product_id else "admin_wizard_cancel"
        keyboard.append([InlineKeyboardButton(self.get_text('cancel_button'), callback_data=cancel_callback)])
        return InlineKeyboardMarkup(keyboard)

    async def admin_handle_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        if user_id != ADMIN_ID or user_id not in self.user_states:
            await query.answer("❌ Session expired")
            return

        category = query.data.replace("admin_set_cat_", "")
        state = self.user_states[user_id]
        chat_id = query.message.chat_id

        await query.answer(f"Selected: {category}")

        # --- КЕЙС 1: РЕДАГУВАННЯ КАТЕГОРІЇ ІСНУЮЧОГО ТОВАРУ ---
        # Виправлено: перевіряємо ключ 'field', як він зберігається в admin_edit_field
        if state.get('field') == 'category':
            product_id = state.get('product_id')
            cursor = self.conn.cursor()
            cursor.execute("UPDATE products SET category = ? WHERE id = ?", (category, product_id))
            self.conn.commit()

            self.user_states.pop(user_id, None)

            try:
                await query.message.delete()
            except:
                pass

            # Локалізоване повідомлення про успіх
            text = self.get_text('admin_category_updated_success', category=category)
            if text == f"_admin_category_updated_success_":  # якщо ключа немає в strings.py
                text = f"✅ Category updated to: <b>{category}</b>"

            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(self.get_text('back_button_3'), callback_data=f"admin_prod_{product_id}")]]),
                parse_mode="HTML"
            )
            return

        # --- КЕЙС 2 ТА 3: СТВОРЕННЯ НОВОГО ТОВАРУ ---
        step = state.get('step')

        # Видаляємо повідомлення з клавіатурою категорій
        try:
            await query.message.delete()
        except:
            pass

        cancel_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_wizard_cancel")]])

        if step == 'waiting_simple_category':
            # Зберігаємо категорію і переходимо до ЕМОДЗІ (а не до INSERT)
            state['product_data']['category'] = category
            state['step'] = 'waiting_simple_emoji'

            m = await context.bot.send_message(
                chat_id=chat_id,
                text=self.get_text('enter_emoji'),
                reply_markup=cancel_kb,
                parse_mode="HTML"
            )
            state['msg_id'] = m.message_id

        elif step == 'waiting_var_category':
            # Для товарів з варіантами — теж до ЕМОДЗІ
            state['product_data']['category'] = category
            state['step'] = 'waiting_var_emoji'

            m = await context.bot.send_message(
                chat_id=chat_id,
                text=self.get_text('enter_emoji'),
                reply_markup=cancel_kb,
                parse_mode="HTML"
            )
            state['msg_id'] = m.message_id

    async def handle_checkout_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id
        data = query.data

        # 1. Підтвердження даних (з Summary до вибору оплати)
        if data == "confirm_details":
            await self.send_payment_keyboard(context, query.message.chat_id, user_id)

        # 2. Назад з вибору оплати до перевірки даних (Summary)
        elif data == "confirm_details_back":
            await self.show_order_summary(context, query.message.chat_id, user_id)

        # 3. НАЗАД з інвойсу до вибору способу оплати
        elif data == "back_to_payment":
            try:
                # Інвойс не можна відредагувати, тому ми його видаляємо
                await query.message.delete()
            except:
                pass
            # Видаляємо старий msg_id, щоб змусити бота надіслати нове повідомлення
            self.user_states[user_id].pop('msg_id', None)
            await self.send_payment_keyboard(context, query.message.chat_id, user_id)

        await query.answer()


    async def process_variant_values_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states[user_id]
        text = update.message.text.strip()
        chat_id = update.effective_chat.id
        v_type = state.get('current_variant_type')

        try:
            opts_map = {}
            for pair in text.split(','):
                p = pair.strip().split('=')
                if len(p) == 3:
                    opts_map[p[0].strip()] = {"qty": int(p[1]), "price": float(p[2])}
                elif len(p) == 2:
                    opts_map[p[0].strip()] = int(p[1])
                else:
                    raise ValueError()

            # ЗАМІЩЕННЯ: Тільки один тип варіантів активний
            state['product_data']['variants'] = {v_type: opts_map}
            await self.show_variant_type_selection(context, chat_id, user_id, status_msg=f"Variant set to: {v_type}")


        except Exception:

            examples = {"Size": "S=10=1200", "Color": "Red=5=500", "Memory": "128GB=10=800"}

            ex = examples.get(v_type, self.get_text('variant_default_format'))

            text_err = (

                f"{self.get_text('variant_error_title', v_type=v_type)}\n\n"

                f"{self.get_text('variant_error_msg')}\n"

                f"{self.get_text('variant_example_label')} <code>{ex}</code>"

            )

            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.get_text('back_button'), callback_data="admin_step_variants_init")]])

            m = await context.bot.send_message(chat_id=chat_id, text=text_err, reply_markup=kb, parse_mode="HTML")

            state['msg_id'] = m.message_id


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
    application.add_handler(CallbackQueryHandler(bot.admin_back_to_variant_types, pattern=r'^admin_step_variants_init$'))
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