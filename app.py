import os
import re
import sqlite3
from datetime import datetime
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client

app = Flask(__name__)
DB_NAME = 'orders.db'

TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.environ.get('TWILIO_WHATSAPP_NUMBER')  # e.g. 'whatsapp:+1234567890'
TWILIO_DESIGNER_NUMBER = os.environ.get('TWILIO_DESIGNER_NUMBER')  # designer's whatsapp number, e.g. 'whatsapp:+1987654321'

# Twilio REST client (only created if creds are present)
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Product catalog: includes demo prices, a catalog_item_id that corresponds to the
# WhatsApp Business Catalog item id (you keep the catalog in WhatsApp; no public URLs here).
# Users are instructed to view the designer's WhatsApp Catalog to see images/previews.
PRODUCT_CATALOG = {
    "1": {
        "name": "A0 Poster (84.1cm x 118.9cm)"
        "https://wa.me/p/32444440828502644/254754597946",
        "price": 1500.00
       
    },
    "2": {
        "name": "https://wa.me/p/25293719003556275/254754597946",
        "price": 1200.00
        
    },
    "3": {
        "name": "https://wa.me/p/32223331983949052/254754597946",
        "price": 900.00
       
    },
    "4": {
        "name": "https://wa.me/p/32327837460165064/254754597946",
        "price": 600.00
        
    },
    "5": {
        "name": "https://wa.me/p/25442241375373277/254754597946",
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


@app.route("/whatsapp", methods=['POST'])
def webhook():
    incoming_msg = request.values.get('Body', '').strip()
    from_number_raw = request.values.get('From', '')
    user_number = get_user_number(from_number_raw)

    resp = MessagingResponse()
    msg = resp.message()

    lower_msg = incoming_msg.lower()

    # Show menu: list products and instruct user how to order via the WhatsApp Catalog item id.
    if lower_msg == 'menu':
        menu_text = "Welcome to Doga's Graphic Design services.\n"
        menu_text += "Reply with number to order\n\n"
        for pid, details in PRODUCT_CATALOG.items():
            menu_text += f"{pid}. {details['name']} - KES {details['price']:.2f}\n"
        menu_text += "\nExample: order 1"
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

        # Find product by key or by catalog_item_id
        product = PRODUCT_CATALOG.get(requested)
        if not product:
            # search by catalog_item_id
            for pid, v in PRODUCT_CATALOG.items():
                if v.get('name') == requested:
                    product = v
                    requested = pid  # normalize to product id
                    break

        if not product:
            msg.body("Invalid product ID. Send 'menu' to see available products and catalog ids.")
            return str(resp)

        # Insert order into DB
        conn = get_db_connection()
        with conn:
            conn.execute('INSERT INTO orders (user_number, product_id) VALUES (?, ?)',
                         (user_number, requested))
        conn.close()

        # Notify designer (handoff) including demo price so designer knows price before engaging
        handoff_notification(user_number, f"New order: {product['name']} - KES {product['price']:.2f}")

        msg.body(f"Order confirmed for {product['name']} at KES {product['price']:.2f}. "
                 "A designer will be with you shortly to discuss details and the deposit.")
        return str(resp)

    # Help / support
    if lower_msg == 'support':
        handoff_notification(user_number, "User requested support.")
        msg.body("You will be connected to a support agent shortly.")
        return str(resp)

    # Designer-only: show all orders recorded (brief)
    if user_number == (TWILIO_DESIGNER_NUMBER.replace("whatsapp:", "") if TWILIO_DESIGNER_NUMBER else None) and lower_msg == 'orders':
        conn = get_db_connection()
        rows = conn.execute('SELECT * FROM orders ORDER BY timestamp DESC LIMIT 10').fetchall()
        conn.close()
        if not rows:
            msg.body("No orders recorded.")
        else:
            text = "Recent orders:\n"
            for r in rows:
                prod = PRODUCT_CATALOG.get(r['product_id'], {'name': r['product_id'], 'price': 0})
                text += f"- {r['id']}: {prod['name']} for {r['user_number']} at KES {prod['price']:.2f} ({r['timestamp']})\n"
            msg.body(text)
        return str(resp)

    # When the designer (or anyone) pastes the Mpesa message (ends with *334#), capture payment info.
    if incoming_msg.strip().endswith('*334#'):
        payer_name = parse_mpesa_name(incoming_msg)
        conn = get_db_connection()
        with conn:
            conn.execute('INSERT INTO payments (mpesa_message, payer_name) VALUES (?, ?)',
                         (incoming_msg, payer_name))
        conn.close()
        msg.body(f"Payment message received and recorded for '{payer_name}'. Designer can now issue receipt by sending 'receipt'.")
        return str(resp)

    # Designer requests printing of receipt: only allow from designer number
    if lower_msg == 'receipt':
        # Ensure requester is the designer
        if TWILIO_DESIGNER_NUMBER and user_number != TWILIO_DESIGNER_NUMBER.replace("whatsapp:", ""):
            msg.body("Only the designer can request printing of receipts.")
            return str(resp)

        conn = get_db_connection()
        # Get last payment and last order (simple demo behavior)
        last_payment = conn.execute('SELECT * FROM payments ORDER BY timestamp DESC LIMIT 1').fetchone()
        last_order = conn.execute('SELECT * FROM orders ORDER BY timestamp DESC LIMIT 1').fetchone()
        conn.close()

        if not last_payment:
            msg.body("No payment records found. Paste the Mpesa message (ending with *334#) first.")
            return str(resp)
        if not last_order:
            msg.body("No orders found in the system.")
            return str(resp)

        customer_name = last_payment['payer_name'] or parse_mpesa_name(last_payment['mpesa_message'])
        # For demo: print receipt for the single latest order (designer can adapt later)
        print_receipt(customer_name, [last_order])
        msg.body(f"Receipt printed to server console for {customer_name}.")
        return str(resp)

    # Numeric quick-help to point user to catalog item id (no public media URLs used)
    if incoming_msg in PRODUCT_CATALOG:
        p = PRODUCT_CATALOG[incoming_msg]
        reply = (f"To view images and full details, open our WhatsApp profile -> Catalog.\n"
                 f"Look for: {p['name']}\nCatalog ID: {p['name']}\n"
                 f"If this is the one you want, reply: order {incoming_msg}")
        msg.body(reply)
        return str(resp)

    # Record a sample order for testing (keeps backward compatibility with 'record')
    if lower_msg == 'record':
        conn = get_db_connection()
        with conn:
            conn.execute('INSERT INTO orders (user_number, product_id) VALUES (?, ?)',
                         (user_number, '1'))
        conn.close()
        msg.body("Sample order recorded in orders database.")
        return str(resp)

    # Default response
    msg.body("Welcome to our store! Send 'menu' to see products (catalog IDs included), 'support' for help, "
             "or paste an Mpesa message (ending with *334#) after payment. Designer: send 'receipt' to print.")
    return str(resp)


if __name__ == "__main__":
    init_db() 
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))



