@app.route("/whatsapp", methods=['POST'])
def webhook():
    incoming_msg = request.values.get('Body', '').strip()
    from_number_raw = request.values.get('From', '')
    user_number = get_user_number(from_number_raw)

    resp = MessagingResponse()
    msg = resp.message()

    lower_msg = incoming_msg.lower()

    # Show menu
    if lower_msg == 'menu':
        menu_text = "👋 Welcome to Doga's Graphic Design services.\n"
        menu_text += "Reply with the number to order:\n\n"
        for pid, details in PRODUCT_CATALOG.items():
            menu_text += f"*{pid}. {details['name']}* - KES {details['price']:.2f}\n"
        menu_text += "\nExample: 1"
        msg.body(menu_text)
        return str(resp)

    # Ordering by just sending the product number (e.g., "1", "2", ...)
    if lower_msg in PRODUCT_CATALOG.keys():
        requested = lower_msg

        product = PRODUCT_CATALOG.get(requested)
        if not product:
            msg.body("Invalid product ID. Send 'menu' to see available products.")
            return str(resp)

        conn = get_db_connection()
        with conn:
            conn.execute('INSERT INTO orders (user_number, product_id) VALUES (?, ?)',
                         (user_number, requested))
        conn.close()

        handoff_notification(user_number, f"New order: {product['name']} - KES {product['price']:.2f}")

        if twilio_client and TWILIO_WHATSAPP_NUMBER:
            try:
                confirmation_body = (
                    f"✅ Order confirmed for *{product['name']}* at KES {product['price']:.2f}.\n\n"
                    "Tap the button below to review sample designs in our catalog."
                )

                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_NUMBER,
                    to=from_number_raw,
                    body=confirmation_body,
                    persistent_action=[
                        f'Visit Catalog|{product["url"]}'
                    ]
                )
                return str(MessagingResponse())
            except Exception as e:
                print(f"Failed to send button message: {e}")

        fallback_text = (
            f"✅ Order confirmed for *{product['name']}* at KES {product['price']:.2f}.\n\n"
            f"Before your designer contacts you, please review sample designs here:\n"
            f"{product['url']}\n\n"
            "A designer will be with you shortly to discuss details and the deposit."
        )
        msg.body(fallback_text)
        return str(resp)

    # Backwards-compatible "order <id>" flow
    if lower_msg.startswith('order'):
        tokens = incoming_msg.split()
        try:
            requested = tokens[1]
        except IndexError:
            msg.body("Please specify a product ID. Example: '1'")
            return str(resp)

        product = PRODUCT_CATALOG.get(requested)
        if not product:
            msg.body("Invalid product ID. Send 'menu' to see available products.")
            return str(resp)

        conn = get_db_connection()
        with conn:
            conn.execute('INSERT INTO orders (user_number, product_id) VALUES (?, ?)',
                         (user_number, requested))
        conn.close()

        handoff_notification(user_number, f"New order: {product['name']} - KES {product['price']:.2f}")

        if twilio_client and TWILIO_WHATSAPP_NUMBER:
            try:
                confirmation_body = (
                    f"✅ Order confirmed for *{product['name']}* at KES {product['price']:.2f}.\n\n"
                    "Tap the button below to review sample designs in our catalog."
                )

                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_NUMBER,
                    to=from_number_raw,
                    body=confirmation_body,
                    persistent_action=[
                        f'Visit Catalog|{product["url"]}'
                    ]
                )
                return str(MessagingResponse())
            except Exception as e:
                print(f"Failed to send button message: {e}")

        fallback_text = (
            f"✅ Order confirmed for *{product['name']}* at KES {product['price']:.2f}.\n\n"
            f"Before your designer contacts you, please review sample designs here:\n"
            f"{product['url']}\n\n"
            "A designer will be with you shortly to discuss details and the deposit."
        )
        msg.body(fallback_text)
        return str(resp)

    msg.body("Sorry, I didn't understand that. Send 'menu' to see options.")
    return str(resp)
