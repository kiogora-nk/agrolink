"""
BioFarm Fruits - Complete Integrated Application
All features working together: Products, Orders, Contact, Training, CMS, Admin
"""

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta, timezone
import os
import secrets
import json
import base64
from io import BytesIO

import qrcode

try:
    from dotenv import load_dotenv
    load_dotenv()  # load Gmail creds / secrets from a local .env if present
except ImportError:
    pass

import crop_ai  # trained crop-disease classifier (local module, no heavy deps)
import notifications  # email + WhatsApp/SMS link helpers
import pdf_reports  # receipt / monthly-report PDF + CSV builders
import climate_service  # Open-Meteo climate checker

# ============================================================================
# APP INITIALIZATION
# ============================================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-this')
_database_url = os.environ.get('DATABASE_URL', 'sqlite:///biofarm.db')
if _database_url.startswith('postgres://'):
    # SQLAlchemy only accepts postgresql:// (Render/Heroku inject the legacy prefix).
    _database_url = _database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Company Settings
app.config['COMPANY_NAME'] = os.environ.get('COMPANY_NAME', 'BioFarm Fruits')
app.config['COMPANY_PHONE'] = os.environ.get('COMPANY_PHONE', '0746767123')
app.config['COMPANY_PHONE_ALT'] = os.environ.get('COMPANY_PHONE_ALT', '0745395981')
app.config['COMPANY_WHATSAPP'] = os.environ.get('COMPANY_WHATSAPP', '0746767123')
app.config['COMPANY_WHATSAPP_ALT'] = os.environ.get('COMPANY_WHATSAPP_ALT', '0716091772')
app.config['COMPANY_EMAIL'] = os.environ.get('COMPANY_EMAIL', 'biofreshf@gmail.com')
app.config['COMPANY_ADDRESS'] = os.environ.get('COMPANY_ADDRESS', 'Nairobi, Kenya')
app.config['COMPANY_TIKTOK'] = os.environ.get('COMPANY_TIKTOK', 'https://www.tiktok.com/@dragon_fruit_012')
app.config['COMPANY_YOUTUBE'] = os.environ.get('COMPANY_YOUTUBE', 'https://www.youtube.com/@bio_fresh_orchard')
app.config['COMPANY_FACEBOOK'] = os.environ.get('COMPANY_FACEBOOK', '#')
app.config['COMPANY_INSTAGRAM'] = os.environ.get('COMPANY_INSTAGRAM', '#')
app.config['COMPANY_X'] = os.environ.get('COMPANY_X', '#')
app.config['COMPANY_LINKEDIN'] = os.environ.get('COMPANY_LINKEDIN', '#')

# The chief admin who must always receive system receipts and monthly reports.
app.config['CHIEF_ADMIN_EMAIL'] = os.environ.get('CHIEF_ADMIN_EMAIL', 'kiogo951@gmail.com')

# Email (Gmail SMTP). Set MAIL_USERNAME / MAIL_PASSWORD (16-char Google App
# Password) in the environment or a .env file for REAL delivery. See .env.example.
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get(
    'MAIL_DEFAULT_SENDER', app.config['COMPANY_EMAIL'])

db = SQLAlchemy(app)
notifications.init_mail(app)
csrf = CSRFProtect(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# ----------------------------------------------------------------------------
# Security hardening: security headers (CSP, HSTS, nosniff...) + rate limits.
# ----------------------------------------------------------------------------
try:
    from flask_talisman import Talisman
    # CSP allows the Tailwind CDN and inline scripts/styles (templates embed
    # small inline scripts), plus remote product images.
    _csp = {
        'default-src': "'self'",
        'script-src': ["'self'", "'unsafe-inline'", 'https://cdn.tailwindcss.com'],
        'style-src': ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com',
                      'https://cdn.tailwindcss.com'],
        'font-src': ["'self'", 'https://fonts.gstatic.com', 'data:'],
        'img-src': ["'self'", 'data:', 'https:'],
        'connect-src': ["'self'", 'https://api.open-meteo.com'],
        'frame-ancestors': "'none'",
        'object-src': "'none'",
    }
    # Local dev runs over plain http (incl. LAN testing from a phone); only
    # enforce HTTPS in production.
    _is_prod = (os.environ.get('FLASK_ENV') == 'production'
                or os.environ.get('RENDER') == '1')
    Talisman(
        app,
        content_security_policy=_csp,
        force_https=_is_prod,
        strict_transport_security=_is_prod,
        session_cookie_secure=_is_prod,
    )
except ImportError:  # pragma: no cover - older environments without the dep
    pass

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=['500 per hour'],
        storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'),
    )
except ImportError:  # pragma: no cover
    limiter = None


def utcnow():
    """Timezone-aware UTC timestamp (replaces deprecated datetime.utcnow)."""
    return datetime.now(timezone.utc)


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_uploaded_image(file_storage):
    """Save an uploaded image and return its stored filename, or None."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        return None
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    ext = file_storage.filename.rsplit('.', 1)[1].lower()
    filename = f"{secrets.token_hex(16)}.{ext}"
    file_storage.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    return filename

# ============================================================================
# MODELS - Complete Database Schema
# ============================================================================

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    password_hash = db.Column(db.String(200))
    role = db.Column(db.String(20), default='customer')
    avatar = db.Column(db.String(200))
    bio = db.Column(db.Text)
    location = db.Column(db.String(120))
    verified = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    # Batch number granted by the chief admin once a chief admin has proven
    # themselves through usage — shown as an official branch/identity ID.
    batch_number = db.Column(db.String(20), unique=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    last_login = db.Column(db.DateTime)

    def is_admin(self):
        return self.role in ('admin', 'chief_admin')

    def is_chief_admin(self):
        return self.role == 'chief_admin'

    def can_manage_products(self):
        return self.role in ('admin', 'chief_admin', 'farmer')

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50))
    sub_category = db.Column(db.String(50))
    organic = db.Column(db.Boolean, default=False)
    image = db.Column(db.String(200))
    stock = db.Column(db.Integer, default=0)
    farmer_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.String(20), default='approved')
    featured = db.Column(db.Boolean, default=False)
    views = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    
    farmer = db.relationship('User', backref='products')

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    total_price = db.Column(db.Float)
    status = db.Column(db.String(20), default='pending')
    delivery_address = db.Column(db.Text)
    delivery_phone = db.Column(db.String(20))
    notes = db.Column(db.Text)
    receipt_number = db.Column(db.String(20), unique=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    delivered_at = db.Column(db.DateTime)
    
    user = db.relationship('User', backref='orders')
    product = db.relationship('Product', backref='orders')
    
    def generate_receipt_number(self):
        if not self.receipt_number:
            self.receipt_number = f"BIO-{utcnow().strftime('%Y%m')}-{self.id:04d}"
        return self.receipt_number

class ContactMessage(db.Model):
    __tablename__ = 'contact_messages'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20))
    subject = db.Column(db.String(200))
    message = db.Column(db.Text, nullable=False)
    read = db.Column(db.Boolean, default=False)
    replied = db.Column(db.Boolean, default=False)
    reply_message = db.Column(db.Text)
    replied_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)
    
    user = db.relationship('User', backref='messages')

class TrainingSession(db.Model):
    __tablename__ = 'training_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    session_type = db.Column(db.String(50))
    preferred_date = db.Column(db.DateTime)
    preferred_time = db.Column(db.String(20))
    message = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=utcnow)
    
    user = db.relationship('User', backref='training_sessions')

class Review(db.Model):
    __tablename__ = 'reviews'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    verified_purchase = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    
    product = db.relationship('Product', backref='reviews')
    user = db.relationship('User', backref='reviews')

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    title = db.Column(db.String(200))
    message = db.Column(db.Text)
    type = db.Column(db.String(50))
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    
    user = db.relationship('User', backref='notifications')

class DiseaseDetection(db.Model):
    """A crop disease diagnosis produced by the AI detector (crop_ai)."""
    __tablename__ = 'disease_detections'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    crop_type = db.Column(db.String(50))
    symptoms = db.Column(db.Text)
    diagnosis = db.Column(db.String(120))
    treatment = db.Column(db.Text)
    confidence = db.Column(db.Float)
    image = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=utcnow)

    user = db.relationship('User', backref='disease_detections')

class SiteContent(db.Model):
    """Editable site copy managed through the CMS (hero text, about, etc.)."""
    __tablename__ = 'site_content'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

class BlogPost(db.Model):
    """CMS blog / news articles."""
    __tablename__ = 'blog_posts'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), unique=True, nullable=False)
    body = db.Column(db.Text)
    image = db.Column(db.String(200))
    published = db.Column(db.Boolean, default=True)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    author = db.relationship('User', backref='posts')

class ErrorReport(db.Model):
    """A captured application error, surfaced to admins so they can resolve it.

    Each 500 generates one of these with a short human-friendly reference code
    that the user is shown. Admins see the list on the admin dashboard, can read
    the technical detail, and mark it resolved.
    """
    __tablename__ = 'error_reports'
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(20), unique=True, index=True)
    path = db.Column(db.String(300))
    method = db.Column(db.String(10))
    error_type = db.Column(db.String(120))
    detail = db.Column(db.Text)            # traceback / technical detail (admin only)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    user_note = db.Column(db.Text)         # optional "what were you doing?" from the user
    status = db.Column(db.String(20), default='open')  # open | resolved
    created_at = db.Column(db.DateTime, default=utcnow)
    resolved_at = db.Column(db.DateTime)
    resolved_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    user = db.relationship('User', foreign_keys=[user_id])
    resolver = db.relationship('User', foreign_keys=[resolved_by])

class PasswordResetRequest(db.Model):
    """An admin-assisted password reset request (the last-resort option).

    Raised only when a user can't reset via email or phone themselves. Admins
    see these in a queue, generate a reset link, and share it with the user
    over WhatsApp / call.
    """
    __tablename__ = 'password_reset_requests'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    identifier = db.Column(db.String(120))   # what the user typed (email/phone/username)
    contact_phone = db.Column(db.String(20))
    status = db.Column(db.String(20), default='open')  # open | handled
    created_at = db.Column(db.DateTime, default=utcnow)
    handled_at = db.Column(db.DateTime)
    handled_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    user = db.relationship('User', foreign_keys=[user_id])
    handler = db.relationship('User', foreign_keys=[handled_by])

# ============================================================================
# USER LOADER
# ============================================================================

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ============================================================================
# AUTHORIZATION DECORATORS
# ============================================================================

def admin_required(f):
    """Allow admins and chief admins only."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin():
            flash('Admin access required.', 'error')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return wrapper


def chief_admin_required(f):
    """Allow chief admins only."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_chief_admin():
            flash('Chief admin access required.', 'error')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return wrapper


def product_manager_required(f):
    """Allow farmers, admins and chief admins to manage products."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.can_manage_products():
            flash('You do not have permission to manage products.', 'error')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return wrapper


def get_content(key, default=''):
    """Fetch an editable content block by key."""
    item = SiteContent.query.filter_by(key=key).first()
    return item.value if item and item.value is not None else default


def set_content(key, value):
    item = SiteContent.query.filter_by(key=key).first()
    if item:
        item.value = value
    else:
        db.session.add(SiteContent(key=key, value=value))

# ============================================================================
# CONTEXT PROCESSOR - Makes global variables available in all templates
# ============================================================================

@app.context_processor
def inject_globals():
    return {
        'current_user': current_user,
        'company_name': app.config['COMPANY_NAME'],
        'company_phone': app.config['COMPANY_PHONE'],
        'company_phone_alt': app.config['COMPANY_PHONE_ALT'],
        'company_whatsapp': app.config['COMPANY_WHATSAPP'],
        'company_whatsapp_alt': app.config['COMPANY_WHATSAPP_ALT'],
        'company_email': app.config['COMPANY_EMAIL'],
        'company_address': app.config['COMPANY_ADDRESS'],
        'logo_file': _logo_filename(),
        'site_url': os.environ.get('SITE_URL', 'http://localhost:5000'),
        'youtube_url': app.config['COMPANY_YOUTUBE'],
        'instagram_url': app.config['COMPANY_INSTAGRAM'],
        'facebook_url': app.config['COMPANY_FACEBOOK'],
        'tiktok_url': app.config['COMPANY_TIKTOK'],
        'x_url': app.config['COMPANY_X'],
        'linkedin_url': app.config['COMPANY_LINKEDIN'],
        'whatsapp_url': notifications.whatsapp_link(app.config['COMPANY_WHATSAPP']),
        'whatsapp_url_alt': notifications.whatsapp_link(app.config['COMPANY_WHATSAPP_ALT']),
        'wa_link': notifications.whatsapp_link,
        'sms_link': notifications.sms_link,
        'tel_link': notifications.tel_link,
        'csrf_token': generate_csrf(),
        'content': get_content,
        'now': utcnow(),
        # Notification bell in the navbar: unread count + latest items.
        'unread_notifications': (
            Notification.query.filter_by(user_id=current_user.id, read=False).count()
            if current_user.is_authenticated else 0),
        'recent_notifications': (
            Notification.query.filter_by(user_id=current_user.id)
                .order_by(Notification.created_at.desc()).limit(5).all()
            if current_user.is_authenticated else []),
    }

# ============================================================================
# PUBLIC ROUTES
# ============================================================================

@app.route('/')
def home():
    """Homepage - Featured products and statistics"""
    featured_products = Product.query.filter_by(status='approved', featured=True).limit(8).all()
    latest_products = Product.query.filter_by(status='approved').order_by(Product.created_at.desc()).limit(8).all()
    fruits = Product.query.filter_by(status='approved', category='fruits').limit(8).all()
    seedlings = Product.query.filter_by(status='approved', category='seedlings').limit(8).all()
    reviews = Review.query.order_by(Review.created_at.desc()).limit(6).all()
    
    # index.html iterates over `products`; show featured first, fall back to latest
    display_products = featured_products or latest_products

    stats = {
        'products': Product.query.filter_by(status='approved').count(),
        'customers': User.query.filter_by(role='customer').count(),
        'farmers': User.query.filter_by(role='farmer').count(),
        'reviews': Review.query.count(),
        'orders': Order.query.count(),
        'training': TrainingSession.query.filter_by(status='completed').count()
    }

    return render_template('index.html',
                         products=display_products,
                         featured=featured_products,
                         latest=latest_products,
                         fruits=fruits,
                         seedlings=seedlings,
                         reviews=reviews,
                         stats=stats)

@app.route('/products')
def products():
    """Product listing page with filters"""
    category = request.args.get('category')
    search = request.args.get('search')
    
    query = Product.query.filter_by(status='approved')
    
    if category and category != 'all':
        query = query.filter_by(category=category)
    if search:
        query = query.filter(
            Product.name.ilike(f'%{search}%') | 
            Product.description.ilike(f'%{search}%')
        )
    
    products = query.order_by(Product.created_at.desc()).all()
    categories = db.session.query(Product.category).distinct().filter_by(status='approved').all()
    categories = [c[0] for c in categories if c[0]]
    
    return render_template('products.html', 
                         products=products, 
                         categories=categories,
                         selected_category=category,
                         search=search)

@app.route('/product/<int:id>')
def product_detail(id):
    """Product detail page with reviews"""
    product = Product.query.filter_by(id=id, status='approved').first_or_404()
    product.views += 1
    db.session.commit()
    
    reviews = Review.query.filter_by(product_id=id).order_by(Review.created_at.desc()).all()
    avg_rating = db.session.query(db.func.avg(Review.rating)).filter_by(product_id=id).scalar() or 0
    
    related = Product.query.filter_by(category=product.category, status='approved')\
                          .filter(Product.id != id).limit(4).all()
    
    return render_template('product_detail.html',
                         product=product,
                         reviews=reviews,
                         avg_rating=round(avg_rating, 1),
                         related=related)

@app.route('/product/<int:id>/review', methods=['POST'])
@login_required
def add_review(id):
    """Add or update a product review"""
    product = Product.query.get_or_404(id)
    
    rating = request.form.get('rating', type=int)
    comment = request.form.get('comment')
    
    if not rating or rating < 1 or rating > 5:
        flash('Please provide a valid rating (1-5).', 'error')
        return redirect(url_for('product_detail', id=id))
    
    existing = Review.query.filter_by(product_id=id, user_id=current_user.id).first()

    # A "verified purchase" reviewer has actually ordered this product
    # (any order that has not been cancelled counts).
    is_buyer = Order.query.filter(
        Order.user_id == current_user.id,
        Order.product_id == id,
        Order.status != 'cancelled',
    ).first() is not None

    if existing:
        existing.rating = rating
        existing.comment = comment
        existing.verified_purchase = is_buyer
        flash('Review updated!', 'success')
    else:
        review = Review(
            product_id=id,
            user_id=current_user.id,
            rating=rating,
            comment=comment,
            verified_purchase=is_buyer,
        )
        db.session.add(review)
        flash('Review added!', 'success')
    
    db.session.commit()
    return redirect(url_for('product_detail', id=id))

@app.route('/training', methods=['GET', 'POST'])
def training():
    """Training booking page"""
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        session_type = request.form.get('session_type')
        preferred_date = request.form.get('preferred_date')
        preferred_time = request.form.get('preferred_time')
        message = request.form.get('message')
        
        session = TrainingSession(
            user_id=current_user.id if current_user.is_authenticated else None,
            name=name,
            email=email,
            phone=phone,
            session_type=session_type,
            preferred_date=datetime.strptime(preferred_date, '%Y-%m-%d') if preferred_date else None,
            preferred_time=preferred_time,
            message=message
        )
        db.session.add(session)
        db.session.commit()
        
        flash('Training session booked! We will contact you within 24 hours.', 'success')
        return redirect(url_for('training'))
    
    return render_template('training.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    """Contact page - send messages to admin"""
    if request.method == 'POST':
        msg = ContactMessage(
            user_id=current_user.id if current_user.is_authenticated else None,
            name=request.form.get('name'),
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            subject=request.form.get('subject', 'General Inquiry'),
            message=request.form.get('message')
        )
        db.session.add(msg)
        db.session.commit()

        # Alert admins by email (and in-app) so nothing sits unread.
        _notify_admins_new_message(msg)

        flash('Your message has been sent! We will respond within 24 hours.', 'success')
        return redirect(url_for('contact'))
    
    return render_template('contact.html')

@app.route('/disease-detection', methods=['GET', 'POST'])
def disease_detection():
    """AI crop disease detection from a symptom description (image optional).

    Public: anyone can use it; results are logged against the user when signed
    in. Uses the trained text model (crop_ai.predict); if an image is uploaded
    and the optional vision API is configured, that is tried first.
    """
    if request.method == 'POST':
        crop_type = request.form.get('crop_type', 'general')
        symptoms = (request.form.get('symptoms') or '').strip()
        if not symptoms:
            flash('Please describe the symptoms you are seeing.', 'error')
            return redirect(url_for('disease_detection'))

        saved = save_uploaded_image(request.files.get('image'))

        result = None
        if saved:
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], saved)
            result = crop_ai.predict_from_image(image_path, crop_type)
        if not result:
            result = crop_ai.predict(symptoms, crop_type)

        record = DiseaseDetection(
            user_id=current_user.id if current_user.is_authenticated else None,
            crop_type=crop_type,
            symptoms=symptoms,
            diagnosis=result['disease'],
            treatment=result['treatment'],
            confidence=result.get('confidence') or 0.0,
            image=saved,
        )
        db.session.add(record)
        db.session.commit()

        return render_template(
            'disease_result.html',
            crop_type=crop_type,
            symptoms=symptoms,
            diagnosis=result['disease'],
            treatment=result['treatment'],
            confidence=result.get('confidence') or 0.0,
        )

    recent = []
    if current_user.is_authenticated:
        recent = (DiseaseDetection.query
                  .filter_by(user_id=current_user.id)
                  .order_by(DiseaseDetection.created_at.desc())
                  .limit(5).all())
    return render_template('disease_detection.html', recent=recent)

# ============================================================================
# CLIMATE CHECKER — live forecast + farming advice (Open-Meteo, no API key)
# ============================================================================

def _rate(limit):
    """Apply a Flask-Limiter decorator if the extension is available.

    Keeps route code identical whether or not flask_limiter is installed."""
    if limiter is None:
        return lambda f: f
    return limiter.limit(limit)


@app.route('/climate')
def climate():
    """Climate checker page (public)."""
    return render_template('climate.html')


@app.route('/api/climate')
@_rate('30 per minute')
def api_climate():
    """Weather + advice for ?lat=&lon= (geolocation) or ?location= (town name)."""
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    location_name = request.args.get('location')

    try:
        if lat and lon:
            place = {'name': 'Your location', 'country': ''}
            data = climate_service.get_forecast(lat, lon)
        elif location_name:
            place = climate_service.geocode(location_name)
            data = climate_service.get_forecast(place['latitude'], place['longitude'])
        else:
            return jsonify({'error': 'Provide either lat/lon or a location name.'}), 400
    except climate_service.ClimateError as exc:
        return jsonify({'error': str(exc)}), 502

    return jsonify({'place': place, **data})

# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/login', methods=['GET', 'POST'])
@_rate('10 per minute')
def login():
    """User login"""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter(
            (User.username == username) | (User.email == username)
        ).first()
        
        if user and check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash('Your account has been deactivated.', 'error')
                return render_template('login.html')
            
            login_user(user)
            user.last_login = utcnow()
            db.session.commit()
            flash(f'Welcome, {user.username}!', 'success')
            return redirect(request.args.get('next') or url_for('home'))
        
        flash('Invalid username or password.', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
@_rate('5 per minute')
def register():
    """User registration"""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')
        phone = request.form.get('phone')
        role = request.form.get('role', 'customer')
        # Never allow self-assigning privileged roles at registration
        if role not in ('customer', 'farmer'):
            role = 'customer'

        if not username or not email or not password:
            flash('Please fill in all required fields.', 'error')
            return render_template('register.html')
        
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('register.html')
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'error')
            return render_template('register.html')
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'error')
            return render_template('register.html')
        
        user = User(
            username=username,
            email=email,
            phone=phone,
            role=role,
            password_hash=generate_password_hash(password)
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash('Registration successful! Welcome to BioFarm Fruits.', 'success')
        return redirect(url_for('home'))
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

# ============================================================================
# PASSWORD RESET — email (primary), phone (secondary), admin (last resort)
# ============================================================================

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

RESET_TOKEN_MAX_AGE = 3600  # reset links valid for 1 hour


def _reset_serializer():
    return URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='password-reset')


def make_reset_token(user):
    return _reset_serializer().dumps({'uid': user.id})


def verify_reset_token(token, max_age=RESET_TOKEN_MAX_AGE):
    """Return the User for a valid, unexpired token, else None."""
    try:
        data = _reset_serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    return User.query.get(data.get('uid'))


def _mask_email(email):
    """me@example.com -> m***@example.com (so users recognise it without exposure)."""
    if not email or '@' not in email:
        return email or ''
    local, domain = email.split('@', 1)
    shown = local[0] if local else ''
    return f'{shown}***@{domain}'


def _send_reset_link(user):
    """Email a tokenized reset link to the user's address on file."""
    token = make_reset_token(user)
    link = url_for('reset_password', token=token, _external=True)
    return notifications.send_email(
        subject=f'Reset your {app.config["COMPANY_NAME"]} password',
        recipients=user.email,
        body=(f'Hi {user.username},\n\n'
              f'We received a request to reset your password. '
              f'Click the link below within 1 hour to set a new one:\n\n{link}\n\n'
              f'If you did not request this, you can safely ignore this email.\n\n'
              f'{app.config["COMPANY_NAME"]}'),
    )


@app.route('/forgot-password', methods=['GET', 'POST'])
@_rate('5 per minute')
def forgot_password():
    """Step 1: choose how to recover - by email or by phone."""
    if request.method == 'POST':
        method = request.form.get('method', 'email')
        identifier = (request.form.get('identifier') or '').strip()

        if method == 'phone':
            user = User.query.filter_by(phone=identifier).first() if identifier else None
        else:  # email (also accept username for convenience)
            user = User.query.filter(
                (User.email == identifier.lower()) | (User.username == identifier)
            ).first() if identifier else None

        # Always show the same confirmation so we never reveal who has an account.
        if user and user.email:
            _send_reset_link(user)
            if method == 'phone':
                flash(f'If that number is registered, a reset link has been sent to the '
                      f'email on file ({_mask_email(user.email)}).', 'success')
                return render_template('forgot_password.html',
                                       masked_email=_mask_email(user.email),
                                       show_admin_option=True, identifier=identifier)
            flash('If that account exists, a reset link is on its way to the email address.',
                  'success')
        else:
            # No usable email/account: steer them toward the admin last-resort path.
            flash('We could not send a reset link automatically. '
                  'If you no longer have access to your email, request admin help below.',
                  'info')
            return render_template('forgot_password.html',
                                   show_admin_option=True, identifier=identifier)
        return render_template('forgot_password.html', show_admin_option=True,
                               identifier=identifier)

    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
@_rate('10 per minute')
def reset_password(token):
    """Step 2: user sets a new password using a valid token."""
    user = verify_reset_token(token)
    if not user:
        flash('That reset link is invalid or has expired. Please request a new one.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm_password') or ''
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('reset_password.html')
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html')
        user.password_hash = generate_password_hash(password)
        db.session.commit()
        flash('Your password has been reset. You can now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html')


@app.route('/request-admin-reset', methods=['POST'])
def request_admin_reset():
    """Last resort: queue a reset request for an admin to handle manually."""
    identifier = (request.form.get('identifier') or '').strip()
    contact_phone = (request.form.get('contact_phone') or '').strip()
    if not identifier and not contact_phone:
        flash('Please tell us your username, email or phone so we can find your account.', 'error')
        return redirect(url_for('forgot_password'))

    user = User.query.filter(
        (User.email == identifier.lower()) | (User.username == identifier) |
        (User.phone == identifier) | (User.phone == contact_phone)
    ).first()

    req = PasswordResetRequest(
        user_id=user.id if user else None,
        identifier=identifier,
        contact_phone=contact_phone or (user.phone if user else None),
    )
    db.session.add(req)
    # Alert admins in-app so it lands in their queue immediately.
    for admin in User.query.filter(
        User.role.in_(['admin', 'chief_admin']), User.is_active.is_(True)
    ).all():
        db.session.add(Notification(
            user_id=admin.id,
            title='Password reset help requested',
            message=f'A user ({identifier or contact_phone}) needs a manual password reset.',
            type='reset_request',
        ))
    db.session.commit()
    flash('Your request has been sent to our team. We will contact you by phone or WhatsApp '
          'to help you reset your password.', 'success')
    return redirect(url_for('login'))

# ============================================================================
# USER DASHBOARD
# ============================================================================

@app.route('/dashboard')
@login_required
def dashboard():
    """User dashboard with statistics"""
    stats = {
        'orders': Order.query.filter_by(user_id=current_user.id).count(),
        'reviews': Review.query.filter_by(user_id=current_user.id).count(),
        'training': TrainingSession.query.filter_by(user_id=current_user.id).count(),
        'notifications': Notification.query.filter_by(user_id=current_user.id, read=False).count()
    }
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).limit(10).all()
    # Recent unread notifications (order updates, admin messages, resolved issues).
    alerts = (Notification.query.filter_by(user_id=current_user.id, read=False)
              .order_by(Notification.created_at.desc()).limit(5).all())
    # Any issues this user reported that are still open, so they know we're on it.
    my_open_issues = (ErrorReport.query.filter_by(user_id=current_user.id, status='open')
                      .order_by(ErrorReport.created_at.desc()).all())
    return render_template('dashboard.html', stats=stats, orders=orders,
                           alerts=alerts, my_open_issues=my_open_issues)

@app.route('/my-orders')
@login_required
def my_orders():
    """View user orders"""
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """View and edit the current user's profile"""
    if request.method == 'POST':
        current_user.phone = request.form.get('phone')
        current_user.location = request.form.get('location')
        current_user.bio = request.form.get('bio')
        avatar = request.files.get('avatar')
        saved = save_uploaded_image(avatar)
        if saved:
            current_user.avatar = url_for('uploaded_file', filename=saved)
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html', user=current_user)

@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Let a signed-in user change their own password."""
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    if not current_user.password_hash or \
            not check_password_hash(current_user.password_hash, current_password):
        flash('Your current password is incorrect.', 'error')
    elif len(new_password) < 6:
        flash('New password must be at least 6 characters long.', 'error')
    elif new_password != confirm_password:
        flash('New passwords do not match.', 'error')
    else:
        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash('Your password has been changed successfully.', 'success')
    return redirect(url_for('profile'))

@app.route('/u/<username>')
def public_profile(username):
    """Public, read-only profile for any user. Optional to fill in — an empty
    profile still renders cleanly. Shows the verified badge system-wide, the
    seller's approved products, and the reviews they've written."""
    user = User.query.filter_by(username=username).first_or_404()
    products = (Product.query
                .filter_by(farmer_id=user.id, status='approved')
                .order_by(Product.created_at.desc()).all())
    reviews = (Review.query.filter_by(user_id=user.id)
               .order_by(Review.created_at.desc()).limit(10).all())
    return render_template('public_profile.html',
                           profile_user=user,
                           products=products,
                           reviews=reviews)

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serve uploaded images."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/order/<int:id>/receipt')
@login_required
def view_receipt(id):
    """View order receipt"""
    order = Order.query.get_or_404(id)
    if order.user_id != current_user.id and not current_user.is_admin():
        flash('Unauthorized.', 'error')
        return redirect(url_for('home'))
    return render_template('receipt.html', order=order,
                           qr_data_uri=_receipt_qr_data_uri(order))

@app.route('/order/<int:id>/download-receipt')
@login_required
def download_receipt(id):
    """Download PDF receipt"""
    order = Order.query.get_or_404(id)
    if order.user_id != current_user.id and not current_user.is_admin():
        flash('Unauthorized.', 'error')
        return redirect(url_for('home'))

    buffer = pdf_reports.build_receipt_pdf(order, _company_dict())

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'receipt_{order.receipt_number or order.id}.pdf',
        mimetype='application/pdf'
    )


def _logo_filename():
    """Static-relative path of the brand logo, whichever filename it was
    uploaded under (img/logo.png, img/logo.jpg, ...)."""
    static = app.static_folder or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    for name in ('img/logo.png', 'img/logo.jpg', 'img/logo.png.jpg', 'img/logo.jpeg'):
        if os.path.exists(os.path.join(static, name)):
            return name
    return None


def _logo_path():
    """Absolute logo path for the PDF builders; None when there is no logo."""
    name = _logo_filename()
    if not name:
        return None
    return os.path.join(app.static_folder or 'static', name)


def _company_dict():
    """Company details as a plain dict for the report builders."""
    return {
        'name': app.config['COMPANY_NAME'],
        'address': app.config['COMPANY_ADDRESS'],
        'phone': app.config['COMPANY_PHONE'],
        'email': app.config['COMPANY_EMAIL'],
        'logo_path': _logo_path(),
    }


def _receipt_qr_data_uri(order):
    """Receipt QR as a data URI for the HTML receipt page (CSP allows data:).

    Encodes the same self-contained receipt text as the PDF QR."""
    try:
        company = _company_dict()
        qr = qrcode.QRCode(border=2, box_size=10)
        qr.add_data(pdf_reports.receipt_qr_data(order, company))
        qr.make(fit=True)
        buf = BytesIO()
        qr.make_image(fill_color='black', back_color='white').save(buf, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:  # noqa: BLE001 - a QR failure must not break the page
        return None


def _receipt_attachment(order):
    """The order's receipt PDF as an email attachment tuple."""
    try:
        pdf = pdf_reports.build_receipt_pdf(order, _company_dict())
        return (f'receipt_{order.receipt_number or order.id}.pdf',
                pdf.getvalue(), 'application/pdf')
    except Exception as exc:  # noqa: BLE001 - a PDF failure must not block email
        app.logger.error('Receipt PDF build failed for order %s: %s', order.id, exc)
        return None

@app.route('/place-order/<int:product_id>', methods=['POST'])
@login_required
@_rate('20 per minute')
def place_order(product_id):
    """Place an order"""
    product = Product.query.get_or_404(product_id)
    
    if product.stock <= 0:
        flash('This product is out of stock.', 'error')
        return redirect(url_for('product_detail', id=product_id))
    
    quantity = request.form.get('quantity', 1, type=int)
    if quantity > product.stock:
        flash(f'Only {product.stock} units available.', 'error')
        return redirect(url_for('product_detail', id=product_id))

    # Delivery contact details are mandatory so admins can reach the buyer
    # to arrange delivery.
    delivery_address = (request.form.get('delivery_address') or '').strip()
    delivery_phone = (request.form.get('delivery_phone') or '').strip()
    if not delivery_address:
        flash('Please provide a delivery address so we can deliver your order.', 'error')
        return redirect(url_for('product_detail', id=product_id))
    if not delivery_phone:
        flash('Please provide a delivery phone number so we can reach you about your order.', 'error')
        return redirect(url_for('product_detail', id=product_id))

    order = Order(
        user_id=current_user.id,
        product_id=product_id,
        quantity=quantity,
        total_price=product.price * quantity,
        delivery_address=delivery_address,
        delivery_phone=delivery_phone,
        notes=request.form.get('notes')
    )
    product.stock -= quantity
    db.session.add(order)
    db.session.flush()  # assign order.id before building the receipt number
    order.order_number = order.order_number or f"ORD-{order.id:05d}"
    order.generate_receipt_number()
    db.session.commit()

    # Notify admins in-app + by email, and email the customer a confirmation.
    _notify_new_order(order)

    flash('Order placed successfully! We have received it and will be in touch shortly.', 'success')
    return redirect(url_for('my_orders'))


def _admin_emails():
    """Email addresses of all active admins / chief admins. The configured
    chief-admin report address is always included so nothing is missed."""
    admins = User.query.filter(
        User.role.in_(['admin', 'chief_admin']), User.is_active.is_(True)
    ).all()
    emails = [a.email for a in admins if a.email]
    chief = app.config['CHIEF_ADMIN_EMAIL']
    if chief and chief not in emails:
        emails.append(chief)
    return emails


def _notify_new_order(order):
    """Create in-app notifications for admins and send order emails.

    The receipt PDF is attached to both the customer confirmation and the
    admin notification — every purchase gets a receipt by email."""
    for admin in User.query.filter(
        User.role.in_(['admin', 'chief_admin']), User.is_active.is_(True)
    ).all():
        db.session.add(Notification(
            user_id=admin.id,
            title='New order placed',
            message=f'{order.user.username} ordered {order.quantity} x {order.product.name} '
                    f'(KES {order.total_price:,.2f}).',
            type='order',
        ))
    db.session.commit()

    receipt = _receipt_attachment(order)
    attachments = [receipt] if receipt else []
    notifications.notify_admins_new_order(order, _admin_emails(),
                                          attachments=attachments)
    notifications.notify_customer_order_confirmation(order,
                                                     attachments=attachments)


def _notify_admins_new_message(msg):
    """Email all admins about a new contact-form message and create in-app
    notifications. Email fails soft when SMTP is not configured."""
    admins = User.query.filter(
        User.role.in_(['admin', 'chief_admin']), User.is_active.is_(True)
    ).all()
    for admin in admins:
        db.session.add(Notification(
            user_id=admin.id,
            title='New contact message',
            message=f'{msg.name or "Someone"} ({msg.email or msg.phone or "no contact"}) '
                    f'sent: {msg.subject or "General Inquiry"}',
            type='message',
        ))
    db.session.commit()

    subject = f'New contact message: {msg.subject or "General Inquiry"}'
    body = (
        f'A new message has arrived through the contact form.\n\n'
        f'From: {msg.name or "-"}\n'
        f'Email: {msg.email or "-"}\n'
        f'Phone: {msg.phone or "-"}\n'
        f'Account: {msg.user.username if msg.user else "guest"}\n\n'
        f'{msg.message}\n\n'
        f'Reply from the admin panel: /admin/message/{msg.id}/reply'
    )
    notifications.send_email(subject, _admin_emails(), body)

# ============================================================================
# PRODUCT MANAGEMENT (Farmers, Admins, Chief Admin)
# ============================================================================

PRODUCT_CATEGORIES = ['fruits', 'seedlings', 'vegetables', 'grains', 'dairy', 'livestock']


def _save_product_from_form(product, form, files, allow_status=False):
    """Populate a Product from submitted form data. Returns (ok, error)."""
    name = (form.get('name') or '').strip()
    price_raw = form.get('price')
    if not name:
        return False, 'Product name is required.'
    try:
        price = float(price_raw)
        if price < 0:
            raise ValueError
    except (TypeError, ValueError):
        return False, 'Please enter a valid price.'

    product.name = name
    product.description = (form.get('description') or '').strip()
    product.price = price
    product.category = form.get('category') or 'fruits'
    product.sub_category = (form.get('sub_category') or '').strip() or None
    product.organic = bool(form.get('organic'))
    try:
        product.stock = max(0, int(form.get('stock') or 0))
    except (TypeError, ValueError):
        product.stock = 0

    image_url = (form.get('image_url') or '').strip()
    saved = save_uploaded_image(files.get('image'))
    if saved:
        product.image = url_for('uploaded_file', filename=saved)
    elif image_url:
        product.image = image_url

    if allow_status:
        product.featured = bool(form.get('featured'))
        status = form.get('status')
        if status in ('pending', 'approved', 'rejected'):
            product.status = status
    return True, None


@app.route('/upload-product', methods=['GET', 'POST'])
@product_manager_required
def upload_product():
    """Farmers/admins submit a new product (price included)."""
    is_admin = current_user.is_admin()
    if request.method == 'POST':
        product = Product(farmer_id=current_user.id)
        # Admins get their listings auto-approved; farmer uploads await review
        product.status = 'approved' if is_admin else 'pending'
        ok, error = _save_product_from_form(product, request.form, request.files, allow_status=is_admin)
        if not ok:
            flash(error, 'error')
            return render_template('upload_product.html', categories=PRODUCT_CATEGORIES, is_admin=is_admin)
        db.session.add(product)
        db.session.commit()
        if is_admin:
            flash('Product published.', 'success')
            return redirect(url_for('admin_products'))
        flash('Product submitted for review. We will approve it shortly.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('upload_product.html', categories=PRODUCT_CATEGORIES, is_admin=is_admin)

# ============================================================================
# ADMIN ROUTES
# ============================================================================

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard with statistics"""
    stats = {
        'products': Product.query.count(),
        'pending_products': Product.query.filter_by(status='pending').count(),
        'approved_products': Product.query.filter_by(status='approved').count(),
        'orders': Order.query.count(),
        'users': User.query.count(),
        'messages': ContactMessage.query.filter_by(read=False).count(),
        'total_messages': ContactMessage.query.count(),
        'training': TrainingSession.query.filter_by(status='pending').count(),
        'training_sessions': TrainingSession.query.count(),
        'reviews': Review.query.count(),
        'posts': BlogPost.query.count(),
        'open_errors': ErrorReport.query.filter_by(status='open').count(),
        'reset_requests': PasswordResetRequest.query.filter_by(status='open').count()
    }

    messages = ContactMessage.query.order_by(ContactMessage.created_at.desc()).limit(10).all()

    # Orders: pending first, filled with most recent other statuses (limit 10).
    order_counts = {s: Order.query.filter_by(status=s).count()
                    for s in ORDER_STATUSES}
    recent_orders = Order.query.filter_by(status='pending')\
        .order_by(Order.created_at.desc()).limit(10).all()
    if len(recent_orders) < 10:
        exclude_ids = [o.id for o in recent_orders]
        others = Order.query.filter(Order.status != 'pending')\
            .order_by(Order.created_at.desc()).limit(10 - len(recent_orders)).all()
        recent_orders.extend(o for o in others if o.id not in exclude_ids)

    # Training requests: pending first, then confirmed (limit 10).
    training_counts = {s: TrainingSession.query.filter_by(status=s).count()
                       for s in ['pending', 'confirmed', 'completed']}
    recent_training = TrainingSession.query.filter(
        TrainingSession.status.in_(['pending', 'confirmed']))\
        .order_by(TrainingSession.created_at.desc()).limit(10).all()

    recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()

    return render_template('admin/dashboard.html',
                         stats=stats,
                         messages=messages,
                         recent_orders=recent_orders,
                         order_counts=order_counts,
                         recent_training=recent_training,
                         training_counts=training_counts,
                         recent_users=recent_users)

@app.route('/admin/messages')
@admin_required
def admin_messages():
    """View all contact messages"""
    messages = ContactMessage.query.order_by(ContactMessage.created_at.desc()).all()
    return render_template('admin/messages.html', messages=messages)

@app.route('/admin/message/<int:id>/reply', methods=['GET', 'POST'])
@admin_required
def reply_message(id):
    """Reply to a contact message"""
    message = ContactMessage.query.get_or_404(id)

    if request.method == 'POST':
        reply = request.form.get('reply')
        if not reply:
            flash('Please enter a reply.', 'error')
            return render_template('admin/reply_message.html', message=message)

        message.reply_message = reply
        message.replied = True
        message.read = True
        message.replied_at = utcnow()
        db.session.commit()

        # Actually deliver the reply to the sender: by email always, and
        # in-app when they have an account.
        subject = f'Re: {message.subject or "Your message to BioFarm Fruits"}'
        body = (
            f'Hi {message.name or "there"},\n\n'
            f'You wrote to BioFarm Fruits:\n'
            f'"{message.message}"\n\n'
            f'Our reply:\n'
            f'{reply}\n\n'
            f'Thank you for reaching out.\n'
            f'BioFarm Fruits'
        )
        notifications.send_email(subject, message.email, body)
        if message.user:
            db.session.add(Notification(
                user_id=message.user.id,
                title='Reply to your message',
                message=f'An admin replied to "{message.subject or "General Inquiry"}": {reply[:200]}',
                type='message',
            ))
            db.session.commit()

        flash('Reply sent successfully!', 'success')
        return redirect(url_for('admin_messages'))

    return render_template('admin/reply_message.html', message=message)

@app.route('/admin/message/<int:id>/read', methods=['POST'])
@admin_required
def mark_message_read(id):
    """Mark message as read"""
    message = ContactMessage.query.get_or_404(id)
    message.read = True
    db.session.commit()
    flash('Message marked as read.', 'info')
    return redirect(url_for('admin_messages'))

@app.route('/admin/message/<int:id>/delete', methods=['POST'])
@admin_required
def delete_message(id):
    """Delete a contact message from the admin inbox."""
    message = ContactMessage.query.get_or_404(id)
    name = message.name or 'Unknown sender'
    db.session.delete(message)
    db.session.commit()
    flash(f'Message from {name} deleted.', 'success')
    return redirect(url_for('admin_messages'))

@app.route('/admin/products')
@admin_required
def admin_products():
    """Manage products"""
    products = Product.query.order_by(Product.created_at.desc()).all()
    return render_template('admin/products.html', products=products)

@app.route('/admin/products/new', methods=['GET', 'POST'])
@admin_required
def admin_product_new():
    """Admin creates a product with price and image."""
    if request.method == 'POST':
        product = Product(farmer_id=current_user.id, status='approved')
        ok, error = _save_product_from_form(product, request.form, request.files, allow_status=True)
        if not ok:
            flash(error, 'error')
            return render_template('admin/product_form.html', product=None, categories=PRODUCT_CATEGORIES)
        db.session.add(product)
        db.session.commit()
        flash(f'Product "{product.name}" created.', 'success')
        return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html', product=None, categories=PRODUCT_CATEGORIES)

@app.route('/admin/product/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_product_edit(id):
    """Edit an existing product (name, price, stock, status...)."""
    product = Product.query.get_or_404(id)
    if request.method == 'POST':
        ok, error = _save_product_from_form(product, request.form, request.files, allow_status=True)
        if not ok:
            flash(error, 'error')
            return render_template('admin/product_form.html', product=product, categories=PRODUCT_CATEGORIES)
        db.session.commit()
        flash(f'Product "{product.name}" updated.', 'success')
        return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html', product=product, categories=PRODUCT_CATEGORIES)

@app.route('/admin/product/<int:id>/approve', methods=['POST'])
@app.route('/admin/approve/<int:id>', methods=['POST'])
@admin_required
def approve_product(id):
    """Approve a product"""
    product = Product.query.get_or_404(id)
    product.status = 'approved'
    db.session.commit()
    flash(f'Product "{product.name}" approved!', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/product/<int:id>/reject', methods=['POST'])
@app.route('/admin/reject/<int:id>', methods=['POST'])
@admin_required
def reject_product(id):
    """Reject a product"""
    product = Product.query.get_or_404(id)
    product.status = 'rejected'
    db.session.commit()
    flash(f'Product "{product.name}" rejected.', 'info')
    return redirect(url_for('admin_products'))

@app.route('/admin/product/<int:id>/delete', methods=['POST'])
@admin_required
def delete_product(id):
    """Delete a product"""
    product = Product.query.get_or_404(id)
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted.', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/orders')
@admin_required
def admin_orders():
    """Order management CMS - view and process all orders."""
    status = request.args.get('status')
    query = Order.query
    if status and status != 'all':
        query = query.filter_by(status=status)
    orders = query.order_by(Order.created_at.desc()).all()
    counts = {
        'all': Order.query.count(),
        'pending': Order.query.filter_by(status='pending').count(),
        'confirmed': Order.query.filter_by(status='confirmed').count(),
        'delivered': Order.query.filter_by(status='delivered').count(),
        'cancelled': Order.query.filter_by(status='cancelled').count(),
    }
    return render_template('admin/orders.html', orders=orders,
                           counts=counts, selected_status=status or 'all')


ORDER_STATUSES = ['pending', 'confirmed', 'delivered', 'cancelled']


@app.route('/admin/order/<int:id>/status', methods=['POST'])
@admin_required
def update_order_status(id):
    """Update an order's status and notify the customer by email + in-app."""
    order = Order.query.get_or_404(id)
    status = request.form.get('status')
    if status not in ORDER_STATUSES:
        flash('Invalid status.', 'error')
        return redirect(url_for('admin_orders'))

    order.status = status
    if status == 'delivered':
        order.delivered_at = utcnow()
    db.session.add(Notification(
        user_id=order.user_id,
        title='Order update',
        message=f'Your order {order.receipt_number or order.id} is now "{status}".',
        type='order',
    ))
    db.session.commit()

    if order.user and order.user.email:
        notifications.send_email(
            subject=f'Order {order.receipt_number or order.id} - {status}',
            recipients=order.user.email,
            body=(f'Hi {order.user.username},\n\n'
                  f'Your order {order.receipt_number or order.id} for '
                  f'{order.product.name} is now: {status}.\n\n'
                  f'{app.config["COMPANY_NAME"]}'),
        )
    flash(f'Order marked "{status}".', 'success')
    return redirect(request.referrer or url_for('admin_orders'))


@app.route('/admin/user/<int:id>/message', methods=['GET', 'POST'])
@admin_required
def send_user_message(id):
    """Admin sends a customer a message by email and/or via WhatsApp link."""
    user = User.query.get_or_404(id)
    if request.method == 'POST':
        subject = (request.form.get('subject') or 'Message from BioFarm Fruits').strip()
        message = (request.form.get('message') or '').strip()
        if not message:
            flash('Please enter a message.', 'error')
            return render_template('admin/send_user_message.html', user=user)

        # Always record it in-app so the customer sees it on their dashboard.
        db.session.add(Notification(
            user_id=user.id, title=subject, message=message, type='message'))
        db.session.commit()

        sent_email = False
        if request.form.get('send_email') and user.email:
            sent_email = notifications.send_email(subject, user.email,
                                                  f'{message}\n\n{app.config["COMPANY_NAME"]}')

        if request.form.get('send_whatsapp') and user.phone:
            # Hand back a ready-to-open WhatsApp link for the staff member.
            wa = notifications.whatsapp_link(user.phone, message)
            flash('Message saved. Click to send on WhatsApp: ' + wa
                  if wa else 'Message saved (no valid WhatsApp number).', 'info')
        else:
            flash('Message sent to {}{}.'.format(
                user.username, ' (email delivered)' if sent_email else ''), 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/send_user_message.html', user=user)


@app.route('/admin/notify', methods=['GET', 'POST'])
@admin_required
def send_notification():
    """Broadcast an in-app notification to one user or everyone."""
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        message = (request.form.get('message') or '').strip()
        title = (request.form.get('title') or 'Notification').strip()
        if not message:
            flash('Please enter a message.', 'error')
            return redirect(url_for('send_notification'))

        if user_id:
            targets = User.query.filter_by(id=int(user_id)).all()
        else:
            targets = User.query.filter_by(is_active=True).all()
        for u in targets:
            db.session.add(Notification(user_id=u.id, title=title,
                                        message=message, type='broadcast'))
        db.session.commit()
        flash(f'Notification sent to {len(targets)} user(s).', 'success')
        return redirect(url_for('admin_dashboard'))

    users = User.query.order_by(User.username).all()
    return render_template('admin/send_notification.html', users=users)


@app.route('/notifications')
@login_required
def notifications_page():
    """Customer's own notification inbox."""
    items = (Notification.query.filter_by(user_id=current_user.id)
             .order_by(Notification.created_at.desc()).all())
    return render_template('notifications.html', notifications=items)


@app.route('/notifications/<int:id>/read', methods=['POST'])
@login_required
def notification_mark_read(id):
    """Mark one of my notifications as read."""
    notif = Notification.query.get_or_404(id)
    if notif.user_id != current_user.id:
        abort(403)
    notif.read = True
    db.session.commit()
    flash('Notification marked as read.', 'info')
    return redirect(url_for('notifications_page'))


@app.route('/notifications/read-all', methods=['POST'])
@login_required
def notification_mark_all_read():
    """Mark all of my notifications as read."""
    Notification.query.filter_by(user_id=current_user.id, read=False).update(
        {'read': True})
    db.session.commit()
    flash('All notifications marked as read.', 'success')
    return redirect(url_for('notifications_page'))


@app.route('/notifications/<int:id>/delete', methods=['POST'])
@login_required
def notification_delete(id):
    """Delete one of my notifications."""
    notif = Notification.query.get_or_404(id)
    if notif.user_id != current_user.id:
        abort(403)
    db.session.delete(notif)
    db.session.commit()
    flash('Notification deleted.', 'success')
    return redirect(url_for('notifications_page'))


@app.route('/admin/reset-requests')
@admin_required
def admin_reset_requests():
    """Queue of users who need a manual (admin-assisted) password reset."""
    status = request.args.get('status', 'open')
    query = PasswordResetRequest.query
    if status in ('open', 'handled'):
        query = query.filter_by(status=status)
    requests_list = query.order_by(PasswordResetRequest.created_at.desc()).limit(200).all()
    counts = {
        'open': PasswordResetRequest.query.filter_by(status='open').count(),
        'handled': PasswordResetRequest.query.filter_by(status='handled').count(),
    }
    return render_template('admin/reset_requests.html', requests=requests_list,
                           counts=counts, selected_status=status)


@app.route('/admin/reset-request/<int:id>/link', methods=['POST'])
@admin_required
def admin_generate_reset_link(id):
    """Admin generates a one-hour reset link to share with the user via WhatsApp."""
    req = PasswordResetRequest.query.get_or_404(id)
    if not req.user:
        flash('No matching account was found for this request. '
              'Please contact the user to verify their details.', 'error')
        return redirect(url_for('admin_reset_requests'))

    token = make_reset_token(req.user)
    link = url_for('reset_password', token=token, _external=True)
    req.status = 'handled'
    req.handled_at = utcnow()
    req.handled_by = current_user.id
    db.session.commit()

    wa = notifications.whatsapp_link(
        req.contact_phone or req.user.phone,
        f'Hi {req.user.username}, here is your BioFarm Fruits password reset link '
        f'(valid 1 hour): {link}')
    flash(f'Reset link generated for {req.user.username}. Share it securely: {link}', 'success')
    if wa:
        flash('Send it on WhatsApp: ' + wa, 'info')
    return redirect(url_for('admin_reset_requests'))


@app.route('/admin/errors')
@admin_required
def admin_errors():
    """Error console - admins see reported errors and resolve them."""
    status = request.args.get('status', 'open')
    query = ErrorReport.query
    if status in ('open', 'resolved'):
        query = query.filter_by(status=status)
    reports = query.order_by(ErrorReport.created_at.desc()).limit(200).all()
    counts = {
        'open': ErrorReport.query.filter_by(status='open').count(),
        'resolved': ErrorReport.query.filter_by(status='resolved').count(),
    }
    return render_template('admin/errors.html', reports=reports,
                           counts=counts, selected_status=status)


@app.route('/admin/error/<int:id>/resolve', methods=['POST'])
@admin_required
def resolve_error(id):
    """Mark an error resolved and notify the affected user if there is one."""
    report = ErrorReport.query.get_or_404(id)
    report.status = 'resolved'
    report.resolved_at = utcnow()
    report.resolved_by = current_user.id
    if report.user_id:
        db.session.add(Notification(
            user_id=report.user_id,
            title='Issue resolved',
            message=f'The problem you hit (ref {report.reference}) has been resolved. '
                    f'Please try again - thank you for your patience.',
            type='resolved',
        ))
    db.session.commit()
    flash(f'Error {report.reference} marked resolved.', 'success')
    return redirect(url_for('admin_errors'))


@app.route('/report-issue/<reference>', methods=['POST'])
def report_issue(reference):
    """Let a user attach a note to the error they just hit."""
    report = ErrorReport.query.filter_by(reference=reference).first()
    if report:
        report.user_note = (request.form.get('note') or '').strip()[:1000]
        if not report.user_id and current_user.is_authenticated:
            report.user_id = current_user.id
        db.session.commit()
        flash('Thank you - our team has been notified and will look into it.', 'success')
    else:
        flash('Thank you for your feedback.', 'info')
    return redirect(url_for('dashboard') if current_user.is_authenticated else url_for('home'))


@app.route('/admin/training')
@admin_required
def admin_training():
    """Manage training sessions"""
    sessions = TrainingSession.query.order_by(TrainingSession.created_at.desc()).all()
    return render_template('admin/training.html', sessions=sessions)

@app.route('/admin/training/<int:id>/update', methods=['POST'])
@admin_required
def update_training(id):
    """Update training status"""
    session_obj = TrainingSession.query.get_or_404(id)
    session_obj.status = request.form.get('status')
    db.session.commit()
    flash('Training status updated.', 'success')
    return redirect(url_for('admin_training'))

@app.route('/admin/users')
@admin_required
def admin_users():
    """Manage users"""
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/user/<int:id>/suspend', methods=['POST'])
@admin_required
def suspend_user(id):
    """Suspend or unsuspend a user"""
    user = User.query.get_or_404(id)
    if user.is_chief_admin():
        flash('You cannot suspend a chief admin.', 'error')
        return redirect(url_for('admin_users'))
    user.is_active = not user.is_active
    db.session.commit()
    status = 'suspended' if not user.is_active else 'unsuspended'
    flash(f'User {status}.', 'info')
    return redirect(url_for('admin_users'))

# ============================================================================
# CHIEF ADMIN — full control + role management
# ============================================================================

@app.route('/chief-admin/dashboard')
@chief_admin_required
def chief_admin_dashboard():
    """Chief admin control center."""
    stats = {
        'products': Product.query.count(),
        'pending_products': Product.query.filter_by(status='pending').count(),
        'orders': Order.query.count(),
        'revenue': db.session.query(db.func.coalesce(db.func.sum(Order.total_price), 0)).scalar() or 0,
        'users': User.query.count(),
        'admins': User.query.filter(User.role.in_(['admin', 'chief_admin'])).count(),
        'farmers': User.query.filter_by(role='farmer').count(),
        'messages': ContactMessage.query.filter_by(read=False).count(),
        'training': TrainingSession.query.filter_by(status='pending').count(),
        'posts': BlogPost.query.count(),
        'reviews': Review.query.count(),
    }
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(8).all()
    recent_users = User.query.order_by(User.created_at.desc()).limit(8).all()
    return render_template('chief_admin/dashboard.html',
                           stats=stats,
                           recent_orders=recent_orders,
                           recent_users=recent_users)

@app.route('/chief-admin/user/<int:id>/role', methods=['POST'])
@chief_admin_required
def set_user_role(id):
    """Chief admin promotes/demotes a user."""
    user = User.query.get_or_404(id)
    role = request.form.get('role')
    if role not in ('customer', 'farmer', 'admin', 'chief_admin'):
        flash('Invalid role.', 'error')
        return redirect(url_for('admin_users'))
    if user.id == current_user.id and role != 'chief_admin':
        flash('You cannot remove your own chief admin role.', 'error')
        return redirect(url_for('admin_users'))
    user.role = role
    db.session.commit()
    flash(f'{user.username} is now {role}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/chief-admin/user/<int:id>/verify', methods=['POST'])
@chief_admin_required
def toggle_verified(id):
    """Chief admin grants or revokes a user's verified badge. The badge is a
    trust signal shown system-wide (profiles, product cards, reviews)."""
    user = User.query.get_or_404(id)
    user.verified = not bool(user.verified)
    db.session.commit()
    if user.verified:
        # Let the user know they've been recognised.
        db.session.add(Notification(
            user_id=user.id,
            title='You are now a verified member',
            message='A chief admin has verified your account. A verified badge '
                    'now appears on your profile and listings.',
            type='verification',
        ))
        db.session.commit()
        notifications.send_email(
            subject=f'You are now verified on {app.config["COMPANY_NAME"]}',
            recipients=user.email,
            body=(f'Hi {user.username},\n\nYour account has been verified on '
                  f'{app.config["COMPANY_NAME"]}. A verified badge now appears '
                  f'across the site on your profile and listings.\n\nThank you '
                  f'for being a trusted member of our community.'),
        )
    flash(f'{user.username} is now {"verified" if user.verified else "unverified"}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/chief-admin/user/<int:id>/batch-number', methods=['POST'])
@chief_admin_required
def set_batch_number(id):
    """Chief admin grants, changes or revokes a user's batch number. A batch
    number is earned: the chief admin verifies someone as chief admin first,
    then — after trust built through real usage of the system — assigns them
    an official batch number. It is shown on the user's public profile as
    their official BioFarm Fruits identity ID."""
    user = User.query.get_or_404(id)
    batch = (request.form.get('batch_number') or '').strip().upper()
    if user.id == current_user.id and not batch:
        flash('You cannot remove your own batch number.', 'error')
        return redirect(url_for('admin_users'))
    if batch:
        existing = User.query.filter(User.batch_number == batch, User.id != user.id).first()
        if existing:
            flash(f'Batch number {batch} is already held by {existing.username}.', 'error')
            return redirect(url_for('admin_users'))
        if not user.is_chief_admin():
            flash('Batch numbers are only granted to chief admins.', 'error')
            return redirect(url_for('admin_users'))
    user.batch_number = batch or None
    db.session.commit()
    if batch:
        db.session.add(Notification(
            user_id=user.id,
            title='You have been granted a batch number',
            message=f'Your official batch number is {batch}. It now appears '
                    'on your profile as your BioFarm Fruits identity.',
            type='verification',
        ))
        db.session.commit()
        notifications.send_email(
            subject=f'Your batch number on {app.config["COMPANY_NAME"]}',
            recipients=user.email,
            body=(f'Hi {user.username},\n\n'
                  f'You have been granted the official batch number {batch} on '
                  f'{app.config["COMPANY_NAME"]}. It now appears on your profile '
                  f'as your official identity.\n\nThank you for your service to '
                  f'our community.'),
        )
        flash(f'{user.username} was granted batch number {batch}.', 'success')
    else:
        flash(f'Batch number removed from {user.username}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/chief-admin/create-admin', methods=['GET', 'POST'])
@chief_admin_required
def create_admin():
    """Chief admin creates a new admin/staff account and assigns its role."""
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        phone = (request.form.get('phone') or '').strip()
        password = request.form.get('password') or ''
        role = request.form.get('role', 'admin')

        if role not in ('admin', 'chief_admin', 'farmer'):
            role = 'admin'
        if not username or not email or not password:
            flash('Username, email and password are required.', 'error')
            return render_template('chief_admin/create_admin.html')
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('chief_admin/create_admin.html')
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'error')
            return render_template('chief_admin/create_admin.html')
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'error')
            return render_template('chief_admin/create_admin.html')

        staff = User(
            username=username,
            email=email,
            phone=phone,
            role=role,
            verified=True,
            password_hash=generate_password_hash(password),
        )
        db.session.add(staff)
        db.session.commit()

        # Email the new staff member their access (fails soft if SMTP is off).
        notifications.send_email(
            subject=f'You are now a {role.replace("_", " ")} on {app.config["COMPANY_NAME"]}',
            recipients=email,
            body=(f'Hi {username},\n\n'
                  f'A {role.replace("_", " ")} account has been created for you on '
                  f'{app.config["COMPANY_NAME"]}.\n\n'
                  f'Username: {username}\n'
                  f'Sign in at {os.environ.get("SITE_URL", "http://localhost:5000")}/login\n\n'
                  f'Please change your password after your first login.'),
        )
        flash(f'{role.replace("_", " ").title()} account "{username}" created.', 'success')
        return redirect(url_for('admin_users'))
    return render_template('chief_admin/create_admin.html')

# ============================================================================
# MONTHLY REPORTS — customer statements + system report to the chief admin
# ============================================================================

def _previous_month_range(now=None):
    """(start, end, 'Month YYYY') for the calendar month before `now`."""
    now = now or utcnow()
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = first_of_this_month - timedelta(days=1)
    end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, end, start.strftime('%B %Y')


def send_monthly_reports(manual=False):
    """Email last month's statements: every customer with orders gets a
    personal PDF; the chief admin gets the full system report (PDF + CSV).

    Runs on a schedule (1st of the month) and on demand from the admin panel.
    Returns a short summary string for logging / flash messages."""
    from sqlalchemy import func as sa_func

    start, end, month_label = _previous_month_range()
    orders = (Order.query.filter(Order.created_at >= start, Order.created_at <= end)
              .order_by(Order.created_at.asc()).all())
    revenue = sum(o.total_price or 0 for o in orders)

    # Per-product totals for "top product".
    top_product = None
    if orders:
        top_row = (db.session.query(Product.name, sa_func.sum(Order.total_price).label('total'))
                   .join(Order, Order.product_id == Product.id)
                   .filter(Order.created_at >= start, Order.created_at <= end)
                   .group_by(Product.name)
                   .order_by(sa_func.sum(Order.total_price).desc()).first())
        top_product = f'{top_row[0]} (KES {top_row[1]:,.2f})' if top_row else None

    stats = {
        'orders': len(orders),
        'revenue': revenue,
        'delivered': sum(1 for o in orders if o.status == 'delivered'),
        'cancelled': sum(1 for o in orders if o.status == 'cancelled'),
        'new_users': User.query.filter(User.created_at >= start,
                                       User.created_at <= end).count(),
        'customers_with_orders': len({o.user_id for o in orders}),
        'top_product': top_product,
    }
    company = _company_dict()

    # 1. Personal statement to each customer who ordered.
    by_user = {}
    for o in orders:
        by_user.setdefault(o.user_id, []).append(o)
    customers_mailed = 0
    for user_orders in by_user.values():
        user = user_orders[0].user
        if not user or not user.email:
            continue
        total = sum(o.total_price or 0 for o in user_orders)
        try:
            pdf = pdf_reports.build_customer_monthly_pdf(user, user_orders,
                                                         month_label, company)
            sent = notifications.send_email(
                subject=f'Your {app.config["COMPANY_NAME"]} statement - {month_label}',
                recipients=user.email,
                body=(f'Hi {user.username},\n\n'
                      f'Thank you for shopping with us in {month_label}!\n'
                      f'You placed {len(user_orders)} order(s) totalling '
                      f'KES {total:,.2f}.\n\n'
                      f'Your monthly statement is attached (PDF).\n\n'
                      f'{app.config["COMPANY_NAME"]}'),
                attachments=[(f'statement_{month_label.replace(" ", "_").lower()}.pdf',
                              pdf.getvalue(), 'application/pdf')],
            )
            if sent:
                customers_mailed += 1
        except Exception as exc:  # noqa: BLE001 - one bad statement must not stop the rest
            app.logger.error('Monthly statement failed for user %s: %s',
                             user.id, exc)

    # 2. Full system report to the chief admin (PDF + spreadsheet).
    system_sent = False
    try:
        pdf = pdf_reports.build_system_monthly_pdf(stats, orders, month_label, company)
        csv_buf = pdf_reports.build_system_monthly_csv(orders, stats, month_label)
        slug = month_label.replace(' ', '_').lower()
        system_sent = notifications.send_email(
            subject=f'{app.config["COMPANY_NAME"]} system report - {month_label}',
            recipients=app.config['CHIEF_ADMIN_EMAIL'],
            body=(f'Monthly system report for {month_label}.\n\n'
                  f'Orders: {stats["orders"]}\n'
                  f'Revenue: KES {revenue:,.2f}\n'
                  f'New users: {stats["new_users"]}\n\n'
                  f'The full report is attached as PDF and spreadsheet (CSV).'),
            attachments=[(f'system_report_{slug}.pdf', pdf.getvalue(), 'application/pdf'),
                         (f'system_report_{slug}.csv', csv_buf.getvalue(), 'text/csv')],
        )
    except Exception as exc:  # noqa: BLE001
        app.logger.error('System monthly report failed: %s', exc)

    summary = (f'Monthly reports for {month_label}: {len(orders)} order(s), '
               f'KES {revenue:,.2f} revenue. {customers_mailed} customer statement(s) '
               f'emailed; system report to {app.config["CHIEF_ADMIN_EMAIL"]}: '
               f'{"sent" if system_sent else "not sent (SMTP off or failed)"}.')
    app.logger.info(summary)
    if manual:
        print(summary)
    return summary


_scheduler_started = False


def start_scheduler():
    """Start the monthly-report background scheduler (once per process).

    A lock file stops duplicate schedulers when gunicorn runs several
    workers; DISABLE_SCHEDULER=1 in the environment opts out entirely
    (useful in development)."""
    global _scheduler_started
    if _scheduler_started or os.environ.get('DISABLE_SCHEDULER') == '1':
        return
    _scheduler_started = True

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print('APScheduler not installed - monthly report emails disabled. '
              'Run: pip install APScheduler')
        return

    lock_path = os.path.join(app.instance_path, 'report_scheduler.lock')
    try:
        os.makedirs(app.instance_path, exist_ok=True)
        # Steal a stale lock (left behind by a crashed process over an hour ago).
        if os.path.exists(lock_path):
            import time as _time
            if _time.time() - os.path.getmtime(lock_path) > 3600:
                os.remove(lock_path)
        # O_CREAT | O_EXCL atomically claims the lock; failure means another
        # worker already runs the scheduler.
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode())
        os.close(lock_fd)
    except OSError:
        return  # another worker owns the lock

    scheduler = BackgroundScheduler(daemon=True)

    def _run_monthly_reports():
        with app.app_context():
            send_monthly_reports()

    # 1st of each month at 07:23 local server time (off-peak, off the :00 mark).
    scheduler.add_job(
        func=_run_monthly_reports,
        trigger='cron', day=1, hour=7, minute=23,
        id='monthly_reports', replace_existing=True,
    )
    scheduler.start()
    print('Monthly report scheduler started (1st of month, 07:23).')


@app.route('/admin/reports/send-now', methods=['POST'])
@chief_admin_required
def admin_send_reports_now():
    """Chief admin trigger: run the monthly report job immediately."""
    summary = send_monthly_reports(manual=True)
    flash(summary, 'info')
    return redirect(url_for('chief_admin_dashboard'))


@app.route('/admin/reports/preview')
@chief_admin_required
def admin_preview_report():
    """Download last month's system report PDF right from the browser."""
    start, end, month_label = _previous_month_range()
    orders = (Order.query.filter(Order.created_at >= start, Order.created_at <= end)
              .order_by(Order.created_at.asc()).all())
    revenue = sum(o.total_price or 0 for o in orders)
    stats = {
        'orders': len(orders), 'revenue': revenue,
        'delivered': sum(1 for o in orders if o.status == 'delivered'),
        'cancelled': sum(1 for o in orders if o.status == 'cancelled'),
        'new_users': User.query.filter(User.created_at >= start,
                                       User.created_at <= end).count(),
        'customers_with_orders': len({o.user_id for o in orders}),
        'top_product': None,
    }
    buffer = pdf_reports.build_system_monthly_pdf(stats, orders, month_label,
                                                  _company_dict())
    return send_file(buffer, as_attachment=True,
                     download_name=f'system_report_{month_label.replace(" ", "_").lower()}.pdf',
                     mimetype='application/pdf')

# ============================================================================
# CMS — editable site content
# ============================================================================

CONTENT_FIELDS = [
    ('hero_tagline', 'Hero tagline', 'text'),
    ('hero_title', 'Hero title', 'text'),
    ('hero_subtitle', 'Hero subtitle', 'textarea'),
    ('about_title', 'About title', 'text'),
    ('about_body', 'About body', 'textarea'),
    ('contact_note', 'Contact note', 'textarea'),
]

@app.route('/admin/cms', methods=['GET', 'POST'])
@admin_required
def admin_cms():
    """Edit site content blocks shown on public pages."""
    if request.method == 'POST':
        for key, _label, _type in CONTENT_FIELDS:
            set_content(key, (request.form.get(key) or '').strip())
        db.session.commit()
        flash('Site content updated.', 'success')
        return redirect(url_for('admin_cms'))
    values = {key: get_content(key) for key, _l, _t in CONTENT_FIELDS}
    return render_template('admin/cms.html', fields=CONTENT_FIELDS, values=values)

# ============================================================================
# CMS — blog / news
# ============================================================================

def _slugify(text):
    base = ''.join(c if c.isalnum() else '-' for c in (text or '').lower()).strip('-')
    while '--' in base:
        base = base.replace('--', '-')
    base = base or 'post'
    slug = base
    i = 2
    while BlogPost.query.filter_by(slug=slug).first():
        slug = f'{base}-{i}'
        i += 1
    return slug

@app.route('/blog')
def blog():
    """Public blog listing."""
    posts = BlogPost.query.filter_by(published=True).order_by(BlogPost.created_at.desc()).all()
    return render_template('blog.html', posts=posts)

@app.route('/blog/<slug>')
def blog_post(slug):
    """Public single blog post."""
    post = BlogPost.query.filter_by(slug=slug, published=True).first_or_404()
    return render_template('blog_post.html', post=post)

@app.route('/admin/blog')
@admin_required
def admin_blog():
    posts = BlogPost.query.order_by(BlogPost.created_at.desc()).all()
    return render_template('admin/blog_list.html', posts=posts)

@app.route('/admin/blog/new', methods=['GET', 'POST'])
@admin_required
def admin_blog_new():
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        if not title:
            flash('Title is required.', 'error')
            return render_template('admin/blog_form.html', post=None)
        post = BlogPost(
            title=title,
            slug=_slugify(title),
            body=request.form.get('body'),
            published=bool(request.form.get('published')),
            author_id=current_user.id,
        )
        saved = save_uploaded_image(request.files.get('image'))
        if saved:
            post.image = url_for('uploaded_file', filename=saved)
        elif request.form.get('image_url'):
            post.image = request.form.get('image_url').strip()
        db.session.add(post)
        db.session.commit()
        flash('Post created.', 'success')
        return redirect(url_for('admin_blog'))
    return render_template('admin/blog_form.html', post=None)

@app.route('/admin/blog/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_blog_edit(id):
    post = BlogPost.query.get_or_404(id)
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        if not title:
            flash('Title is required.', 'error')
            return render_template('admin/blog_form.html', post=post)
        post.title = title
        post.body = request.form.get('body')
        post.published = bool(request.form.get('published'))
        saved = save_uploaded_image(request.files.get('image'))
        if saved:
            post.image = url_for('uploaded_file', filename=saved)
        elif request.form.get('image_url'):
            post.image = request.form.get('image_url').strip()
        db.session.commit()
        flash('Post updated.', 'success')
        return redirect(url_for('admin_blog'))
    return render_template('admin/blog_form.html', post=post)

@app.route('/admin/blog/<int:id>/delete', methods=['POST'])
@admin_required
def admin_blog_delete(id):
    post = BlogPost.query.get_or_404(id)
    db.session.delete(post)
    db.session.commit()
    flash('Post deleted.', 'success')
    return redirect(url_for('admin_blog'))

# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


def _log_error_report(exc):
    """Persist an error so admins can see and resolve it. Returns a reference
    code to show the user, or None if logging itself failed."""
    import traceback
    reference = f'ERR-{utcnow().strftime("%Y%m%d")}-{secrets.token_hex(3).upper()}'
    try:
        db.session.rollback()  # clear any half-finished transaction from the crash
        report = ErrorReport(
            reference=reference,
            path=request.path,
            method=request.method,
            error_type=type(exc).__name__,
            detail=''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))[:8000],
            user_id=current_user.id if current_user.is_authenticated else None,
        )
        db.session.add(report)
        db.session.commit()

        # Notify admins in-app so it shows up without them hunting for it.
        for admin in User.query.filter(
            User.role.in_(['admin', 'chief_admin']), User.is_active.is_(True)
        ).all():
            db.session.add(Notification(
                user_id=admin.id,
                title='New error to resolve',
                message=f'{type(exc).__name__} on {request.path} (ref {reference}).',
                type='error',
            ))
        db.session.commit()
        return reference
    except Exception as log_exc:  # noqa: BLE001 - never let logging mask the error
        db.session.rollback()
        app.logger.error('Failed to persist error report: %s', log_exc)
        return None


@app.errorhandler(500)
@app.errorhandler(Exception)
def server_error(e):
    from werkzeug.exceptions import HTTPException
    # Let normal HTTP errors (404, 403, ...) use their own handlers.
    if isinstance(e, HTTPException) and e.code != 500:
        return e
    app.logger.exception('Unhandled exception')
    reference = _log_error_report(e)
    return render_template('500.html', reference=reference), 500

# ============================================================================
# SEED DATA
# ============================================================================

def seed_data():
    """Seed database with initial data"""
    with app.app_context():
        # Create admin
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(
                username='admin',
                email='admin@biofarmfruits.co.ke',
                password_hash=generate_password_hash('Admin@2025'),
                role='chief_admin',
                verified=True
            )
            db.session.add(admin)
            db.session.commit()
            print('Admin created: admin / Admin@2025')

        # Create farmer
        farmer = User.query.filter_by(username='biofarm_farmer').first()
        if not farmer:
            farmer = User(
                username='biofarm_farmer',
                email='farm@biofarmfruits.co.ke',
                password_hash=generate_password_hash('Farmer@2025'),
                role='farmer',
                verified=True
            )
            db.session.add(farmer)
            db.session.commit()
            print('Farmer created')

        # System review account — the "first few" reviews are built by the
        # system so products never launch with an empty reviews section.
        team = User.query.filter_by(username='biofarm_team').first()
        if not team:
            team = User(
                username='biofarm_team',
                email='team@biofarmfruits.co.ke',
                password_hash=generate_password_hash(secrets.token_hex(16)),  # no human login
                role='customer',
                verified=True,
                bio='Official BioFarm Fruits team account. We share early hands-on '
                    'impressions of our produce and seedlings.',
                location='Nairobi, Kenya',
            )
            db.session.add(team)
            db.session.commit()
            print('System review account created')
        
        # Seed products
        if Product.query.count() == 0:
            products = [
                ('Dragon Fruit Red', 'Sweet red dragon fruit with high antioxidants. Perfect for fresh eating.', 680, 'fruits', True, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 50, True),
                ('Dragon Fruit White', 'Crisp white dragon fruit with mild sweet flavor. Great for salads.', 620, 'fruits', True, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 45, True),
                ('Hass Avocado Seedlings', 'Premium Hass avocado seedlings. Disease-free and high-yield variety.', 300, 'seedlings', False, 'https://images.unsplash.com/photo-1523049673857-eb18f1d7b578?w=400', 100, True),
                ('Dragon Fruit Seedlings', 'Healthy dragon fruit cuttings ready for planting.', 200, 'seedlings', False, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 150, True),
                ('Hass Avocado Fruit', 'Creamy Hass avocados. Rich in healthy fats.', 180, 'fruits', True, 'https://images.unsplash.com/photo-1601039641847-7857b994d704?w=400', 30, False),
                ('Fuerte Avocado Fruit', 'Smooth green Fuerte avocados. Great for restaurants.', 150, 'fruits', False, 'https://images.unsplash.com/photo-1590431306482-f700ee050c59?w=400', 25, False),
                ('Purple Dragon Fruit', 'Purple dragon fruit variety with vivid flesh.', 720, 'fruits', True, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 20, False),
                ('Soursop', 'Fresh soursop fruits for juice processors and wellness markets.', 260, 'fruits', True, 'https://images.unsplash.com/photo-1619566636858-adf3ef46400b?w=400', 15, False),
            ]
            
            for name, desc, price, category, organic, image, stock, featured in products:
                product = Product(
                    name=name,
                    description=desc,
                    price=price,
                    category=category,
                    organic=organic,
                    image=image,
                    stock=stock,
                    farmer_id=farmer.id,
                    status='approved',
                    featured=featured
                )
                db.session.add(product)
            db.session.commit()
            print('Products seeded!')

        # Seed the first few system-built reviews (only when none exist yet).
        if Review.query.count() == 0:
            team = User.query.filter_by(username='biofarm_team').first()
            sample_reviews = [
                ('Dragon Fruit Red', 5, 'Deep red flesh, sweetness was spot on, and it '
                 'arrived in perfect condition. Our kids now ask for it by name.'),
                ('Dragon Fruit Red', 4, 'Great quality fruit. A little pricier than the '
                 'market but you can taste the difference.'),
                ('Dragon Fruit Seedlings', 5, 'The cuttings rooted quickly and are thriving '
                 'six weeks in. Clear planting instructions came along too.'),
                ('Hass Avocado Seedlings', 5, 'Healthy, disease-free seedlings. Strong stems '
                 'and good root ball on every single one.'),
                ('Hass Avocado Fruit', 4, 'Creamy and ripened perfectly. Ordering again '
                 'next month.'),
                ('Soursop', 5, 'Fresh soursop is hard to find locally — this was juicy '
                 'and made wonderful juice.'),
            ]
            added = 0
            for product_name, rating, comment in sample_reviews:
                product = Product.query.filter_by(name=product_name).first()
                if not product:
                    continue
                db.session.add(Review(
                    product_id=product.id,
                    user_id=team.id,
                    rating=rating,
                    comment=comment,
                    verified_purchase=True,
                ))
                added += 1
            if added:
                db.session.commit()
                print(f'{added} system reviews seeded!')

        # Seed editable CMS content
        defaults = {
            'hero_tagline': 'Welcome to BioFarm Fruits',
            'hero_title': 'Quality Dragon Fruits & Seedlings',
            'hero_subtitle': 'We offer premium dragon fruits, avocado seedlings, and expert farming training.',
            'about_title': 'About BioFarm Fruits',
            'about_body': 'BioFarm Fruits supplies quality dragon fruits, avocado seedlings, and hands-on farming training across Kenya.',
            'contact_note': 'Reach out to us directly through phone, WhatsApp, or social media.',
        }
        changed = False
        for key, value in defaults.items():
            if not SiteContent.query.filter_by(key=key).first():
                db.session.add(SiteContent(key=key, value=value))
                changed = True
        if changed:
            db.session.commit()
            print('Site content seeded!')

def sync_schema():
    """Self-healing migration: add any model columns that are missing from
    existing tables so schema drift never crashes the app again. Brand-new
    tables are handled by db.create_all(); this only patches columns onto
    tables that already exist. Idempotent - safe to run on every startup."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    dialect = db.engine.dialect
    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all() handles brand-new tables
        have = {c['name'] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have or column.primary_key:
                continue  # SQLite cannot ALTER-ADD a primary key
            col_type = column.type.compile(dialect=dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
            default = column.default
            if default is not None and getattr(default, 'is_scalar', False):
                val = default.arg
                if isinstance(val, bool):
                    ddl += f' DEFAULT {1 if val else 0}'
                elif isinstance(val, (int, float)):
                    ddl += f' DEFAULT {val}'
                elif isinstance(val, str):
                    ddl += " DEFAULT '{}'".format(val.replace("'", "''"))
            try:
                db.session.execute(text(ddl))
                db.session.commit()
                print(f'Schema sync: added {table.name}.{column.name}')
            except Exception as exc:
                db.session.rollback()
                print(f'Schema sync: skipped {table.name}.{column.name} ({exc})')

def init_db():
    """Create uploads folder, tables, schema patches, and seed data.
    Safe to call repeatedly (create_all / sync_schema / seed_data are all
    idempotent). Called at import so gunicorn workers get a ready database,
    not just 'python app.py'."""
    os.makedirs('uploads', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    os.makedirs('templates/admin', exist_ok=True)

    with app.app_context():
        db.create_all()
        sync_schema()
        seed_data()


def create_app():
    """Gunicorn entry point: return a fully-initialised app."""
    init_db()
    return app


# ============================================================================
# RUN APP
# ============================================================================

if __name__ == '__main__':
    # Initialize database (init_db also runs for gunicorn via create_app)
    init_db()

    # Monthly report emails (customer statements + system report).
    start_scheduler()

    print('''
    ======================================================================
                          BIOFARM FRUITS
                          Enterprise Edition
    ----------------------------------------------------------------------
      Server:      http://localhost:5000
      Admin:       admin / Admin@2025  (chief admin)
      Farmer:      biofarm_farmer / Farmer@2025
      Features:    Products | Orders | Contact | Training | CMS | Blog
    ======================================================================
    ''')
    app.run(host='0.0.0.0', port=5000, debug=True)