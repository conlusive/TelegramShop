import logging
import json
import sqlite3
import time
import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, \
    PreCheckoutQueryHandler
from telegram.constants import ParseMode
from strings import STRINGS
from dom import (
    BOT_TOKEN, ADMIN_ID, BOT_TIMEZONE, SHIPPING_MODE,
    DB_NAME, SHOP_NAME, CURRENCY_SYMBOL, STORE_MESSAGES,
    SUPPORT_USER, CHANNEL_LINK, PAYMENT_TOKENS, CURRENCY_CODE
)

# ==================== ЛІЦЕНЗІЯ ТА АДМІНИ ====================

LICENSE_TYPE = "Basic" # "Basic" , "Pro"

if isinstance(ADMIN_ID, str):
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_ID.split(',') if x.strip().isdigit()]
elif isinstance(ADMIN_ID, int):
    ADMIN_IDS = [ADMIN_ID]
elif isinstance(ADMIN_ID, list):
    ADMIN_IDS = [int(x) for x in ADMIN_ID]
else:
    ADMIN_IDS = []

if LICENSE_TYPE == "Basic" and len(ADMIN_IDS) > 1:
    print("⚠️ NOTE: The 'Basic' license only supports 1 administrator. Only the first one is left.")
    ADMIN_IDS = [ADMIN_IDS[0]]

# -------------------- LOGGING --------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 5
ADMIN_ITEMS_PER_PAGE = 10


class OnlineShopBot:
    def __init__(self):
        self.init_database()
        self.user_states = {}

    def get_text(self, key, **kwargs):
        lang = SHIPPING_MODE if SHIPPING_MODE in STRINGS else 'INTERNATIONAL'
        # Автоматично передаємо обидва варіанти символів у кожен текст
        kwargs.setdefault('currency_symbol', CURRENCY_SYMBOL)
        kwargs.setdefault('symbol', CURRENCY_SYMBOL)

        try:
            return STRINGS[lang].get(key, f"_{key}_").format(**kwargs)
        except KeyError as e:
            # Захист від падінь: якщо в тексті є зайва змінна, бот не впаде, а просто виведе текст
            logger.error(f"Помилка форматування тексту для ключа '{key}': не вистачає змінної {e}")
            return STRINGS[lang].get(key, f"_{key}_").replace(f"{{{e.args[0]}}}", "")

    # -------------------- DATABASE --------------------
    def _add_column_if_not_exists(self, cursor, table_name: str, column_name: str, column_type: str):
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]
        if column_name not in columns:
            try:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                logger.info(self.get_text('column_added', column_name=column_name, table_name=table_name))
            except Exception as e:
                logger.error(self.get_text('error_adding_column', column_name=column_name, table_name=table_name, e=e))

    def init_database(self):
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = self.conn.cursor()

        cursor.execute('''CREATE TABLE IF NOT EXISTS products
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
                          )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS cart
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
            product_id
            INTEGER
            NOT
            NULL,
            quantity
            INTEGER
            DEFAULT
            1,
            selected_options
            TEXT,
            FOREIGN
            KEY
                          (
            product_id
                          ) REFERENCES products
                          (
                              id
                          ))''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS orders
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
                              full_name
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
                          )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS users
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
                              full_name
                              TEXT,
                              blocked
                              INTEGER
                              DEFAULT
                              0
                          )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS promocodes
                          (
                              id
                              INTEGER
                              PRIMARY
                              KEY
                              AUTOINCREMENT,
                              code
                              TEXT
                              UNIQUE,
                              discount
                              INTEGER,
                              max_uses
                              INTEGER
                              DEFAULT
                              100,
                              current_uses
                              INTEGER
                              DEFAULT
                              0,
                              is_reusable
                              INTEGER
                              DEFAULT
                              0
                          )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS used_promocodes
                          (
                              user_id
                              INTEGER,
                              code
                              TEXT
                          )''')

        self._add_column_if_not_exists(cursor, "products", "emoji", "TEXT")
        self._add_column_if_not_exists(cursor, "products", "image_url", "TEXT")
        self._add_column_if_not_exists(cursor, "products", "variants", "TEXT")
        self._add_column_if_not_exists(cursor, "orders", "payment_method", "TEXT")
        self._add_column_if_not_exists(cursor, "orders", "email", "TEXT")
        self._add_column_if_not_exists(cursor, "orders", "full_name", "TEXT")
        self._add_column_if_not_exists(cursor, "orders", "promo_code", "TEXT")
        self._add_column_if_not_exists(cursor, "users", "email", "TEXT")
        self._add_column_if_not_exists(cursor, "users", "full_name", "TEXT")
        self._add_column_if_not_exists(cursor, "users", "blocked", "INTEGER DEFAULT 0")

        self._add_column_if_not_exists(cursor, "promocodes", "max_uses", "INTEGER DEFAULT 100")
        self._add_column_if_not_exists(cursor, "promocodes", "current_uses", "INTEGER DEFAULT 0")
        self._add_column_if_not_exists(cursor, "promocodes", "is_reusable", "INTEGER DEFAULT 0")

        self.conn.commit()

    # -------------------- UTILS --------------------
    def escape_html(self, text):
        if not text: return ""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def escape_md(self, text):
        if not text: return ""
        return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

    def generate_receipt(self, order_id, user_name, email, phone, address, payment, products_list, total, date,
                         receipt_format='html'):
        shipping_label = self.get_text('shipping_label_ukraine') if SHIPPING_MODE == 'UKRAINE' else self.get_text(
            'shipping_label_international')

        bold_start, bold_end = ("<b>", "</b>") if receipt_format == 'html' else ("**", "**")
        escaper = self.escape_html if receipt_format == 'html' else self.escape_md

        # Оновлений формат рядка товару (як на 2-му скріншоті)
        product_line_format = "{emoji} {name}{opts} x{quantity} = " + bold_start + "{total}{symbol}" + bold_end + "\n"

        products_text = ""
        calc_subtotal = 0

        for item in products_list:
            # ВИПРАВЛЕНО: Використовуємо .values() замість .items(), щоб було (41), а не (('ShoeSize', '41'))
            opts_str = f" ({', '.join([str(v) for v in item.get('selected_options', {}).values()])})" if item.get(
                'selected_options') else ""
            item_total = item.get('total', 0)
            calc_subtotal += item_total

            products_text += product_line_format.format(
                emoji=item.get('emoji', '📦'), name=escaper(item.get('name', self.get_text('unknown'))),
                opts=escaper(opts_str), quantity=item.get('quantity', 1),
                total=item_total, symbol=CURRENCY_SYMBOL
            )

        if calc_subtotal > total:
            discount_diff = calc_subtotal - total
            products_text += self.get_text('receipt_discount', amount=round(discount_diff, 2), symbol=CURRENCY_SYMBOL)

        return self.get_text(
            'receipt', bold_start=bold_start, bold_end=bold_end, order_id=order_id,
            user_name=escaper(user_name), email=escaper(email), phone=escaper(str(phone)),
            shipping_label=shipping_label, address=escaper(address), payment=payment,
            products_text=products_text, total=total, date=date, symbol=CURRENCY_SYMBOL
        )

    def calculate_item_price(self, base_price, variants_json, selected_options_json):
        final_price = base_price
        if not variants_json or not selected_options_json: return float(final_price)
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
            logger.error(self.get_text('price_calculation_error', e=e))
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
                if "." in date_input: date_input = date_input.split(".")[0]
                dt = datetime.strptime(date_input, "%Y-%m-%d %H:%M:%S")
            else:
                dt = date_input
            if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
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
                                logger.error(f"Restore variants error: {e}")
            self.conn.commit()

    async def handle_cancel_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        self.user_states.pop(update.effective_user.id, None)
        await query.answer(self.get_text('order_cancelled_2'))
        if getattr(query.message, 'invoice', None):
            try:
                await query.message.delete()
            except:
                pass
        await self.show_cart(update, context)

    async def user_cancel_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        match = re.match(r"user_cancel_(\d+)", query.data)
        if not match: return await query.answer(self.get_text('invalid_request'))

        order_id = int(match.group(1))
        uid = query.from_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT status FROM orders WHERE id = ? AND user_id = ?", (order_id, uid))
        row = cursor.fetchone()

        if not row: return await query.answer(self.get_text('invalid_request'))
        if row[0] in ('cancelled', 'delivered'): return await query.answer(
            self.get_text('order_already_delivered_or_canceled'))

        self.restore_stock(order_id)
        cursor.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        self.conn.commit()

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id,
                                               text=self.get_text('customer_canceled_order', order_id=order_id),
                                               parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

        await query.answer(self.get_text('order_canceled'))
        await self.show_my_orders(update, context)

    # -------------------- KEYBOARDS --------------------
    def build_main_keyboard(self, user_id):
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT SUM(quantity) FROM cart WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            cart_count = result[0] if result and result[0] else 0
        except Exception:
            cart_count = 0

        cart_text = self.get_text('my_cart_count', cart_count=cart_count) if cart_count > 0 else self.get_text(
            'my_cart')

        if int(user_id) in ADMIN_IDS:
            return InlineKeyboardMarkup([
                [InlineKeyboardButton(self.get_text('admin_panel_button'), callback_data="admin_panel")],
                [InlineKeyboardButton(self.get_text('product_catalog_button'), callback_data="catalog")],
                [InlineKeyboardButton(cart_text, callback_data="cart"),
                 InlineKeyboardButton(self.get_text('my_profile_button'), callback_data="my_profile")]
            ])

        return InlineKeyboardMarkup([
            [InlineKeyboardButton(self.get_text('product_catalog_button'), callback_data="catalog")],
            [InlineKeyboardButton(cart_text, callback_data="cart")],
            [InlineKeyboardButton(self.get_text('my_orders_button'), callback_data="my_orders")],
            [InlineKeyboardButton(self.get_text('my_profile_button'), callback_data="my_profile")],
            [InlineKeyboardButton(self.get_text('help_button'), callback_data="help")]
        ])

    # -------------------- CLIENT SCREENS --------------------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        self.conn.commit()
        if self.is_user_blocked(user_id):
            await update.message.reply_text(self.get_text('user_blocked'))
            return
        await self.show_main_menu(update, context)

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user = update.effective_user
        user_id = user.id

        if int(user_id) in ADMIN_IDS:
            welcome_text = self.get_text('admin_welcome', safe_name=self.escape_html(user.first_name))
        else:
            base_welcome = STORE_MESSAGES[SHIPPING_MODE]['welcome'].format(shop_name=SHOP_NAME)
            missing = self.get_profile_completion_status(user_id)
            if missing:
                promo = self.get_text(f'welcome_promo_{len(missing)}')
                labels = []
                if "full_name" in missing: labels.append(self.get_text('missing_name'))
                if "email" in missing: labels.append(self.get_text('missing_email'))
                if "address" in missing: labels.append(self.get_text(
                    'missing_address_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'missing_address_international'))
                if "phone" in missing: labels.append(self.get_text('missing_phone'))
                promo_block = self.get_text('missing_fields_info', promo=promo, missing_labels=', '.join(labels))

                parts = base_welcome.split("\n\n", 1)
                welcome_text = f"{parts[0]}\n{promo_block}\n\n{parts[1]}" if len(
                    parts) > 1 else f"{promo_block}\n\n{base_welcome}"
            else:
                welcome_text = base_welcome

        reply_markup = self.build_main_keyboard(user_id)

        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup,
                                                              parse_mode=ParseMode.HTML)
            except Exception:
                pass
        else:
            await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        text = STORE_MESSAGES[SHIPPING_MODE]['help'].format(shop_name=SHOP_NAME, support=SUPPORT_USER,
                                                            channel=CHANNEL_LINK)
        keyboard = [[InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]]
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                      parse_mode=ParseMode.HTML)

    # -------------------- CATALOG & PRODUCTS --------------------
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
                    [[InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]]),
                                              parse_mode="HTML")
            except:
                pass
            return

        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        if page > total_pages: page = total_pages
        if page < 1: page = 1
        offset = (page - 1) * ITEMS_PER_PAGE

        cursor.execute("SELECT DISTINCT category FROM products ORDER BY category ASC LIMIT ? OFFSET ?",
                       (ITEMS_PER_PAGE, offset))
        text = self.get_text('product_catalog')
        if total_pages > 1: text += self.get_text('page_indicator', page=page, total_pages=total_pages)
        text += self.get_text('select_category')

        keyboard = []
        for (cat_name,) in cursor.fetchall():
            cursor.execute("SELECT emoji FROM products WHERE category = ? LIMIT 1", (cat_name,))
            res = cursor.fetchone()
            emo = res[0] if res and res[0] else "📂"
            keyboard.append([InlineKeyboardButton(f"{emo} {cat_name}", callback_data=f"category_{cat_name}_1_{page}")])

        nav = []
        if page > 1: nav.append(
            InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"catalog_page_{page - 1}"))
        if page < total_pages: nav.append(
            InlineKeyboardButton(self.get_text('next_button'), callback_data=f"catalog_page_{page + 1}"))
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

    async def show_category_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        parts = query.data.split("_")
        cat_page, prod_page, category = 1, 1, ""

        try:
            if len(parts) >= 4 and parts[-1].isdigit() and parts[-2].isdigit():
                cat_page, prod_page = int(parts[-1]), int(parts[-2])
                category = "_".join(parts[1:-2])
            elif len(parts) >= 3 and parts[-1].isdigit():
                prod_page = int(parts[-1])
                category = "_".join(parts[1:-1])
            else:
                category = query.data.replace("category_", "")
        except:
            category = query.data.replace("category_", "")

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products WHERE category = ?", (category,))
        total_items = cursor.fetchone()[0]

        if total_items == 0: return await query.answer(self.get_text('no_products_yet'))

        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        offset = (prod_page - 1) * ITEMS_PER_PAGE

        cursor.execute("SELECT id, name, price, emoji, variants FROM products WHERE category = ? LIMIT ? OFFSET ?",
                       (category, ITEMS_PER_PAGE, offset))
        text = self.get_text('category_header', category=self.escape_html(category), prod_page=prod_page,
                             total_pages=total_pages)
        keyboard = []

        for p_id, name, base_price, emoji, variants_json in cursor.fetchall():
            display_price = f"{base_price}{CURRENCY_SYMBOL}"
            if variants_json:
                try:
                    v_data, all_prices = json.loads(variants_json), []
                    for v_type, options in v_data.items():
                        if isinstance(options, dict):
                            for opt, info in options.items():
                                if isinstance(info, dict) and 'price' in info and float(info['price']) > 0:
                                    all_prices.append(float(info['price']))

                    if all_prices and min(all_prices) != max(all_prices):
                        display_price = self.get_text('price_from', price=min(all_prices)).replace('$', CURRENCY_SYMBOL)
                    elif all_prices:
                        display_price = f"{all_prices[0]}{CURRENCY_SYMBOL}"
                except:
                    pass
            keyboard.append([InlineKeyboardButton(f"{emoji or '📦'} {name} - {display_price}",
                                                  callback_data=f"product_{p_id}_{prod_page}_{cat_page}")])

        nav = []
        if prod_page > 1: nav.append(InlineKeyboardButton(self.get_text('prev_button'),
                                                          callback_data=f"category_{category}_{prod_page - 1}_{cat_page}"))
        if prod_page < total_pages: nav.append(InlineKeyboardButton(self.get_text('next_button'),
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

    async def show_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id_override=None):
        query = update.callback_query
        user_id = update.effective_user.id

        if product_id_override:
            product_id = product_id_override
            state = self.user_states.get(user_id, {})
            prod_page, cat_page = state.get('prod_page', 1), state.get('cat_page', 1)
        else:
            parts = query.data.split('_')
            try:
                if parts[0] in ['product', 'prod']:
                    product_id, prod_page, cat_page = int(parts[1] if parts[0] == 'product' else parts[2]), int(
                        parts[2] if parts[0] == 'product' else parts[3]), int(
                        parts[3] if parts[0] == 'product' else parts[4])
                else:
                    product_id, prod_page, cat_page = int(parts[1]), 1, 1
            except:
                product_id, prod_page, cat_page = int(parts[1]), 1, 1

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if not product: return

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

        if all_prices and min(all_prices) != max(all_prices):
            display_price = self.get_text('price_from', price=min(all_prices)).replace('$', CURRENCY_SYMBOL)
        elif all_prices:
            display_price = f"{all_prices[0]}{CURRENCY_SYMBOL}"
        else:
            display_price = f"{product['price']}{CURRENCY_SYMBOL}"

        stock = product['stock']
        stock_status = self.get_text('in_stock') if stock > 5 else (
            self.get_text('low_stock', stock=stock) if stock > 0 else self.get_text('out_of_stock'))

        cursor.execute("SELECT SUM(quantity) FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        in_cart = cursor.fetchone()[0] or 0

        variants_display = ""
        if product['variants']:
            try:
                for v_type, options in json.loads(product['variants']).items():
                    variants_display += self.get_text('variant_display', v_type=v_type,
                                                      opt_list=', '.join(map(str, options.keys())))
            except:
                pass

        text = (f"{product['emoji'] or '📦'} <b>{self.escape_html(product['name'])}</b>\n\n"
                f"{self.escape_html(product['description'] or self.get_text('no_description'))}\n{variants_display}\n\n"
                f"{self.get_text('product_details', display_price=display_price, stock_status=stock_status, in_cart=in_cart)}")

        keyboard = [
            [InlineKeyboardButton(self.get_text('btn_minus'),
                                  callback_data=f"prod_minus_{product_id}_{prod_page}_{cat_page}"),
             InlineKeyboardButton(self.get_text('btn_plus'),
                                  callback_data=f"prod_plus_{product_id}_{prod_page}_{cat_page}")],
            [InlineKeyboardButton(
                self.get_text('cart_button_count', in_cart=in_cart) if in_cart > 0 else self.get_text('cart_button'),
                callback_data="cart"),
                InlineKeyboardButton(self.get_text('back_button'),
                                     callback_data=f"category_{product['category']}_{prod_page}_{cat_page}")]
        ]

        try:
            if product['image_url']:
                await query.message.delete()
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=product['image_url'], caption=text,
                                             reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            else:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except:
            pass

    async def handle_product_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        try:
            parts = query.data.split("_")
            action_type, product_id = parts[1], int(parts[2])
            if len(parts) >= 5:
                if user_id not in self.user_states: self.user_states[user_id] = {}
                self.user_states[user_id]['prod_page'], self.user_states[user_id]['cat_page'] = int(parts[3]), int(
                    parts[4])
        except:
            return await query.answer(self.get_text('error_parsing_data'))

        cursor = self.conn.cursor()
        cursor.execute("SELECT stock, variants FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row: return await query.answer(self.get_text('product_not_found'))
        stock, variants_json = row

        if action_type == "plus":
            if variants_json and json.loads(variants_json):
                return await self.start_variant_selection(update, context, product_id)

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
                return await query.answer(self.get_text('stock_limit', limit=stock), show_alert=True)

        elif action_type == "minus":
            cursor.execute(
                "SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ? ORDER BY id DESC LIMIT 1",
                (user_id, product_id))
            target = cursor.fetchone()
            if target:
                if target[1] > 1:
                    cursor.execute("UPDATE cart SET quantity = quantity - 1 WHERE id = ?", (target[0],))
                else:
                    cursor.execute("DELETE FROM cart WHERE id = ?", (target[0],))
            else:
                return await query.answer(self.get_text('cart_empty_2'))

        self.conn.commit()
        await self.show_product(update, context, product_id_override=product_id)

    # -------------------- VARIANTS --------------------
    async def start_variant_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id):
        user_id = update.effective_user.id
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT variants FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()

        if not row or not row['variants']: return await self.add_item_to_cart_db(update, context, product_id, None)
        try:
            variants_data = json.loads(row['variants'])
        except:
            return await self.add_item_to_cart_db(update, context, product_id, None)

        priority_keys = [self.get_text(k) for k in
                         ['color_variant_key', 'colour_variant_key', 'color_variant_key_uk', 'color_variant_key_ru',
                          'size_variant_key', 'size_variant_key_uk', 'size_variant_key_ru']]
        sorted_keys = sorted(variants_data.keys(),
                             key=lambda k: priority_keys.index(k.lower()) if k.lower() in priority_keys else 999)

        self.user_states[user_id] = {
            'step': 'selecting_variant', 'product_id': product_id, 'variant_keys': sorted_keys,
            'current_key_index': 0, 'variants_data': variants_data, 'selected_options': {}
        }
        await self.ask_next_variant(update, context)

    async def add_to_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        product_id = int(query.data.replace("add_to_cart_", ""))
        await self.start_variant_selection(update, context, product_id)

    async def ask_next_variant(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state or 'variant_keys' not in state:
            try:
                await update.callback_query.answer(self.get_text('session_expired'))
            except:
                pass
            return

        if state['current_key_index'] >= len(state['variant_keys']):
            await self.add_item_to_cart_db(update, context, state['product_id'], state['selected_options'])
            self.user_states.pop(user_id, None)
            return

        current_key = state['variant_keys'][state['current_key_index']]
        options_data = state['variants_data'].get(current_key, {})

        row, final_keyboard = [], []
        if isinstance(options_data, dict):
            for opt, val in sorted(options_data.items(), key=lambda x: x[0]):
                qty = val.get('qty', 0) if isinstance(val, dict) else (int(val) if str(val).isdigit() else 0)
                price_info = f" {val['price']}{CURRENCY_SYMBOL}" if isinstance(val, dict) and 'price' in val else ""
                row.append(InlineKeyboardButton(f"{opt}{price_info}",
                                                callback_data=f"var_sel_{state['current_key_index']}_{opt}") if qty > 0 else InlineKeyboardButton(
                    f"{opt} (❌)", callback_data="noop"))
        elif isinstance(options_data, list):
            for opt in options_data: row.append(
                InlineKeyboardButton(str(opt), callback_data=f"var_sel_{state['current_key_index']}_{opt}"))

        for i in range(0, len(row), 2): final_keyboard.append(row[i:i + 2])
        final_keyboard.append([InlineKeyboardButton(self.get_text('cancel_button'), callback_data="cancel_selection")])

        # --- Виправлення для перекладу та відображення тегів ---
        # Локалізуємо назву ключа (наприклад, ShoeSize -> Розмір взуття)
        v_type_localized = self.get_text(f'type_{current_key}')
        if v_type_localized == f"_type_{current_key}_":
            v_type_localized = current_key

        text = self.get_text('select_variant', current_key=v_type_localized)
        query = update.callback_query

        try:
            if query.message.photo:
                # Змінено на ParseMode.HTML
                await query.edit_message_caption(caption=text, reply_markup=InlineKeyboardMarkup(final_keyboard),
                                                 parse_mode=ParseMode.HTML)
            else:
                # Змінено на ParseMode.HTML
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(final_keyboard),
                                              parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await query.message.delete()
            except:
                pass
            # Змінено на ParseMode.HTML
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text,
                                           reply_markup=InlineKeyboardMarkup(final_keyboard),
                                           parse_mode=ParseMode.HTML)

    async def handle_variant_selection_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        if query.data == "cancel_selection":
            state = self.user_states.pop(user_id, None)
            if state and 'product_id' in state:
                await self.show_product(update, context, product_id_override=state['product_id'])
            else:
                try:
                    await query.message.delete()
                except:
                    pass
            return

        try:
            parts = query.data.split("_")
            idx, value = int(parts[2]), "_".join(parts[3:])
        except:
            return await query.answer(self.get_text('error_parsing_data'))

        state = self.user_states.get(user_id)
        if not state or state.get('step') != 'selecting_variant':
            await query.answer(self.get_text('session_expired'))
            try:
                await query.message.delete()
            except:
                pass
            return

        state['selected_options'][state['variant_keys'][idx]] = value
        state['current_key_index'] += 1
        await self.ask_next_variant(update, context)

    # -------------------- PROFILE --------------------
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

        shipping_label = self.get_text(
            'shipping_label_profile_ukraine') if SHIPPING_MODE == 'UKRAINE' else self.get_text(
            'shipping_label_profile_international')
        text = self.get_text('profile_details', name=self.escape_html(name), email=self.escape_html(email),
                             shipping_label=shipping_label, address=self.escape_html(address),
                             phone=self.escape_html(phone))

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(self.get_text('edit_name_button'), callback_data="edit_full_name")],
            [InlineKeyboardButton(self.get_text('edit_email_button'), callback_data="edit_email")],
            [InlineKeyboardButton(self.get_text('edit_shipping_info_button'), callback_data="edit_address")],
            [InlineKeyboardButton(self.get_text('edit_phone_button'), callback_data="edit_phone")],
            [InlineKeyboardButton(self.get_text('delete_data_button'), callback_data="profile_delete_menu")],
            [InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]
        ])

        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=keyboard,
                                           parse_mode="HTML")

    def get_profile_completion_status(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row: return ["full_name", "email", "address", "phone"]
        full_name, email, address, phone = row
        missing_fields = []
        if not full_name: missing_fields.append("full_name")
        if not email: missing_fields.append("email")
        if not address: missing_fields.append("address")
        if not phone: missing_fields.append("phone")
        return missing_fields

    async def handle_delete_profile_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        field_map = {
            "delete_profile_full_name": ("full_name", self.get_text('missing_name')),
            "delete_profile_phone": ("phone", self.get_text('missing_phone')),
            "delete_profile_address": ("address", self.get_text(
                'missing_address_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'missing_address_international')),
            "delete_profile_email": ("email", self.get_text('missing_email'))
        }
        if query.data not in field_map: return await query.answer(self.get_text('invalid_action'))

        db_field, display_name = field_map[query.data]
        cursor = self.conn.cursor()
        cursor.execute(f"UPDATE users SET {db_field} = NULL WHERE user_id = ?", (query.from_user.id,))
        self.conn.commit()
        await query.answer(self.get_text('data_deleted', display_name=display_name))
        await self.profile_delete_menu(update, context)

    async def _edit_user_profile_attribute(self, update: Update, context: ContextTypes.DEFAULT_TYPE, field: str,
                                           prompt: str):
        query = update.callback_query
        await query.answer()
        self.user_states[update.effective_user.id] = {'step': f'waiting_{field}_profile',
                                                      'msg_id': query.message.message_id}
        await query.edit_message_text(prompt, reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="my_profile")]]), parse_mode="HTML")

    async def edit_full_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._edit_user_profile_attribute(update, context, "full_name", self.get_text('enter_full_name'))

    async def edit_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._edit_user_profile_attribute(update, context, "email", self.get_text('enter_email'))

    async def edit_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._edit_user_profile_attribute(update, context, "address", self.get_text(
            'enter_address_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'enter_address_international'))

    async def edit_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._edit_user_profile_attribute(update, context, "phone",
                                                self.get_text('enter_phone', example=self.get_text('ex_phone')))

    async def profile_delete_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        cursor = self.conn.cursor()
        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?",
                       (update.effective_user.id,))
        row = cursor.fetchone()

        if not row:
            await query.answer(self.get_text('session_expired'))
            return await self.show_profile(update, context)

        keyboard = []
        if row[0]: keyboard.append(
            [InlineKeyboardButton(self.get_text('delete_name_btn'), callback_data="delete_profile_full_name")])
        if row[1]: keyboard.append(
            [InlineKeyboardButton(self.get_text('delete_email_btn'), callback_data="delete_profile_email")])
        if row[2]: keyboard.append([InlineKeyboardButton(
            self.get_text('delete_shipping_ukr_btn' if SHIPPING_MODE == 'UKRAINE' else 'delete_shipping_int_btn'),
            callback_data="delete_profile_address")])
        if row[3]: keyboard.append(
            [InlineKeyboardButton(self.get_text('delete_phone_btn'), callback_data="delete_profile_phone")])
        keyboard.append([InlineKeyboardButton(self.get_text('back_to_profile_btn'), callback_data="my_profile")])

        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(chat_id=update.effective_chat.id, text=self.get_text('profile_delete_title'),
                                       reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    async def handle_profile_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state: return

        text = update.message.text.strip() if update.message.text else ""
        msg = update.message
        chat_id = update.message.chat_id

        try:
            await msg.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        cursor = self.conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        error_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="my_profile")]])

        if state['step'] == 'waiting_full_name_profile':
            if len(text.split()) < 2: return await self._send_error(chat_id, 'err_invalid_name', error_kb, state,
                                                                    context)
            cursor.execute("UPDATE users SET full_name = ? WHERE user_id = ?", (text, user_id))
        elif state['step'] == 'waiting_email_profile':
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text): return await self._send_error(chat_id,
                                                                                                  'err_invalid_email',
                                                                                                  error_kb, state,
                                                                                                  context)
            cursor.execute("UPDATE users SET email = ? WHERE user_id = ?", (text, user_id))
        elif state['step'] == 'waiting_phone_profile':
            if not (
                    re.fullmatch(r"^\+380\d{9}$", text) if SHIPPING_MODE == 'UKRAINE' else re.fullmatch(
                        r"^\+\d{10,15}$",
                        text)): return await self._send_error(
                chat_id, 'err_invalid_phone', error_kb, state, context)
            cursor.execute("UPDATE users SET phone = ? WHERE user_id = ?", (text, user_id))
        elif state['step'] == 'waiting_address_profile':
            is_valid = (text.count(',') >= 1 or len(text.split()) >= 3) if SHIPPING_MODE == 'UKRAINE' else text.count(
                ',') >= 3
            if not is_valid: return await self._send_error(chat_id, 'err_invalid_address', error_kb, state, context)
            cursor.execute("UPDATE users SET address = ? WHERE user_id = ?", (text, user_id))

        self.conn.commit()
        self.user_states.pop(user_id, None)
        await self.show_profile(update, context)

    async def _send_error(self, chat_id, error_key, kb, state, context):
        m = await context.bot.send_message(chat_id=chat_id, text=self.get_text(error_key), reply_markup=kb,
                                           parse_mode="HTML")
        state['msg_id'] = m.message_id

    # -------------------- CART --------------------
    async def show_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT c.id, p.name, p.price, c.quantity, p.emoji, c.selected_options, p.variants, p.id FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
            (user_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            text = STORE_MESSAGES[SHIPPING_MODE]['cart_empty']
            keyboard = [[InlineKeyboardButton(self.get_text('go_to_catalog_button'), callback_data="catalog")],
                        [InlineKeyboardButton(self.get_text('my_orders_button_2'), callback_data="my_orders")],
                        [InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]]
            try:
                if query and getattr(query.message, 'photo', None):
                    await query.message.delete()
                    await context.bot.send_message(chat_id=chat_id, text=text,
                                                   reply_markup=InlineKeyboardMarkup(keyboard),
                                                   parse_mode=ParseMode.HTML)
                elif query:
                    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                  parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_message(chat_id=chat_id, text=text,
                                                   reply_markup=InlineKeyboardMarkup(keyboard),
                                                   parse_mode=ParseMode.HTML)
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text=text,
                                               reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            return

        total_amount, text, keyboard = 0, "", []

        promo_msg = self.user_states.get(user_id, {}).pop('promo_msg', None)
        if promo_msg:
            text += promo_msg + "\n\n"

        text += self.get_text('cart_header')

        for cart_id, name, base_price, quantity, emoji, opts_json, variants_json, product_id in cart_items:
            real_price = self.calculate_item_price(base_price, variants_json, opts_json)
            item_total = real_price * quantity
            total_amount += item_total
            opts_str = f" ({', '.join([str(v) for v in json.loads(opts_json).values()])})" if opts_json else ""
            text += f"{emoji or '📦'} <b>{self.escape_html(name)}</b>{self.escape_html(opts_str)}\n   {quantity} x {real_price}{CURRENCY_SYMBOL} = {item_total}{CURRENCY_SYMBOL}\n"
            keyboard.append([InlineKeyboardButton(self.get_text('btn_minus'), callback_data=f"cart_minus_{cart_id}"),
                             InlineKeyboardButton(f"{name} ({quantity})", callback_data=f"product_{product_id}"),
                             InlineKeyboardButton(self.get_text('btn_plus'), callback_data=f"cart_plus_{cart_id}")])

        discount_amount = 0
        active_promo = self.user_states.get(user_id, {}).get('active_promo')
        if active_promo:
            discount_amount = total_amount * (active_promo['discount'] / 100.0)
            total_amount -= discount_amount
            text += f"\n" + self.get_text('cart_discount_info', discount=active_promo['discount'],
                                          discount_amount=round(discount_amount, 2))

        text += self.get_text('cart_total', total_amount=round(total_amount, 2)).replace('$', CURRENCY_SYMBOL)

        promo_btn = []
        if not active_promo:
            promo_btn = [[InlineKeyboardButton(self.get_text('cart_promo_btn'), callback_data="ask_promo_code")]]

        keyboard.extend(promo_btn)
        keyboard.extend([
            [InlineKeyboardButton(self.get_text('checkout_button'), callback_data="checkout")],
            [InlineKeyboardButton(self.get_text('clear_cart_button'), callback_data="clear_cart"),
             InlineKeyboardButton(self.get_text('back_to_catalog_button'), callback_data="catalog")],
            [InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]
        ])

        try:
            if query and getattr(query.message, 'photo', None):
                await query.message.delete()
                await context.bot.send_message(chat_id=chat_id, text=text,
                                               reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            elif query:
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                              parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_message(chat_id=chat_id, text=text,
                                               reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

    async def handle_cart_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        try:
            action, cart_id = query.data.rsplit("_", 1);
            cart_id = int(cart_id)
        except:
            return

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT c.quantity, c.selected_options, p.stock, p.variants FROM cart c JOIN products p ON c.product_id = p.id WHERE c.id = ?",
            (cart_id,))
        row = cursor.fetchone()
        if not row: return await self.show_cart(update, context)

        current_qty, opts_json, product_stock, variants_json = row
        limit = product_stock

        if variants_json and opts_json:
            try:
                for k, v in json.loads(opts_json).items():
                    v_data = json.loads(variants_json)
                    if k in v_data and isinstance(v_data[k], dict) and v in v_data[k]:
                        limit = v_data[k][v].get('qty', limit) if isinstance(v_data[k][v], dict) else limit
                        break
            except:
                pass

        if "plus" in action:
            if current_qty < limit:
                cursor.execute("UPDATE cart SET quantity = ? WHERE id = ?", (current_qty + 1, cart_id))
            else:
                return await query.answer(self.get_text('stock_limit', limit=limit), show_alert=True)
        elif "minus" in action:
            if current_qty > 1:
                cursor.execute("UPDATE cart SET quantity = ? WHERE id = ?", (current_qty - 1, cart_id))
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

        limit = prod_row[0]
        if options and prod_row[1]:
            try:
                for key, val in options.items():
                    v_data = json.loads(prod_row[1]).get(key, {})
                    if isinstance(v_data, dict) and val in v_data:
                        limit = v_data[val].get('qty', 0) if isinstance(v_data[val], dict) else int(v_data[val])
            except:
                pass

        cursor.execute("SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ? AND selected_options IS ?",
                       (user_id, product_id, options_json))
        cart_row = cursor.fetchone()
        current_in_cart = cart_row[1] if cart_row else 0

        if current_in_cart + 1 > limit:
            if update.callback_query: await update.callback_query.answer(self.get_text('limit_reached', limit=limit),
                                                                         show_alert=True)
            return await self.show_product(update, context, product_id_override=product_id)

        if cart_row:
            cursor.execute("UPDATE cart SET quantity = quantity + 1 WHERE id = ?", (cart_row[0],))
        else:
            cursor.execute("INSERT INTO cart (user_id, product_id, quantity, selected_options) VALUES (?, ?, 1, ?)",
                           (user_id, product_id, options_json))

        self.conn.commit()
        if update.callback_query: await update.callback_query.answer(self.get_text('added_to_cart'), show_alert=False)
        await self.show_product(update, context, product_id_override=product_id)

    async def clear_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cart WHERE user_id = ?", (user_id,))
        items_count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        self.conn.commit()
        self.user_states.setdefault(user_id, {}).pop('active_promo', None)
        await update.callback_query.answer(self.get_text('cart_cleared', items_count=items_count))
        await self.show_cart(update, context)

    # -------------------- CHECKOUT --------------------
    async def checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id

        cursor = self.conn.cursor()
        cursor.execute("SELECT product_id FROM cart WHERE user_id = ?", (user_id,))
        if not cursor.fetchone(): return await query.answer(self.get_text('cart_empty_3'))

        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()

        # Перевіряємо, чи є хоча б одне збережене поле (щоб не було глухого кута)
        has_saved_data = user_data and any(user_data)

        if has_saved_data:
            # Дані є — показуємо кнопку "Використати мій профіль"
            self.user_states.setdefault(user_id, {})['step'] = 'waiting_full_name'
            keyboard = [
                [InlineKeyboardButton(self.get_text('use_profile_data_button'), callback_data="use_profile_data")],
                [InlineKeyboardButton(self.get_text('back_to_cart_button'), callback_data="cart")],
                [InlineKeyboardButton(self.get_text('cancel_order_button'), callback_data="cancel_order")]
            ]
            await query.edit_message_text(text=self.get_text('checkout_step_1'),
                                          reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            self.user_states[user_id]['msg_id'] = query.message.message_id
        else:
            # Даних немає — відразу переходимо до Кроку 1 (введення імені)
            self.user_states.setdefault(user_id, {})
            self.user_states[user_id].update({
                'full_name': None, 'email': None, 'address': None, 'phone': None, 'from_profile': False
            })
            await self.continue_checkout_flow(update, context)

    async def use_profile_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id
        await query.answer(self.get_text('loading_profile_data'))

        cursor = self.conn.cursor()
        cursor.execute("SELECT full_name, email, address, phone FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()

        if not user_data: return await self.checkout(update, context)

        full_name, email, address, phone = user_data
        self.user_states.setdefault(user_id, {}).update(
            {'full_name': full_name, 'email': email, 'address': address, 'phone': phone,
             'msg_id': query.message.message_id, 'from_profile': True})

        if full_name and email and address and phone:
            try:
                await query.message.delete()
            except:
                pass
            await self.show_order_summary(context, query.message.chat_id, user_id)
        else:
            await self.continue_checkout_flow(update, context)

    async def continue_checkout_flow(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states[user_id]
        chat_id = update.effective_chat.id

        if state.get('is_editing_single'):
            state['is_editing_single'] = False
            return await self.show_order_summary(context, chat_id, user_id)

        header = self.get_text('checkout_profile_loaded_header') if state.get('from_profile') else self.get_text(
            'checkout_header')

        if not state.get('full_name'):
            state['step'], text, back_cb = 'waiting_full_name', header + self.get_text('checkout_step_1_of_4',
                                                                                       total_steps="4"), "cart"
        elif not state.get('email'):
            state['step'], text, back_cb = 'waiting_email', header + self.get_text('checkout_step_2_of_4',
                                                                                   total_steps="4"), "back_to_name"
        elif not state.get('address'):
            state['step'] = 'waiting_shipping'
            text = header + self.get_text(
                'checkout_step_3_of_4_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'checkout_step_3_of_4_international',
                total_steps="4")
            back_cb = "back_to_email"
        elif not state.get('phone'):
            state['step'], text, back_cb = 'waiting_phone', header + self.get_text('checkout_step_4_of_4',
                                                                                   total_steps="4",
                                                                                   example=self.get_text(
                                                                                       'ex_phone')), "back_to_shipping"
        else:
            return await self.show_order_summary(context, chat_id, user_id)

        kb = InlineKeyboardMarkup([[InlineKeyboardButton(self.get_text('back_button_2'), callback_data=back_cb)],
                                   [InlineKeyboardButton(self.get_text('cancel_order_button'),
                                                         callback_data="cancel_order")]])
        if getattr(update, "callback_query", None):
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            if 'msg_id' in state:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
                except:
                    pass
            m = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
            state['msg_id'] = m.message_id

    async def handle_checkout_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        user_id = update.effective_user.id
        if user_id not in self.user_states: return
        state, msg, chat_id = self.user_states[user_id], update.message, update.message.chat_id

        try:
            await msg.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        async def send_err(key, cb):
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text(key),
                                               reply_markup=InlineKeyboardMarkup(
                                                   [[InlineKeyboardButton(self.get_text('back_button_2'),
                                                                          callback_data=cb)],
                                                    [InlineKeyboardButton(self.get_text('cancel_order_button'),
                                                                          callback_data="cancel_order")]]),
                                               parse_mode="HTML")
            state['msg_id'] = m.message_id

        text = msg.text.strip() if msg.text else ""
        is_edit = state.get('is_editing_single', False)

        if state['step'] == 'waiting_full_name':
            if len(text.split()) < 2: return await send_err('err_invalid_name',
                                                            "confirm_details_back" if is_edit else "cart")
            state['full_name'] = text
        elif state['step'] == 'waiting_email':
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text): return await send_err('err_invalid_email',
                                                                                          "confirm_details_back" if is_edit else "back_to_name")
            state['email'] = text
        elif state['step'] == 'waiting_shipping':
            is_valid = (text.count(',') >= 1 or len(text.split()) >= 3) if SHIPPING_MODE == 'UKRAINE' else (
                    text.count(',') >= 3 and any(c.isdigit() for c in text.split(',')[-1]))
            if not is_valid: return await send_err('err_invalid_address',
                                                   "confirm_details_back" if is_edit else "back_to_email")
            state['address'] = text
        elif state['step'] == 'waiting_phone':
            if not (
                    re.fullmatch(r"^\+380\d{9}$", text) if SHIPPING_MODE == 'UKRAINE' else re.fullmatch(
                        r"^\+\d{10,15}$",
                        text)): return await send_err(
                'err_invalid_phone', "confirm_details_back" if is_edit else "back_to_shipping")
            state['phone'] = text
            return await self.show_order_summary(context, chat_id, user_id)

        await self.continue_checkout_flow(update, context)

    async def show_order_summary(self, context, chat_id, user_id):
        state = self.user_states[user_id]
        state['step'], state['is_editing_single'] = 'waiting_confirmation', False

        summary_text = self.get_text('confirm_details',
                                     full_name=self.escape_html(state.get('full_name')),
                                     email=self.escape_html(state.get('email')),
                                     address=self.escape_html(state.get('address')),
                                     phone=self.escape_html(state.get('phone')))

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(self.get_text('summary_edit_name_btn'), callback_data="edit_check_name"),
             InlineKeyboardButton(self.get_text('summary_edit_email_btn'), callback_data="edit_check_email")],
            [InlineKeyboardButton(self.get_text('summary_edit_address_btn'), callback_data="edit_check_address"),
             InlineKeyboardButton(self.get_text('summary_edit_phone_btn'), callback_data="edit_check_phone")],
            [InlineKeyboardButton(self.get_text('summary_confirm_btn'), callback_data="confirm_details")],
            [InlineKeyboardButton(self.get_text('cancel_order_button'), callback_data="cancel_order")]
        ])

        try:
            if 'msg_id' in state:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=state['msg_id'], text=summary_text,
                                                    reply_markup=keyboard, parse_mode="HTML")
            else:
                raise Exception()
        except Exception:
            m = await context.bot.send_message(chat_id=chat_id, text=summary_text, reply_markup=keyboard,
                                               parse_mode="HTML")
            state['msg_id'] = m.message_id

    async def handle_checkout_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query, user_id = update.callback_query, update.effective_user.id
        state = self.user_states.get(user_id)
        if not state: return

        edit_map = {
            "edit_check_name": ("full_name", self.get_text('summary_name_label'), "waiting_full_name",
                                self.get_text('ex_name')),
            "edit_check_email": ("email", self.get_text('summary_email_label'), "waiting_email",
                                 self.get_text('ex_email')),
            "edit_check_address": ("address", self.get_text('summary_address_label'), "waiting_shipping",
                                   self.get_text('ex_address')),
            "edit_check_phone": ("phone", self.get_text('summary_phone_label'), "waiting_phone",
                                 self.get_text('ex_phone'))
        }

        if query.data in edit_map:
            field_key, display_name, next_step, example = edit_map[query.data]
            state['step'], state['is_editing_single'] = next_step, True
            text = f"<b>{self.get_text('edit_field_title', field=display_name)}</b>\n\n<b>{self.get_text('current_value_label')}</b> <code>{self.escape_html(state.get(field_key, self.get_text('not_set')))}</code>\n\n<b>{self.get_text('example_label')}</b> {example}\n\n{self.get_text('enter_new_value_prompt')}"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.get_text('back_to_summary_btn'), callback_data="confirm_details_back")]]),
                                          parse_mode="HTML")
            return

        if query.data == "back_to_name":
            state['full_name'] = None
        elif query.data == "back_to_email":
            state['email'] = None
        elif query.data == "back_to_shipping":
            state['address'] = None
        elif query.data == "back_to_phone_input":
            state['phone'] = None
        elif query.data == "back_to_payment":
            try:
                await query.message.delete()
            except:
                pass
            return await self.send_payment_keyboard(context, query.message.chat_id, user_id)

        await self.continue_checkout_flow(update, context)

    async def handle_checkout_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        user_id = update.effective_user.id

        if query.data == "confirm_details":
            await self.send_payment_keyboard(context, query.message.chat_id, user_id)
        elif query.data == "confirm_details_back":
            await self.show_order_summary(context, query.message.chat_id, user_id)
        elif query.data == "back_to_payment":
            try:
                await query.message.delete()
            except:
                pass
            self.user_states[user_id].pop('msg_id', None)
            await self.send_payment_keyboard(context, query.message.chat_id, user_id)
        await query.answer()

    async def send_payment_keyboard(self, context, chat_id, user_id):
        self.user_states[user_id]['step'] = 'waiting_payment'
        keyboard = []
        if SHIPPING_MODE == 'UKRAINE':
            keyboard.append([InlineKeyboardButton(self.get_text('method_cod'), callback_data="pay_cod")])
            keyboard.append([InlineKeyboardButton(self.get_text('method_card_courier'), callback_data="pay_card")])
            keyboard.append([InlineKeyboardButton(self.get_text('method_online_card'), callback_data="pay_online")])
            main_text = self.get_text('payment_step_header_ukraine')
        else:
            keyboard.append([InlineKeyboardButton(self.get_text('method_online_card'), callback_data="pay_online")])
            main_text = self.get_text('payment_step_header_int')

        keyboard.append([InlineKeyboardButton(self.get_text('back_button_2'), callback_data="confirm_details_back")])
        keyboard.append([InlineKeyboardButton(self.get_text('cancel_order_button'), callback_data="cancel_order")])

        try:
            if 'msg_id' in self.user_states[user_id]:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=self.user_states[user_id]['msg_id'],
                                                    text=main_text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                    parse_mode="HTML")
            else:
                raise Exception()
        except Exception:
            m = await context.bot.send_message(chat_id=chat_id, text=main_text,
                                               reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            self.user_states[user_id]['msg_id'] = m.message_id

    async def choose_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        if not self.user_states.get(user_id): return

        if query.data == "pay_online":
            try:
                await query.message.delete()
            except:
                pass
            return await self.send_invoice(update.effective_chat.id, user_id, context)

        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT p.price, c.quantity, p.variants, c.selected_options FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
            (user_id,))
        total_amount = sum(self.calculate_item_price(p, v, o) * q for p, q, v, o in cursor.fetchall())

        active_promo = self.user_states.get(user_id, {}).get('active_promo')
        if active_promo:
            total_amount -= total_amount * (active_promo['discount'] / 100.0)

        method_name = self.get_text('method_card_courier') if query.data == "pay_card" else self.get_text('method_cod')
        await self.finalize_order(update, context, method_name, total_amount)

    async def send_invoice(self, chat_id, user_id, context: ContextTypes.DEFAULT_TYPE):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                'SELECT p.price, c.quantity, p.variants, c.selected_options FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
                (user_id,))
            cart_data = cursor.fetchall()

            if not cart_data: return await context.bot.send_message(chat_id=chat_id,
                                                                    text=self.get_text('cart_empty_3'))

            total_amount = sum(self.calculate_item_price(p, v, o) * q for p, q, v, o in cart_data)

            active_promo = self.user_states.get(user_id, {}).get('active_promo')
            if active_promo:
                total_amount -= total_amount * (active_promo['discount'] / 100.0)

            telegram_amount = int(round(total_amount, 2) * 100)

            description = f"{self.get_text('invoice_desc')}\n{self.get_text('invoice_to_pay', amount=round(total_amount, 2), symbol=CURRENCY_SYMBOL)}"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(self.get_text('pay_button_text'), pay=True)],
                                             [InlineKeyboardButton(self.get_text('back_button_2'),
                                                                   callback_data="back_to_payment")],
                                             [InlineKeyboardButton(self.get_text('cancel_order_button'),
                                                                   callback_data="cancel_order")]])

            provider_key = 'PORTMONE' if SHIPPING_MODE == 'UKRAINE' else 'REDSYS'

            m = await context.bot.send_invoice(
                chat_id=chat_id, title=self.get_text('invoice_title', shop_name=SHOP_NAME),
                description=description, payload=f"order_{user_id}_{int(time.time())}",
                provider_token=PAYMENT_TOKENS[provider_key], currency=CURRENCY_CODE,
                prices=[LabeledPrice(self.get_text('invoice_label'), telegram_amount)], start_parameter="test-payment",
                is_flexible=False, reply_markup=keyboard
            )
            self.user_states[user_id]['invoice_msg_id'] = m.message_id
        except Exception as e:
            logger.error(f"❌ Error in send_invoice: {e}")
            await context.bot.send_message(chat_id=chat_id, text=self.get_text('invoice_creation_error'))

    async def precheckout_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.pre_checkout_query
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT c.quantity, p.name, p.stock FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?",
            (query.from_user.id,))
        for qty, name, stock in cursor.fetchall():
            if stock < qty: return await query.answer(ok=False,
                                                      error_message=self.get_text('stock_out_error', name=name))
        await query.answer(ok=True)

    async def successful_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_states: self.user_states[user_id] = {'step': 'completed'}
        state = self.user_states[user_id]

        try:
            await update.message.delete()
        except:
            pass
        if 'invoice_msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=state['invoice_msg_id'])
            except:
                pass

        await self.finalize_order(update, context, self.get_text('method_online_card'),
                                  update.message.successful_payment.total_amount / 100)

    async def finalize_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payment_method,
                             pre_calc_total=None):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        query = getattr(update, "callback_query", None)
        state = self.user_states.setdefault(user_id, {})
        state['payment'] = payment_method

        result = await self.create_order(update, context, send_message=True)
        if not result: return await context.bot.send_message(chat_id=chat_id,
                                                             text=self.get_text('order_failed_cart_empty'))

        order_id, products_list, total_amount, promo_code_used = result

        details = self.generate_receipt(order_id, state.get('full_name', update.effective_user.full_name),
                                        state.get('email', '—'), state.get('phone', '—'), state.get('address', '—'),
                                        payment_method, products_list, total_amount,
                                        datetime.now(ZoneInfo(BOT_TIMEZONE)).strftime('%d.%m.%Y %H:%M'),
                                        receipt_format='html')
        final_text = f"{STORE_MESSAGES[SHIPPING_MODE]['order_success'].format(order_id=order_id)}\n\n{details}"

        if promo_code_used:
            final_text += self.get_text('order_promo_info', code=promo_code_used)

        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]])

        if query:
            try:
                await query.edit_message_text(text=final_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            except Exception:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(chat_id=chat_id, text=final_text, reply_markup=kb,
                                               parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(chat_id=chat_id, text=final_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        self.user_states.pop(user_id, None)

    async def create_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE, send_message=False,
                           payment_method_override=None):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id, {})
        cursor = self.conn.cursor()

        cursor.execute(
            'SELECT c.id, p.name, p.price, c.quantity, p.emoji, c.selected_options, p.variants, p.id, p.stock FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?',
            (user_id,))
        cart_items = cursor.fetchall()
        if not cart_items: return None

        products_list, total_amount = [], 0
        for cart_id, name, base_price, quantity, emoji, opts_json, variants_json, product_id, p_stock in cart_items:
            real_price = self.calculate_item_price(base_price, variants_json, opts_json)
            item_total = real_price * quantity
            total_amount += item_total
            sel_opts = json.loads(opts_json) if opts_json else {}
            products_list.append(
                {"product_id": product_id, "name": name, "price": real_price, "quantity": quantity, "total": item_total,
                 "emoji": emoji, "selected_options": sel_opts})

            new_variants_json = variants_json
            if variants_json and sel_opts:
                try:
                    v_data, changed = json.loads(variants_json), False
                    for key, val in sel_opts.items():
                        if key in v_data and isinstance(v_data[key], dict) and val in v_data[key]:
                            if isinstance(v_data[key][val], dict) and 'qty' in v_data[key][val]:
                                v_data[key][val]['qty'] = max(0, v_data[key][val]['qty'] - quantity)
                                changed = True
                            elif isinstance(v_data[key][val], int):
                                v_data[key][val] = max(0, v_data[key][val] - quantity)
                                changed = True
                    if changed: new_variants_json = json.dumps(v_data, ensure_ascii=False)
                except Exception as e:
                    logger.error(f"Error deducting variants stock: {e}")

            cursor.execute("UPDATE products SET stock = ?, variants = ? WHERE id = ?",
                           (max(0, p_stock - quantity), new_variants_json, product_id))

        active_promo = state.get('active_promo')
        promo_code_used = None
        if active_promo:
            total_amount -= total_amount * (active_promo['discount'] / 100.0)
            promo_code_used = f"{active_promo['code']} (-{active_promo['discount']}%)"
            promo_code = active_promo['code']
            state.pop('active_promo', None)

            cursor.execute("UPDATE promocodes SET current_uses = current_uses + 1 WHERE code = ?", (promo_code,))
            cursor.execute("INSERT INTO used_promocodes (user_id, code) VALUES (?, ?)", (user_id, promo_code))

        total_amount = round(total_amount, 2)

        payment_method = payment_method_override or state.get('payment', self.get_text('payment_unknown'))
        full_name = state.get('full_name', update.effective_user.full_name)
        email = state.get('email', self.get_text('not_specified_dash'))
        address = state.get('address', self.get_text('not_specified_dash'))
        phone = state.get('phone', self.get_text('not_specified_dash'))

        cursor.execute(
            "INSERT INTO orders (user_id, user_name, full_name, products, total_amount, phone, address, payment_method, email, promo_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, update.effective_user.full_name, full_name, json.dumps(products_list, ensure_ascii=False),
             total_amount, phone, address, payment_method, email, promo_code_used))
        order_id = cursor.lastrowid
        cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        self.conn.commit()

        if send_message: await self.notify_admin_new_order(context, order_id, full_name, email, phone, address,
                                                           payment_method, products_list, total_amount, promo_code_used)
        return order_id, products_list, total_amount, promo_code_used

    async def notify_admin_new_order(self, context, order_id, full_name, email, phone, address, payment_method,
                                     products_list, total_amount, promo_code_used=None):
        region_header = self.get_text(
            'new_order_notification_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'new_order_notification_international')
        address_label = self.get_text(
            'delivery_notification_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'delivery_notification_international')
        pay_label = self.get_text(
            'payment_notification_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'payment_notification_international')

        items_str = ""
        for item in products_list:
            opts_str = f" ({', '.join([str(v) for v in item.get('selected_options', {}).values()])})" if item.get(
                'selected_options') else ""
            items_str += f"▫️ {item['emoji']} {item['name']}{opts_str} x {item['quantity']} - <b>{item.get('price', 0)}{CURRENCY_SYMBOL}</b>\n"

        text = self.get_text('admin_new_order_notification', region_header=region_header, order_id=order_id,
                             full_name=full_name, email=email, phone=phone, address_label=address_label,
                             address=address, pay_label=pay_label, payment_method=payment_method,
                             items_str=items_str, total_amount=total_amount)

        if promo_code_used:
            text += self.get_text('order_promo_info', code=promo_code_used)

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(self.get_text('failed_to_notify_admin', admin=admin_id, e=e))

    # -------------------- ORDERS HISTORY --------------------
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
            return await query.edit_message_text(self.get_text('no_orders_yet'), reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")]]),
                                                 parse_mode="HTML")

        per_page = ITEMS_PER_PAGE
        total_pages = (total_orders - 1) // per_page + 1
        cursor.execute(
            'SELECT id, total_amount, status, created_at, products FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?',
            (user_id, per_page, page * per_page))

        text = self.get_text('your_orders_header', page=page + 1, total_pages=total_pages)
        keyboard = []
        status_map = {'pending': self.get_text('status_pending'),
                      'confirmed': self.get_text('status_confirmed'),
                      'shipped': self.get_text('status_shipped'),
                      'delivered': self.get_text('status_delivered'),
                      'cancelled': self.get_text('status_cancelled')}

        for order in cursor.fetchall():
            try:
                products_list = []
                for p in json.loads(order["products"]):
                    clean_name = re.sub(r'\s*\(?x\d+\)?\)*$', '', str(p.get('name', self.get_text('product'))))
                    products_list.append(f"{p.get('emoji', '📦')} {clean_name}")
                products_str = ", ".join(products_list)
            except Exception as e:
                logger.error(f"Помилка замовлень: {e}")
                products_str = self.get_text('order_items')

            if len(products_str) > 35: products_str = products_str[:32] + "..."

            display_status = status_map.get(order['status'], order['status'])

            text += self.get_text('order_summary_line', order_id=order['id'],
                                  products_str=self.escape_html(products_str), total_amount=order['total_amount'],
                                  status_text=display_status,
                                  date=self.format_date(order['created_at']))
            keyboard.append([InlineKeyboardButton(self.get_text('details_button', order_id=order['id']),
                                                  callback_data=f"order_details_{order['id']}_{page}")])

        nav = []
        if page > 0: nav.append(
            InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"my_orders_page_{page - 1}"))
        if page + 1 < total_pages: nav.append(
            InlineKeyboardButton(self.get_text('next_button'), callback_data=f"my_orders_page_{page + 1}"))
        if nav: keyboard.append(nav)
        keyboard.append([InlineKeyboardButton(self.get_text('main_menu_button'), callback_data="main_menu")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    async def handle_my_orders_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        await query.answer()
        match = re.match(r'^my_orders_page_(\d+)$', query.data)
        if match: await self.show_my_orders(update, context, int(match.group(1)))

    async def show_order_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE, order_id=None,
                                 origin_page=0):
        if await self.check_user_blocked(update, context): return
        query = getattr(update, "callback_query", None)
        user_id = update.effective_user.id

        if order_id is None and query:
            match = re.search(r'_(\d+)(?:_(\d+))?$', query.data)
            if match:
                order_id = int(match.group(1))
                if match.group(2): origin_page = int(match.group(2))
            else:
                return

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE id = ?" if int(
            user_id) in ADMIN_IDS else "SELECT * FROM orders WHERE id = ? AND user_id = ?",
                       (order_id,) if int(user_id) in ADMIN_IDS else (order_id, user_id))
        order = cursor.fetchone()

        if not order:
            if query: await query.answer(self.get_text('order_not_found'))
            return

        products_text = ""
        calc_subtotal = 0
        try:
            for p in json.loads(order["products"]):
                opts_str = f" ({', '.join([str(v) for v in p.get('selected_options', {}).values()])})" if p.get(
                    'selected_options') else ""
                clean_name = re.sub(r'\s*\(?x\d+\)?\)*$', '', str(p.get('name', self.get_text('unknown'))))
                item_total = p.get('total', 0)
                calc_subtotal += item_total
                products_text += f"{p.get('emoji', '📦')} {self.escape_html(clean_name)}{self.escape_html(opts_str)} x{p.get('quantity', 1)} = <b>{item_total}{CURRENCY_SYMBOL}</b>\n"

            if calc_subtotal > order['total_amount']:
                discount_diff = calc_subtotal - order['total_amount']
                products_text += self.get_text('receipt_discount', amount=round(discount_diff, 2),
                                               symbol=CURRENCY_SYMBOL)
        except Exception as e:
            logger.error(f"Помилка розбору товарів: {e}")
            products_text = "\n".join(
                [f"📦 {self.escape_html(line)}" for line in str(order["products"]).split('\n') if line.strip()]) if \
                order["products"] else self.get_text('items_info_unavailable')

        status_map = {'pending': self.get_text('status_pending'), 'confirmed': self.get_text('status_confirmed'),
                      'shipped': self.get_text('status_shipped'), 'delivered': self.get_text('status_delivered'),
                      'cancelled': self.get_text('status_cancelled')}

        display_status = status_map.get(order['status'], order['status'])

        text = self.get_text('order_details_text', order_id=order['id'], user_name=self.escape_html(order['user_name']),
                             email=self.escape_html(order['email'] or self.get_text('not_specified_dash')),
                             phone=self.escape_html(order['phone'] or self.get_text('not_specified_dash')),
                             address=self.escape_html(order['address']),
                             payment_method=self.escape_html(
                                 order['payment_method'] or self.get_text('not_specified_dash')),
                             products_text=products_text, total_amount=order['total_amount'],
                             status_display=display_status,
                             date=self.format_date(order['created_at']))

        promo_val = order['promo_code'] if 'promo_code' in order.keys() else None
        if promo_val:
            text += self.get_text('order_promo_info', code=promo_val)

        keyboard = []
        if int(user_id) in ADMIN_IDS:
            if order['status'] not in ('cancelled', 'delivered'):
                keyboard.extend([[InlineKeyboardButton(self.get_text('confirm_button'),
                                                       callback_data=f"admin_confirm_{order['id']}_{origin_page}"),
                                  InlineKeyboardButton(self.get_text('sent_button'),
                                                       callback_data=f"admin_ship_{order['id']}_{origin_page}")],
                                 [InlineKeyboardButton(self.get_text('delivered_button'),
                                                       callback_data=f"admin_deliver_{order['id']}_{origin_page}"),
                                  InlineKeyboardButton(self.get_text('cancel_button_2'),
                                                       callback_data=f"admin_cancel_{order['id']}_{origin_page}")]])
            keyboard.append([InlineKeyboardButton(self.get_text('back_to_all_orders_button'),
                                                  callback_data=f"admin_all_orders_page_{origin_page}")])
        else:
            if order['status'] not in ('cancelled', 'delivered'): keyboard.append(
                [InlineKeyboardButton(self.get_text('cancel_order_button_3'),
                                      callback_data=f"user_cancel_{order['id']}")])
            keyboard.append([InlineKeyboardButton(self.get_text('back_to_list_button'),
                                                  callback_data=f"my_orders_page_{origin_page}")])

        if query:
            try:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            except Exception:
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_message(query.message.chat_id, text, reply_markup=InlineKeyboardMarkup(keyboard),
                                               parse_mode="HTML")
        else:
            await context.bot.send_message(update.effective_chat.id, text, reply_markup=InlineKeyboardMarkup(keyboard),
                                           parse_mode="HTML")

    # -------------------- ADMIN PANEL --------------------
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return await update.callback_query.answer(
            self.get_text('access_denied'))
        keyboard = [[InlineKeyboardButton(self.get_text('all_orders_button'), callback_data="admin_all_orders")],
                    [InlineKeyboardButton(self.get_text('products_button'), callback_data="admin_products")],
                    [InlineKeyboardButton(self.get_text('stats_button'), callback_data="admin_statistics"),
                     InlineKeyboardButton(self.get_text('revenue_button'), callback_data="admin_revenue_chart")],
                    [InlineKeyboardButton(self.get_text('users_button'), callback_data="admin_user_management")],
                    [InlineKeyboardButton(self.get_text('admin_broadcast_button'),
                                          callback_data="admin_broadcast_prompt"),
                     InlineKeyboardButton(self.get_text('admin_promo_button'), callback_data="admin_promo_menu")],
                    [InlineKeyboardButton(self.get_text('main_menu_button_3'), callback_data="main_menu")]]
        await update.callback_query.edit_message_text(self.get_text('admin_panel_header'),
                                                      reply_markup=InlineKeyboardMarkup(keyboard),
                                                      parse_mode=ParseMode.MARKDOWN)

    # -------------------- ADMIN BROADCAST (РОЗСИЛКА) --------------------
    async def admin_broadcast_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        self.user_states[update.effective_user.id] = {'step': 'waiting_broadcast_message'}
        await update.callback_query.edit_message_text(self.get_text('admin_broadcast_prompt'),
                                                      reply_markup=InlineKeyboardMarkup(
                                                          [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                                 callback_data="admin_panel")]]),
                                                      parse_mode="HTML")

    async def handle_broadcast_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states[user_id]
        if int(user_id) not in ADMIN_IDS: return
        chat_id = update.effective_chat.id

        try:
            await update.message.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('admin_broadcast_started'),
                                           parse_mode="HTML")

        cursor = self.conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()

        success, failed = 0, 0
        for (uid,) in users:
            if int(uid) in ADMIN_IDS: continue
            try:
                await update.message.copy(chat_id=uid)
                success += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

        self.user_states.pop(user_id, None)
        try:
            await m.delete()
        except:
            pass

        await context.bot.send_message(chat_id=chat_id,
                                       text=self.get_text('admin_broadcast_finished', success=success, failed=failed),
                                       parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.get_text('back_to_admin_panel_button'), callback_data="admin_panel")]]))

    # -------------------- PROMO CODES ADMIN --------------------
    async def admin_promo_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        self.user_states.pop(update.effective_user.id, None)
        cursor = self.conn.cursor()
        cursor.execute("SELECT code, discount, max_uses, current_uses, is_reusable FROM promocodes")
        promos = cursor.fetchall()

        promo_list = ""
        if not promos:
            promo_list = self.get_text('promo_no_active')
        else:
            for code, discount, max_uses, current_uses, is_reusable in promos:
                left = max_uses - current_uses
                is_reusable = is_reusable or 0
                reusable_text = self.get_text('promo_reusable_multi') if is_reusable else self.get_text(
                    'promo_reusable_once')
                promo_list += self.get_text('promo_item_detailed', code=code, discount=discount, left=left,
                                            reusable_text=reusable_text) + "\n"

        keyboard = [
            [InlineKeyboardButton(self.get_text('admin_promo_add_btn'), callback_data="admin_promo_add_prompt")],
            [InlineKeyboardButton(self.get_text('admin_promo_del_btn'),
                                  callback_data="admin_promo_del_prompt")] if promos else [],
            [InlineKeyboardButton(self.get_text('back_to_admin_panel_button'), callback_data="admin_panel")]
        ]

        try:
            await update.callback_query.edit_message_text(self.get_text('admin_promo_menu', promo_list=promo_list),
                                                          reply_markup=InlineKeyboardMarkup(keyboard),
                                                          parse_mode="HTML")
        except:
            pass

    async def admin_promo_add_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        query = update.callback_query

        self.user_states[update.effective_user.id] = {
            'step': 'waiting_promo_code_name',
            'msg_id': query.message.message_id
        }

        await query.edit_message_text(self.get_text('admin_promo_ask_code'),
                                      reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                 callback_data="admin_promo_menu")]]),
                                      parse_mode="HTML")

    async def admin_promo_del_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, code FROM promocodes")
        promos = cursor.fetchall()

        if not promos:
            return await self.admin_promo_menu(update, context)

        kb = [[InlineKeyboardButton(f"🗑 {code}", callback_data=f"admin_pdel_{pid}")] for pid, code in promos]
        kb.append([InlineKeyboardButton(self.get_text('back_button_3'), callback_data="admin_promo_menu")])

        await update.callback_query.edit_message_text(self.get_text('admin_promo_del_select'),
                                                      reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    async def admin_delete_promo_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        query = update.callback_query
        pid = int(query.data.split('_')[2])
        cursor = self.conn.cursor()

        cursor.execute("SELECT code FROM promocodes WHERE id = ?", (pid,))
        row = cursor.fetchone()
        if row:
            code = row[0]
            cursor.execute("DELETE FROM promocodes WHERE id = ?", (pid,))
            self.conn.commit()
            await query.answer(self.get_text('admin_promo_deleted', code=code))
        else:
            await query.answer(self.get_text('admin_promo_not_found'))

        await self.admin_promo_menu(update, context)

    async def handle_promo_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states[user_id]
        step = state.get('step')
        text = update.message.text.strip() if update.message.text else ""
        chat_id = update.message.chat_id

        try:
            await update.message.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        if step == 'waiting_promo_code_name':
            state['promo_code'] = text.upper()
            state['step'] = 'waiting_promo_discount'
            m = await context.bot.send_message(chat_id=chat_id,
                                               text=self.get_text('admin_promo_ask_discount', code=state['promo_code']),
                                               reply_markup=InlineKeyboardMarkup(
                                                   [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                          callback_data="admin_promo_menu")]]),
                                               parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_promo_discount':
            try:
                discount = int(text)
                if not (1 <= discount <= 99): raise ValueError
                state['promo_discount'] = discount
                state['step'] = 'waiting_promo_uses'
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('admin_promo_ask_uses'),
                                                   reply_markup=InlineKeyboardMarkup(
                                                       [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                              callback_data="admin_promo_menu")]]),
                                                   parse_mode="HTML")
                state['msg_id'] = m.message_id
            except ValueError:
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('err_invalid_discount'),
                                                   reply_markup=InlineKeyboardMarkup(
                                                       [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                              callback_data="admin_promo_menu")]]),
                                                   parse_mode="HTML")
                state['msg_id'] = m.message_id

        elif step == 'waiting_promo_uses':
            try:
                max_uses = int(text)
                if max_uses <= 0: raise ValueError
                state['promo_max_uses'] = max_uses
                state['step'] = 'waiting_promo_reusable'

                kb = [
                    [InlineKeyboardButton(self.get_text('btn_promo_once'), callback_data="admin_promo_reusable_0")],
                    [InlineKeyboardButton(self.get_text('btn_promo_multi'), callback_data="admin_promo_reusable_1")],
                    [InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_promo_menu")]
                ]
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('admin_promo_ask_reusable'),
                                                   reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
                state['msg_id'] = m.message_id
            except ValueError:
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('err_invalid_uses'),
                                                   reply_markup=InlineKeyboardMarkup(
                                                       [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                              callback_data="admin_promo_menu")]]),
                                                   parse_mode="HTML")
                state['msg_id'] = m.message_id

    async def admin_promo_set_reusable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        query = update.callback_query
        user_id = update.effective_user.id
        state = self.user_states.get(user_id)
        if not state or state.get('step') != 'waiting_promo_reusable': return

        is_reusable = int(query.data.split('_')[-1])
        code = state.get('promo_code')
        discount = state.get('promo_discount')
        max_uses = state.get('promo_max_uses')

        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO promocodes (code, discount, max_uses, current_uses, is_reusable) VALUES (?, ?, ?, 0, ?)",
            (code, discount, max_uses, is_reusable)
        )
        self.conn.commit()

        await query.edit_message_text(text=self.get_text('admin_promo_added', code=code, discount=discount),
                                      reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton(self.get_text('back_button_3'),
                                                                 callback_data="admin_promo_menu")]]),
                                      parse_mode="HTML")
        self.user_states.pop(user_id, None)

    # -------------------- PROMO CODES CLIENT --------------------
    async def ask_promo_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return
        query = update.callback_query
        self.user_states.setdefault(update.effective_user.id, {})['step'] = 'waiting_user_promo'
        msg = await query.edit_message_text(self.get_text('ask_promo_code'), reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="cart")]]), parse_mode="HTML")
        self.user_states[update.effective_user.id]['msg_id'] = msg.message_id

    async def handle_user_promo_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state = self.user_states.get(user_id, {})
        text = update.message.text.strip().upper() if update.message.text else ""
        chat_id = update.message.chat_id

        try:
            await update.message.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        cursor = self.conn.cursor()
        cursor.execute("SELECT discount, max_uses, current_uses, is_reusable FROM promocodes WHERE code = ?", (text,))
        res = cursor.fetchone()

        if res:
            discount, max_uses, current_uses, is_reusable = res
            is_reusable = is_reusable or 0
            if current_uses >= max_uses:
                state['promo_msg'] = self.get_text('promo_limit_reached')
            else:
                if not is_reusable:
                    cursor.execute("SELECT 1 FROM used_promocodes WHERE user_id = ? AND code = ?", (user_id, text))
                    if cursor.fetchone():
                        state['promo_msg'] = self.get_text('promo_already_used')
                        state.pop('step', None)
                        await self.show_cart(update, context)
                        return

                self.user_states.setdefault(user_id, {})['active_promo'] = {'code': text, 'discount': discount}
                state['promo_msg'] = self.get_text('promo_applied_success', code=text, discount=discount)
        else:
            state['promo_msg'] = self.get_text('promo_applied_error')

        state.pop('step', None)
        await self.show_cart(update, context)

    async def admin_statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return await update.callback_query.answer(
            self.get_text('access_denied'))
        cursor = self.conn.cursor()

        cursor.execute(
            "SELECT COUNT(*), (SELECT COUNT(*) FROM orders WHERE status = 'pending'), (SELECT SUM(total_amount) FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered')), (SELECT COUNT(*) FROM users), (SELECT COUNT(DISTINCT user_id) FROM orders) FROM orders")
        total_orders, pending_orders, total_revenue, total_users, active_buyers = cursor.fetchone()

        cursor.execute("SELECT products FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered')")
        product_sales = {}
        for (products_json,) in cursor.fetchall():
            try:
                for item in json.loads(products_json): product_sales[
                    item.get('name', self.get_text('unknown'))] = product_sales.get(
                    item.get('name', self.get_text('unknown')), 0) + item.get('quantity', 0)
            except:
                pass

        sorted_sales = sorted(product_sales.items(), key=lambda x: x[1], reverse=True)

        pcs_str = self.get_text('unit_pcs')
        top_text = "\n".join([f"🔥 {name}: {qty} {pcs_str}" for name, qty in sorted_sales[:5]]) if sorted_sales[
            :5] else self.get_text('no_data')
        bottom_text = "\n".join([f"🧊 {name}: {qty} {pcs_str}" for name, qty in sorted_sales[-5:]]) if sorted_sales[
            -5:] else self.get_text('no_data')

        text = self.get_text('admin_stats_text', total_revenue=total_revenue or 0, total_orders=total_orders,
                             pending_orders=pending_orders, total_users=total_users, active_buyers=active_buyers,
                             top_text=top_text, bottom_text=bottom_text)

        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(self.get_text('back_to_admin_panel_button'), callback_data="admin_panel")]]),
                                                      parse_mode="HTML")

    async def admin_revenue(self, update: Update, context: ContextTypes.DEFAULT_TYPE, period: str = "all"):
        if int(update.effective_user.id) not in ADMIN_IDS: return await update.callback_query.answer(
            self.get_text('access_denied'))

        period_sql, label = "", self.get_text('all_time')
        if period == "today":
            period_sql, label = " AND created_at >= date('now', 'localtime')", self.get_text('today')
        elif period == "week":
            period_sql, label = " AND created_at >= date('now', '-7 days')", self.get_text('last_7_days')
        elif period == "month":
            period_sql, label = " AND created_at >= date('now', '-30 days')", self.get_text('last_30_days')

        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT SUM(total_amount), COUNT(id) FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered'){period_sql}")
        total_rev, total_orders = cursor.fetchone()
        cursor.execute(f"SELECT SUM(total_amount) FROM orders WHERE status = 'pending'{period_sql}")
        pending_rev = cursor.fetchone()[0] or 0

        text = self.get_text('financial_report', label=label.upper(), total_rev=total_rev or 0,
                             avg_check=round((total_rev or 0) / (total_orders or 1), 2), total_orders=total_orders or 0,
                             pending_rev=pending_rev)
        keyboard = [[InlineKeyboardButton(self.get_text('today_button'), callback_data="rev_today"),
                     InlineKeyboardButton(self.get_text('week_button'), callback_data="rev_week"),
                     InlineKeyboardButton(self.get_text('month_button'), callback_data="rev_month")],
                    [InlineKeyboardButton(self.get_text('all_time_button'), callback_data="rev_all")],
                    [InlineKeyboardButton(self.get_text('back_to_admin_panel_button'), callback_data="admin_panel")]]

        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                          parse_mode="HTML")
        except:
            pass

    async def handle_revenue_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await self.admin_revenue(update, context, period=update.callback_query.data.replace("rev_", ""))

    # -------------------- ADMIN: USERS & ORDERS --------------------
    async def admin_user_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
        if int(update.effective_user.id) not in ADMIN_IDS: return await update.callback_query.answer(
            self.get_text('access_denied'))

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        total_pages = max(1, (total_users - 1) // ADMIN_ITEMS_PER_PAGE + 1)

        cursor.execute("SELECT user_id, blocked FROM users LIMIT ? OFFSET ?",
                       (ADMIN_ITEMS_PER_PAGE, page * ADMIN_ITEMS_PER_PAGE))
        keyboard = []
        for user_id, blocked in cursor.fetchall():
            try:
                chat = await context.bot.get_chat(user_id)
                user_display = f"@{chat.username}" if chat.username else (
                        chat.first_name or self.get_text('user_id', user_id=user_id))
            except:
                user_display = self.get_text('user_id', user_id=user_id)

            keyboard.append(
                [InlineKeyboardButton(self.get_text('user_display', user_display=user_display), callback_data="noop"),
                 InlineKeyboardButton(self.get_text('unblock_button') if blocked else self.get_text('block_button'),
                                      callback_data=f"admin_user_block_{user_id}_{0 if blocked else 1}")])

        nav = []
        if page > 0: nav.append(
            InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"admin_user_page_{page - 1}"))
        if page + 1 < total_pages: nav.append(
            InlineKeyboardButton(self.get_text('next_button'), callback_data=f"admin_user_page_{page + 1}"))
        if nav: keyboard.append(nav)
        keyboard.append([InlineKeyboardButton(self.get_text('admin_panel_button_2'), callback_data="admin_panel")])

        try:
            await update.callback_query.edit_message_text(
                self.get_text('user_management_header', page=page + 1, total_pages=total_pages,
                              total_users=total_users), reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN)
        except:
            pass

    async def admin_user_block(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return await update.callback_query.answer(
            self.get_text('access_denied'))
        _, _, _, user_id, block_status = update.callback_query.data.split("_")

        cursor = self.conn.cursor()
        cursor.execute("UPDATE users SET blocked = ? WHERE user_id = ?", (int(block_status), int(user_id)))
        self.conn.commit()
        await self.admin_user_management(update, context)

    async def handle_admin_user_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        match = re.match(r'^admin_user_page_(\d+)$', update.callback_query.data)
        if match: await self.admin_user_management(update, context, int(match.group(1)))

    async def admin_all_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
        query = update.callback_query
        if int(update.effective_user.id) not in ADMIN_IDS: return await query.answer(
            self.get_text('access_denied_not_admin'), show_alert=True)

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) as total FROM orders")
        total_orders = cursor.fetchone()["total"]
        total_pages = max(1, (total_orders - 1) // ADMIN_ITEMS_PER_PAGE + 1) if total_orders else 1

        cursor.execute(
            'SELECT id, user_name, total_amount, status, created_at FROM orders ORDER BY id DESC LIMIT ? OFFSET ?',
            (ADMIN_ITEMS_PER_PAGE, page * ADMIN_ITEMS_PER_PAGE))
        orders = cursor.fetchall()

        if not orders: return await query.edit_message_text(self.get_text('no_orders_in_db'),
                                                            reply_markup=InlineKeyboardMarkup(
                                                                [[InlineKeyboardButton(self.get_text('back_button_2'),
                                                                                       callback_data="admin_panel")]]))

        text = self.get_text('all_orders_header', page=page + 1, total_pages=total_pages)
        keyboard = []

        for o in orders:
            status_text = self.get_text('status_' + o['status'])
            text += f"{status_text} <code>#{o['id']}</code> | {o['user_name']} | {o['total_amount']}{CURRENCY_SYMBOL} | {self.format_date(o['created_at'])}\n"
            keyboard.append([InlineKeyboardButton(self.get_text('details_button_2', order_id=o['id']),
                                                  callback_data=f"order_details_{o['id']}_{page}")])

        nav = []
        if page > 0: nav.append(
            InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"admin_all_orders_page_{page - 1}"))
        if page + 1 < total_pages: nav.append(
            InlineKeyboardButton(self.get_text('next_button'), callback_data=f"admin_all_orders_page_{page + 1}"))
        if nav: keyboard.append(nav)
        keyboard.append([InlineKeyboardButton(self.get_text('back_to_admin_button'), callback_data="admin_panel")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        await query.answer()

    async def handle_admin_all_orders_pagination(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        match = re.match(r'^admin_all_orders_page_(\d+)$', update.callback_query.data)
        if match: await self.admin_all_orders(update, context, int(match.group(1)))

    async def admin_order_status_change(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if int(update.effective_user.id) not in ADMIN_IDS: return await query.answer(self.get_text('access_denied'))

        match = re.search(r'admin_(confirm|ship|deliver|cancel)_(\d+)(?:_(\d+))?', query.data)
        if not match: return await query.answer(self.get_text('error_parsing_data'))

        new_status = {"confirm": "confirmed", "ship": "shipped", "deliver": "delivered", "cancel": "cancelled"}.get(
            match.group(1))
        order_id, origin_page = int(match.group(2)), int(match.group(3)) if match.group(3) else 0

        cursor = self.conn.cursor()
        if new_status == 'cancelled':
            cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
            if cursor.fetchone()[0] != 'cancelled': self.restore_stock(order_id)

        cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        self.conn.commit()

        status_localized = self.get_text('status_' + new_status)
        await query.answer(self.get_text('status_updated', new_status=status_localized))

        try:
            cursor.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
            if row := cursor.fetchone():
                msg_text = self.get_text(
                    'order_update_notification_ukraine' if SHIPPING_MODE == 'UKRAINE' else 'order_update_notification_international',
                    order_id=order_id, display_status=status_localized)
                await context.bot.send_message(chat_id=row[0], text=msg_text, parse_mode="HTML")
        except Exception as e:
            logger.error(self.get_text('failed_to_notify_user', e=e))

        await self.show_order_details(update, context, order_id=order_id, origin_page=origin_page)

    # -------------------- ADMIN: PRODUCTS MANAGEMENT --------------------
    async def admin_categories_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        page = int(query.data.split("_")[-1]) if query and query.data.startswith("admin_cat_page_") and \
                                                 query.data.split("_")[-1].isdigit() else 1

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT category) FROM products")

        total_pages = max(1, (cursor.fetchone()[0] + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        page = max(1, min(page, total_pages))

        cursor.execute(f"SELECT DISTINCT category FROM products ORDER BY category ASC LIMIT {ITEMS_PER_PAGE} OFFSET ?",
                       ((page - 1) * ITEMS_PER_PAGE,))
        text = self.get_text('product_management_header') + (self.get_text('page_indicator_2', page=page,
                                                                           total_pages=total_pages) if total_pages > 1 else "") + self.get_text(
            'select_category_to_edit')

        keyboard = []
        for (cat_name,) in cursor.fetchall():
            cursor.execute("SELECT COUNT(*) FROM products WHERE category = ?", (cat_name,))
            keyboard.append([InlineKeyboardButton(
                self.get_text('category_button_count', cat_name=cat_name, count=cursor.fetchone()[0]),
                callback_data=f"admin_list_cat_{cat_name}_1")])

        nav = []
        if page > 1: nav.append(
            InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"admin_cat_page_{page - 1}"))
        if page < total_pages: nav.append(
            InlineKeyboardButton(self.get_text('next_button'), callback_data=f"admin_cat_page_{page + 1}"))
        if nav: keyboard.append(nav)

        keyboard.extend([[InlineKeyboardButton(self.get_text('add_product_button'), callback_data="admin_add_product")],
                         [InlineKeyboardButton(self.get_text('back_to_admin_panel_button'),
                                               callback_data="admin_panel")]])

        try:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                          parse_mode=ParseMode.MARKDOWN)
        except:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_products_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE, category_override=None):
        query = update.callback_query
        if category_override:
            category, page = category_override, 1
        else:
            try:
                parts = query.data.split("_");
                page, category = int(parts[-1]), "_".join(parts[3:-1])
            except:
                return await query.answer(self.get_text('error_parsing_category'))

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products WHERE category = ?", (category,))
        total_items = cursor.fetchone()[0]

        if total_items == 0: return await self.admin_categories_menu(update, context)

        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        cursor.execute("SELECT id, name, stock FROM products WHERE category = ? LIMIT ? OFFSET ?",
                       (category, ITEMS_PER_PAGE, (page - 1) * ITEMS_PER_PAGE))

        text = self.get_text('category_header_2', category=category, page=page, total_pages=total_pages)
        keyboard = [
            [InlineKeyboardButton(f"{'✅' if p_stock > 0 else '❌'} {p_name}", callback_data=f"admin_prod_{p_id}_{page}")]
            for p_id, p_name, p_stock in cursor.fetchall()]

        nav = []
        if page > 1: nav.append(
            InlineKeyboardButton(self.get_text('prev_button'), callback_data=f"admin_list_cat_{category}_{page - 1}"))
        if page < total_pages: nav.append(
            InlineKeyboardButton(self.get_text('next_button'), callback_data=f"admin_list_cat_{category}_{page + 1}"))
        if nav: keyboard.append(nav)

        keyboard.append(
            [InlineKeyboardButton(self.get_text('back_to_categories_button'), callback_data="admin_products")])

        try:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                          parse_mode=ParseMode.MARKDOWN)
        except:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def admin_product_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE, product_id_override=None):
        query, origin_page = update.callback_query, 1
        try:
            if product_id_override:
                product_id = int(product_id_override)
            elif query:
                parts = query.data.split("_");
                product_id = int(parts[2]);
                origin_page = int(parts[3]) if len(
                    parts) >= 4 else 1
            else:
                return
        except:
            return

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if not product: return await query.answer(self.get_text('product_not_found_2')) if query else None

        self.user_states[update.effective_user.id] = {'product_id': product_id, 'step': 'edit_product_menu'}
        stock_details, all_prices = "", []

        if product['variants']:
            try:
                v_data = json.loads(product['variants'])
                stock_details = self.get_text('admin_stock_details_header')
                for key, val in v_data.items():
                    if isinstance(val, dict):
                        stock_details += f"  🔹 {key}:\n"
                        for opt, info in sorted(val.items(), key=lambda x: x[0]):
                            qty = info.get('qty', 0) if isinstance(info, dict) else int(info)
                            price_str = f" ({float(info['price'])}{CURRENCY_SYMBOL})" if isinstance(info,
                                                                                                    dict) and 'price' in info else ""
                            if isinstance(info, dict) and 'price' in info:
                                all_prices.append(float(info['price']))
                            elif product['price'] > 0:
                                all_prices.append(product['price'])
                            stock_details += f"    - {opt}: {'✅' if qty > 0 else '❌'} {qty}{price_str}\n"
            except:
                pass

        if not all_prices: all_prices.append(product['price'])

        if all_prices and min(all_prices) != max(all_prices):
            display_price = self.get_text('price_from', price=min(all_prices))
        else:
            display_price = f"{all_prices[0] if all_prices else product['price']}{CURRENCY_SYMBOL}"

        text = self.get_text('product_management_details', product_id=product['id'], stock=product['stock'],
                             stock_details=stock_details, name=product['name'], description=product['description'],
                             display_price=display_price, category=product['category'], emoji=product['emoji'])

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
                                  callback_data=f"admin_delete_product_confirm_{product_id}")],
            [InlineKeyboardButton(self.get_text('back_to_list_button_2'),
                                  callback_data=f"admin_list_cat_{product['category']}_{origin_page}")]
        ]

        try:
            if query: await query.message.delete()
        except:
            pass

        if product['image_url']:
            try:
                return await context.bot.send_photo(chat_id=update.effective_chat.id, photo=product['image_url'],
                                                    caption=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                    parse_mode=ParseMode.MARKDOWN)
            except:
                pass
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text,
                                       reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # -------------------- ADMIN: ADD/EDIT/DELETE --------------------
    async def admin_add_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        self.user_states[update.effective_user.id] = {'step': 'add_product_name', 'product_data': {}}
        msg = await update.callback_query.edit_message_text(self.get_text('adding_new_product_name'),
                                                            reply_markup=InlineKeyboardMarkup(
                                                                [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                                       callback_data="admin_products")]]),
                                                            parse_mode=ParseMode.MARKDOWN)
        self.user_states[update.effective_user.id]['msg_id'] = msg.message_id

    async def admin_edit_field(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        match = re.search(r"admin_edit_field_(.+)_(\d+)$", query.data)
        if not match: return await query.answer(self.get_text('invalid_request_2'))

        field, product_id = match.group(1), int(match.group(2))
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

        if not product: return await query.answer(self.get_text('product_not_found_2'))

        if field == "variants":
            current_text = self.get_text('none')
            if product['variants']:
                try:
                    lines = [f"{v_type}: {', '.join([f'{opt}={info.get(qty, info)}' for opt, info in options.items()])}"
                             for v_type, options in json.loads(product['variants']).items()]
                    current_text = "<code>" + "\n".join(lines) + "</code>"
                except:
                    current_text = f"<code>{product['variants']}</code>"
            msg_text = self.get_text('editing_variants_instructions', current_text=current_text)
        else:
            current_val = product[field] if product[field] is not None else self.get_text('not_set')
            display_name = {"name": self.get_text('summary_name_label'), "description": self.get_text('desc'),
                            "price": self.get_text('price'), "stock": self.get_text('stock'),
                            "emoji": self.get_text('emoji'), "category": self.get_text('category')}.get(field,
                                                                                                        field.capitalize())
            msg_text = f"<b>{self.get_text('edit_field_title', field=display_name)}</b>\n\n<b>{self.get_text('current_value_label')}</b> <code>{current_val}</code>\n\n{self.get_text('enter_new_value_prompt')}"

        self.user_states[update.effective_user.id] = {'step': 'edit_product_field', 'product_id': product_id,
                                                      'field': field}
        await query.message.delete()
        sent_msg = await context.bot.send_message(chat_id=query.message.chat_id, text=msg_text,
                                                  reply_markup=self.get_existing_categories_keyboard(
                                                      product_id=product_id) if field == "category" else InlineKeyboardMarkup(
                                                      [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                             callback_data=f"admin_prod_{product_id}")]]),
                                                  parse_mode="HTML")
        self.user_states[update.effective_user.id]['msg_id'] = sent_msg.message_id

    async def admin_image_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        query = update.callback_query
        match = re.match(r"admin_image_menu_(\d+)", query.data)
        if not match: return await query.answer(self.get_text('invalid_request'))

        product_id = int(match.group(1))
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

        if not product: return await query.answer(self.get_text('product_not_found_2'))

        has_image = bool(product['image_url'])
        text = self.get_text('product_image_management', product_name=product['name'],
                             status=self.get_text('admin_img_status_set') if has_image else self.get_text(
                                 'admin_img_status_none'))

        keyboard = []
        if not has_image:
            keyboard.append([InlineKeyboardButton(self.get_text('add_photo_button'),
                                                  callback_data=f"admin_image_set_{product_id}")])
        else:
            keyboard.extend([[InlineKeyboardButton(self.get_text('change_photo_button'),
                                                   callback_data=f"admin_image_set_{product_id}")],
                             [InlineKeyboardButton(self.get_text('delete_photo_button'),
                                                   callback_data=f"admin_image_delete_{product_id}")]])
        keyboard.append(
            [InlineKeyboardButton(self.get_text('back_to_editing_button'), callback_data=f"admin_prod_{product_id}")])

        if query.message.photo:
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard),
                                          parse_mode=ParseMode.HTML)

    async def admin_image_set_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        match = re.match(r"admin_image_set_(\d+)", query.data)
        if not match: return

        self.user_states[query.from_user.id] = {'step': 'waiting_product_image', 'product_id': int(match.group(1))}
        try:
            await query.message.delete()
        except:
            pass

        msg = await context.bot.send_message(chat_id=query.message.chat_id, text=self.get_text('send_product_image'),
                                             reply_markup=InlineKeyboardMarkup(
                                                 [[InlineKeyboardButton(self.get_text('cancel_button_3'),
                                                                        callback_data=f"admin_image_menu_{match.group(1)}")]]),
                                             parse_mode=ParseMode.MARKDOWN)
        self.user_states[query.from_user.id]['msg_id'] = msg.message_id

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
        except:
            pass

        await context.bot.send_message(chat_id=query.message.chat_id,
                                       text=self.get_text('product_image_management_no_image'),
                                       reply_markup=InlineKeyboardMarkup(
                                           [[InlineKeyboardButton(self.get_text('add_photo_button'),
                                                                  callback_data=f"admin_image_set_{product_id}")],
                                            [InlineKeyboardButton(self.get_text('back_to_editing_button'),
                                                                  callback_data=f"admin_prod_{product_id}")]]),
                                       parse_mode=ParseMode.MARKDOWN)

    async def admin_delete_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        query = update.callback_query
        match = re.match(r"admin_delete_product_(\d+)", query.data)
        if not match: return await query.answer(self.get_text('invalid_request_2'))

        product_id = int(match.group(1))
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if not row: return await query.answer(self.get_text('product_not_found_2'))

        try:
            await query.message.delete()
        except:
            pass

        await context.bot.send_message(chat_id=query.message.chat_id,
                                       text=self.get_text('confirm_delete_product', name=row[0]),
                                       reply_markup=InlineKeyboardMarkup(
                                           [[InlineKeyboardButton(self.get_text('yes_delete_button'),
                                                                  callback_data=f"admin_delete_product_confirm_{product_id}")],
                                            [InlineKeyboardButton(self.get_text('cancel_button'),
                                                                  callback_data="admin_products")]]),
                                       parse_mode=ParseMode.MARKDOWN)

    async def admin_wizard_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        self.user_states.pop(update.effective_user.id, None)
        await update.callback_query.answer(self.get_text('cancelled'))
        await self.admin_categories_menu(update, context)

    async def admin_delete_product_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if int(update.effective_user.id) not in ADMIN_IDS: return
        query = update.callback_query
        match = re.match(r"admin_delete_product_confirm_(\d+)", query.data)
        if not match: return await query.answer(self.get_text('invalid_request_2'))

        product_id = int(match.group(1))
        cursor = self.conn.cursor()
        cursor.execute("SELECT name, category FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()

        if not row:
            await query.answer(self.get_text('product_already_deleted'))
            return await self.admin_categories_menu(update, context)

        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        self.conn.commit()
        await query.answer(self.get_text('product_deleted', name=row[0]))
        await self.admin_products_list(update, context, category_override=row[1])

    # -------------------- INPUT HANDLERS (МАЙСТЕР-РОУТЕР) --------------------
    async def master_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.check_user_blocked(update, context): return

        if getattr(update.message, 'successful_payment', None): return

        user_id = update.effective_user.id

        if user_id in self.user_states:
            step = self.user_states[user_id].get('step', '')

            if step == 'waiting_broadcast_message':
                return await self.handle_broadcast_input(update, context)

            if step in ['waiting_promo_code_name', 'waiting_promo_discount', 'waiting_promo_uses']:
                return await self.handle_promo_input(update, context)

            if step == 'waiting_user_promo':
                return await self.handle_user_promo_input(update, context)

            if step.startswith('add_product') or step.startswith('edit_') or step.startswith(
                    'waiting_simple_') or step.startswith('waiting_var_') or step in ['waiting_product_image',
                                                                                      'waiting_variant_values',
                                                                                      'waiting_type_decision']:
                await self.handle_admin_product_input(update, context)
            elif step.startswith('waiting_') and '_profile' in step:
                await self.handle_profile_input(update, context)
            elif step.startswith('waiting_'):
                await self.handle_checkout_input(update, context)
        else:
            if update.message and update.message.text:
                await update.message.reply_text(self.get_text('use_start'))

    async def handle_edit_field_input(self, update, context, state, input_value, msg):
        user_id, chat_id = update.effective_user.id, update.effective_chat.id
        product_id, field = state.get('product_id'), state.get('field')

        try:
            await msg.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        async def send_error(text):
            new_msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data=f"admin_prod_{product_id}")]]),
                                                     parse_mode="HTML")
            state['msg_id'] = new_msg.message_id

        cursor, value, new_total_stock = self.conn.cursor(), input_value, None

        if field in ['price', 'stock']:
            try:
                value = float(str(input_value).replace('$', '').replace(' ', '').replace(',',
                                                                                         '.').strip()) if field == 'price' else int(
                    str(input_value).replace(' ', '').strip())
            except ValueError:
                return await send_error(
                    self.get_text('err_invalid_number', val=input_value,
                                  ex_val=self.get_text('ex_price_val') if field == 'price' else self.get_text(
                                      'ex_stock_val')))
        elif field == 'variants':
            try:
                if ":" in str(input_value):
                    v_type_part, options_part = str(input_value).split(":", 1)
                    v_data, calculated_stock = {v_type_part.strip(): {}}, 0
                    for opt in options_part.split(","):
                        parts = opt.strip().split("=")
                        qty = int(parts[1].strip()) if len(parts) > 1 else 0
                        v_data[v_type_part.strip()][parts[0].strip()] = {"qty": qty,
                                                                         "price": float(parts[2].strip()) if len(
                                                                             parts) > 2 else 0}
                        calculated_stock += qty
                    value, new_total_stock = json.dumps(v_data, ensure_ascii=False), calculated_stock
                else:
                    raise ValueError()
            except Exception:
                return await send_error(self.get_text('err_variant_format', val=input_value))

        try:
            if new_total_stock is not None:
                cursor.execute("UPDATE products SET variants = ?, stock = ? WHERE id = ?",
                               (value, new_total_stock, product_id))
            else:
                cursor.execute(f"UPDATE products SET {field} = ? WHERE id = ?", (value, product_id))
            self.conn.commit()
        except Exception as e:
            return await send_error(self.get_text('error_db', e=e))

        self.user_states.pop(user_id, None)
        display_field = {"name": self.get_text('summary_name_label'), "price": self.get_text('price'),
                         "stock": self.get_text('stock'), "variants": self.get_text('variants')}.get(field,
                                                                                                     str(field).capitalize())
        await context.bot.send_message(chat_id=chat_id,
                                       text=self.get_text('status_updated', new_status=f"<b>{display_field}</b>"),
                                       reply_markup=InlineKeyboardMarkup(
                                           [[InlineKeyboardButton(self.get_text('back_button_3'),
                                                                  callback_data=f"admin_prod_{product_id}")]]),
                                       parse_mode="HTML")

    async def handle_admin_product_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if int(user_id) not in ADMIN_IDS or user_id not in self.user_states: return

        state, msg = self.user_states[user_id], update.message
        chat_id, step = msg.chat_id, state.get("step")

        try:
            await msg.delete()
        except:
            pass
        if 'msg_id' in state:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=state['msg_id'])
            except:
                pass

        input_value = update.message.photo[-1].file_id if update.message.photo else (
            update.message.text.strip() if update.message.text else "")
        is_photo = bool(update.message.photo)
        cancel_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(self.get_text('cancel_button'), callback_data="admin_wizard_cancel")]])

        if step == 'edit_product_field': return await self.handle_edit_field_input(update, context, state, input_value,
                                                                                   msg)

        if step == 'waiting_product_image':
            img = input_value if (is_photo or input_value.startswith('http')) else None
            if img or input_value == '-':
                cursor = self.conn.cursor()
                cursor.execute("UPDATE products SET image_url = ? WHERE id = ?",
                               (None if input_value == '-' else img, state.get('product_id')))
                self.conn.commit()
                self.user_states.pop(user_id, None)
                await context.bot.send_message(chat_id=chat_id, text=self.get_text('admin_img_status_set'),
                                               reply_markup=InlineKeyboardMarkup(
                                                   [[InlineKeyboardButton(self.get_text('back_button_3'),
                                                                          callback_data=f"admin_prod_{state.get('product_id')}")]]),
                                               parse_mode="HTML")
            else:
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('error_photo_required'),
                                                   reply_markup=cancel_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
            return

        if step == 'add_product_name':
            state['product_data'], state['step'] = {'name': input_value}, 'add_product_description'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('enter_product_description'),
                                               reply_markup=cancel_kb, parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'add_product_description':
            state['product_data']['description'] = input_value
            state['step'] = 'waiting_type_decision'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('choose_product_type'),
                                               reply_markup=InlineKeyboardMarkup(
                                                   [[InlineKeyboardButton(self.get_text('simple_product_button'),
                                                                          callback_data="admin_decision_vars_no")],
                                                    [InlineKeyboardButton(self.get_text('has_variants_button'),
                                                                          callback_data="admin_decision_vars_yes")],
                                                    [InlineKeyboardButton(self.get_text('cancel_button'),
                                                                          callback_data="admin_wizard_cancel")]]),
                                               parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_simple_price':
            try:
                state['product_data']['price'] = float(
                    input_value.replace('$', '').replace(' ', '').replace(',', '.').strip())
                state['step'] = 'waiting_simple_stock'
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('admin_wizard_simple_stock'),
                                                   reply_markup=cancel_kb, parse_mode="HTML")
            except:
                m = await context.bot.send_message(chat_id=chat_id,
                                                   text=self.get_text('err_invalid_number', val=input_value,
                                                                      ex_val=self.get_text('ex_price_val')),
                                                   reply_markup=cancel_kb,
                                                   parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_simple_stock':
            try:
                state['product_data']['stock'] = int(input_value)
                state['step'] = 'waiting_simple_category'
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('enter_category'),
                                                   reply_markup=self.get_existing_categories_keyboard(),
                                                   parse_mode="HTML")
            except:
                m = await context.bot.send_message(chat_id=chat_id,
                                                   text=self.get_text('err_invalid_number', val=input_value,
                                                                      ex_val=self.get_text('ex_stock_val')),
                                                   reply_markup=cancel_kb,
                                                   parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_simple_category':
            state['product_data']['category'], state['step'] = input_value, 'waiting_simple_emoji'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('enter_emoji'),
                                               reply_markup=cancel_kb, parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_simple_emoji':
            state['product_data']['emoji'], state['step'] = input_value, 'waiting_simple_image'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('admin_wizard_variant_photo'),
                                               reply_markup=cancel_kb, parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step in ['waiting_simple_image', 'waiting_var_image']:
            img = input_value if (is_photo or input_value.startswith('http')) else None
            if not img and input_value != '-':
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('error_photo_required'),
                                                   reply_markup=cancel_kb, parse_mode="HTML")
                state['msg_id'] = m.message_id
                return

            if step == 'waiting_simple_image':
                p = state['product_data']
                cursor = self.conn.cursor()
                cursor.execute(
                    "INSERT INTO products (name, description, price, image_url, category, stock, emoji) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (p['name'], p['description'], p['price'], img if input_value != '-' else None, p['category'],
                     p['stock'], p['emoji']))
                self.conn.commit()
                self.user_states.pop(user_id, None)
                await context.bot.send_message(chat_id=chat_id, text=self.get_text('product_created', name=p['name']),
                                               reply_markup=InlineKeyboardMarkup(
                                                   [[InlineKeyboardButton(self.get_text('back_button_3'),
                                                                          callback_data="admin_products")]]),
                                               parse_mode="HTML")
            else:
                state['product_data']['image_url'], state[
                    'step'] = img if input_value != '-' else None, 'waiting_var_category'
                m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('enter_category'),
                                                   reply_markup=self.get_existing_categories_keyboard(),
                                                   parse_mode="HTML")
                state['msg_id'] = m.message_id

        elif step == 'waiting_var_category':
            state['product_data']['category'], state['step'] = input_value, 'waiting_var_emoji'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('enter_emoji'),
                                               reply_markup=cancel_kb, parse_mode="HTML")
            state['msg_id'] = m.message_id

        elif step == 'waiting_var_emoji':
            state['product_data']['emoji'], state['step'] = input_value, 'add_product_variants_loop'
            await self.show_variant_type_selection(context, chat_id, user_id)

        elif step == 'waiting_variant_values':
            await self.process_variant_values_input(update, context)

    async def show_variant_type_selection(self, context, chat_id, user_id, status_msg="", edit_query=None):
        state = self.user_states[user_id]
        variants = state['product_data'].get('variants', {})

        added_info = ""
        if variants:
            v_type_raw = list(variants.keys())[0]
            v_type_localized = self.get_text(f'type_{v_type_raw}')
            if v_type_localized == f"_type_{v_type_raw}_": v_type_localized = v_type_raw
            added_info = f"{self.get_text('active_variant_label')}{v_type_localized} (<i>{', '.join(variants[v_type_raw].keys())}</i>)\n────────────────────\n\n"

        text = f"{self.get_text('status_message', status_msg=status_msg) if status_msg else ''}{self.get_text('admin_wizard_variant_title', added_info=added_info)}"

        if edit_query:
            try:
                return await edit_query.edit_message_text(text, reply_markup=self.get_variant_type_keyboard(),
                                                          parse_mode="HTML")
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
        if not state: return

        data = query.data.replace("vartype_", "")

        if data == "DONE":
            p = state.get('product_data', {})
            vars_data = p.get('variants', {})

            if not vars_data: return await query.answer(self.get_text('add_variants_before_finishing'), show_alert=True)

            total_stock = sum(opt.get('qty', 0) if isinstance(opt, dict) else opt for v_type in vars_data for opt in
                              vars_data[v_type].values())

            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO products (name, description, price, image_url, emoji, category, stock, variants) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (p.get("name"), p.get("description"), 0.0, p.get("image_url"), p.get("emoji", "📦"), p.get("category"),
                 total_stock, json.dumps(vars_data, ensure_ascii=False)))
            self.conn.commit()

            self.user_states.pop(user_id, None)
            await query.message.delete()
            return await context.bot.send_message(chat_id=query.message.chat_id,
                                                  text=self.get_text('product_created_2', name=p.get('name')),
                                                  reply_markup=InlineKeyboardMarkup(
                                                      [[InlineKeyboardButton(self.get_text('back_button_3'),
                                                                             callback_data="admin_products")]]),
                                                  parse_mode="HTML")

        state['current_variant_type'], state['step'] = data, 'waiting_variant_values'
        v_type_localized = self.get_text(f'type_{data}')
        if v_type_localized == f"_type_{data}_": v_type_localized = data
        ex = self.get_text(f'admin_ex_{data}')
        if ex == f"_admin_ex_{data}_": ex = self.get_text('admin_ex_default')

        await query.edit_message_text(text=self.get_text('variant_input_prompt', v_type=v_type_localized, example=ex),
                                      reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton(self.get_text('back_to_types_btn'),
                                                                 callback_data="admin_step_variants_init"),
                                            InlineKeyboardButton(self.get_text('cancel_button'),
                                                                 callback_data="admin_wizard_cancel")]]),
                                      parse_mode="HTML")

    async def admin_handle_variant_decision(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        try:
            await query.message.delete()
        except:
            pass

        if user_id not in self.user_states: self.user_states[user_id] = {'product_data': {}}

        if query.data == "admin_decision_vars_no":
            self.user_states[user_id]['step'] = 'waiting_simple_price'
            text = self.get_text('admin_wizard_simple_price')
        else:
            self.user_states[user_id]['step'] = 'waiting_var_image'
            self.user_states[user_id]['product_data']['variants'] = {}
            text = self.get_text('admin_wizard_variant_photo')

        msg = await context.bot.send_message(chat_id=query.message.chat_id, text=text,
                                             reply_markup=InlineKeyboardMarkup(
                                                 [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                        callback_data="admin_wizard_cancel")]]),
                                             parse_mode="HTML")
        self.user_states[user_id]['msg_id'] = msg.message_id

    async def admin_back_to_variant_types(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        if user_id not in self.user_states: return await query.answer(self.get_text('session_expired'))

        state = self.user_states[user_id]
        state['step'], variants, added_info = 'add_product_variants_loop', state.get('product_data', {}).get('variants',
                                                                                                             {}), ""

        if variants:
            v_type_raw = list(variants.keys())[0]
            v_type_localized = self.get_text(f'type_{v_type_raw}')
            if v_type_localized == f"_type_{v_type_raw}_": v_type_localized = v_type_raw
            added_info = f"{self.get_text('active_variant_label')}{v_type_localized} (<i>{', '.join(variants[v_type_raw].keys())}</i>)\n────────────────────"

        try:
            await query.edit_message_text(self.get_text('admin_wizard_variant_title', added_info=added_info),
                                          reply_markup=self.get_variant_type_keyboard(), parse_mode="HTML")
        except:
            await query.answer()

    def get_existing_categories_keyboard(self, product_id=None):
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT category FROM products")
        categories = [row[0] for row in cursor.fetchall() if row[0]]

        keyboard = [[InlineKeyboardButton(cat, callback_data=f"admin_set_cat_{cat}") for cat in categories[i:i + 2]] for
                    i in range(0, len(categories), 2)]
        keyboard.append([InlineKeyboardButton(self.get_text('cancel_button'),
                                              callback_data=f"admin_prod_{product_id}" if product_id else "admin_wizard_cancel")])
        return InlineKeyboardMarkup(keyboard)

    async def admin_handle_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        if int(user_id) not in ADMIN_IDS or user_id not in self.user_states: return await query.answer(
            self.get_text('session_expired'))

        category, state, chat_id = query.data.replace("admin_set_cat_", ""), self.user_states[
            user_id], query.message.chat_id
        await query.answer(self.get_text('admin_category_selected', category=category))

        if state.get('field') == 'category':
            cursor = self.conn.cursor()
            cursor.execute("UPDATE products SET category = ? WHERE id = ?", (category, state.get('product_id')))
            self.conn.commit()
            self.user_states.pop(user_id, None)

            try:
                await query.message.delete()
            except:
                pass

            text = self.get_text('admin_category_updated_success', category=category)
            if text == f"_admin_category_updated_success_": text = self.get_text('admin_category_updated',
                                                                                 category=category)
            return await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.get_text('back_button_3'),
                                       callback_data=f"admin_prod_{state.get('product_id')}")]]), parse_mode="HTML")

        try:
            await query.message.delete()
        except:
            pass

        if state.get('step') in ['waiting_simple_category', 'waiting_var_category']:
            state['product_data']['category'] = category
            state['step'] = 'waiting_simple_emoji' if state.get(
                'step') == 'waiting_simple_category' else 'waiting_var_emoji'
            m = await context.bot.send_message(chat_id=chat_id, text=self.get_text('enter_emoji'),
                                               reply_markup=InlineKeyboardMarkup(
                                                   [[InlineKeyboardButton(self.get_text('cancel_button'),
                                                                          callback_data="admin_wizard_cancel")]]),
                                               parse_mode="HTML")
            state['msg_id'] = m.message_id

    async def process_variant_values_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        state, text, chat_id = self.user_states[user_id], update.message.text.strip(), update.effective_chat.id
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

            state['product_data']['variants'] = {v_type: opts_map}
            await self.show_variant_type_selection(context, chat_id, user_id, status_msg=f"Variant set to: {v_type}")

        except Exception:
            ex = {"Size": "S=10=1200", "Color": "Red=5=500", "Memory": "128GB=10=800"}.get(v_type, self.get_text(
                'variant_default_format'))
            text_err = f"{self.get_text('variant_error_title', v_type=v_type)}\n\n{self.get_text('variant_error_msg')}\n{self.get_text('variant_example_label')} <code>{ex}</code>"
            m = await context.bot.send_message(chat_id=chat_id, text=text_err, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.get_text('back_button'), callback_data="admin_step_variants_init")]]),
                                               parse_mode="HTML")
            state['msg_id'] = m.message_id

    # -------------------- MAIN ROUTING --------------------
    def main(self):
        if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            logger.critical("BOT_TOKEN not found. Please set it as an environment variable.")
            return

        application = Application.builder().token(BOT_TOKEN).build()

        application.add_handler(CommandHandler("start", self.start))

        application.add_handler(PreCheckoutQueryHandler(self.precheckout_callback))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, self.successful_payment_callback))

        application.add_handler(
            MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, self.master_message_handler))

        application.add_handler(CallbackQueryHandler(self.show_main_menu, pattern=r'^main_menu$'))
        application.add_handler(CallbackQueryHandler(self.show_help, pattern=r'^help$'))
        application.add_handler(CallbackQueryHandler(self.show_catalog, pattern=r'^catalog(_page_\d+)?$'))
        application.add_handler(CallbackQueryHandler(self.show_category_products, pattern=r'^category_'))
        application.add_handler(CallbackQueryHandler(self.show_product, pattern=r'^product_'))
        application.add_handler(CallbackQueryHandler(self.handle_product_action, pattern=r'^prod_(plus|minus)_'))

        application.add_handler(CallbackQueryHandler(self.show_profile, pattern=r'^my_profile$'))
        application.add_handler(CallbackQueryHandler(self.edit_phone, pattern=r'^edit_phone$'))
        application.add_handler(CallbackQueryHandler(self.edit_email, pattern=r'^edit_email$'))
        application.add_handler(CallbackQueryHandler(self.edit_address, pattern=r'^edit_address$'))
        application.add_handler(CallbackQueryHandler(self.profile_delete_menu, pattern=r'^profile_delete_menu$'))
        application.add_handler(CallbackQueryHandler(self.handle_delete_profile_data, pattern=r'^delete_profile_'))
        application.add_handler(CallbackQueryHandler(self.edit_full_name, pattern=r'^edit_full_name$'))

        application.add_handler(CallbackQueryHandler(self.show_cart, pattern=r'^(cart|my_cart)$'))
        application.add_handler(CallbackQueryHandler(self.clear_cart, pattern=r'^clear_cart$'))
        application.add_handler(CallbackQueryHandler(self.handle_cart_update, pattern=r'^cart_(plus|minus)_'))
        application.add_handler(CallbackQueryHandler(self.add_to_cart, pattern=r'^add_to_cart_'))

        application.add_handler(CallbackQueryHandler(self.ask_promo_code, pattern=r'^ask_promo_code$'))

        application.add_handler(CallbackQueryHandler(self.checkout, pattern=r'^checkout$'))
        application.add_handler(CallbackQueryHandler(self.use_profile_data, pattern=r'^use_profile_data$'))
        application.add_handler(
            CallbackQueryHandler(self.handle_checkout_confirm, pattern=r'^(confirm_details|confirm_details_back)$'))
        application.add_handler(CallbackQueryHandler(self.choose_payment, pattern=r'^pay_(cod|card|bank|online)$'))

        application.add_handler(CallbackQueryHandler(self.handle_cancel_order, pattern=r'^cancel_order$'))
        application.add_handler(CallbackQueryHandler(self.handle_checkout_back, pattern=r'^(back_to_|edit_)'))

        application.add_handler(CallbackQueryHandler(self.show_my_orders, pattern=r'^my_orders$'))
        application.add_handler(CallbackQueryHandler(self.handle_my_orders_pagination, pattern=r'^my_orders_page_\d+$'))
        application.add_handler(CallbackQueryHandler(self.show_order_details, pattern=r'^order_details_'))
        application.add_handler(CallbackQueryHandler(self.user_cancel_order, pattern=r'^user_cancel_'))

        application.add_handler(CallbackQueryHandler(self.admin_panel, pattern=r'^admin_panel$'))
        application.add_handler(CallbackQueryHandler(self.admin_statistics, pattern=r'^admin_statistics$'))
        application.add_handler(CallbackQueryHandler(self.admin_revenue, pattern=r'^admin_revenue_chart$'))
        application.add_handler(CallbackQueryHandler(self.handle_revenue_period, pattern=r'^rev_'))

        application.add_handler(CallbackQueryHandler(self.admin_broadcast_prompt, pattern=r'^admin_broadcast_prompt$'))
        application.add_handler(CallbackQueryHandler(self.admin_promo_menu, pattern=r'^admin_promo_menu$'))
        application.add_handler(CallbackQueryHandler(self.admin_promo_add_prompt, pattern=r'^admin_promo_add_prompt$'))
        application.add_handler(CallbackQueryHandler(self.admin_promo_del_prompt, pattern=r'^admin_promo_del_prompt$'))
        application.add_handler(CallbackQueryHandler(self.admin_delete_promo_action, pattern=r'^admin_pdel_\d+$'))

        application.add_handler(CallbackQueryHandler(self.admin_promo_set_reusable, pattern=r'^admin_promo_reusable_'))

        application.add_handler(CallbackQueryHandler(self.admin_user_management, pattern=r'^admin_user_management$'))
        application.add_handler(
            CallbackQueryHandler(self.handle_admin_user_pagination, pattern=r'^admin_user_page_\d+$'))
        application.add_handler(CallbackQueryHandler(self.admin_user_block, pattern=r'^admin_user_block_'))

        application.add_handler(CallbackQueryHandler(self.admin_all_orders, pattern=r'^admin_all_orders$'))
        application.add_handler(
            CallbackQueryHandler(self.handle_admin_all_orders_pagination, pattern=r'^admin_all_orders_page_\d+$'))
        application.add_handler(
            CallbackQueryHandler(self.admin_order_status_change, pattern=r'^admin_(confirm|ship|deliver|cancel)'))

        application.add_handler(
            CallbackQueryHandler(self.admin_categories_menu, pattern=r'^admin_products$|^admin_cat_page_'))
        application.add_handler(CallbackQueryHandler(self.admin_products_list, pattern=r'^admin_list_cat_'))
        application.add_handler(CallbackQueryHandler(self.admin_handle_category_selection, pattern=r'^admin_set_cat_'))
        application.add_handler(CallbackQueryHandler(self.admin_product_menu, pattern=r'^admin_prod_'))
        application.add_handler(CallbackQueryHandler(self.admin_add_product, pattern=r'^admin_add_product$'))
        application.add_handler(CallbackQueryHandler(self.admin_edit_field, pattern=r'^admin_edit_field_'))
        application.add_handler(CallbackQueryHandler(self.admin_delete_product, pattern=r'^admin_delete_product_\d+'))
        application.add_handler(
            CallbackQueryHandler(self.admin_delete_product_confirm, pattern=r'^admin_delete_product_confirm_'))

        application.add_handler(CallbackQueryHandler(self.admin_image_menu, pattern=r'^admin_image_menu_'))
        application.add_handler(CallbackQueryHandler(self.admin_image_set_prompt, pattern=r'^admin_image_set_'))
        application.add_handler(CallbackQueryHandler(self.admin_image_delete, pattern=r'^admin_image_delete_'))
        application.add_handler(
            CallbackQueryHandler(self.admin_handle_variant_decision, pattern=r'^admin_decision_vars_'))
        application.add_handler(CallbackQueryHandler(self.admin_wizard_cancel, pattern=r'^admin_wizard_cancel$'))
        application.add_handler(
            CallbackQueryHandler(self.admin_back_to_variant_types, pattern=r'^admin_step_variants_init$'))

        application.add_handler(CallbackQueryHandler(self.handle_variant_type_selection, pattern=r'^vartype_'))
        application.add_handler(
            CallbackQueryHandler(self.handle_variant_selection_user, pattern=r'^var_sel_|^cancel_selection$'))

        application.add_error_handler(self.error_handler)

        print(f"🛍️ Online store bot launched! License: {LICENSE_TYPE}")
        print(f"👑 Admin IDs: {ADMIN_IDS}")
        print("Press Ctrl+C to stop.")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


def main():
    bot = OnlineShopBot()
    bot.main()


if __name__ == '__main__':
    main()