import os, io, json, random, secrets, re, logging, sys
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from functools import wraps
from dotenv import load_dotenv
load_dotenv()

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, jsonify, send_file, abort, send_from_directory)
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user, UserMixin)
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_mail import Mail, Message
from flask_compress import Compress
from flask_talisman import Talisman
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from werkzeug.exceptions import BadRequest

# Monkey-patch werkzeug to suppress TLS handshake error logging
import werkzeug.serving
original_log_request = werkzeug.serving.WSGIRequestHandler.log_request

def silent_log_request(self, code='-', size='-'):
    # Suppress 400 errors from malformed TLS requests
    if code == 400:
        return
    original_log_request(self, code, size)

werkzeug.serving.WSGIRequestHandler.log_request = silent_log_request

# Suppress werkzeug console output
logging.getLogger('werkzeug').setLevel(logging.ERROR)

try:
    from flask_caching import Cache
except ImportError:
    class Cache:
        def __init__(self, app=None, config=None):
            self.app = app
            self.config = config or {}
        def init_app(self, app, config=None):
            self.app = app
            if config:
                self.config.update(config)
        def cached(self, timeout=0, query_string=False):
            def decorator(f):
                return f
            return decorator
        def memoize(self, timeout=0):
            def decorator(f):
                return f
            return decorator
        def delete_memoized(self, *args, **kwargs):
            return None
        def clear(self):
            return True

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import jwt, pyotp, qrcode, requests
from authlib.integrations.flask_client import OAuth
from openai import OpenAI
from sqlalchemy import func, extract, text

# -------------------------------------------------------------------
# Optional production dependencies
# -------------------------------------------------------------------
try:
    import cloudinary
    import cloudinary.uploader
    from cloudinary.utils import cloudinary_url
except ImportError:
    cloudinary = None

try:
    import stripe
except ImportError:
    stripe = None

try:
    import pytz
except ImportError:
    pytz = None

# -------------------------------------------------------------------
# Robust nairobi_now() with fallbacks
# -------------------------------------------------------------------
def nairobi_now():
    try:
        return datetime.now(ZoneInfo("Africa/Nairobi")).replace(tzinfo=None)
    except Exception:
        try:
            if pytz:
                tz = pytz.timezone('Africa/Nairobi')
                return datetime.now(tz).replace(tzinfo=None)
        except Exception:
            pass
        return datetime.utcnow()

# -------------------------------------------------------------------
# App Initialization
# -------------------------------------------------------------------
app = Flask(__name__)

# Handle bad requests gracefully
@app.errorhandler(BadRequest)
def handle_bad_request(e):
    return "Bad Request", 400

# FIXED: Only enforce HTTPS in production when DEBUG is False
# AND when not running locally (check for localhost)
debug_mode = os.getenv('DEBUG', 'False').lower() == 'true'
is_local = os.getenv('LOCAL_DEV', 'True').lower() == 'true'

if not debug_mode and not is_local:
    Talisman(app, content_security_policy=None, force_https=True)
else:
    # In development, don't force HTTPS
    print("Running in development mode - HTTPS enforcement disabled")

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', secrets.token_hex(32))

# Database: SQLite only
database_url = 'sqlite:///market2farm.db'
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,
    'pool_recycle': 3600,
    'pool_pre_ping': True
}

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SITE_URL'] = os.getenv('SITE_URL', 'http://localhost:5000')
app.config['SECURITY_PASSWORD_SALT'] = os.getenv('SECURITY_PASSWORD_SALT', 'password-reset-salt')

# Email configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', 'noreply@market2farm.com')

if cloudinary:
    cloudinary.config(
        cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
        api_key=os.getenv('CLOUDINARY_API_KEY'),
        api_secret=os.getenv('CLOUDINARY_API_SECRET'),
        secure=True
    )

if stripe:
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

mail = Mail(app)
Compress(app)

db = SQLAlchemy(app)
CORS(app, supports_credentials=True)
csrf = CSRFProtect(app)
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})

limiter = Limiter(get_remote_address, app=app,
                  default_limits=["100 per day", "20 per hour"],
                  storage_uri='memory://')

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)
facebook = oauth.register(
    name='facebook',
    client_id=os.getenv('FACEBOOK_CLIENT_ID'),
    client_secret=os.getenv('FACEBOOK_CLIENT_SECRET'),
    access_token_url='https://graph.facebook.com/v18.0/oauth/access_token',
    authorize_url='https://www.facebook.com/v18.0/dialog/oauth',
    api_base_url='https://graph.facebook.com/v18.0/',
    client_kwargs={'scope': 'email'},
)

openai_client = None
if os.getenv('OPENAI_API_KEY'):
    openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# ---------- Database Models ----------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    password_hash = db.Column(db.String(128))
    role = db.Column(db.String(20), default='buyer')
    verified = db.Column(db.Boolean, default=False)
    email_verified = db.Column(db.Boolean, default=False)
    two_factor_secret = db.Column(db.String(32))
    two_factor_enabled = db.Column(db.Boolean, default=False)
    suspended = db.Column(db.Boolean, default=False)
    subscription_active = db.Column(db.Boolean, default=False)
    subscription_plan = db.Column(db.String(50))
    subscription_expiry = db.Column(db.DateTime)
    bio = db.Column(db.Text)
    location = db.Column(db.String(120))
    avatar = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_ip = db.Column(db.String(45))

class SubscriptionPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    price_monthly = db.Column(db.Float)
    price_yearly = db.Column(db.Float)
    is_active = db.Column(db.Boolean, default=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50))
    organic = db.Column(db.Boolean, default=False)
    image = db.Column(db.String(200))
    farmer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=nairobi_now)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    quantity = db.Column(db.Integer, default=1)
    total_price = db.Column(db.Float)
    status = db.Column(db.String(20), default='pending')
    payment_method = db.Column(db.String(50))
    transaction_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ExecutiveUpdate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    attachment = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    published_by = db.Column(db.Integer, db.ForeignKey('user.id'))

class PaymentTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    payment_method = db.Column(db.String(50))
    amount = db.Column(db.Float)
    transaction_code = db.Column(db.String(100))
    status = db.Column(db.String(20), default='pending')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    message = db.Column(db.Text)
    read = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(200))
    ip_address = db.Column(db.String(45))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class ChatbotLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_message = db.Column(db.Text)
    bot_reply = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class DiseaseDetectionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text)
    crop_type = db.Column(db.String(50))
    result = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class CaptchaModel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    captcha_text = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.relationship('Product', backref=db.backref('reviews', lazy=True))
    user = db.relationship('User', backref=db.backref('reviews', lazy=True))

class ProductView(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow)

class Wallet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    balance = db.Column(db.Float, default=0.0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class WalletTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    transaction_type = db.Column(db.String(30), default='credit')
    reference = db.Column(db.String(120))
    description = db.Column(db.String(240))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class MarketplaceConnection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    status = db.Column(db.String(30), default='awaiting_admin')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime)

class Cart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(db.Integer, db.ForeignKey('cart.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)

class ContactRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    from_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    message = db.Column(db.Text, nullable=False)
    reply = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Inquiry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    buyer = db.relationship('User', foreign_keys=[buyer_id])
    product = db.relationship('Product', backref='inquiries')

class AdminReply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inquiry_id = db.Column(db.Integer, db.ForeignKey('inquiry.id'), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reply = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    inquiry = db.relationship('Inquiry', backref='replies')
    admin = db.relationship('User', foreign_keys=[admin_id])

class OrderNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    note = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PrivateMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    from_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    from_user = db.relationship('User', foreign_keys=[from_user_id])
    to_user = db.relationship('User', foreign_keys=[to_user_id])

# ---------- Helper Functions ----------
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'chief_admin']:
            abort(403)
        return f(*args, **kwargs)
    return decorated

def chief_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'chief_admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated

def log_audit(user_id, action, ip):
    audit = AuditLog(user_id=user_id, action=action, ip_address=ip)
    db.session.add(audit)
    db.session.commit()

def notify_user(user_id, message):
    db.session.add(Notification(user_id=user_id, message=message))
    db.session.commit()

def notify_admins(message):
    admins = User.query.filter(User.role.in_(['admin', 'chief_admin']), User.suspended == False).all()
    for admin in admins:
        notify_user(admin.id, message)

def get_wallet(user_id):
    wallet = Wallet.query.filter_by(user_id=user_id).first()
    if not wallet:
        wallet = Wallet(user_id=user_id, balance=0)
        db.session.add(wallet)
        db.session.flush()
    return wallet

def record_wallet_credit(user_id, amount, reference, description):
    wallet = get_wallet(user_id)
    wallet.balance = float(wallet.balance or 0) + float(amount or 0)
    db.session.add(WalletTransaction(
        user_id=user_id,
        amount=float(amount or 0),
        transaction_type='credit',
        reference=reference,
        description=description
    ))
    db.session.commit()
    return wallet

def payment_methods():
    return [
        {'id': 'mpesa', 'name': 'M-Pesa STK Push', 'region': 'Kenya', 'currency': 'KSH'},
        {'id': 'airtel_money', 'name': 'Airtel Money', 'region': 'Kenya', 'currency': 'KSH'},
        {'id': 'bank_transfer', 'name': 'Bank Transfer', 'region': 'Kenya', 'currency': 'KSH'},
        {'id': 'paypal', 'name': 'PayPal', 'region': 'International', 'currency': 'USD'},
        {'id': 'stripe_card', 'name': 'Visa/Mastercard', 'region': 'International', 'currency': 'USD'},
        {'id': 'binance_pay', 'name': 'Binance Pay', 'region': 'International', 'currency': 'USD'}
    ]

def payment_currency(payment_method):
    method = next((m for m in payment_methods() if m['id'] == payment_method), None)
    return method['currency'] if method else 'KSH'

def display_amount(amount_ksh, payment_method):
    currency = payment_currency(payment_method)
    if currency == 'USD':
        amount = round(float(amount_ksh or 0) / 130, 2)
        return {'amount': amount, 'currency': 'USD', 'label': f'USD {amount:,.2f}'}
    amount = float(amount_ksh or 0)
    return {'amount': amount, 'currency': 'KSH', 'label': f'KSH {amount:,.0f}'}

def auto_verify_payment(payment_method, amount, order=None, purpose='order'):
    prefix = payment_method.upper().replace('-', '_')
    transaction = PaymentTransaction(
        order_id=order.id if order else None,
        payment_method=payment_method,
        amount=float(amount or 0),
        transaction_code=f'{prefix}{datetime.utcnow().strftime("%Y%m%d%H%M%S")}',
        status='completed'
    )
    db.session.add(transaction)
    db.session.flush()
    if order:
        order.status = 'paid_awaiting_admin_connection'
        order.payment_method = payment_method
        order.transaction_id = transaction.transaction_code
        record_wallet_credit(order.buyer_id, amount, transaction.transaction_code, f'Payment received for order #{order.id}')
    db.session.commit()
    return transaction

def record_pending_payment(order, payment_method, reference):
    transaction = PaymentTransaction(
        order_id=order.id,
        payment_method=payment_method,
        amount=float(order.total_price or 0),
        transaction_code=reference,
        status='pending_verification'
    )
    db.session.add(transaction)
    order.status = 'pending_payment_verification'
    db.session.commit()
    return transaction

def latest_ssl_log_snapshot():
    forwarded_proto = request.headers.get('X-Forwarded-Proto', '')
    forwarded_ssl = request.headers.get('X-Forwarded-SSL', '')
    logs = AuditLog.query.filter(
        (AuditLog.action.ilike('%ssl%')) |
        (AuditLog.action.ilike('%certificate%')) |
        (AuditLog.action.ilike('%tls%')) |
        (AuditLog.action.ilike('%deploy%'))
    ).order_by(AuditLog.timestamp.desc()).limit(8).all()
    return {
        'request_secure': request.is_secure or forwarded_proto == 'https' or forwarded_ssl == 'on',
        'forwarded_proto': forwarded_proto or 'not provided',
        'host': request.host,
        'site_url': app.config['SITE_URL'],
        'logs': logs
    }

def filter_contact_info(text):
    text = re.sub(r'\b(\+?254|0)?[7-9][0-9]{8}\b', '[PHONE REMOVED]', text)
    text = re.sub(r'\b[0-9]{10,15}\b', '[NUMBER REMOVED]', text)
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL REMOVED]', text)
    text = re.sub(r'(wa\.me|whatsapp\.com|t\.me|telegram|instagram\.com|facebook\.com|twitter\.com)/\S+', '[LINK REMOVED]', text)
    return text

def send_email(to, subject, body):
    if app.config['MAIL_USERNAME'] and app.config['MAIL_PASSWORD']:
        try:
            msg = Message(subject, recipients=[to], body=body)
            mail.send(msg)
            return True
        except Exception as e:
            print(f"SMTP email error: {e}")
    sg_key = os.getenv('SENDGRID_API_KEY')
    if sg_key:
        try:
            import sendgrid
            from sendgrid.helpers.mail import Mail
            sg = sendgrid.SendGridAPIClient(sg_key)
            email_msg = Mail(
                from_email=app.config['MAIL_DEFAULT_SENDER'],
                to_emails=to,
                subject=subject,
                plain_text_content=body
            )
            response = sg.send(email_msg)
            if response.status_code in [200, 202]:
                return True
        except Exception as e:
            print(f"SendGrid exception: {e}")
    print("Email not configured. Skipping.")
    return False

def send_sms(phone_number, message):
    print(f"SMS to {phone_number}: {message}")
    return True

def generate_reset_token(email):
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    return serializer.dumps(email, salt=app.config['SECURITY_PASSWORD_SALT'])

def confirm_reset_token(token, expiration=3600):
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        email = serializer.loads(token, salt=app.config['SECURITY_PASSWORD_SALT'], max_age=expiration)
    except (SignatureExpired, BadSignature):
        return None
    return email

def ensure_runtime_tables():
    db.create_all()
    
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS wallet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            balance FLOAT DEFAULT 0.0,
            updated_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES user (id)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS wallet_transaction (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount FLOAT NOT NULL,
            transaction_type VARCHAR(30) DEFAULT 'credit',
            reference VARCHAR(120),
            description VARCHAR(240),
            created_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES user (id)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS marketplace_connection (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            admin_id INTEGER,
            status VARCHAR(30) DEFAULT 'awaiting_admin',
            notes TEXT,
            created_at DATETIME,
            approved_at DATETIME,
            FOREIGN KEY (order_id) REFERENCES "order" (id),
            FOREIGN KEY (buyer_id) REFERENCES user (id),
            FOREIGN KEY (seller_id) REFERENCES user (id),
            FOREIGN KEY (admin_id) REFERENCES user (id)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            created_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES user (id)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS cart_item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            FOREIGN KEY (cart_id) REFERENCES cart (id),
            FOREIGN KEY (product_id) REFERENCES product (id)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS contact_request (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            order_id INTEGER,
            message TEXT NOT NULL,
            reply TEXT,
            status VARCHAR(20) DEFAULT 'pending',
            created_at DATETIME,
            FOREIGN KEY (from_user_id) REFERENCES user (id),
            FOREIGN KEY (to_user_id) REFERENCES user (id),
            FOREIGN KEY (order_id) REFERENCES "order" (id)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS inquiry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            created_at DATETIME,
            FOREIGN KEY (buyer_id) REFERENCES user (id),
            FOREIGN KEY (product_id) REFERENCES product (id)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_reply (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inquiry_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            reply TEXT NOT NULL,
            created_at DATETIME,
            FOREIGN KEY (inquiry_id) REFERENCES inquiry (id),
            FOREIGN KEY (admin_id) REFERENCES user (id)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS order_note (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            admin_id INTEGER,
            note TEXT NOT NULL,
            created_at DATETIME,
            FOREIGN KEY (order_id) REFERENCES "order" (id),
            FOREIGN KEY (admin_id) REFERENCES user (id)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS private_message (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            read BOOLEAN DEFAULT 0,
            created_at DATETIME,
            FOREIGN KEY (from_user_id) REFERENCES user (id),
            FOREIGN KEY (to_user_id) REFERENCES user (id)
        )
    """))
    db.session.commit()

def table_columns(table_name):
    rows = db.session.execute(text(f'PRAGMA table_info("{table_name}")')).fetchall()
    return {row[1] for row in rows}

def add_column_if_missing(table_name, column_name, column_sql):
    columns = table_columns(table_name)
    if column_name not in columns:
        db.session.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN {column_sql}'))
        db.session.commit()
        print(f"Added column {column_name} to {table_name}")

def migrate_existing_database():
    add_column_if_missing('user', 'phone', 'phone VARCHAR(20)')
    add_column_if_missing('user', 'role', "role VARCHAR(20) DEFAULT 'buyer'")
    add_column_if_missing('user', 'verified', 'verified BOOLEAN DEFAULT 0')
    add_column_if_missing('user', 'email_verified', 'email_verified BOOLEAN DEFAULT 0')
    add_column_if_missing('user', 'two_factor_secret', 'two_factor_secret VARCHAR(32)')
    add_column_if_missing('user', 'two_factor_enabled', 'two_factor_enabled BOOLEAN DEFAULT 0')
    add_column_if_missing('user', 'suspended', 'suspended BOOLEAN DEFAULT 0')
    add_column_if_missing('user', 'subscription_active', 'subscription_active BOOLEAN DEFAULT 0')
    add_column_if_missing('user', 'subscription_plan', 'subscription_plan VARCHAR(50)')
    add_column_if_missing('user', 'subscription_expiry', 'subscription_expiry DATETIME')
    add_column_if_missing('user', 'bio', 'bio TEXT')
    add_column_if_missing('user', 'location', 'location VARCHAR(120)')
    add_column_if_missing('user', 'avatar', 'avatar VARCHAR(200)')
    add_column_if_missing('user', 'created_at', 'created_at DATETIME')
    add_column_if_missing('user', 'last_login_ip', 'last_login_ip VARCHAR(45)')
    add_column_if_missing('product', 'description', 'description TEXT')
    add_column_if_missing('product', 'price', 'price FLOAT DEFAULT 0')
    add_column_if_missing('product', 'category', 'category VARCHAR(50)')
    add_column_if_missing('product', 'organic', 'organic BOOLEAN DEFAULT 0')
    add_column_if_missing('product', 'image', 'image VARCHAR(200)')
    add_column_if_missing('product', 'farmer_id', 'farmer_id INTEGER')
    add_column_if_missing('product', 'status', "status VARCHAR(20) DEFAULT 'pending'")
    add_column_if_missing('product', 'created_at', 'created_at DATETIME')
    add_column_if_missing('order', 'buyer_id', 'buyer_id INTEGER')
    add_column_if_missing('order', 'product_id', 'product_id INTEGER')
    add_column_if_missing('order', 'quantity', 'quantity INTEGER DEFAULT 1')
    add_column_if_missing('order', 'total_price', 'total_price FLOAT DEFAULT 0')
    add_column_if_missing('order', 'status', "status VARCHAR(20) DEFAULT 'pending'")
    add_column_if_missing('order', 'payment_method', 'payment_method VARCHAR(50)')
    add_column_if_missing('order', 'transaction_id', 'transaction_id VARCHAR(100)')
    add_column_if_missing('order', 'created_at', 'created_at DATETIME')
    add_column_if_missing('payment_transaction', 'order_id', 'order_id INTEGER')
    add_column_if_missing('payment_transaction', 'payment_method', 'payment_method VARCHAR(50)')
    add_column_if_missing('payment_transaction', 'amount', 'amount FLOAT DEFAULT 0')
    add_column_if_missing('payment_transaction', 'transaction_code', 'transaction_code VARCHAR(100)')
    add_column_if_missing('payment_transaction', 'status', "status VARCHAR(20) DEFAULT 'pending'")
    add_column_if_missing('payment_transaction', 'timestamp', 'timestamp DATETIME')
    add_column_if_missing('executive_update', 'attachment', 'attachment VARCHAR(200)')
    add_column_if_missing('disease_detection_log', 'crop_type', 'crop_type VARCHAR(50)')
    db.session.commit()

def generate_captcha():
    ops = ['+', '-', '*']
    op = random.choice(ops)
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    if op == '-': 
        a = max(a, b)
    text = f"{a} {op} {b}"
    answer = eval(text)
    c = CaptchaModel(captcha_text=str(answer))
    db.session.add(c)
    db.session.commit()
    return text, c.id

def get_recommendations(user, product_id=None, limit=6):
    if user.is_authenticated and product_id:
        subq = db.session.query(Order.buyer_id).filter(Order.product_id == product_id).subquery()
        other_products = db.session.query(Product).join(Order).filter(
            Order.buyer_id.in_(subq),
            Product.id != product_id,
            Product.status == 'approved'
        ).group_by(Product.id).order_by(func.count(Order.id).desc()).limit(limit).all()
        if other_products:
            return other_products
    if user.is_authenticated:
        viewed_cats = db.session.query(Product.category).join(ProductView).filter(ProductView.user_id == user.id).distinct().limit(3).all()
        if viewed_cats:
            cat_list = [c[0] for c in viewed_cats]
            return Product.query.filter(Product.category.in_(cat_list), Product.status == 'approved').limit(limit).all()
    return ranked_products(limit=limit)

def ranked_products(limit=8, category=None):
    products = Product.query.filter_by(status='approved')
    if category:
        products = products.filter(Product.category == category)
    products = products.all()
    scored = []
    now = datetime.utcnow()
    for product in products:
        sold = db.session.query(func.coalesce(func.sum(Order.quantity), 0)).filter(
            Order.product_id == product.id,
            Order.status.in_(['paid', 'paid_awaiting_admin_connection', 'admin_connected'])
        ).scalar() or 0
        views = ProductView.query.filter_by(product_id=product.id).count()
        review_count = Review.query.filter_by(product_id=product.id).count()
        rating = db.session.query(func.coalesce(func.avg(Review.rating), 0)).filter(Review.product_id == product.id).scalar() or 0
        age_days = max((now - product.created_at).days, 0) if product.created_at else 30
        freshness = max(0, 30 - age_days) / 30
        score = (float(sold) * 4.0) + (views * 0.35) + (float(rating) * 2.0) + (review_count * 0.75)
        score += 1.5 if product.organic else 0
        score += freshness * 2.0
        scored.append((score, product))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [product for _, product in scored[:limit]]

def admin_dashboard_metrics():
    paid_statuses = ['paid', 'paid_awaiting_admin_connection', 'admin_connected']
    paid_revenue = db.session.query(func.coalesce(func.sum(Order.total_price), 0)).filter(Order.status.in_(paid_statuses)).scalar() or 0
    active_plan = SubscriptionPlan.query.filter_by(is_active=True).first()
    pending_products = Product.query.filter_by(status='pending').count()
    failed_logins = AuditLog.query.filter(AuditLog.action.ilike('%failed%')).count()
    suspicious_ips = db.session.query(AuditLog.ip_address, func.count(AuditLog.id).label('hits')).filter(
        AuditLog.ip_address.isnot(None)
    ).group_by(AuditLog.ip_address).order_by(func.count(AuditLog.id).desc()).limit(5).all()
    top_products = db.session.query(Product.name, func.coalesce(func.sum(Order.quantity), 0).label('units')) \
        .outerjoin(Order, Product.id == Order.product_id) \
        .group_by(Product.id) \
        .order_by(func.coalesce(func.sum(Order.quantity), 0).desc()) \
        .limit(8).all()
    role_counts = {str(role or 'unknown'): count for role, count in db.session.query(User.role, func.count(User.id)).group_by(User.role).all()}
    order_statuses = {str(status or 'unknown'): count for status, count in db.session.query(Order.status, func.count(Order.id)).group_by(Order.status).all()}
    recent_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(12).all()
    executive_updates = ExecutiveUpdate.query.order_by(ExecutiveUpdate.created_at.desc()).limit(8).all()
    pending_payments = PaymentTransaction.query.filter_by(status='pending_verification').order_by(PaymentTransaction.timestamp.desc()).limit(12).all()
    recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()
    latest_products = Product.query.order_by(Product.created_at.desc()).limit(10).all()
    admin_users = User.query.filter(User.role.in_(['admin', 'chief_admin'])).order_by(User.role.desc(), User.username).all()
    pending_connections = MarketplaceConnection.query.filter_by(status='awaiting_admin').order_by(MarketplaceConnection.created_at.desc()).limit(10).all()
    wallet_total = db.session.query(func.coalesce(func.sum(Wallet.balance), 0)).scalar() or 0
    disease_scans_count = 0
    try:
        disease_scans_count = DiseaseDetectionLog.query.count()
    except Exception:
        disease_scans_count = 0
    ai_usage = {
        'chatbot': ChatbotLog.query.count(),
        'disease_scans': disease_scans_count,
        'climate_requests': 0
    }
    risk_score = min(100, (pending_products * 3) + (failed_logins * 4) + (len(suspicious_ips) * 6))
    return {
        'stats': {
            'users': User.query.count(),
            'buyers': role_counts.get('buyer', 0),
            'farmers': role_counts.get('farmer', 0),
            'admins': role_counts.get('admin', 0) + role_counts.get('chief_admin', 0),
            'products': Product.query.count(),
            'approved_products': Product.query.filter_by(status='approved').count(),
            'pending_products': pending_products,
            'orders': Order.query.count(),
            'paid_orders': sum(order_statuses.get(status, 0) for status in paid_statuses),
            'revenue': float(paid_revenue),
            'subscriptions_active': User.query.filter_by(subscription_active=True).count(),
            'risk_score': risk_score,
            'failed_logins': failed_logins,
            'pending_connections': MarketplaceConnection.query.filter_by(status='awaiting_admin').count(),
            'wallet_total': float(wallet_total)
        },
        'active_plan': active_plan,
        'role_counts': role_counts,
        'order_statuses': order_statuses,
        'top_products': [{'name': name, 'units': int(units or 0)} for name, units in top_products],
        'recent_logs': recent_logs,
        'executive_updates': executive_updates,
        'ssl_status': latest_ssl_log_snapshot(),
        'pending_payments': pending_payments,
        'recent_users': recent_users,
        'admin_users': admin_users,
        'latest_products': latest_products,
        'pending_connections': pending_connections,
        'suspicious_ips': suspicious_ips,
        'ai_usage': ai_usage
    }

def seed_inbuilt_marketplace():
    red_dragon_image = 'https://commons.wikimedia.org/wiki/Special:Redirect/file/Red%20dragon%20fruit.jpg'
    dragon_plate_image = 'https://commons.wikimedia.org/wiki/Special:Redirect/file/Dragon%20Fruit%20on%20a%20plate%2002.jpg'
    demo_farmer = User.query.filter_by(username='market2farm_farmer').first()
    if not demo_farmer:
        demo_farmer = User(
            username='market2farm_farmer',
            email='farmer@market2farm.com',
            password_hash=generate_password_hash('Farmer@2025'),
            role='farmer',
            verified=True,
            location='Nairobi, Kenya'
        )
        db.session.add(demo_farmer)
        db.session.flush()
    products = [
        ('Dragon Fruit Red Flesh', 'Tropical dragon fruits with deep red flesh, high antioxidants, and a clean sweet finish.', 680, 'fruits', True, red_dragon_image),
        ('Dragon Fruit White Flesh', 'Crisp white-flesh pitaya for salads, smoothies, hotels, and premium fruit baskets.', 620, 'fruits', True, dragon_plate_image),
        ('Hass Avocados', 'Creamy Hass avocados sorted for export quality with rich oil content and firm skin.', 180, 'avocado', True, 'https://images.unsplash.com/photo-1601039641847-7857b994d704?auto=format&fit=crop&w=900&q=80'),
        ('Fuerte Avocados', 'Smooth green Fuerte avocados, ideal for restaurants, fresh markets, and processors.', 150, 'avocado', False, 'https://images.unsplash.com/photo-1590431306482-f700ee050c59?auto=format&fit=crop&w=900&q=80'),
        ('Pinkerton Avocados', 'Long-neck Pinkerton avocados with small seed, creamy flesh, and excellent slicing quality.', 170, 'avocado', True, 'https://images.unsplash.com/photo-1519162808019-7de1683fa2ad?auto=format&fit=crop&w=900&q=80'),
        ('Reed Avocados', 'Large round Reed avocados with buttery texture for premium retail and hotel kitchens.', 210, 'avocado', False, 'https://images.unsplash.com/photo-1590431306482-f700ee050c59?auto=format&fit=crop&w=900&q=80'),
        ('Purple Dragon Fruit', 'Purple dragon fruit variety with vivid flesh for juices, fruit bowls, and export baskets.', 720, 'fruits', True, red_dragon_image),
        ('Yellow Dragon Fruit', 'Sweet yellow pitaya with firm skin and high market value for premium buyers.', 760, 'fruits', True, dragon_plate_image),
        ('Soursop Fruits', 'Fresh soursop fruits for juice processors, retailers, and wellness markets.', 260, 'fruits', True, 'https://images.unsplash.com/photo-1619566636858-adf3ef46400b?auto=format&fit=crop&w=900&q=80'),
        ('Sugar Baby Watermelons', 'Sweet compact watermelons with dark green skin and crisp red flesh.', 55, 'fruits', False, 'https://images.unsplash.com/photo-1563114773-84221bd62daa?auto=format&fit=crop&w=900&q=80'),
        ('Crimson Sweet Watermelons', 'Large juicy Crimson Sweet watermelons suitable for wholesale and open-air markets.', 48, 'fruits', False, 'https://images.unsplash.com/photo-1587049352851-8d4e89133924?auto=format&fit=crop&w=900&q=80'),
        ('Jumbo Red Onions', 'Clean dry red onions with strong shelf life for retail and bulk kitchen supply.', 95, 'vegetables', False, 'https://images.unsplash.com/photo-1508747703725-719777637510?auto=format&fit=crop&w=900&q=80'),
        ('White Onions', 'Mild white onions for fresh salads, food service, and sauce production.', 110, 'vegetables', False, 'https://images.unsplash.com/photo-1618512496248-a07fe83aa8cb?auto=format&fit=crop&w=900&q=80'),
        ('Roma Tomatoes', 'Firm Roma tomatoes with low moisture and rich color for sauces and hotels.', 90, 'vegetables', True, 'https://images.unsplash.com/photo-1546094096-0df4bcaaa337?auto=format&fit=crop&w=900&q=80'),
        ('Cherry Tomatoes', 'Sweet greenhouse cherry tomatoes packed for premium retail and salads.', 220, 'vegetables', True, 'https://images.unsplash.com/photo-1592924357228-91a4daadcfea?auto=format&fit=crop&w=900&q=80'),
        ('Dry White Maize', 'Clean dried maize grains for schools, millers, livestock feed, and bulk households.', 65, 'grains', False, 'https://images.unsplash.com/photo-1551754655-cd27e38d2076?auto=format&fit=crop&w=900&q=80'),
        ('Green Maize', 'Fresh green maize cobs harvested young for roasting, boiling, and market stalls.', 35, 'grains', False, 'https://images.unsplash.com/photo-1551754655-cd27e38d2076?auto=format&fit=crop&w=900&q=80'),
        ('Rosecoco Beans', 'High-protein Rosecoco beans, clean sorted and ready for retail packaging.', 180, 'legumes', False, 'https://images.unsplash.com/photo-1598515214211-89d3c73ae83b?auto=format&fit=crop&w=900&q=80'),
        ('Yellow Beans', 'Yellow beans with fast cooking quality for households, schools, and wholesalers.', 190, 'legumes', False, 'https://images.unsplash.com/photo-1598515214211-89d3c73ae83b?auto=format&fit=crop&w=900&q=80')
    ]
    for name, description, price, category, organic, image in products:
        existing = Product.query.filter_by(name=name).first()
        if existing:
            existing.description = description
            existing.category = category
            existing.organic = organic
            existing.image = image
            existing.status = 'approved'
        else:
            db.session.add(Product(
                name=name,
                description=description,
                price=price,
                category=category,
                organic=organic,
                image=image,
                farmer_id=demo_farmer.id,
                status='approved'
            ))
    db.session.commit()

@app.context_processor
def inject_globals():
    plan = SubscriptionPlan.query.filter_by(is_active=True).first()
    recs = get_recommendations(current_user)
    unread_chat_count = 0
    if current_user.is_authenticated:
        unread_chat_count = PrivateMessage.query.filter_by(to_user_id=current_user.id, read=False).count()
    return {
        'site_url': app.config['SITE_URL'],
        'current_user': current_user,
        'subscription_plan': plan,
        'csrf_token': generate_csrf(),
        'recommended_products': recs,
        'unread_chat_count': unread_chat_count
    }

# ---------- Public Routes ----------
@app.route('/')
def home():
    return redirect(url_for('marketplace'))

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def login_page():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter((User.username == username) | (User.email == username)).first()
        if user and check_password_hash(user.password_hash, password):
            if user.suspended:
                flash('Account suspended.', 'error')
                return render_template('login.html')
            if user.two_factor_enabled:
                session['2fa_user_id'] = user.id
                return redirect(url_for('verify_2fa_page'))
            login_user(user)
            user.last_login_ip = request.remote_addr
            db.session.commit()
            log_audit(user.id, 'Login', request.remote_addr)
            flash('Logged in successfully.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('marketplace'))
        flash('Invalid credentials.', 'error')
    return render_template('login.html')

@app.route('/verify-2fa', methods=['GET', 'POST'])
def verify_2fa_page():
    if '2fa_user_id' not in session:
        return redirect(url_for('login_page'))
    user = db.session.get(User, session['2fa_user_id'])
    if not user:
        return redirect(url_for('login_page'))
    if request.method == 'POST':
        token = request.form.get('token')
        if token and pyotp.TOTP(user.two_factor_secret).verify(token):
            login_user(user)
            session.pop('2fa_user_id', None)
            flash('2FA verified successfully.', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid 2FA token.', 'error')
    return render_template('verify_2fa.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_reset_token(email)
            reset_url = url_for('reset_password', token=token, _external=True)
            body = f"""Hello {user.username},

You requested a password reset for your Market2Farm account.

Click the link below to reset your password (valid for 1 hour):

{reset_url}

If you did not request this, please ignore this email.

Market2Farm Team
"""
            send_email(email, 'Market2Farm Password Reset', body)
            flash('If that email exists in our system, we have sent a password reset link.', 'info')
        else:
            flash('If that email exists in our system, we have sent a password reset link.', 'info')
        return redirect(url_for('login_page'))
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    email = confirm_reset_token(token)
    if not email:
        flash('The reset link is invalid or has expired.', 'error')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        confirm = request.form.get('confirm_password', '').strip()
        if not password or len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return redirect(url_for('reset_password', token=token))
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return redirect(url_for('reset_password', token=token))
        user = User.query.filter_by(email=email).first()
        if user:
            user.password_hash = generate_password_hash(password)
            db.session.commit()
            log_audit(user.id, 'Password reset via email', request.remote_addr)
            flash('Your password has been updated. Please log in.', 'success')
            return redirect(url_for('login_page'))
        else:
            flash('User not found.', 'error')
            return redirect(url_for('forgot_password'))
    return render_template('reset_password.html', token=token)

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def register_page():
    captcha_question, captcha_id = generate_captcha()
    if request.method == 'POST':
        captcha_answer = request.form.get('captcha_answer')
        captcha_record = CaptchaModel.query.get(int(request.form['captcha_id']))
        if not captcha_record or captcha_answer != captcha_record.captcha_text:
            flash('Captcha incorrect.', 'error')
            return redirect(url_for('register_page'))
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        phone = request.form.get('phone')
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return redirect(url_for('register_page'))
        if User.query.filter_by(username=username).first():
            flash('Username exists.', 'error')
            return redirect(url_for('register_page'))
        if User.query.filter_by(email=email).first():
            flash('Email exists.', 'error')
            return redirect(url_for('register_page'))
        hashed_pw = generate_password_hash(password)
        user = User(username=username, email=email, password_hash=hashed_pw,
                    phone=phone, role=request.form.get('role', 'buyer'))
        db.session.add(user)
        db.session.commit()
        log_audit(user.id, 'Registered', request.remote_addr)
        login_user(user)
        flash('Registration successful!', 'success')
        return redirect(url_for('marketplace'))
    return render_template('register.html', captcha_question=captcha_question, captcha_id=captcha_id)

@app.route('/logout')
@login_required
def logout():
    log_audit(current_user.id, 'Logout', request.remote_addr)
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

# ---------- Marketplace ----------
@app.route('/marketplace')
@cache.cached(timeout=60, query_string=True)
def marketplace():
    page = request.args.get('page', 1, type=int)
    query = Product.query.filter_by(status='approved')
    pagination = query.paginate(page=page, per_page=12)
    return render_template('marketplace.html', products=pagination.items, pagination=pagination)

@app.route('/product/<int:id>')
def product_detail(id):
    product = db.session.get(Product, id)
    if not product or product.status != 'approved':
        abort(404)
    recommendations = get_recommendations(current_user, product_id=id, limit=4)
    return render_template('product_detail.html', product=product, recommendations=recommendations)

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload_product():
    if current_user.role not in ['farmer', 'admin', 'chief_admin']:
        flash('Only farmers can upload products.', 'error')
        return redirect(url_for('marketplace'))
    if request.method == 'POST':
        name = request.form['name']
        desc = request.form['description']
        price = float(request.form['price'])
        category = request.form['category']
        organic = 'organic' in request.form
        image = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                if cloudinary:
                    try:
                        upload_result = cloudinary.uploader.upload(file, folder='market2farm/products')
                        image = upload_result['secure_url']
                    except Exception as e:
                        flash(f'Image upload failed: {str(e)}', 'error')
                        return redirect(url_for('upload_product'))
                else:
                    filename = secure_filename(file.filename)
                    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    image = url_for('uploaded_file', filename=filename, _external=True)
        product = Product(name=name, description=desc, price=price, category=category,
                          organic=organic, image=image, farmer_id=current_user.id)
        db.session.add(product)
        db.session.commit()
        flash('Product submitted for review.', 'info')
        return redirect(url_for('marketplace'))
    return render_template('upload_product.html')

# ---------- Admin: Product Approval & Deletion ----------
@app.route('/admin/products')
@admin_required
def admin_products():
    products = Product.query.filter_by(status='pending').all()
    return render_template('admin/products.html', products=products)

@app.route('/admin/approve/<int:id>', methods=['POST'])
@admin_required
def approve_product(id):
    product = db.session.get(Product, id)
    if product:
        product.status = 'approved'
        db.session.commit()
        cache.clear()
        log_audit(current_user.id, f'Approved product {id}', request.remote_addr)
        flash('Product approved.', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/reject/<int:id>', methods=['POST'])
@admin_required
def reject_product(id):
    product = db.session.get(Product, id)
    if product:
        product.status = 'rejected'
        db.session.commit()
        cache.clear()
        log_audit(current_user.id, f'Rejected product {id}', request.remote_addr)
        flash('Product rejected.', 'info')
    return redirect(url_for('admin_products'))

@app.route('/admin/product/<int:id>/delete', methods=['POST'])
@admin_required
def delete_product(id):
    product = db.session.get(Product, id)
    if not product:
        flash('Product not found.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    Order.query.filter_by(product_id=id).delete()
    Review.query.filter_by(product_id=id).delete()
    ProductView.query.filter_by(product_id=id).delete()
    CartItem.query.filter_by(product_id=id).delete()
    db.session.delete(product)
    db.session.commit()
    cache.clear()
    log_audit(current_user.id, f'Deleted product {id} ({product.name})', request.remote_addr)
    flash(f'Product "{product.name}" has been permanently removed.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))

# ---------- Notifications ----------
@app.route('/admin/send_notification', methods=['GET', 'POST'])
@admin_required
def send_notification():
    if request.method == 'POST':
        message = request.form['message']
        user_id = request.form.get('user_id')
        if user_id:
            notif = Notification(user_id=user_id, message=message)
            db.session.add(notif)
        else:
            users = User.query.all()
            for u in users:
                notif = Notification(user_id=u.id, message=message)
                db.session.add(notif)
        db.session.commit()
        flash('Notification sent.', 'success')
        return redirect(url_for('send_notification'))
    users = User.query.all()
    return render_template('admin/send_notification.html', users=users)

@app.route('/my_notifications')
@login_required
def my_notifications():
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.timestamp.desc()).all()
    return render_template('notifications.html', notifications=notifs)

@app.route('/api/notifications/mark-read', methods=['POST'])
@login_required
def mark_notification_read():
    data = request.json
    notif_id = data.get('notification_id')
    notif = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
    if notif:
        notif.read = True
        db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/api/notifications/mark-all-read', methods=['POST'])
@login_required
def mark_all_notifications_read():
    Notification.query.filter_by(user_id=current_user.id, read=False).update({'read': True})
    db.session.commit()
    return jsonify({'status': 'ok'})

# ---------- AI Chatbot API ----------
@app.route('/api/chatbot', methods=['POST'])
def chatbot():
    user_message = request.json.get('message', '')
    system_guide = (
        "You are Market2Farm AI, a simple, patient assistant for small-scale farmers and buyers. "
        "Explain in plain English. Market2Farm connects farmers and buyers through an admin-centered marketplace. "
        "Farmers upload products, admins approve them, buyers order, payments are automatically verified, wallet records are updated, "
        "and an admin or chief admin approves buyer-seller connection before direct contact. "
        "Supported products include avocados, dragon fruits, soursop, watermelons, maize, beans, onions, and tomatoes. "
        "Payments include M-Pesa, Airtel Money, bank transfer, PayPal, card/Stripe, and Binance Pay. "
        "Kenya payments show KSH and international payments show USD. "
        "Market2Farm is on YouTube, Instagram, TikTok, and WhatsApp. "
        "For help, users can contact Kiogo Systems through WhatsApp, platform notifications, or call verified users only after admin approval."
    )
    if openai_client:
        try:
            completion = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "system", "content": system_guide},
                           {"role": "user", "content": user_message}],
                max_tokens=300
            )
            reply = completion.choices[0].message.content.strip()
        except Exception as e:
            reply = f"AI error: {str(e)}. Please try again."
    else:
        msg_lower = user_message.lower()
        greetings = ['hi', 'hello', 'hey', 'mambo', 'niaje', 'habari']
        if any(word in msg_lower for word in greetings):
            reply = "Hello. I am Market2Farm AI. You can ask me about ordering, selling farm produce, payment, subscriptions, crop diseases, climate, or how to contact us."
        elif 'order' in msg_lower or 'buy' in msg_lower or 'purchase' in msg_lower:
            reply = "To buy, open a product, click Order Now, choose a payment method, then wait for admin approval. Admin stays at the center before buyer and seller connect."
        elif 'sell' in msg_lower or 'seller' in msg_lower or 'farmer' in msg_lower or 'upload' in msg_lower:
            reply = "To sell, register as a farmer, upload your product with price and photo, then wait for admin approval. Approved products appear in the marketplace."
        elif 'payment' in msg_lower or 'pay' in msg_lower or 'wallet' in msg_lower or 'currency' in msg_lower or 'dollar' in msg_lower or 'ksh' in msg_lower:
            reply = "Market2Farm supports M-Pesa, Airtel Money, bank transfer, PayPal, Visa/Mastercard, and Binance Pay. Kenya methods show KSH. International methods show USD. Demo payments are automatically verified and recorded in your wallet."
        elif 'contact' in msg_lower or 'call' in msg_lower or 'whatsapp' in msg_lower or 'youtube' in msg_lower or 'instagram' in msg_lower or 'tiktok' in msg_lower:
            reply = "You can reach Market2Farm through WhatsApp, YouTube, Instagram, and TikTok links in the footer. For trade safety, buyer and seller phone contact opens only after admin approval."
        elif 'subscription' in msg_lower or 'premium' in msg_lower or 'fee' in msg_lower:
            reply = "If the chief admin enables a subscription plan, users can pay by KSH or USD method and access premium tools immediately after automatic verification."
        elif 'admin' in msg_lower or 'approval' in msg_lower or 'connect' in msg_lower:
            reply = "Admins approve products, monitor orders, send notifications, and connect buyers to sellers. The chief admin can add/remove admins, verify legit users, set subscription fees, and change built-in product prices."
        elif 'disease' in msg_lower:
            reply = "Use Disease AI to upload a crop image or describe symptoms. Simple examples: yellow leaves may need nitrogen, spots may be fungal, and wilting may need urgent field hygiene and crop rotation."
        elif 'weather' in msg_lower or 'climate' in msg_lower or 'rain' in msg_lower or 'temperature' in msg_lower:
            reply = "Open Climate Intelligence for rain and temperature guidance. It helps farmers decide when to irrigate, mulch, plant, or protect young crops."
        elif 'price' in msg_lower or 'cost' in msg_lower:
            reply = "Product prices are shown in the marketplace. Admin or chief admin can update prices for avocado, dragon fruit, soursop, watermelon, maize, beans, onions, and tomatoes."
        elif any(crop in msg_lower for crop in ['avocado', 'dragon', 'soursop', 'watermelon', 'maize', 'beans', 'onion', 'tomato']):
            reply = "Those crops are supported in the Market2Farm marketplace. Open Marketplace to view current stock, price, organic status, and order through admin-secured payment."
        else:
            reply = "I can help. Try asking: how do I order, how do I sell, how do payments work, what is my wallet, how do subscriptions work, how do I contact Market2Farm, or what crop disease advice do you need?"
    log = ChatbotLog(user_message=user_message, bot_reply=reply)
    db.session.add(log)
    db.session.commit()
    return jsonify({'reply': reply})

# ---------- AI Disease Detection ----------
@app.route('/api/disease/detect', methods=['POST'])
@csrf.exempt
def disease_detect():
    image = request.files.get('image')
    text_desc = request.form.get('description', '')
    crop_type = request.form.get('crop_type', 'general')
    result = {}
    if openai_client:
        prompt = f"""You are an expert agricultural disease detection system.
Crop type: {crop_type}
Symptoms described by farmer: {text_desc}
{ 'An image was uploaded for analysis.' if image else 'No image uploaded.' }

Based on the above, provide a structured diagnosis in the following JSON format only, without extra text:
{{
    "disease": "Name of the disease (or condition)",
    "confidence": a number between 0 and 1,
    "treatment": "Short actionable treatment advice (one sentence)",
    "prevention": "Short prevention tip (optional)"
}}

If no disease is detected, set disease to "No disease detected" and confidence to 0.95.
Use realistic confidence scores.
"""
        try:
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "system", "content": "You are a helpful agricultural expert."},
                           {"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            result_text = response.choices[0].message.content.strip()
            if result_text.startswith('```json'):
                result_text = result_text[7:]
            if result_text.endswith('```'):
                result_text = result_text[:-3]
            result = json.loads(result_text)
        except Exception as e:
            result = {"disease": "Analysis error", "confidence": 0.5, "treatment": "Please consult an agronomist."}
    else:
        result = {"disease": "No disease detected", "confidence": 0.95, "treatment": "No treatment needed."}
        if crop_type == 'tomato':
            if 'yellow' in text_desc.lower() or 'curl' in text_desc.lower():
                result = {"disease": "Tomato yellow leaf curl virus", "confidence": 0.87,
                          "treatment": "Remove infected plants, control whiteflies with neem oil."}
            elif 'spot' in text_desc.lower() or 'blight' in text_desc.lower():
                result = {"disease": "Early blight", "confidence": 0.84,
                          "treatment": "Apply copper fungicide, improve air circulation."}
        elif crop_type == 'maize':
            if 'wilt' in text_desc.lower():
                result = {"disease": "Maize lethal necrosis", "confidence": 0.91,
                          "treatment": "Use resistant varieties, practice crop rotation."}
            elif 'streak' in text_desc.lower():
                result = {"disease": "Maize streak virus", "confidence": 0.88,
                          "treatment": "Control leafhoppers, plant tolerant hybrids."}
        elif crop_type == 'avocado':
            if 'root rot' in text_desc.lower():
                result = {"disease": "Phytophthora root rot", "confidence": 0.92,
                          "treatment": "Improve drainage, apply phosphonate fungicides."}
        elif 'yellow' in text_desc.lower():
            result = {"disease": "Nitrogen deficiency", "confidence": 0.82,
                      "treatment": "Apply nitrogen-rich fertilizer (e.g., urea or compost manure)."}
        elif 'spots' in text_desc.lower() or 'spot' in text_desc.lower():
            result = {"disease": "Fungal leaf spot", "confidence": 0.76,
                      "treatment": "Remove affected leaves and apply copper-based fungicide."}
        elif 'wilt' in text_desc.lower():
            result = {"disease": "Bacterial wilt", "confidence": 0.88,
                      "treatment": "Crop rotation, use disease-resistant varieties, remove infected plants."}
    if image:
        filename = secure_filename(image.filename)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        result['note'] = f'Image {filename} received.'
    log = DiseaseDetectionLog(description=text_desc, crop_type=crop_type, result=json.dumps(result))
    db.session.add(log)
    db.session.commit()
    return jsonify(result)

# ---------- Climate Intelligence ----------
@app.route('/api/climate', methods=['GET'])
def climate_api():
    lat = request.args.get('lat', '-1.2921')
    lon = request.args.get('lon', '36.8219')
    try:
        resp = requests.get(f'https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max,precipitation_sum&timezone=Africa/Nairobi')
        data = resp.json()
        today = data['daily']
        rec = []
        if today['precipitation_sum'][0] > 5: 
            rec.append("High rain expected – protect young crops with mulch or covers.")
        if today['temperature_2m_max'][0] > 30: 
            rec.append("Heat wave expected – irrigate early morning or evening.")
        return jsonify({'temperature_max': today['temperature_2m_max'][0], 'precipitation': today['precipitation_sum'][0], 'recommendations': rec})
    except Exception as e:
        return jsonify({'error':'Could not fetch climate data','details':str(e)}), 500

# ---------- Payments ----------
@app.route('/api/orders', methods=['POST'])
@login_required
def create_order():
    data = request.json or {}
    product = db.session.get(Product, int(data.get('product_id',0)))
    qty = max(int(data.get('quantity',1)),1)
    if not product or product.status != 'approved':
        return jsonify({'error':'Product not available'}),400
    if product.farmer_id == current_user.id:
        return jsonify({'error':'You cannot order your own product'}),400
    order = Order(buyer_id=current_user.id, product_id=product.id, quantity=qty, total_price=float(product.price or 0)*qty, status='pending_payment')
    db.session.add(order)
    db.session.flush()
    db.session.add(MarketplaceConnection(order_id=order.id, buyer_id=current_user.id, seller_id=product.farmer_id, status='awaiting_payment'))
    notify_admins(f'New order #{order.id} for {product.name}. Admin must approve buyer-seller connection.')
    db.session.commit()
    log_audit(current_user.id, f'Created order {order.id}', request.remote_addr)
    return jsonify({'order_id':order.id, 'total':order.total_price, 'status':order.status, 'payment_methods':payment_methods(), 'message':'Order created. Choose a payment method to continue.'})

@app.route('/api/payment/methods')
def api_payment_methods():
    return jsonify({'methods':payment_methods()})

@app.route('/api/payment/initiate', methods=['POST'])
@login_required
def payment_initiate():
    data = request.json or {}
    order = db.session.get(Order, int(data.get('order_id',0)))
    method = data.get('payment_method','mpesa')
    valid = {m['id'] for m in payment_methods()}
    if method not in valid: 
        return jsonify({'error':'Invalid payment method'}),400
    if not order or order.buyer_id != current_user.id: 
        return jsonify({'error':'Invalid order'}),400

    if method == 'stripe_card' and stripe:
        return jsonify({'redirect': True, 'action': '/api/create-checkout-session', 'order_id': order.id})

    if method in ['mpesa','airtel_money']:
        phone = re.sub(r'\D', '', data.get('phone', ''))
        if not re.fullmatch(r'(2547|2541|07|01)\d{8}', phone):
            return jsonify({'error':'Enter a valid M-Pesa/Airtel phone number. Payment will only be verified by admin after checking provider records.'}),400
        reference = f'{method.upper()}-{phone[-4:]}-{datetime.utcnow().strftime("%Y%m%d%H%M%S")}'
        transaction = record_pending_payment(order, method, reference)
        notify_user(current_user.id, f'Payment request for order #{order.id} was received. Admin will verify it before matching fulfillment.')
        notify_admins(f'Payment verification needed for order #{order.id} using {method}. Reference: {reference}.')
        db.session.commit()
        log_audit(current_user.id, f'Submitted pending payment for order {order.id} using {method}', request.remote_addr)
        wallet = get_wallet(current_user.id)
        return jsonify({'success':True, 'message':f'Payment request submitted through {method.replace("_"," ").title()}. It is pending admin verification.', 'transaction_id':transaction.id, 'transaction_code':transaction.transaction_code, 'display_amount':display_amount(order.total_price, method), 'wallet_balance':wallet.balance, 'order_status':order.status})
    elif method in ['bank_transfer','paypal','binance_pay']:
        ref = data.get('reference')
        if not ref:
            instructions = {'bank_transfer':'Please transfer to: Bank: KCB, Account: 1234567890, Name: Market2Farm Ltd. Then enter the transaction reference.',
                            'paypal':'Send payment to paypal@market2farm.com. Then enter the PayPal transaction ID.',
                            'binance_pay':'Send USDT to wallet address: 0x123... Then enter the transaction hash.'}
            return jsonify({'requires_manual':True, 'instructions':instructions.get(method,'Please complete payment and enter reference'), 'message':'Manual payment required. Please provide transaction reference.'}),202
        ref = filter_contact_info(ref.strip())[:100]
        transaction = record_pending_payment(order, method, ref)
        db.session.commit()
        log_audit(current_user.id, f'Initiated manual payment for order {order.id} using {method}', request.remote_addr)
        notify_admins(f'Manual payment for order #{order.id} using {method}. Reference: {ref}. Please verify.')
        return jsonify({'success':True, 'message':f'Payment reference recorded. Admin will verify and complete your order.', 'transaction_id':transaction.id, 'transaction_code':ref, 'display_amount':display_amount(order.total_price, method), 'wallet_balance':get_wallet(current_user.id).balance, 'order_status':order.status})
    else: 
        return jsonify({'error':'Unsupported payment method'}),400

# ---------- Stripe Checkout Endpoints ----------
@app.route('/api/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    if not stripe:
        return jsonify({'error': 'Stripe is not configured. Please contact administrator.'}), 500
    
    data = request.json or {}
    order_id = data.get('order_id')
    order = Order.query.get(order_id)
    if not order or order.buyer_id != current_user.id:
        return jsonify({'error': 'Invalid order'}), 400

    amount_in_cents = int(order.total_price * 100)
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'kes',
                    'unit_amount': amount_in_cents,
                    'product_data': {
                        'name': f'Market2Farm Order #{order.id}',
                        'description': f'Product ID: {order.product_id}',
                    },
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('payment_success', order_id=order.id, _external=True),
            cancel_url=url_for('payment_cancel', order_id=order.id, _external=True),
            metadata={'order_id': order.id}
        )
        return jsonify({'sessionId': checkout_session.id, 'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/payment/success/<int:order_id>')
@login_required
def payment_success(order_id):
    order = Order.query.get(order_id)
    if order and order.buyer_id == current_user.id:
        order.status = 'paid_awaiting_admin_connection'
        transaction = PaymentTransaction(
            order_id=order.id,
            payment_method='stripe_card',
            amount=order.total_price,
            transaction_code=f'STRIPE-{order.id}-{datetime.utcnow().timestamp()}',
            status='completed'
        )
        db.session.add(transaction)
        record_wallet_credit(order.buyer_id, order.total_price, transaction.transaction_code, f'Stripe payment for order #{order.id}')
        db.session.commit()
        flash('Payment successful! Admin will now process your order.', 'success')
    else:
        flash('Order not found or not yours.', 'error')
    return redirect(url_for('my_orders'))

@app.route('/payment/cancel/<int:order_id>')
@login_required
def payment_cancel(order_id):
    flash('Payment was cancelled or failed. You can try again.', 'error')
    order = Order.query.get(order_id)
    product_id = order.product_id if order else 1
    return redirect(url_for('product_detail', id=product_id))

@app.route('/api/payment/mpesa/initiate', methods=['POST'])
@login_required
def mpesa_initiate():
    order_id = request.json.get('order_id')
    order = Order.query.get(order_id)
    if not order or order.buyer_id != current_user.id: 
        return jsonify({'error':'Invalid order'}),400
    reference = f'MPESA-{datetime.utcnow().strftime("%Y%m%d%H%M%S")}'
    transaction = record_pending_payment(order, 'mpesa', reference)
    db.session.commit()
    log_audit(current_user.id, f'Submitted M-Pesa payment request for order {order.id}', request.remote_addr)
    notify_admins(f'M-Pesa payment verification needed for order #{order.id}. Reference: {reference}.')
    return jsonify({'message':'Payment request received. Admin must verify provider records before fulfillment.', 'transaction_id':transaction.id})

def get_or_create_cart(user_id):
    cart = Cart.query.filter_by(user_id=user_id).first()
    if not cart:
        cart = Cart(user_id=user_id)
        db.session.add(cart)
        db.session.commit()
    return cart

@app.route('/cart')
@login_required
def view_cart():
    page = request.args.get('page',1,type=int)
    per_page = 20
    cart = get_or_create_cart(current_user.id)
    pagination = db.session.query(CartItem,Product).join(Product, CartItem.product_id == Product.id).filter(CartItem.cart_id == cart.id).paginate(page=page, per_page=per_page)
    total = sum(item[0].quantity * item[1].price for item in pagination.items)
    return render_template('cart.html', cart_items=pagination.items, total=total, pagination=pagination)

@app.route('/api/cart/add', methods=['POST'])
@login_required
def add_to_cart():
    data = request.json
    product = db.session.get(Product, int(data.get('product_id',0)))
    if not product or product.status != 'approved': 
        return jsonify({'error':'Product not available'}),400
    qty = max(int(data.get('quantity',1)),1)
    cart = get_or_create_cart(current_user.id)
    item = CartItem.query.filter_by(cart_id=cart.id, product_id=product.id).first()
    if item: 
        item.quantity += qty
    else: 
        item = CartItem(cart_id=cart.id, product_id=product.id, quantity=qty)
        db.session.add(item)
    db.session.commit()
    return jsonify({'success':True, 'message':'Added to cart'})

@app.route('/api/cart/update', methods=['POST'])
@login_required
def update_cart():
    data = request.json
    cart = get_or_create_cart(current_user.id)
    item = CartItem.query.filter_by(id=data.get('item_id'), cart_id=cart.id).first()
    if not item: 
        return jsonify({'error':'Item not found'}),404
    qty = max(int(data.get('quantity',0)),0)
    if qty == 0: 
        db.session.delete(item)
    else: 
        item.quantity = qty
    db.session.commit()
    return jsonify({'success':True})

@app.route('/api/cart/remove', methods=['POST'])
@login_required
def remove_cart_item():
    data = request.json
    cart = get_or_create_cart(current_user.id)
    CartItem.query.filter_by(id=data.get('item_id'), cart_id=cart.id).delete()
    db.session.commit()
    return jsonify({'success':True})

@app.route('/api/checkout', methods=['POST'])
@login_required
def secure_checkout():
    cart = get_or_create_cart(current_user.id)
    items = db.session.query(CartItem,Product).join(Product, CartItem.product_id == Product.id).filter(CartItem.cart_id == cart.id).all()
    if not items: 
        return jsonify({'error':'Cart is empty'}),400
    total = sum(item[0].quantity * item[1].price for item in items)
    if total <= 0: 
        return jsonify({'error':'Invalid total amount'}),400
    orders = []
    for cart_item, product in items:
        order = Order(buyer_id=current_user.id, product_id=product.id, quantity=cart_item.quantity, total_price=float(product.price)*cart_item.quantity, status='pending_payment')
        db.session.add(order)
        db.session.flush()
        db.session.add(MarketplaceConnection(order_id=order.id, buyer_id=current_user.id, seller_id=product.farmer_id, status='awaiting_payment'))
        orders.append(order)
    CartItem.query.filter_by(cart_id=cart.id).delete()
    db.session.commit()
    return jsonify({'success':True, 'order_ids':[o.id for o in orders], 'total':total, 'redirect':url_for('my_orders')})

@app.route('/my_orders')
@login_required
def my_orders():
    orders = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders)

@app.route('/seller/orders')
@login_required
def seller_orders():
    if current_user.role not in ['farmer','admin','chief_admin']: 
        abort(403)
    product_ids = [p.id for p in Product.query.filter_by(farmer_id=current_user.id).all()]
    orders = Order.query.filter(Order.product_id.in_(product_ids)).order_by(Order.created_at.desc()).all()
    return render_template('seller_orders.html', orders=orders)

@app.route('/api/wallet')
@login_required
def wallet_info():
    wallet = get_wallet(current_user.id)
    return jsonify({'balance':wallet.balance})

@app.route('/subscribe')
@login_required
def subscribe():
    plan = SubscriptionPlan.query.filter_by(is_active=True).first()
    if not plan: 
        flash('No active subscription plan.', 'info')
        return redirect(url_for('dashboard'))
    return render_template('subscribe.html', plan=plan)

@app.route('/api/subscription/activate', methods=['POST'])
@login_required
def activate_subscription():
    plan = SubscriptionPlan.query.filter_by(is_active=True).first()
    if not plan: 
        return jsonify({'error':'No active plan'}),400
    method = (request.json or {}).get('payment_method','mpesa') if request.is_json else request.form.get('payment_method','mpesa')
    if method not in {m['id'] for m in payment_methods()}: 
        return jsonify({'error':'Invalid payment method'}),400
    transaction = auto_verify_payment(method, plan.price_monthly, purpose='subscription')
    record_wallet_credit(current_user.id, plan.price_monthly, transaction.transaction_code, f'Subscription payment for {plan.name}')
    current_user.subscription_active = True
    current_user.subscription_plan = plan.name
    current_user.subscription_expiry = datetime.utcnow() + timedelta(days=30)
    db.session.commit()
    log_audit(current_user.id, f'Activated subscription {plan.name}', request.remote_addr)
    return jsonify({'success':True, 'message':f'{plan.name} subscription activated automatically.', 'transaction_code':transaction.transaction_code, 'display_amount':display_amount(plan.price_monthly, method), 'redirect':url_for('dashboard')})

@app.route('/chief/admin/subscription/toggle', methods=['POST'])
@chief_required
def toggle_subscription():
    plan = SubscriptionPlan.query.first()
    if plan and plan.is_active:
        plan.is_active = False
        flash('Subscriptions disabled.', 'info')
    else:
        if plan: 
            plan.is_active = True
        else: 
            plan = SubscriptionPlan(name='Premium', price_monthly=500, price_yearly=5000, is_active=True)
            db.session.add(plan)
        db.session.commit()
        flash('Subscriptions enabled.', 'success')
    return redirect(url_for('chief_dashboard'))

@app.route('/chief/admin/subscription/set', methods=['POST'])
@chief_required
def set_subscription_plan():
    name = request.form.get('name','Premium').strip() or 'Premium'
    monthly = float(request.form.get('price_monthly',0) or 0)
    yearly = float(request.form.get('price_yearly', monthly*10) or 0)
    plan = SubscriptionPlan.query.first()
    if not plan: 
        plan = SubscriptionPlan()
    plan.name = name
    plan.price_monthly = monthly
    plan.price_yearly = yearly
    plan.is_active = True
    db.session.commit()
    log_audit(current_user.id, f'Set subscription plan {name} KES {monthly}/month', request.remote_addr)
    flash('Subscription plan updated and published to users.', 'success')
    return redirect(url_for('chief_dashboard'))

@app.route('/chief/admin/updates/create', methods=['POST'])
@chief_required
def chief_create_update():
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    if not title or not content:
        flash('Executive update title and content are required.', 'error')
        return redirect(url_for('chief_dashboard'))
    attachment = None
    file = request.files.get('attachment')
    if file and file.filename:
        filename = secure_filename(f"executive_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{file.filename}")
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        attachment = filename
    update = ExecutiveUpdate(title=title, content=content, attachment=attachment, published_by=current_user.id)
    db.session.add(update)
    db.session.commit()
    log_audit(current_user.id, f'Published executive update: {title}', request.remote_addr)
    flash('Executive update published to the main portal.', 'success')
    return redirect(url_for('chief_dashboard'))

@app.route('/chief/admin/updates/<int:update_id>/delete', methods=['POST'])
@chief_required
def chief_delete_update(update_id):
    update = db.session.get(ExecutiveUpdate, update_id)
    if not update:
        flash('Executive update not found.', 'error')
        return redirect(url_for('chief_dashboard'))
    db.session.delete(update)
    db.session.commit()
    log_audit(current_user.id, f'Deleted executive update {update_id}', request.remote_addr)
    flash('Executive update removed.', 'success')
    return redirect(url_for('chief_dashboard'))

@app.route('/executive-updates/<int:update_id>/attachment')
@login_required
def executive_update_attachment(update_id):
    update = db.session.get(ExecutiveUpdate, update_id)
    if not update or not update.attachment:
        abort(404)
    return send_from_directory(app.config['UPLOAD_FOLDER'], update.attachment, as_attachment=True)

@app.route('/chief/admin/security/ssl/review', methods=['POST'])
@chief_required
def chief_review_ssl():
    status = 'secure' if latest_ssl_log_snapshot()['request_secure'] else 'not secure'
    log_audit(current_user.id, f'Reviewed SSL certificate deployment and web server logs: request {status}', request.remote_addr)
    flash('SSL certificate and web server log review recorded.', 'success')
    return redirect(url_for('chief_dashboard'))

@app.route('/chief-admin')
@chief_required
def chief_dashboard():
    return render_template('chief_admin/dashboard.html', **admin_dashboard_metrics())

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    return render_template('admin/dashboard.html', **admin_dashboard_metrics())

@app.route('/api/admin/stats')
@admin_required
def admin_stats_api():
    m = admin_dashboard_metrics()
    return jsonify({'order_statuses':m['order_statuses'], 'top_products':m['top_products']})

@app.route('/api/chief/stats')
@chief_required
def chief_stats():
    m = admin_dashboard_metrics()
    return jsonify({'total_users':m['stats']['users'], 'buyers':m['stats']['buyers'], 'farmers':m['stats']['farmers'], 'admins':m['stats']['admins'], 'total_orders':m['stats']['orders'], 'paid_orders':m['stats']['paid_orders'], 'revenue':m['stats']['revenue'], 'products':m['stats']['products'], 'approved_products':m['stats']['approved_products'], 'pending_products':m['stats']['pending_products'], 'chatbot_queries':m['ai_usage']['chatbot'], 'disease_scans':m['ai_usage']['disease_scans'], 'active_subscriptions':m['stats']['subscriptions_active'], 'security_risk_score':m['stats']['risk_score'], 'failed_logins':m['stats']['failed_logins']})

@app.route('/api/chief/user-signups')
@chief_required
def chief_user_signups():
    today = date.today()
    days = [(today - timedelta(days=i)) for i in range(29,-1,-1)]
    labels = [d.strftime('%b %d') for d in days]
    data = [User.query.filter(func.date(User.created_at) == d).count() for d in days]
    return jsonify({'labels':labels, 'data':data})

@app.route('/api/chief/revenue-trend')
@chief_required
def chief_revenue_trend():
    today = date.today()
    days = [(today - timedelta(days=i)) for i in range(29,-1,-1)]
    labels = [d.strftime('%b %d') for d in days]
    data = []
    for d in days:
        rev = db.session.query(func.sum(Order.total_price)).filter(Order.status == 'paid', func.date(Order.created_at) == d).scalar() or 0
        data.append(float(rev))
    return jsonify({'labels':labels, 'data':data})

@app.route('/api/chief/order-status')
@chief_required
def chief_order_status():
    statuses = db.session.query(Order.status, func.count(Order.id)).group_by(Order.status).all()
    return jsonify({s:c for s,c in statuses})

@app.route('/api/chief/top-products')
@chief_required
def chief_top_products():
    top = db.session.query(Product.name, func.sum(Order.quantity).label('total_qty')).join(Order, Product.id == Order.product_id).group_by(Product.id).order_by(func.sum(Order.quantity).desc()).limit(5).all()
    labels = [p[0] for p in top]
    data = [p[1] for p in top]
    return jsonify({'labels':labels, 'data':data})

@app.route('/api/chief/users')
@chief_required
def chief_manage_users():
    users = User.query.all()
    return jsonify([{'id':u.id, 'username':u.username, 'email':u.email, 'role':u.role, 'suspended':u.suspended} for u in users])

@app.route('/api/chief/user/<int:id>/suspend', methods=['POST'])
@chief_required
def chief_suspend_user(id):
    user = db.session.get(User, id)
    if user:
        if user.role == 'chief_admin' and User.query.filter_by(role='chief_admin', suspended=False).count() <= 1:
            return jsonify({'error':'Cannot suspend the last active chief admin'}),400
        user.suspended = not user.suspended
        db.session.commit()
        log_audit(current_user.id, f"{'Suspended' if user.suspended else 'Unsuspended'} user {id}", request.remote_addr)
        return jsonify({'success':True, 'suspended':user.suspended})
    return jsonify({'error':'User not found'}),404

@app.route('/api/chief/user/<int:id>/changerole', methods=['POST'])
@chief_required
def chief_change_role(id):
    user = db.session.get(User, id)
    if user:
        new_role = request.json.get('role')
        if new_role in ['buyer','farmer','admin','chief_admin']:
            if user.role == 'chief_admin' and new_role != 'chief_admin' and User.query.filter_by(role='chief_admin').count() <= 1:
                return jsonify({'error':'Cannot demote the last chief admin'}),400
            user.role = new_role
            db.session.commit()
            log_audit(current_user.id, f'Changed role of user {id} to {new_role}', request.remote_addr)
            return jsonify({'success':True})
        return jsonify({'error':'Invalid role'}),400
    return jsonify({'error':'User not found'}),404

@app.route('/chief/user/<int:id>/delete', methods=['POST'])
@chief_required
def chief_delete_user(id):
    user = db.session.get(User, id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('chief_dashboard'))
    if user.role == 'chief_admin' and User.query.filter_by(role='chief_admin').count() <= 1:
        flash('Cannot delete the last chief admin.', 'error')
        return redirect(url_for('chief_dashboard'))
    Order.query.filter_by(buyer_id=id).delete()
    Product.query.filter_by(farmer_id=id).update({'farmer_id': None})
    Notification.query.filter_by(user_id=id).delete()
    AuditLog.query.filter_by(user_id=id).delete()
    Cart.query.filter_by(user_id=id).delete()
    Wallet.query.filter_by(user_id=id).delete()
    Inquiry.query.filter_by(buyer_id=id).delete()
    PrivateMessage.query.filter((PrivateMessage.from_user_id==id) | (PrivateMessage.to_user_id==id)).delete()
    db.session.delete(user)
    db.session.commit()
    log_audit(current_user.id, f'Deleted user {id}', request.remote_addr)
    flash('User permanently deleted.', 'success')
    return redirect(url_for('chief_dashboard'))

@app.route('/chief/admin/create', methods=['POST'])
@chief_required
def chief_create_admin():
    username = request.form.get('username','').strip()
    email = request.form.get('email','').strip().lower()
    phone = request.form.get('phone','').strip()
    password = request.form.get('password','Admin@2025')
    if not username or not email: 
        flash('Username and email are required.', 'error')
        return redirect(url_for('chief_dashboard'))
    if User.query.filter((User.username == username) | (User.email == email)).first(): 
        flash('User already exists.', 'error')
        return redirect(url_for('chief_dashboard'))
    admin = User(username=username, email=email, phone=phone, password_hash=generate_password_hash(password), role='admin', verified=True, email_verified=True)
    db.session.add(admin)
    db.session.commit()
    log_audit(current_user.id, f'Created admin {username}', request.remote_addr)
    flash('Admin added successfully.', 'success')
    return redirect(url_for('chief_dashboard'))

@app.route('/chief/admin/<int:id>/remove', methods=['POST'])
@chief_required
def chief_remove_admin(id):
    user = db.session.get(User, id)
    if not user or user.role != 'admin': 
        flash('Only regular admins can be removed here.', 'error')
        return redirect(url_for('chief_dashboard'))
    user.role = 'buyer'
    user.suspended = True
    db.session.commit()
    log_audit(current_user.id, f'Removed admin {id}', request.remote_addr)
    flash('Admin removed and suspended.', 'success')
    return redirect(url_for('chief_dashboard'))

@app.route('/chief/user/<int:id>/verify', methods=['POST'])
@chief_required
def chief_verify_user(id):
    user = db.session.get(User, id)
    if not user: 
        flash('User not found.', 'error')
        return redirect(url_for('chief_dashboard'))
    user.verified = True
    user.email_verified = True
    db.session.commit()
    log_audit(current_user.id, f'Verified legit user {id}', request.remote_addr)
    flash(f'{user.username} marked as legit and verified.', 'success')
    return redirect(url_for('chief_dashboard'))

@app.route('/admin/connection/<int:order_id>/approve', methods=['POST'])
@admin_required
def approve_connection(order_id):
    order = db.session.get(Order, order_id)
    conn = MarketplaceConnection.query.filter_by(order_id=order_id).first()
    if not order or not conn: 
        flash('Connection request not found.', 'error')
        return redirect(url_for('admin_dashboard'))
    if order.status not in ['paid_awaiting_admin_connection', 'admin_matched']:
        flash('Verify payment before approving fulfillment.', 'error')
        return redirect(url_for('admin_dashboard'))
    conn.status = 'admin_managed'
    conn.admin_id = current_user.id
    conn.notes = request.form.get('notes','')
    conn.approved_at = datetime.utcnow()
    order.status = 'admin_matched'
    db.session.commit()
    notify_user(conn.buyer_id, f'Market2Farm admin approved fulfillment for order #{order.id}. The marketplace team will coordinate delivery.')
    notify_user(conn.seller_id, f'Market2Farm admin selected your stock for order #{order.id}. Coordinate only through the admin/system.')
    db.session.commit()
    log_audit(current_user.id, f'Approved admin-managed fulfillment for order {order_id}', request.remote_addr)
    flash('Admin-managed fulfillment approved. Buyer and seller identities remain hidden.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/payment/<int:transaction_id>/verify', methods=['POST'])
@admin_required
def admin_verify_payment(transaction_id):
    transaction = db.session.get(PaymentTransaction, transaction_id)
    if not transaction:
        flash('Payment transaction not found.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    order = db.session.get(Order, transaction.order_id)
    if not order:
        flash('Order not found for this payment.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    if transaction.status != 'pending_verification':
        flash('This payment has already been processed.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    decision = request.form.get('decision', 'approve')
    if decision == 'reject':
        transaction.status = 'rejected'
        order.status = 'payment_rejected'
        notify_user(order.buyer_id, f'Payment for order #{order.id} could not be verified. Please contact Market2Farm admin.')
        db.session.commit()
        log_audit(current_user.id, f'Rejected payment transaction {transaction.id} for order {order.id}', request.remote_addr)
        flash('Payment rejected.', 'success')
        return redirect(request.referrer or url_for('admin_dashboard'))
    transaction.status = 'completed'
    order.status = 'paid_awaiting_admin_connection'
    order.payment_method = transaction.payment_method
    order.transaction_id = transaction.transaction_code
    conn = MarketplaceConnection.query.filter_by(order_id=order.id).first()
    if conn:
        conn.status = 'awaiting_admin'
    record_wallet_credit(order.buyer_id, transaction.amount, transaction.transaction_code, f'Verified payment for order #{order.id}')
    notify_user(order.buyer_id, f'Payment for order #{order.id} has been verified. Market2Farm admin will match fulfillment.')
    notify_admins(f'Order #{order.id} payment verified. Select/admin-manage the best fulfillment match.')
    db.session.commit()
    log_audit(current_user.id, f'Verified payment transaction {transaction.id} for order {order.id}', request.remote_addr)
    flash('Payment verified. Order moved to admin-managed matching.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/connect/<int:order_id>', methods=['POST'])
@admin_required
def admin_connect_buyer_seller(order_id):
    order = db.session.get(Order, order_id)
    if not order: 
        flash('Order not found.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    conn = MarketplaceConnection.query.filter_by(order_id=order_id).first()
    if not conn: 
        flash('Connection record not found.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    if order.status not in ['paid_awaiting_admin_connection', 'admin_matched']:
        flash('Verify payment before approving fulfillment.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    conn.status = 'admin_managed'
    conn.admin_id = current_user.id
    conn.approved_at = datetime.utcnow()
    order.status = 'admin_matched'
    db.session.commit()
    buyer = db.session.get(User, order.buyer_id)
    seller = db.session.get(User, order.product.farmer_id)
    msg = f'Market2Farm admin selected fulfillment for order #{order.id}. All communication remains through the admin/system.'
    notify_user(buyer.id, msg)
    if seller:
        notify_user(seller.id, f'Market2Farm admin selected your stock for order #{order.id}. Do not contact the buyer directly.')
    if buyer.email: 
        send_email(buyer.email, f'Order #{order.id} Fulfillment Update', msg)
    if seller and seller.email: 
        send_email(seller.email, f'Order #{order.id} Fulfillment Update', f'Market2Farm admin selected your stock. Coordinate only through admin/system.')
    if buyer.phone: 
        send_sms(buyer.phone, f'Market2Farm: Order #{order.id} is being coordinated by admin.')
    if seller and seller.phone: 
        send_sms(seller.phone, f'Market2Farm: Order #{order.id} selected. Coordinate through admin only.')
    flash('Admin-managed fulfillment started. Buyer and seller identities remain hidden.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/product/<int:id>/price', methods=['POST'])
@admin_required
def update_product_price(id):
    product = db.session.get(Product, id)
    if not product: 
        flash('Product not found.', 'error')
        return redirect(url_for('admin_dashboard'))
    product.price = float(request.form.get('price', product.price) or product.price)
    product.description = request.form.get('description', product.description)
    db.session.commit()
    cache.clear()
    log_audit(current_user.id, f'Updated product {id} price to {product.price}', request.remote_addr)
    flash('Product price updated.', 'success')
    return redirect(url_for('chief_dashboard' if current_user.role == 'chief_admin' else 'admin_dashboard'))

@app.route('/api/chief/recent-audit')
@chief_required
def chief_recent_audit():
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(20).all()
    return jsonify([{'action':l.action, 'ip':l.ip_address, 'timestamp':l.timestamp.strftime('%Y-%m-%d %H:%M') if l.timestamp else 'unknown'} for l in logs])

@app.route('/admin/user/<int:user_id>/message', methods=['GET','POST'])
@admin_required
def admin_send_user_message(user_id):
    target = db.session.get(User, user_id)
    if not target: 
        flash('User not found.', 'error')
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        subject = request.form.get('subject', 'Message from Market2Farm Admin')
        body = request.form.get('message','').strip()
        send_email_notif = request.form.get('send_email') == 'on'
        send_sms_notif = request.form.get('send_sms') == 'on'
        if not body: 
            flash('Message cannot be empty.', 'error')
            return redirect(url_for('admin_send_user_message', user_id=user_id))
        notify_user(target.id, f'Admin message: {subject}\n\n{body}')
        if send_email_notif and target.email: 
            send_email(target.email, f'Market2Farm: {subject}', body)
        if send_sms_notif and target.phone: 
            send_sms(target.phone, f'Market2Farm: {subject[:30]} - {body[:100]}')
        log_audit(current_user.id, f'Sent message to user {target.id}', request.remote_addr)
        flash('Message sent successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('admin/send_user_message.html', user=target)

@app.route('/product/<int:product_id>/inquiry', methods=['GET','POST'])
@login_required
def product_inquiry(product_id):
    product = db.session.get(Product, product_id)
    if not product or product.status != 'approved': 
        abort(404)
    if request.method == 'POST':
        msg = request.form.get('message','').strip()
        if not msg: 
            flash('Please enter a message.', 'error')
            return redirect(url_for('product_detail', id=product_id))
        filtered = filter_contact_info(msg)
        inquiry = Inquiry(buyer_id=current_user.id, product_id=product_id, message=filtered)
        db.session.add(inquiry)
        db.session.commit()
        notify_admins(f'New inquiry from {current_user.username} about product: {product.name}')
        flash('Your inquiry has been sent to the admin. You will receive a reply soon.', 'success')
        return redirect(url_for('product_detail', id=product_id))
    return render_template('inquiry_form.html', product=product)

@app.route('/my_inquiries')
@login_required
def my_inquiries():
    inquiries = Inquiry.query.filter_by(buyer_id=current_user.id).order_by(Inquiry.created_at.desc()).all()
    return render_template('user_inquiries.html', inquiries=inquiries)

@app.route('/admin/inquiries')
@admin_required
def admin_inquiries():
    inquiries = Inquiry.query.order_by(Inquiry.created_at.desc()).all()
    return render_template('admin/inquiries.html', inquiries=inquiries)

@app.route('/admin/inquiry/<int:inquiry_id>/chat', methods=['GET','POST'])
@admin_required
def admin_inquiry_chat(inquiry_id):
    inquiry = db.session.get(Inquiry, inquiry_id)
    if not inquiry: 
        abort(404)
    if request.method == 'POST':
        reply_text = request.form.get('reply','').strip()
        if not reply_text: 
            flash('Reply cannot be empty.', 'error')
            return redirect(url_for('admin_inquiry_chat', inquiry_id=inquiry_id))
        filtered = filter_contact_info(reply_text)
        reply = AdminReply(inquiry_id=inquiry.id, admin_id=current_user.id, reply=filtered)
        db.session.add(reply)
        inquiry.status = 'replied'
        db.session.commit()
        buyer = inquiry.buyer
        notify_user(buyer.id, f'Admin replied to your inquiry about "{inquiry.product.name}".')
        if buyer.email: 
            send_email(buyer.email, f'Market2Farm: Reply to your inquiry', f'Admin said: {filtered}\n\nView your inquiries: {url_for("my_inquiries", _external=True)}')
        if buyer.phone: 
            send_sms(buyer.phone, f'Market2Farm: Admin replied to your inquiry. Check your account.')
        flash('Reply sent to buyer.', 'success')
        return redirect(url_for('admin_inquiry_chat', inquiry_id=inquiry_id))
    replies = AdminReply.query.filter_by(inquiry_id=inquiry.id).order_by(AdminReply.created_at.asc()).all()
    return render_template('admin/inquiry_chat.html', inquiry=inquiry, replies=replies)

@app.route('/admin/inquiry/<int:inquiry_id>/reply', methods=['GET','POST'])
@admin_required
def admin_reply_inquiry(inquiry_id):
    return redirect(url_for('admin_inquiry_chat', inquiry_id=inquiry_id))

@app.route('/admin/user/<int:user_id>')
@admin_required
def admin_user_detail(user_id):
    user = db.session.get(User, user_id)
    if not user: 
        abort(404)
    orders = Order.query.filter_by(buyer_id=user.id).order_by(Order.created_at.desc()).all()
    products = Product.query.filter_by(farmer_id=user.id).all()
    inquiries = Inquiry.query.filter_by(buyer_id=user.id).all()
    return render_template('admin/user_detail.html', user=user, orders_as_buyer=orders, products=products, inquiries=inquiries)

@app.route('/admin/order/<int:order_id>/note', methods=['POST'])
@admin_required
def add_order_note(order_id):
    order = db.session.get(Order, order_id)
    if not order: 
        flash('Order not found.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    note = request.form.get('note','').strip()
    if not note: 
        flash('Note cannot be empty.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    filtered = filter_contact_info(note)
    order_note = OrderNote(order_id=order.id, admin_id=current_user.id, note=filtered)
    db.session.add(order_note)
    db.session.commit()
    flash('Note added to order.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/order/<int:order_id>/confirm', methods=['POST'])
@admin_required
def admin_confirm_order(order_id):
    order = db.session.get(Order, order_id)
    if not order: 
        flash('Order not found.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    if order.status != 'admin_connected': 
        flash('Order must be in "admin_connected" status to confirm.', 'error')
        return redirect(request.referrer or url_for('admin_dashboard'))
    order.status = 'completed'
    db.session.commit()
    notify_user(order.buyer_id, f'Your order #{order.id} has been marked as completed by admin.')
    notify_user(order.product.farmer_id, f'Order #{order.id} has been marked as completed.')
    flash('Order marked as completed.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/chat/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def admin_chat(user_id):
    other_user = db.session.get(User, user_id)
    if not other_user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if not message:
            flash('Message cannot be empty.', 'error')
            return redirect(url_for('admin_chat', user_id=user_id))
        filtered = filter_contact_info(message)
        msg = PrivateMessage(from_user_id=current_user.id, to_user_id=user_id, message=filtered)
        db.session.add(msg)
        db.session.commit()
        notify_user(user_id, f'New message from admin {current_user.username}: {filtered[:100]}')
        other = db.session.get(User, user_id)
        if other.email:
            send_email(other.email, f'New message from Market2Farm admin', f'Admin said: {filtered}\n\nReply at: {url_for("user_chats", _external=True)}')
        flash('Message sent.', 'success')
        return redirect(url_for('admin_chat', user_id=user_id))
    messages = PrivateMessage.query.filter(
        ((PrivateMessage.from_user_id == current_user.id) & (PrivateMessage.to_user_id == user_id)) |
        ((PrivateMessage.from_user_id == user_id) & (PrivateMessage.to_user_id == current_user.id))
    ).order_by(PrivateMessage.created_at.asc()).all()
    PrivateMessage.query.filter_by(from_user_id=user_id, to_user_id=current_user.id, read=False).update({'read': True})
    db.session.commit()
    return render_template('admin/chat.html', other_user=other_user, messages=messages)

@app.route('/my_chats')
@login_required
def user_chats():
    admin_ids = [u.id for u in User.query.filter(User.role.in_(['admin', 'chief_admin'])).all()]
    conversations = db.session.query(PrivateMessage).filter(
        ((PrivateMessage.from_user_id == current_user.id) & (PrivateMessage.to_user_id.in_(admin_ids))) |
        ((PrivateMessage.from_user_id.in_(admin_ids)) & (PrivateMessage.to_user_id == current_user.id))
    ).order_by(PrivateMessage.created_at.desc()).all()
    chats = {}
    for msg in conversations:
        other_id = msg.from_user_id if msg.to_user_id == current_user.id else msg.to_user_id
        if other_id not in chats:
            chats[other_id] = {'user': db.session.get(User, other_id), 'last_message': msg.message, 'last_time': msg.created_at, 'unread': 0}
        if not msg.read and msg.from_user_id != current_user.id:
            chats[other_id]['unread'] += 1
    return render_template('user_chats.html', chats=chats.values())

@app.route('/chat/<int:user_id>', methods=['GET', 'POST'])
@login_required
def user_chat(user_id):
    other = db.session.get(User, user_id)
    if not other or other.role not in ['admin', 'chief_admin']:
        abort(403)
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if not message:
            flash('Message cannot be empty.', 'error')
            return redirect(url_for('user_chat', user_id=user_id))
        filtered = filter_contact_info(message)
        msg = PrivateMessage(from_user_id=current_user.id, to_user_id=user_id, message=filtered)
        db.session.add(msg)
        db.session.commit()
        notify_user(user_id, f'New message from {current_user.username}: {filtered[:100]}')
        other_user = db.session.get(User, user_id)
        if other_user.email:
            send_email(other_user.email, f'New message from {current_user.username}', filtered)
        flash('Message sent.', 'success')
        return redirect(url_for('user_chat', user_id=user_id))
    messages = PrivateMessage.query.filter(
        ((PrivateMessage.from_user_id == current_user.id) & (PrivateMessage.to_user_id == user_id)) |
        ((PrivateMessage.from_user_id == user_id) & (PrivateMessage.to_user_id == current_user.id))
    ).order_by(PrivateMessage.created_at.asc()).all()
    PrivateMessage.query.filter_by(from_user_id=user_id, to_user_id=current_user.id, read=False).update({'read': True})
    db.session.commit()
    return render_template('user_chat.html', other_user=other, messages=messages)

@app.route('/start-chat')
@login_required
def start_chat():
    admin = User.query.filter(User.role.in_(['admin', 'chief_admin'])).first()
    if not admin:
        flash('No admin available. Please try again later.', 'error')
        return redirect(url_for('dashboard'))
    return redirect(url_for('user_chat', user_id=admin.id))

@app.route('/admin/chats')
@admin_required
def admin_chats():
    sent = db.session.query(PrivateMessage.to_user_id).filter(PrivateMessage.from_user_id == current_user.id)
    received = db.session.query(PrivateMessage.from_user_id).filter(PrivateMessage.to_user_id == current_user.id)
    all_user_ids = set([uid for (uid,) in sent.union(received).all() if uid != current_user.id])
    conversations = []
    for uid in all_user_ids:
        user = db.session.get(User, uid)
        if not user:
            continue
        last_msg = PrivateMessage.query.filter(
            ((PrivateMessage.from_user_id == current_user.id) & (PrivateMessage.to_user_id == uid)) |
            ((PrivateMessage.from_user_id == uid) & (PrivateMessage.to_user_id == current_user.id))
        ).order_by(PrivateMessage.created_at.desc()).first()
        unread = PrivateMessage.query.filter_by(from_user_id=uid, to_user_id=current_user.id, read=False).count()
        conversations.append({
            'user': user,
            'last_message': last_msg.message if last_msg else '',
            'last_time': last_msg.created_at if last_msg else None,
            'unread': unread
        })
    conversations.sort(key=lambda x: x['last_time'] or datetime.min, reverse=True)
    return render_template('admin/chats_list.html', conversations=conversations)

@app.route('/profile/<int:user_id>')
def view_profile(user_id):
    user = db.session.get(User, user_id)
    if not user: 
        abort(404)
    products = []
    if user.role == 'farmer': 
        products = Product.query.filter_by(farmer_id=user.id, status='approved').all()
    return render_template('profile.html', user=user, products=products)

@app.route('/profile/edit', methods=['GET','POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        current_user.bio = request.form.get('bio','')
        current_user.location = request.form.get('location','')
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and file.filename:
                if cloudinary:
                    try:
                        upload_result = cloudinary.uploader.upload(file, folder='market2farm/avatars')
                        current_user.avatar = upload_result['secure_url']
                    except Exception as e:
                        flash(f'Avatar upload failed: {str(e)}', 'error')
                else:
                    filename = secure_filename(f"avatar_{current_user.id}_{file.filename}")
                    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    current_user.avatar = url_for('uploaded_file', filename=filename, _external=True)
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('view_profile', user_id=current_user.id))
    return render_template('edit_profile.html')

@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    curr = request.form['current_password']
    new = request.form['new_password']
    confirm = request.form['confirm_password']
    if not check_password_hash(current_user.password_hash, curr): 
        flash('Current password is incorrect.', 'error')
    elif new != confirm: 
        flash('New passwords do not match.', 'error')
    else:
        current_user.password_hash = generate_password_hash(new)
        db.session.commit()
        flash('Password changed successfully.', 'success')
    return redirect(url_for('edit_profile'))

@app.route('/api/auth/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/api/auth/google/callback')
def google_callback():
    token = google.authorize_access_token()
    user_info = google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
    email = user_info.get('email')
    if not email: 
        flash('Could not retrieve email from Google.', 'error')
        return redirect(url_for('login_page'))
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(username=email.split('@')[0], email=email, password_hash=generate_password_hash('oauth-google'), role='buyer')
        db.session.add(user)
        db.session.commit()
    login_user(user)
    flash('Logged in with Google successfully.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/api/product/<int:product_id>/review', methods=['POST'])
@login_required
def add_review(product_id):
    data = request.json
    rating = data.get('rating')
    comment = data.get('comment')
    if not rating or rating < 1 or rating > 5: 
        return jsonify({'error':'Rating must be between 1 and 5'}),400
    existing = Review.query.filter_by(product_id=product_id, user_id=current_user.id).first()
    if existing: 
        return jsonify({'error':'You have already reviewed this product'}),400
    review = Review(product_id=product_id, user_id=current_user.id, rating=rating, comment=comment)
    db.session.add(review)
    db.session.commit()
    return jsonify({'message':'Review added'}),201

@app.route('/api/product/<int:product_id>/reviews')
def get_reviews(product_id):
    page = request.args.get('page',1,type=int)
    per_page = 5
    reviews = Review.query.filter_by(product_id=product_id).order_by(Review.created_at.desc()).paginate(page=page, per_page=per_page)
    avg = db.session.query(func.avg(Review.rating)).filter_by(product_id=product_id).scalar() or 0
    total = Review.query.filter_by(product_id=product_id).count()
    return jsonify({'reviews':[{'username':r.user.username,'rating':r.rating,'comment':r.comment,'created_at':r.created_at.strftime('%Y-%m-%d')} for r in reviews.items], 'has_next':reviews.has_next, 'avg_rating':round(float(avg),1), 'total_reviews':total})

@app.route('/search')
def search_products():
    q = request.args.get('q','')
    category = request.args.get('category')
    min_price = request.args.get('min_price',type=float)
    max_price = request.args.get('max_price',type=float)
    organic = request.args.get('organic') == 'true'
    sort = request.args.get('sort','newest')
    page = request.args.get('page',1,type=int)
    query = Product.query.filter_by(status='approved')
    if q: 
        query = query.filter(Product.name.ilike(f'%{q}%') | Product.description.ilike(f'%{q}%'))
    if category and category != 'all': 
        query = query.filter_by(category=category)
    if min_price: 
        query = query.filter(Product.price >= min_price)
    if max_price: 
        query = query.filter(Product.price <= max_price)
    if organic: 
        query = query.filter_by(organic=True)
    if sort == 'price_asc': 
        query = query.order_by(Product.price.asc())
    elif sort == 'price_desc': 
        query = query.order_by(Product.price.desc())
    elif sort == 'rating':
        subq = db.session.query(Review.product_id, func.avg(Review.rating).label('avg_rating')).group_by(Review.product_id).subquery()
        query = query.outerjoin(subq, Product.id == subq.c.product_id).order_by(subq.c.avg_rating.desc().nullslast())
    else: 
        query = query.order_by(Product.created_at.desc())
    pagination = query.paginate(page=page, per_page=12)
    categories = [c[0] for c in db.session.query(Product.category).distinct().all() if c[0]]
    return render_template('search_results.html', products=pagination.items, pagination=pagination, query=q, selected_category=category, min_price=min_price, max_price=max_price, organic=organic, sort=sort, categories=categories)

@app.route('/seller/dashboard')
@login_required
def seller_dashboard():
    if current_user.role not in ['farmer','admin','chief_admin']: 
        abort(403)
    products = Product.query.filter_by(farmer_id=current_user.id).all()
    product_ids = [p.id for p in products]
    orders = Order.query.filter(Order.product_id.in_(product_ids)).all()
    total_revenue = sum(o.total_price for o in orders if o.status in ['paid','completed'])
    total_orders = len(orders)
    views = ProductView.query.filter(ProductView.product_id.in_(product_ids)).count()
    monthly = db.session.query(extract('year', Order.created_at).label('year'), extract('month', Order.created_at).label('month'), func.sum(Order.total_price)).filter(Order.product_id.in_(product_ids), Order.status.in_(['paid','completed'])).group_by('year','month').order_by('year','month').limit(6).all()
    labels = [f"{int(m[1])}/{int(m[0])}" for m in monthly]
    sales = [float(m[2] or 0) for m in monthly]
    return render_template('seller_dashboard.html', products=products, total_revenue=total_revenue, total_orders=total_orders, views=views, labels=labels, sales_data=sales)

@app.route('/api/track-view', methods=['POST'])
@login_required
def track_product_view():
    pid = request.json.get('product_id')
    if pid:
        view = ProductView(user_id=current_user.id, product_id=pid)
        db.session.add(view)
        db.session.commit()
    return jsonify({'status':'ok'})

@app.route('/api/notifications/unread-count')
@login_required
def unread_notifications_count():
    count = Notification.query.filter_by(user_id=current_user.id, read=False).count()
    return jsonify({'unread':count})

@app.route('/api/my-orders')
@login_required
def api_my_orders():
    limit = request.args.get('limit', 5, type=int)
    orders = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.created_at.desc()).limit(limit).all()
    return jsonify([{'id': o.id, 'total': o.total_price, 'status': o.status, 'created_at': o.created_at.isoformat()} for o in orders])

@app.route('/static/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/disease-detection')
def disease_detection_page():
    return render_template('disease_detection.html')

@app.route('/chatbot-page')
def chatbot_page():
    return render_template('chatbot.html')

@app.route('/climate-page')
def climate_page():
    return render_template('climate.html')

@app.route('/about-system')
def about_system():
    return render_template('about_system.html')

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

# ---------- Database Initialization ----------
with app.app_context():
    print("Initializing database...")
    db.create_all()
    ensure_runtime_tables()
    migrate_existing_database()
    
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_product_status ON product(status)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_product_status_price ON product(status, price)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_order_buyer ON "order"(buyer_id)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_order_buyer_status ON "order"(buyer_id, status)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_review_product ON review(product_id)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_productview_user ON product_view(user_id)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_product_category ON product(category)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_cart_user_id ON cart(user_id)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_cartitem_cart_id ON cart_item(cart_id)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_notification_user_read ON notification(user_id, read)'))
    db.session.commit()
    
    if not User.query.filter_by(role='chief_admin').first():
        chief = User(username='chief_admin', email='chief@market2farm.com', password_hash=generate_password_hash('Admin@2025'), role='chief_admin')
        db.session.add(chief)
        db.session.commit()
        print("Created chief_admin user (username: chief_admin, password: Admin@2025)")
    
    seed_inbuilt_marketplace()
    print("Database initialization complete!")

# -------------------------------------------------------------------
# FIXED: Main entry point with SSL disabled for development
# -------------------------------------------------------------------
if __name__ == '__main__':
    debug_mode = os.getenv('DEBUG', 'False').lower() == 'true'
    
    print("\n" + "=" * 70)
    print(" MARKET2FARM APPLICATION - DEVELOPMENT MODE")
    print("=" * 70)
    print(f" Debug Mode: {'ON' if debug_mode else 'OFF'}")
    print(f" HTTPS Enforcement: DISABLED (for local development)")
    print(f" Database: sqlite:///market2farm.db")
    print(f" Upload Folder: {app.config['UPLOAD_FOLDER']}")
    print("-" * 70)
    print(" Access the application at:")
    print("   → http://localhost:5000")
    print("   → http://127.0.0.1:5000")
    print("-" * 70)
    print(" Test Accounts:")
    print("   → Chief Admin: chief_admin / Admin@2025")
    print("   → Demo Farmer:  market2farm_farmer / Farmer@2025")
    print("   → Demo Buyer:   (Register a new account)")
    print("=" * 70)
    print(" Press CTRL+C to stop the server")
    print("=" * 70)
    print()
    
    try:
        app.run(host='127.0.0.1', port=5000, debug=debug_mode, use_reloader=False, threaded=True)
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            print("\n" + "!" * 70)
            print(" ERROR: Port 5000 is already in use!")
            print("!" * 70)
            print("\n Try these solutions:")
            print(" 1. Find and kill the process using port 5000:")
            print("    netstat -ano | findstr :5000")
            print("    taskkill /PID <PID> /F")
            print(" 2. Or try a different port by changing the code")
            print(" 3. Wait a few seconds and try again")
        else:
            print(f"\n ERROR: {e}")
    except KeyboardInterrupt:
        print("\n\n" + "=" * 70)
        print(" SERVER STOPPED - Goodbye!")
        print("=" * 70)
    except Exception as e:
        print(f"\n Unexpected error: {e}")
        import traceback
        traceback.print_exc()