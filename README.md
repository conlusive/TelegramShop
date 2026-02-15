# TelegramShop Bot

A fully functional e-commerce Telegram bot built with **Python**, **python-telegram-bot** (v22+), and **SQLite3**. This bot allows users to browse products, manage a shopping cart, and process orders through integrated payment systems.

## 🚀 Features

- **Catalog Management**: Dynamic product browsing with category support.
- **Shopping Cart**: Add/remove items and calculate total prices.
- **Order Processing**: Automated order creation and storage in SQLite.
- **Payment Integration**: Built-in support for Telegram Payments (Invoices and Shipping).
- **Database**: Persistent storage for products, users, and order history.
- **Localization**: Centralized string management for easy UI customization.

## 🛠 Tech Stack

- **Language**: Python 3.10+
- **Library**: [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) (Asynchronous)
- **Database**: SQLite3
- **Project Architecture**: Modular design (logic, constants, and credentials separation).

## 📁 Project Structure

```text
├── main.py          # Entry point, bot handlers, and core logic
├── dom.py           # Configuration (Tokens & API keys) - [Excluded from Git]
├── strings.py       # UI/UX text constants and messages
├── shop.db          # SQLite database file
├── requirements.txt # Project dependencies
└── .gitignore       # Rules to exclude sensitive files
```

## ⚙️ Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone [https://github.com/your-username/TelegramShop.git](https://github.com/your-username/TelegramShop.git)
   cd TelegramShop
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   # MacOS/Linux:
   source .venv/bin/activate
   # Windows:
   .venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configuration**:
   Create a `dom.py` file in the root directory and add your credentials:
   ```python
   BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
   PAYMENT_TOKENS = {
       'PROVIDER_NAME': 'YOUR_PAYMENT_PROVIDER_TOKEN'
   }
   ```

5. **Run the bot**:
   ```bash
   python main.py
   ```

## 📝 Usage

- `/start` - Initialize the bot and open the main menu.
- **Catalog** - Browse available items and categories.
- **Cart** - View selected items, adjust quantities, and proceed to checkout.
- **Support** - Direct contact for customer assistance.

## 🔒 Security Note

The file `dom.py` contains sensitive API tokens and is explicitly added to `.gitignore` to prevent accidental exposure. **Never share your BOT_TOKEN or database files containing user data publicly.**