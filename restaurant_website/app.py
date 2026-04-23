from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, flash, g, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "restaurant.db"

app = Flask(__name__)
app.config.update(
    SECRET_KEY="dev-secret-key-change-me",
    DATABASE=str(DATABASE),
)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(app.config["DATABASE"])
    with closing(db.cursor()) as cur:
        cur.executescript(
            """
            DROP TABLE IF EXISTS menu_items;
            DROP TABLE IF EXISTS reservations;
            DROP TABLE IF EXISTS waitlist;
            DROP TABLE IF EXISTS dining_tables;
            DROP TABLE IF EXISTS orders;

            CREATE TABLE menu_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price REAL NOT NULL,
                category TEXT NOT NULL,
                is_popular INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                party_size INTEGER NOT NULL,
                reservation_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Booked',
                table_id INTEGER,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (table_id) REFERENCES dining_tables(id)
            );

            CREATE TABLE waitlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                party_size INTEGER NOT NULL,
                check_in_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Waiting',
                quoted_wait_min INTEGER NOT NULL DEFAULT 15,
                table_id INTEGER,
                FOREIGN KEY (table_id) REFERENCES dining_tables(id)
            );

            CREATE TABLE dining_tables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                capacity INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'Available',
                occupied_until TEXT,
                current_party_name TEXT
            );

            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                email TEXT NOT NULL,
                items TEXT NOT NULL,
                total REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'Received'
            );
            """
        )

        menu_seed = [
            ("BBQ Chicken Pizza", "Fresh mozzarella, basil, tomato sauce, BBQ sauce, and Chicken", 13.99, "Pizza", 1),
            ("Pepperoni Pizza", "Classic pepperoni and mozzarella", 15.49, "Pizza", 1),
            ("French Fries", "Crispy fries with parmesan", 7.99, "Appetizers", 1),
            ("Caesar Salad", "Romaine, parmesan, croutons, Caesar dressing", 9.49, "Salads", 0),
            ("Grilled Salmon", "Salmon filet with lemon herb butter", 21.99, "Entrees", 1),
            ("Pasta Alfredo", "Creamy Alfredo sauce over fettuccine", 16.99, "Entrees", 0),
            ("Cheesecake", "House-made New York style cheesecake", 6.99, "Desserts", 0),
            ("Iced Tea", "Fresh brewed unsweetened iced tea", 2.99, "Drinks", 0),
        ]
        cur.executemany(
            "INSERT INTO menu_items (name, description, price, category, is_popular) VALUES (?, ?, ?, ?, ?)",
            menu_seed,
        )

        tables_seed = [
            ("T1", 2, "Available", None, None),
            ("T2", 2, "Available", None, None),
            ("T3", 4, "Available", None, None),
            ("T4", 4, "Available", None, None),
            ("T5", 6, "Available", None, None),
            ("T6", 8, "Available", None, None),
        ]
        cur.executemany(
            "INSERT INTO dining_tables (name, capacity, status, occupied_until, current_party_name) VALUES (?, ?, ?, ?, ?)",
            tables_seed,
        )

        now = datetime.now()
        reservation_seed = [
            (
                "Ava Thompson",
                "ava@example.com",
                "555-111-2222",
                2,
                (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                "Booked",
                None,
                "Window seat preferred",
                now.isoformat(timespec="minutes"),
            ),
            (
                "Marcus Reed",
                "marcus@example.com",
                "555-333-4444",
                4,
                (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "Booked",
                None,
                "Birthday dinner",
                now.isoformat(timespec="minutes"),
            ),
        ]
        cur.executemany(
            """
            INSERT INTO reservations
            (customer_name, email, phone, party_size, reservation_time, status, table_id, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            reservation_seed,
        )

    db.commit()
    db.close()


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return get_db().execute(query, params).fetchall()


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    return get_db().execute(query, params).fetchone()


def estimate_wait_time(party_size: int) -> int:
    db = get_db()
    available_now = db.execute(
        "SELECT COUNT(*) AS count FROM dining_tables WHERE status = 'Available' AND capacity >= ?",
        (party_size,),
    ).fetchone()["count"]
    if available_now > 0:
        return 0

    waiting_ahead = db.execute(
        "SELECT COUNT(*) AS count FROM waitlist WHERE status = 'Waiting' AND party_size <= ?",
        (party_size,),
    ).fetchone()["count"]

    next_table = db.execute(
        """
        SELECT occupied_until FROM dining_tables
        WHERE capacity >= ? AND occupied_until IS NOT NULL
        ORDER BY occupied_until ASC
        LIMIT 1
        """,
        (party_size,),
    ).fetchone()

    base_wait = 20
    if next_table:
        try:
            next_time = datetime.fromisoformat(next_table["occupied_until"])
            delta = max(0, int((next_time - datetime.now()).total_seconds() // 60))
            base_wait = max(base_wait, delta)
        except ValueError:
            pass

    return base_wait + (waiting_ahead * 12)


def assign_best_table(party_name: str, party_size: int) -> bool:
    db = get_db()
    table = db.execute(
        """
        SELECT id, name, capacity FROM dining_tables
        WHERE status = 'Available' AND capacity >= ?
        ORDER BY capacity ASC, id ASC
        LIMIT 1
        """,
        (party_size,),
    ).fetchone()
    if not table:
        return False

    occupied_until = (datetime.now() + timedelta(minutes=75)).isoformat(timespec="minutes")
    db.execute(
        """
        UPDATE dining_tables
        SET status = 'Occupied', occupied_until = ?, current_party_name = ?
        WHERE id = ?
        """,
        (occupied_until, party_name, table["id"]),
    )
    db.commit()
    return True


def release_table(table_id: int) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE dining_tables
        SET status = 'Available', occupied_until = NULL, current_party_name = NULL
        WHERE id = ?
        """,
        (table_id,),
    )
    db.commit()


def auto_seat_waitlist() -> int:
    db = get_db()
    seated = 0
    waiting_groups = db.execute(
        "SELECT * FROM waitlist WHERE status = 'Waiting' ORDER BY check_in_time ASC"
    ).fetchall()

    for group in waiting_groups:
        best_table = db.execute(
            """
            SELECT id, capacity FROM dining_tables
            WHERE status = 'Available' AND capacity >= ?
            ORDER BY capacity ASC, id ASC
            LIMIT 1
            """,
            (group["party_size"],),
        ).fetchone()
        if not best_table:
            continue

        occupied_until = (datetime.now() + timedelta(minutes=75)).isoformat(timespec="minutes")
        db.execute(
            "UPDATE dining_tables SET status = 'Occupied', occupied_until = ?, current_party_name = ? WHERE id = ?",
            (occupied_until, group["customer_name"], best_table["id"]),
        )
        db.execute(
            "UPDATE waitlist SET status = 'Seated', table_id = ? WHERE id = ?",
            (best_table["id"], group["id"]),
        )
        seated += 1

    db.commit()
    return seated


@app.route("/")
def home() -> str:
    popular_items = fetch_all(
        "SELECT * FROM menu_items WHERE is_popular = 1 ORDER BY category, name"
    )
    upcoming_reservations = fetch_all(
        """
        SELECT * FROM reservations
        WHERE reservation_time >= ?
        ORDER BY reservation_time ASC
        LIMIT 5
        """,
        (datetime.now().strftime("%Y-%m-%dT%H:%M"),),
    )
    current_wait = estimate_wait_time(2)
    return render_template(
        "home.html",
        popular_items=popular_items,
        upcoming_reservations=upcoming_reservations,
        current_wait=current_wait,
    )


@app.route("/menu")
def menu() -> str:
    menu_items = fetch_all("SELECT * FROM menu_items ORDER BY category, name")
    categories = sorted({item["category"] for item in menu_items})
    return render_template("menu.html", menu_items=menu_items, categories=categories)


@app.route("/reserve", methods=["GET", "POST"])
def reserve() -> str:
    if request.method == "POST":
        customer_name = request.form.get("customer_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        party_size = int(request.form.get("party_size", 1))
        reservation_time = request.form.get("reservation_time", "").strip()
        notes = request.form.get("notes", "").strip()

        if not customer_name or not email or not phone or not reservation_time:
            flash("Please complete all required fields.", "error")
            return redirect(url_for("reserve"))

        get_db().execute(
            """
            INSERT INTO reservations
            (customer_name, email, phone, party_size, reservation_time, status, table_id, notes, created_at)
            VALUES (?, ?, ?, ?, ?, 'Booked', NULL, ?, ?)
            """,
            (
                customer_name,
                email,
                phone,
                party_size,
                reservation_time,
                notes,
                datetime.now().isoformat(timespec="minutes"),
            ),
        )
        get_db().commit()
        flash("Reservation created successfully.", "success")
        return redirect(url_for("reserve"))

    return render_template("reserve.html")


@app.route("/waitlist", methods=["GET", "POST"])
def waitlist() -> str:
    if request.method == "POST":
        customer_name = request.form.get("customer_name", "").strip()
        phone = request.form.get("phone", "").strip()
        party_size = int(request.form.get("party_size", 1))

        if not customer_name or not phone:
            flash("Name and phone are required.", "error")
            return redirect(url_for("waitlist"))

        quoted_wait = estimate_wait_time(party_size)
        get_db().execute(
            """
            INSERT INTO waitlist (customer_name, phone, party_size, check_in_time, status, quoted_wait_min)
            VALUES (?, ?, ?, ?, 'Waiting', ?)
            """,
            (
                customer_name,
                phone,
                party_size,
                datetime.now().isoformat(timespec="minutes"),
                quoted_wait,
            ),
        )
        get_db().commit()
        flash(f"Added to the waitlist. Estimated wait: {quoted_wait} minutes.", "success")
        return redirect(url_for("waitlist"))

    waiting_groups = fetch_all("SELECT * FROM waitlist ORDER BY check_in_time ASC")
    return render_template("waitlist.html", waiting_groups=waiting_groups)


@app.route("/admin")
def admin() -> str:
    reservations = fetch_all(
        "SELECT * FROM reservations ORDER BY reservation_time ASC, id ASC"
    )
    waitlist_groups = fetch_all(
        "SELECT * FROM waitlist ORDER BY check_in_time ASC, id ASC"
    )
    tables = fetch_all("SELECT * FROM dining_tables ORDER BY capacity ASC, id ASC")
    return render_template(
        "admin.html",
        reservations=reservations,
        waitlist_groups=waitlist_groups,
        tables=tables,
    )


@app.post("/admin/seat_reservation/<int:reservation_id>")
def seat_reservation(reservation_id: int):
    reservation = fetch_one("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
    if reservation is None:
        flash("Reservation not found.", "error")
        return redirect(url_for("admin"))

    assigned = assign_best_table(reservation["customer_name"], reservation["party_size"])
    if not assigned:
        flash("No matching table is available right now.", "error")
        return redirect(url_for("admin"))

    table = fetch_one(
        "SELECT id FROM dining_tables WHERE current_party_name = ? ORDER BY id DESC LIMIT 1",
        (reservation["customer_name"],),
    )
    get_db().execute(
        "UPDATE reservations SET status = 'Seated', table_id = ? WHERE id = ?",
        (table["id"] if table else None, reservation_id),
    )
    get_db().commit()
    flash("Reservation party seated.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/mark_done/reservation/<int:reservation_id>")
def complete_reservation(reservation_id: int):
    reservation = fetch_one("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
    if reservation and reservation["table_id"]:
        release_table(reservation["table_id"])
    get_db().execute("UPDATE reservations SET status = 'Completed' WHERE id = ?", (reservation_id,))
    get_db().commit()
    auto_seat_waitlist()
    flash("Reservation marked completed and table released.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/mark_done/waitlist/<int:waitlist_id>")
def complete_waitlist(waitlist_id: int):
    group = fetch_one("SELECT * FROM waitlist WHERE id = ?", (waitlist_id,))
    if group and group["table_id"]:
        release_table(group["table_id"])
    get_db().execute("UPDATE waitlist SET status = 'Completed' WHERE id = ?", (waitlist_id,))
    get_db().commit()
    auto_seat_waitlist()
    flash("Waitlist group marked completed and table released.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/auto-seat")
def auto_seat():
    seated_count = auto_seat_waitlist()
    flash(f"Auto-seat processed. Groups seated: {seated_count}.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/reset-demo")
def reset_demo():
    init_db()
    flash("Demo database reset.", "success")
    return redirect(url_for("admin"))


@app.route("/about")
def about() -> str:
    return render_template("about.html")


if __name__ == "__main__":
    if not DATABASE.exists():
        init_db()
    app.run(debug=True)
