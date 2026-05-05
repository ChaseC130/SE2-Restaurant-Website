from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional

from flask import (
    Flask, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "restaurant.db"
OUTBOX = BASE_DIR / "instance" / "email_outbox.log"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me-for-class-demo")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    DATABASE=str(DATABASE),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# -------------------------
# Database helpers
# -------------------------

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        DATABASE.parent.mkdir(exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_one(sql: str, args: tuple = ()) -> Optional[sqlite3.Row]:
    return get_db().execute(sql, args).fetchone()


def query_all(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return get_db().execute(sql, args).fetchall()


def init_db(reset: bool = False):
    if reset and DATABASE.exists():
        DATABASE.unlink()
    db = get_db()
    schema = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'customer',
        email_confirmed INTEGER NOT NULL DEFAULT 0,
        confirmation_token TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS menu_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        price REAL NOT NULL CHECK(price >= 0),
        category TEXT NOT NULL,
        is_popular INTEGER NOT NULL DEFAULT 0,
        is_available INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS dining_tables (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT NOT NULL UNIQUE,
        capacity INTEGER NOT NULL CHECK(capacity > 0),
        status TEXT NOT NULL DEFAULT 'available',
        current_party_name TEXT,
        seated_until TEXT
    );

    CREATE TABLE IF NOT EXISTS reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        customer_name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT NOT NULL,
        party_size INTEGER NOT NULL CHECK(party_size > 0),
        reservation_time TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'booked',
        table_id INTEGER,
        confirmation_sent INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(table_id) REFERENCES dining_tables(id)
    );

    CREATE TABLE IF NOT EXISTS waitlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT NOT NULL,
        party_size INTEGER NOT NULL CHECK(party_size > 0),
        estimated_wait INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'waiting',
        table_id INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY(table_id) REFERENCES dining_tables(id)
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        customer_name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT NOT NULL,
        order_type TEXT NOT NULL DEFAULT 'pickup',
        status TEXT NOT NULL DEFAULT 'received',
        subtotal REAL NOT NULL,
        tax REAL NOT NULL,
        total REAL NOT NULL,
        confirmation_sent INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        menu_item_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        item_name TEXT NOT NULL,
        unit_price REAL NOT NULL,
        line_total REAL NOT NULL,
        FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
        FOREIGN KEY(menu_item_id) REFERENCES menu_items(id)
    );
    """
    db.executescript(schema)
    db.commit()
    seed_data()


def seed_data():
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")

    if not query_one("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)):
        db.execute(
            "INSERT INTO users (name, email, password_hash, role, email_confirmed, created_at) VALUES (?, ?, ?, 'admin', 1, ?)",
            ("Admin User", ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), now),
        )

    if query_one("SELECT COUNT(*) AS c FROM menu_items")["c"] == 0:
        items = [
            ("Classic Burger", "Beef patty, cheddar, lettuce, tomato, house sauce", 12.99, "Entrees", 1),
            ("Chicken Alfredo", "Grilled chicken, fettuccine, parmesan cream sauce", 15.99, "Entrees", 1),
            ("Margherita Pizza", "Fresh mozzarella, basil, tomato sauce", 13.99, "Entrees", 1),
            ("Caesar Salad", "Romaine, parmesan, croutons, Caesar dressing", 8.99, "Starters", 0),
            ("Loaded Fries", "Crispy fries, cheese, bacon, scallions", 7.99, "Starters", 1),
            ("Chocolate Cake", "Rich layered cake with chocolate ganache", 6.99, "Desserts", 0),
            ("Lemonade", "Fresh house-made lemonade", 3.49, "Drinks", 0),
        ]
        db.executemany(
            "INSERT INTO menu_items (name, description, price, category, is_popular, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(n, d, p, c, pop, now) for n, d, p, c, pop in items],
        )

    if query_one("SELECT COUNT(*) AS c FROM dining_tables")["c"] == 0:
        tables = [("T1", 2), ("T2", 2), ("T3", 4), ("T4", 4), ("T5", 6), ("T6", 8)]
        db.executemany("INSERT INTO dining_tables (label, capacity) VALUES (?, ?)", tables)
    db.commit()


# -------------------------
# Auth helpers
# -------------------------

def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "year": datetime.now().year}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in first.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# -------------------------
# Business logic
# -------------------------

def send_email(to: str, subject: str, body: str):
    """Local notification system: writes emails to instance/email_outbox.log."""
    OUTBOX.parent.mkdir(exist_ok=True)
    with OUTBOX.open("a", encoding="utf-8") as f:
        f.write("\n" + "=" * 70 + "\n")
        f.write(f"Time: {datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"To: {to}\nSubject: {subject}\n\n{body}\n")


def find_best_table(party_size: int) -> Optional[sqlite3.Row]:
    return query_one(
        """
        SELECT * FROM dining_tables
        WHERE status = 'available' AND capacity >= ?
        ORDER BY capacity ASC, label ASC
        LIMIT 1
        """,
        (party_size,),
    )


def estimate_wait_minutes(party_size: int) -> int:
    available = find_best_table(party_size)
    if available:
        return 0
    waiting_count = query_one("SELECT COUNT(*) AS c FROM waitlist WHERE status = 'waiting'")["c"]
    occupied_fitting = query_one(
        "SELECT COUNT(*) AS c FROM dining_tables WHERE status = 'occupied' AND capacity >= ?",
        (party_size,),
    )["c"]
    base = 15 if party_size <= 2 else 25 if party_size <= 4 else 35
    return base + (waiting_count * 8) + max(0, occupied_fitting - 1) * 5


def seat_party(table_id: int, party_name: str, minutes: int = 75):
    until = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="minutes")
    db = get_db()
    db.execute(
        "UPDATE dining_tables SET status='occupied', current_party_name=?, seated_until=? WHERE id=?",
        (party_name, until, table_id),
    )
    db.commit()


def auto_seat_waitlist():
    db = get_db()
    seated = []
    waiting = query_all("SELECT * FROM waitlist WHERE status='waiting' ORDER BY created_at ASC")
    for entry in waiting:
        table = find_best_table(entry["party_size"])
        if table:
            seat_party(table["id"], entry["customer_name"])
            db.execute(
                "UPDATE waitlist SET status='seated', table_id=? WHERE id=?",
                (table["id"], entry["id"]),
            )
            seated.append(entry)
    db.commit()
    return seated


def parse_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# -------------------------
# Routes
# -------------------------

@app.before_request
def ensure_db_ready():
    init_db(reset=False)


@app.route("/")
def home():
    stats = {
        "available_tables": query_one("SELECT COUNT(*) AS c FROM dining_tables WHERE status='available'")["c"],
        "active_waitlist": query_one("SELECT COUNT(*) AS c FROM waitlist WHERE status='waiting'")["c"],
        "menu_count": query_one("SELECT COUNT(*) AS c FROM menu_items WHERE is_available=1")["c"],
    }
    return render_template("home.html", stats=stats)


@app.route("/menu")
def menu():
    items = query_all("SELECT * FROM menu_items WHERE is_available=1 ORDER BY category, name")
    categories = sorted({item["category"] for item in items})
    return render_template("menu.html", items=items, categories=categories)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or len(password) < 6:
            flash("Name, email, and a password of at least 6 characters are required.", "danger")
            return render_template("register.html")
        if query_one("SELECT id FROM users WHERE email=?", (email,)):
            flash("An account with that email already exists.", "warning")
            return redirect(url_for("login"))
        token = secrets.token_urlsafe(24)
        db = get_db()
        db.execute(
            "INSERT INTO users (name, email, password_hash, role, email_confirmed, confirmation_token, created_at) VALUES (?, ?, ?, 'customer', 0, ?, ?)",
            (name, email, generate_password_hash(password), token, datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
        confirm_link = url_for("confirm_email", token=token, _external=True)
        send_email(email, "Confirm your restaurant account", f"Welcome, {name}!\n\nConfirm your account here:\n{confirm_link}")
        flash("Account created. A local confirmation email was written to instance/email_outbox.log.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/confirm/<token>")
def confirm_email(token):
    user = query_one("SELECT * FROM users WHERE confirmation_token=?", (token,))
    if not user:
        flash("Invalid or expired confirmation link.", "danger")
    else:
        db = get_db()
        db.execute("UPDATE users SET email_confirmed=1, confirmation_token=NULL WHERE id=?", (user["id"],))
        db.commit()
        flash("Email confirmed. You can now log in.", "success")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = query_one("SELECT * FROM users WHERE email=?", (email,))
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")
        session.clear()
        session["user_id"] = user["id"]
        flash(f"Welcome back, {user['name']}.", "success")
        return redirect(request.args.get("next") or url_for("home"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("home"))


@app.route("/reserve", methods=["GET", "POST"])
def reserve():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        party_size = parse_int(request.form.get("party_size"), 0)
        reservation_time = request.form.get("reservation_time", "")
        if not name or not email or not phone or party_size <= 0 or not reservation_time:
            flash("Please complete all reservation fields.", "danger")
            return render_template("reserve.html", user=user)
        table = find_best_table(party_size)
        db = get_db()
        cur = db.execute(
            """
            INSERT INTO reservations (user_id, customer_name, email, phone, party_size, reservation_time, status, table_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'booked', ?, ?)
            """,
            (user["id"] if user else None, name, email, phone, party_size, reservation_time, table["id"] if table else None, datetime.now().isoformat(timespec="seconds")),
        )
        reservation_id = cur.lastrowid
        db.commit()
        if table:
            # For demo: table assignment is reserved but not occupied until admin seats the party.
            table_text = f"Assigned table: {table['label']}"
        else:
            table_text = "No table assigned yet. Staff will confirm availability."
        send_email(email, "Reservation confirmation", f"Hi {name},\n\nYour reservation for {party_size} at {reservation_time} is confirmed.\n{table_text}\nReservation #{reservation_id}")
        db.execute("UPDATE reservations SET confirmation_sent=1 WHERE id=?", (reservation_id,))
        db.commit()
        flash("Reservation created. Confirmation email written to local outbox.", "success")
        return redirect(url_for("my_account") if user else url_for("home"))
    return render_template("reserve.html", user=user)


@app.route("/waitlist", methods=["GET", "POST"])
def waitlist():
    estimated = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        party_size = parse_int(request.form.get("party_size"), 0)
        if not name or not email or not phone or party_size <= 0:
            flash("Please complete all waitlist fields.", "danger")
            return render_template("waitlist.html", estimated=estimated)
        estimated = estimate_wait_minutes(party_size)
        db = get_db()
        db.execute(
            "INSERT INTO waitlist (customer_name, email, phone, party_size, estimated_wait, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, email, phone, party_size, estimated, datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
        send_email(email, "Waitlist confirmation", f"Hi {name},\n\nYou are on the waitlist. Estimated wait: {estimated} minutes.")
        flash(f"Added to waitlist. Estimated wait: {estimated} minutes.", "success")
    return render_template("waitlist.html", estimated=estimated)


@app.route("/order", methods=["GET", "POST"])
def order():
    user = current_user()
    items = query_all("SELECT * FROM menu_items WHERE is_available=1 ORDER BY category, name")
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        order_type = request.form.get("order_type", "pickup")
        selected = []
        for item in items:
            qty = parse_int(request.form.get(f"qty_{item['id']}"), 0)
            if qty > 0:
                selected.append((item, qty))
        if not name or not email or not phone or not selected:
            flash("Please enter contact info and choose at least one item.", "danger")
            return render_template("order.html", items=items, user=user)
        subtotal = round(sum(item["price"] * qty for item, qty in selected), 2)
        tax = round(subtotal * 0.06, 2)
        total = round(subtotal + tax, 2)
        db = get_db()
        cur = db.execute(
            "INSERT INTO orders (user_id, customer_name, email, phone, order_type, subtotal, tax, total, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"] if user else None, name, email, phone, order_type, subtotal, tax, total, datetime.now().isoformat(timespec="seconds")),
        )
        order_id = cur.lastrowid
        for item, qty in selected:
            db.execute(
                "INSERT INTO order_items (order_id, menu_item_id, quantity, item_name, unit_price, line_total) VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, item["id"], qty, item["name"], item["price"], round(item["price"] * qty, 2)),
            )
        db.commit()
        send_email(email, "Order confirmation", f"Hi {name},\n\nYour {order_type} order #{order_id} was received.\nTotal: ${total:.2f}\nEstimated pickup time: 20-30 minutes.")
        db.execute("UPDATE orders SET confirmation_sent=1 WHERE id=?", (order_id,))
        db.commit()
        flash(f"Order #{order_id} placed. Confirmation email written to local outbox.", "success")
        return redirect(url_for("my_account") if user else url_for("home"))
    return render_template("order.html", items=items, user=user)


@app.route("/account")
@login_required
def my_account():
    user = current_user()
    reservations = query_all("SELECT * FROM reservations WHERE user_id=? ORDER BY created_at DESC", (user["id"],))
    orders = query_all("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC", (user["id"],))
    return render_template("account.html", user=user, reservations=reservations, orders=orders)


@app.route("/admin")
@admin_required
def admin():
    tables = query_all("SELECT * FROM dining_tables ORDER BY capacity, label")
    reservations = query_all("SELECT r.*, t.label AS table_label FROM reservations r LEFT JOIN dining_tables t ON r.table_id=t.id ORDER BY r.created_at DESC")
    waiting = query_all("SELECT w.*, t.label AS table_label FROM waitlist w LEFT JOIN dining_tables t ON w.table_id=t.id ORDER BY w.created_at ASC")
    orders = query_all("SELECT * FROM orders ORDER BY created_at DESC LIMIT 25")
    return render_template("admin.html", tables=tables, reservations=reservations, waiting=waiting, orders=orders)


@app.route("/admin/tables/<int:table_id>/free", methods=["POST"])
@admin_required
def free_table(table_id):
    db = get_db()
    db.execute("UPDATE dining_tables SET status='available', current_party_name=NULL, seated_until=NULL WHERE id=?", (table_id,))
    db.commit()
    seated = auto_seat_waitlist()
    flash(f"Table freed. Auto-seated {len(seated)} waitlist group(s).", "success")
    return redirect(url_for("admin"))


@app.route("/admin/reservations/<int:reservation_id>/seat", methods=["POST"])
@admin_required
def seat_reservation(reservation_id):
    reservation = query_one("SELECT * FROM reservations WHERE id=?", (reservation_id,))
    if not reservation:
        flash("Reservation not found.", "danger")
        return redirect(url_for("admin"))
    table = find_best_table(reservation["party_size"])
    if not table:
        flash("No available table can fit this party yet.", "warning")
    else:
        seat_party(table["id"], reservation["customer_name"])
        db = get_db()
        db.execute("UPDATE reservations SET status='seated', table_id=? WHERE id=?", (table["id"], reservation_id))
        db.commit()
        flash(f"Seated at {table['label']}.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def update_order_status(order_id):
    status = request.form.get("status", "received")
    if status not in {"received", "preparing", "ready", "completed", "cancelled"}:
        status = "received"
    db = get_db()
    db.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    db.commit()
    flash("Order status updated.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/menu")
@admin_required
def admin_menu():
    items = query_all("SELECT * FROM menu_items ORDER BY category, name")
    return render_template("admin_menu.html", items=items)


@app.route("/admin/menu/new", methods=["GET", "POST"])
@admin_required
def admin_menu_new():
    if request.method == "POST":
        return save_menu_item()
    return render_template("menu_form.html", item=None)


@app.route("/admin/menu/<int:item_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_menu_edit(item_id):
    item = query_one("SELECT * FROM menu_items WHERE id=?", (item_id,))
    if not item:
        flash("Menu item not found.", "danger")
        return redirect(url_for("admin_menu"))
    if request.method == "POST":
        return save_menu_item(item_id)
    return render_template("menu_form.html", item=item)


def save_menu_item(item_id=None):
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip()
    try:
        price = round(float(request.form.get("price", "0")), 2)
    except ValueError:
        price = -1
    is_popular = 1 if request.form.get("is_popular") else 0
    is_available = 1 if request.form.get("is_available") else 0
    if not name or not description or not category or price < 0:
        flash("Name, description, category, and valid price are required.", "danger")
        item = query_one("SELECT * FROM menu_items WHERE id=?", (item_id,)) if item_id else None
        return render_template("menu_form.html", item=item)
    db = get_db()
    if item_id:
        db.execute(
            "UPDATE menu_items SET name=?, description=?, price=?, category=?, is_popular=?, is_available=? WHERE id=?",
            (name, description, price, category, is_popular, is_available, item_id),
        )
        flash("Menu item updated.", "success")
    else:
        db.execute(
            "INSERT INTO menu_items (name, description, price, category, is_popular, is_available, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, description, price, category, is_popular, is_available, datetime.now().isoformat(timespec="seconds")),
        )
        flash("Menu item created.", "success")
    db.commit()
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/<int:item_id>/delete", methods=["POST"])
@admin_required
def admin_menu_delete(item_id):
    db = get_db()
    db.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
    db.commit()
    flash("Menu item deleted.", "info")
    return redirect(url_for("admin_menu"))


@app.route("/about")
def about():
    return render_template("about.html")


@app.cli.command("init-db")
def init_db_command():
    init_db(reset=True)
    print("Database reset and seeded.")


if __name__ == "__main__":
    with app.app_context():
        init_db(reset=False)
    app.run(debug=True)
