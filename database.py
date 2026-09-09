#!/usr/bin/env python3
"""Market2Farm™ Database CLI Tool"""
import sqlite3, os, csv, json, argparse
from datetime import datetime
from werkzeug.security import generate_password_hash
# near top of app.py
import os
if not os.getenv('DATABASE_URL'):
    os.environ['DATABASE_URL'] = 'sqlite:///market2farm.db'

DB_PATH = os.getenv('DATABASE_URL', 'sqlite:///market2farm.db').replace('sqlite:///', '')

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.executescript('''
    CREATE TABLE IF NOT EXISTS user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        password_hash TEXT,
        role TEXT DEFAULT 'buyer',
        verified BOOLEAN DEFAULT 0,
        email_verified BOOLEAN DEFAULT 0,
        two_factor_secret TEXT,
        two_factor_enabled BOOLEAN DEFAULT 0,
        suspended BOOLEAN DEFAULT 0,
        subscription_active BOOLEAN DEFAULT 0,
        subscription_plan TEXT,
        subscription_expiry TIMESTAMP,
        bio TEXT,
        location TEXT,
        avatar TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login_ip TEXT
    );
    CREATE TABLE IF NOT EXISTS subscription_plan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        price_monthly REAL,
        price_yearly REAL,
        is_active BOOLEAN DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS product (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        price REAL NOT NULL,
        category TEXT,
        organic BOOLEAN DEFAULT 0,
        image TEXT,
        farmer_id INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (farmer_id) REFERENCES user (id)
    );
    CREATE TABLE IF NOT EXISTS "order" (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        buyer_id INTEGER,
        product_id INTEGER,
        quantity INTEGER DEFAULT 1,
        total_price REAL,
        status TEXT DEFAULT 'pending',
        payment_method TEXT,
        transaction_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (buyer_id) REFERENCES user (id),
        FOREIGN KEY (product_id) REFERENCES product (id)
    );
    CREATE TABLE IF NOT EXISTS payment_transaction (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        payment_method TEXT,
        amount REAL,
        transaction_code TEXT,
        status TEXT DEFAULT 'pending',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES "order" (id)
    );
    CREATE TABLE IF NOT EXISTS executive_update (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        attachment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        published_by INTEGER,
        FOREIGN KEY (published_by) REFERENCES user (id)
    );
    CREATE TABLE IF NOT EXISTS notification (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        read BOOLEAN DEFAULT 0,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES user (id)
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        ip_address TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS chatbot_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_message TEXT,
        bot_reply TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS disease_detection_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT,
        result TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS captcha_model (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        captcha_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS review (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        rating INTEGER NOT NULL,
        comment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES product (id),
        FOREIGN KEY (user_id) REFERENCES user (id)
    );
    CREATE TABLE IF NOT EXISTS product_view (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES user (id),
        FOREIGN KEY (product_id) REFERENCES product (id)
    );
    ''')
    conn.commit()
    conn.close()
    print("[OK] Database tables created.")

def seed():
    conn = get_connection()
    cursor = conn.cursor()
    # Users
    try:
        cursor.execute("INSERT INTO user (username, email, password_hash, role) VALUES (?,?,?,?)",
                       ('chief_admin', 'chief@market2farm.com', generate_password_hash('Admin@2025'), 'chief_admin'))
    except sqlite3.IntegrityError: pass
    try:
        cursor.execute("INSERT INTO user (username, email, password_hash, role) VALUES (?,?,?,?)",
                       ('demo_farmer', 'farmer@market2farm.com', generate_password_hash('Farmer@2025'), 'farmer'))
    except sqlite3.IntegrityError: pass
    try:
        cursor.execute("INSERT INTO user (username, email, password_hash, role) VALUES (?,?,?,?)",
                       ('demo_buyer', 'buyer@market2farm.com', generate_password_hash('Buyer@2025'), 'buyer'))
    except sqlite3.IntegrityError: pass

    # Subscription plan
    cursor.execute("INSERT OR IGNORE INTO subscription_plan (name, price_monthly, price_yearly, is_active) VALUES (?,?,?,?)",
                   ('Premium', 500.0, 5000.0, 0))

    # Sample products
    sample_products = [
        ('Dragon Fruit', 'Sweet and exotic dragon fruit', 300.0, 'fruits', 1, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=300', 'approved'),
        ('Sour Sop', 'Creamy tropical sour sop', 250.0, 'fruits', 1, 'https://images.unsplash.com/photo-1603833665858-e61d17a86224?w=300', 'approved'),
        ('Hass Avocado', 'Creamy Hass avocado', 150.0, 'fruits', 1, 'https://images.unsplash.com/photo-1523049673857-eb18f1d7b578?w=300', 'approved'),
        ('Red Onions', 'Fresh red onions', 80.0, 'vegetables', 0, 'https://images.unsplash.com/photo-1587049352847-4a222e784d38?w=300', 'approved'),
        ('Tomatoes', 'Ripe red tomatoes', 70.0, 'vegetables', 0, 'https://images.unsplash.com/photo-1592924357228-91a4daadcfea?w=300', 'approved'),
        ('Maize Flour', 'Stone-ground maize flour', 200.0, 'grains', 0, 'https://images.unsplash.com/photo-1586201375761-83865001e8ac?w=300', 'approved'),
        ('Organic Honey', 'Pure raw honey', 500.0, 'livestock', 1, 'https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=300', 'approved'),
    ]
    for name, desc, price, cat, organic, img, status in sample_products:
        cursor.execute(
            "INSERT OR IGNORE INTO product (name, description, price, category, organic, image, farmer_id, status) VALUES (?,?,?,?,?,?,?,?)",
            (name, desc, price, cat, organic, img, 2, status)
        )
    conn.commit()
    conn.close()
    print("[OK] Demo data seeded.")

def summary():
    conn = get_connection()
    cursor = conn.cursor()
    tables = ['user', 'product', '"order"', 'payment_transaction', 'notification', 'audit_log', 'chatbot_log', 'disease_detection_log', 'review', 'product_view']
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"{table}: {count} rows")
    conn.close()

def backup():
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs('backups', exist_ok=True)
    backup_file = f"backups/market2farm_backup_{ts}.db"
    with open(DB_PATH, 'rb') as src, open(backup_file, 'wb') as dst:
        dst.write(src.read())
    print(f"[OK] Backup saved to {backup_file}")

def restore(backup_file):
    if not os.path.exists(backup_file):
        print(f"[ERROR] File not found: {backup_file}")
        return
    with open(backup_file, 'rb') as src, open(DB_PATH, 'wb') as dst:
        dst.write(src.read())
    print("[OK] Database restored.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Market2Farm Database Manager')
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('init')
    sub.add_parser('seed')
    sub.add_parser('summary')
    sub.add_parser('backup')
    restore_parser = sub.add_parser('restore')
    restore_parser.add_argument('--file', required=True)
    args = parser.parse_args()

    if args.command == 'init': init()
    elif args.command == 'seed': seed()
    elif args.command == 'summary': summary()
    elif args.command == 'backup': backup()
    elif args.command == 'restore': restore(args.file)
    else: parser.print_help()
