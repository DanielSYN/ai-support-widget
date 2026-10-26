"""Demo bot for a made-up outdoor shop, created on first start so there's something to try."""

import sqlite3

DEMO_BOT_ID = "demo-shop"

DEMO_FAQS = [
    ("How long does shipping take?",
     "Standard UK shipping takes 3-5 working days and is free on orders over £50 (otherwise £3.95). "
     "Express shipping arrives the next working day if you order before 2pm and costs £7.95."),
    ("Do you ship internationally?",
     "Yes, we ship to the EU, US and Canada. International delivery takes 7-14 working days and costs £14.95. "
     "Import duties are paid by the customer."),
    ("What is your returns policy?",
     "You can return unworn items within 30 days of delivery for a full refund. "
     "Returns are free in the UK using the prepaid label in your parcel. "
     "Refunds reach your original payment method within 5 working days of us receiving the return."),
    ("Can I exchange an item for a different size?",
     "Yes. Start a return for the item and place a new order for the size you want. "
     "This is the fastest way to get your new size."),
    ("How do your boots fit?",
     "Our hiking boots fit true to size. If you're between sizes or wear thick hiking socks, go half a size up."),
    ("How can I track my order?",
     "You'll get an email with a tracking link as soon as your order ships, usually within one working day."),
    ("Which payment methods do you accept?",
     "We accept Visa, Mastercard, American Express, PayPal, Apple Pay and Google Pay."),
    ("Do you sell gift cards?",
     "Yes, digital gift cards from £10 to £200. They're emailed instantly and are valid for 2 years."),
]

DEMO_EXTRA_INFO = """Customer support hours: Monday to Friday, 9am-5pm UK time. Messages sent outside these hours are answered the next working day.
Our shop in Keswick (12 Lake Road) is open every day from 10am to 6pm.
All waterproof jackets come with a 2-year warranty against manufacturing faults."""

DEMO_ALLOWED_ORIGINS = "http://localhost:8001\nhttp://127.0.0.1:8001"


def seed_demo_bot(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO bots (id, name, greeting, accent_color, allowed_origins, extra_info) VALUES (?, ?, ?, ?, ?, ?)",
        (DEMO_BOT_ID, "Trailhead Outfitters", "Hi! 👋 Questions about orders, delivery or returns? Ask away.",
         "#1f7a5a", DEMO_ALLOWED_ORIGINS, DEMO_EXTRA_INFO),
    )
    conn.executemany(
        "INSERT INTO faqs (bot_id, question, answer) VALUES (?, ?, ?)",
        [(DEMO_BOT_ID, q, a) for q, a in DEMO_FAQS],
    )


def seed_if_empty(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0] == 0:
        seed_demo_bot(conn)
