import os
import re
import sqlite3
from datetime import datetime
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client

app = Flask(__name__)
DB_NAME = 'orders.db'
# environment variables (import os is expected at top of file)
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.environ.get('TWILIO_WHATSAPP_NUMBER')  # e.g. 'whatsapp:+1234567890'
TWILIO_DESIGNER_NUMBER = os.environ.get('TWILIO_DESIGNER_NUMBER')  # designer's whatsapp number, e.g. 'whatsapp:+1987654321'

# Twilio REST client (only created if creds are present)
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Product catalog: cleaned ASCII spacing and valid dict syntax
PRODUCT_CATALOG = {
    "1": {
        "name": "A0 Poster (84.1cm x 118.9cm)",
        "url": "https://wa.me/p/32444440828502644/254754597946",
        "price": 1500.00
    },
    "2": {
        "name": "A1 Poster (59.4cm x 84.4cm)",
        "url": "https://wa.me/p/25293719003556275/254754597946",
        "price": 1200.00
    },
    "3": {
        "name": "A2 Poster (42cm x 59.4cm)",
        "url": "https://wa.me/p/32223331983949052/254754597946",
        "price": 900.00
    },
    "4": {
        "name": "A3 Poster (29.7cm x 42cm)",
        "url": "https://wa.me/p/32327837460165064/254754597946",
        "price": 600.00
    },
    "5": {
        "name": "A4 Poster (21cm x 29.7cm)",
        "url": "https://wa.me/p/25442241375373277/254754597946",
        "price": 400.00
    }
}


def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    with conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_number TEXT NOT NULL,
                product_id TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mpesa_message TEXT NOT NULL,
                payer_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    conn.close()


# initialize DB on import/run
init_db()


def get_user_number(from_number: str) -> str:
    return from_number.replace("whatsapp:", "")


def parse_mpesa_name(mpesa_message: str) -> str:
    # Try a few heuristics to extract a payer name from the Mpesa message.
    # Common patterns: 'lipa na mpesa. Payment of Ksh... from John Doe on ... *334#'
    # We'll look for 'from <name>' first, otherwise fallback to a reasonable token.
    text = mpesa_message.strip()
    # Remove trailing USSD marker if present
    text = text.replace('*334#', '').strip()

    m = re.search(r'from\s+([A-Za-z ]+?)(?:\s+on\b|\s+for\b|\s+\d|$)', text, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        if name:
            return name.title()

    # Try another pattern: 'received from <name>'
    m = re.search(r'received\s+from\s+([A-Za-z ]+)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip().title()

    # Fallback to looking for capitalized words
    tokens = re.findall(r'[A-Z][a-z]+', text)
    if tokens:
        return " ".join(tokens[:2])

    return "Valued Customer"


def handoff_notification(user_number: str, message: str):
    # Notify the designer via Twilio (if configured) or print to console.
    notify_text = f"HANDOFF: user={user_number} - {message}"
    if twilio_client and TWILIO_DESIGNER_NUMBER and TWILIO_WHATSAPP_NUMBER:
        try:
            twilio_client.messages.create(
                body=notify_text,
                from_=TWILIO_WHATSAPP_NUMBER,
                to=TWILIO_DESIGNER_NUMBER
            )
        except Exception as e:
            # Fallback to console log
            print(f"Failed to send handoff via Twilio: {e}. {notify_text}")
    else:
        print(notify_text)


def print_receipt(customer_name: str, order_rows):
    # order_rows: iterable of sqlite row objects or dicts with product_id
    print("=== RECEIPT ===")
    print(f"Customer: {customer_name}")
    total = 0.0
    for row in order_rows:
        pid = row['product_id'] if isinstance(row, sqlite3.Row) else row.get('product_id')
        product = PRODUCT_CATALOG.get(pid)
        if product:
            price = product['price']
            name = product['name']
            total += price
            print(f"- {name}: {price:.2f}")
        else:
            print(f"- Unknown product ({pid})")
    print(f"Total: {total:.2f}")
    print("Thank you for your purchase!")
    return total


# Webhook for WhatsApp via Twilio
@app.route("/whatsapp", methods=['POST'])
def webhook():
    incoming_msg = request.values.get('Body', '').strip()
    from_number_raw = request.values.get('From', '')
    user_number = get_user_number(from_number_raw)

    resp = MessagingResponse()
    msg = resp.message()  # Use this only for simple text replies

    lower_msg = incoming_msg.lower()

    # Show menu: CLEAN menu display, no links
    if lower_msg == 'menu':
        menu_text = "👋 Welcome to Doga's Graphic Design services.\n"
        menu_text += "Reply with the number to order:\n\n"
        for pid, details in PRODUCT_CATALOG.items():
            # Only display name and price here for a clean menu
            menu_text += f"*{pid}. {details['name']}* - KES {details['price']:.2f}\n"
        menu_text += "\nExample: `order 1`"
        msg.body(menu_text)
        return str(resp)

    # Ordering flow
    if lower_msg.startswith('order'):
        tokens = incoming_msg.split()
        try:
            requested = tokens[1]
        except IndexError:
            msg.body("Please specify a product ID. Example: 'order 1'")
            return str(resp)

        # Find product by key
        product = PRODUCT_CATALOG.get(requested)
        if not product:
            msg.body("Invalid product ID. Send 'menu' to see available products.")
            return str(resp)

        # Insert order into DB
        conn = get_db_connection()
        with conn:
            conn.execute('INSERT INTO orders (user_number, product_id) VALUES (?, ?)',
                         (user_number, requested))
        conn.close()

        # Notify designer (handoff)
        handoff_notification(user_number, f"New order: {product['name']} - KES {product['price']:.2f}")

        # --- NEW INTERACTIVE BUTTON MESSAGE LOGIC ---
        if twilio_client and TWILIO_WHATSAPP_NUMBER:
            try:
                # 1. Prepare the Button Message payload
                confirmation_body = (
                    f"✅ Order confirmed for *{product['name']}* at KES {product['price']:.2f}.\n\n"
                    "Tap the button below to review sample designs in our catalog."
                )

                # 2. Send the interactive message using the REST Client
                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_NUMBER,
                    to=from_number_raw,
                    body=confirmation_body,
                    persistent_action=[
                        f'Visit Catalog|{product["url"]}'
                    ]
                )

                # 3. Return an empty TwiML response to Twilio to acknowledge the incoming message
                # The reply has been sent via the REST API above.
                return str(MessagingResponse())
            except Exception as e:
                # Fallback to plain text if the REST API call fails (e.g., due to an issue with Twilio setup)
                print(f"Failed to send button message: {e}")

        # Fallback to the original plain text response if Twilio client is not configured or the button send failed
        fallback_text = (
            f"✅ Order confirmed for *{product['name']}* at KES {product['price']:.2f}.\n\n"
            f"Before your designer contacts you, please review sample designs here:\n"
            f"{product['url']}\n\n"
            "A designer will be with you shortly to discuss details and the deposit."
        )
        msg.body(fallback_text)
        return str(resp)

    # Default reply for unhandled messages
    msg.body("Sorry, I didn't understand that. Send 'menu' to see options.")
    return str(resp)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


