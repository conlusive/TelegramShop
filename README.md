# Telegram Shop Bot

A simple Telegram bot for managing an online shop with SQLite database.
The bot supports both users and administrators:

## Features

### For Users
- Browse product catalog by categories
- View product details
- Add products to cart
- Place orders with phone number and delivery address
- Cancel order at any step

### For Admins
- Add, edit, and delete products
- Manage stock and product details (name, description, price, emoji, category)
- View all user orders
- Cancel actions at any stage

## Tech Stack
- Python 3
- Aiogram (Telegram bot framework)
- SQLite (database)
- PyCharm (development)

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/telegram-shop-bot.git
   cd telegram-shop-bot
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate   # On Windows use venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Add your Telegram bot token to the code:
   - Open `main.py` and set `API_TOKEN = "YOUR_BOT_TOKEN"`

4. Run the bot:
   ```bash
   python main.py
   ```

## Database
The bot uses `shop.db` SQLite database.
On first run, the database and tables will be automatically created.
