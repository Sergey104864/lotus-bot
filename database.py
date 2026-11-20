im"""База данных бота: мастера, услуги, записи."""
from __future__ import annotations
import os
import sqlite3
from datetime import date, datetime, timedelta
DB_PATH = os.getenv("DB_PATH", "booking.db")
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS masters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                spec TEXT,
                exp TEXT,
                about TEXT,
                work_start INTEGER DEFAULT 10,
                work_end INTEGER DEFAULT 20,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER DEFAULT 0,
                duration INTEGER DEFAULT 60,
                master_ids TEXT DEFAULT '',
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                client_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                service_id INTEGER,
                service_name TEXT NOT NULL,
                price INTEGER DEFAULT 0,
                master_id INTEGER,
                master_name TEXT,
                day TEXT NOT NULL,
                time TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                reminded INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bookings_day ON bookings (day, master_id, time);
            """
        )
        conn.commit()
    _seed()
def _seed() -> None:
    """Стартовые мастера и услуги, если база пустая."""
    with connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM masters").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO masters (name, spec, exp, about) VALUES (?, ?, ?, ?)",
                [
                    ("Артём", "Стрижки, fade", "6 лет", "Специализация — классические стрижки и fade. Работает быстро, любит чёткие формы."),
                    ("Кирилл", "Борода, бритьё", "4 года", "Королевское бритьё и оформление бороды. Подскажет уход."),
                    ("Алина", "Стрижки, укладка", "8 лет", "Женские и мужские стрижки, укладка. Поможет подобрать форму под лицо."),
                ],
            )
        if conn.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO services (name, price, duration) VALUES (?, ?, ?)",
                [
                    ("Стрижка", 1500, 60),
                    ("Борода", 900, 30),
                    ("Комплекс (стрижка + борода)", 2200, 90),
                ],
            )
        conn.commit()
# ---------------- мастера и услуги ----------------
def masters(active_only: bool = True) -> list[sqlite3.Row]:
    with connect() as conn:
        sql = "SELECT * FROM masters"
        if active_only:
            sql += " WHERE active = 1"
        return conn.execute(sql + " ORDER BY id").fetchall()
def master(master_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM masters WHERE id = ?", (master_id,)).fetchone()
def services(active_only: bool = True) -> list[sqlite3.Row]:
    with connect() as conn:
        sql = "SELECT * FROM services"
        if active_only:
            sql += " WHERE active = 1"
        return conn.execute(sql + " ORDER BY id").fetchall()
def service(service_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
def masters_for_service(service_id: int) -> list[sqlite3.Row]:
    """Мастера, которые делают услугу. Пустой список в услуге = все мастера."""
    svc = service(service_id)
    if svc is None:
        return []
    ids = [chunk.strip() for chunk in (svc["master_ids"] or "").split(",") if chunk.strip()]
    if not ids:
        return masters()
    placeholders = ",".join("?" for _ in ids)
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM masters WHERE id IN ({placeholders}) AND active = 1 ORDER BY id",
            [int(i) for i in ids],
        ).fetchall()
# ---------------- записи ----------------
def add_booking(
    user_id: int,
    username: str,
    client_name: str,
    phone: str,
    service_id: int,
    service_name: str,
    price: int,
    master_id: int,
    master_name: str,
    day: str,
    time: str,
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO bookings (user_id, username, client_name, phone, service_id, "
            "service_name, price, master_id, master_name, day, time, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                username,
                client_name,
                phone,
                service_id,
                service_name,
                price,
                master_id,
                master_name,
                day,
                time,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
def booked_slots(day: str, master_id: int) -> set[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT time FROM bookings WHERE day = ? AND master_id = ? AND status = 'active'",
            (day, master_id),
        ).fetchall()
    return {row["time"] for row in rows}
def is_free(day: str, time: str, master_id: int) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM bookings WHERE day = ? AND time = ? AND master_id = ? "
            "AND status = 'active' LIMIT 1",
            (day, time, master_id),
        ).fetchone()
    return row is None
def user_bookings(user_id: int) -> list[sqlite3.Row]:
    today = date.today().isoformat()
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM bookings WHERE user_id = ? AND status = 'active' AND day >= ? "
            "ORDER BY day, time",
            (user_id, today),
        ).fetchall()
def booking(booking_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
def cancel(booking_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE bookings SET status = 'cancelled' WHERE id = ? AND status = 'active'",
            (booking_id,),
        )
        conn.commit()
        return cur.rowcount > 0
def day_bookings(day: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM bookings WHERE day = ? AND status = 'active' ORDER BY time", (day,)
        ).fetchall()
def due_reminders(hours: int) -> list[sqlite3.Row]:
    now = datetime.now()
    limit = now + timedelta(hours=hours)
    result = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE status = 'active' AND reminded = 0 AND day >= ?",
            (date.today().isoformat(),),
        ).fetchall()
    for row in rows:
        try:
            moment = datetime.strptime(f"{row['day']} {row['time']}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if now < moment <= limit:
            result.append(row)
    return result
def mark_reminded(booking_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE bookings SET reminded = 1 WHERE id = ?", (booking_id,))
        conn.commit()