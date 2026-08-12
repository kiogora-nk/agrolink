from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import secrets
import re
from functools import wraps
from dotenv import load_dotenv
import requests
import pytz

load_dotenv()

app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///biofarm.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SITE_URL'] = os.getenv('SITE_URL', 'http://localhost:5000')

# Initialize extensions
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# ========== MODELS ==========

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
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    status = db.Column(db.String(20), default='pending')
    views = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    __tablename__ = 'reviews'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    verified_purchase = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    total_price = db.Column(db.Float)
    status = db.Column(db.String(20), default='pending')
    delivery_address = db.Column(db.Text)
    delivery_phone = db.Column(db.String(20))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivered_at = db.Column(db.DateTime)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ContactMessage(db.Model):
    __tablename__ = 'contact_messages'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20))
    subject = db.Column(db.String(200))
    message = db.Column(db.Text, nullable=False)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DiseaseDetection(db.Model):
    __tablename__ = 'disease_detections'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    crop_type = db.Column(db.String(50))
    symptoms = db.Column(db.Text)
    diagnosis = db.Column(db.Text)
    treatment = db.Column(db.Text)
    confidence = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ========== HELPER FUNCTIONS ==========

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'chief_admin']:
            abort(403)
        return f(*args, **kwargs)
    return decorated

def detect_disease(crop_type, symptoms):
    symptoms = symptoms.lower()
    
    if crop_type == 'dragon_fruit':
        if 'yellow' in symptoms and 'spot' in symptoms:
            return "Bacterial Spot", "Remove infected areas, apply copper-based bactericide. Improve air circulation.", 0.85
        elif 'rot' in symptoms or 'soft' in symptoms:
            return "Fruit Rot", "Remove infected fruits. Improve drainage. Apply fungicide.", 0.82
    elif crop_type == 'avocado':
        if 'leaf' in symptoms and 'spot' in symptoms:
            return "Leaf Spot", "Remove fallen leaves. Apply copper fungicide. Avoid overhead watering.", 0.88
        elif 'root' in symptoms and 'rot' in symptoms:
            return "Root Rot", "Improve drainage. Apply phosphonate fungicides.", 0.92
    elif 'yellow' in symptoms:
        return "Nutrient Deficiency", "Apply balanced fertilizer. Check soil pH. Add compost.", 0.75
    elif 'spot' in symptoms:
        return "Fungal Infection", "Apply fungicide. Remove affected leaves. Improve air circulation.", 0.76
    else:
        return "No Disease Detected", "Monitor plants regularly. Maintain good agricultural practices.", 0.95

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ========== ROUTES ==========

@app.route('/')
def home():
    featured = Product.query.filter_by(status='approved', organic=True).limit(8).all()
    fruits = Product.query.filter_by(status='approved', category='fruits').limit(8).all()
    seedlings = Product.query.filter_by(status='approved', category='seedlings').limit(8).all()
    reviews = Review.query.order_by(Review.created_at.desc()).limit(6).all()
    return render_template('index.html', featured=featured, fruits=fruits, seedlings=seedlings, reviews=reviews)

@app.route('/products')
def products():
    category = request.args.get('category')
    search = request.args.get('search')
    query = Product.query.filter_by(status='approved')
    
    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    
    products = query.order_by(Product.created_at.desc()).all()
    return render_template('products.html', products=products)

@app.route('/product/<int:id>')
def product_detail(id):
    product = Product.query.get_or_404(id)
    if product.status != 'approved':
        abort(404)
    
    product.views += 1
    db.session.commit()
    
    reviews = Review.query.filter_by(product_id=id).order_by(Review.created_at.desc()).all()
    avg_rating = db.session.query(db.func.avg(Review.rating)).filter_by(product_id=id).scalar() or 0
    related = Product.query.filter_by(category=product.category, status='approved').filter(Product.id != id).limit(4).all()
    
    return render_template('product_detail.html', 
                          product=product, 
                          reviews=reviews, 
                          avg_rating=round(avg_rating, 1),
                          related=related)

@app.route('/product/<int:id>/review', methods=['POST'])
@login_required
def add_review(id):
    product = Product.query.get_or_404(id)
    rating = request.form.get('rating', type=int)
    comment = request.form.get('comment')
    
    if not rating or rating < 1 or rating > 5:
        flash('Please provide a valid rating.', 'error')
        return redirect(url_for('product_detail', id=id))
    
    existing = Review.query.filter_by(product_id=id, user_id=current_user.id).first()
    if existing:
        existing.rating = rating
        existing.comment = comment
        flash('Review updated!', 'success')
    else:
        review = Review(product_id=id, user_id=current_user.id, rating=rating, comment=comment)
        db.session.add(review)
        flash('Review added!', 'success')
    
    db.session.commit()
    return redirect(url_for('product_detail', id=id))

@app.route('/training', methods=['GET', 'POST'])
def training():
    if request.method == 'POST':
        session = TrainingSession(
            user_id=current_user.id if current_user.is_authenticated else None,
            name=request.form.get('name'),
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            session_type=request.form.get('session_type'),
            message=request.form.get('message')
        )
        db.session.add(session)
        db.session.commit()
        flash('Training session booked! We will contact you within 24 hours.', 'success')
        return redirect(url_for('training'))
    
    return render_template('training.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        message = ContactMessage(
            name=request.form.get('name'),
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            subject=request.form.get('subject'),
            message=request.form.get('message')
        )
        db.session.add(message)
        db.session.commit()
        flash('Message sent! We will respond within 24 hours.', 'success')
        return redirect(url_for('contact'))
    
    return render_template('contact.html')

@app.route('/disease-detection', methods=['GET', 'POST'])
def disease_detection():
    if request.method == 'POST':
        crop_type = request.form.get('crop_type')
        symptoms = request.form.get('symptoms')
        diagnosis, treatment, confidence = detect_disease(crop_type, symptoms)
        
        detection = DiseaseDetection(
            user_id=current_user.id if current_user.is_authenticated else None,
            crop_type=crop_type,
            symptoms=symptoms,
            diagnosis=diagnosis,
            treatment=treatment,
            confidence=confidence
        )
        db.session.add(detection)
        db.session.commit()
        
        return render_template('disease_result.html', 
                             crop_type=crop_type,
                             symptoms=symptoms,
                             diagnosis=diagnosis,
                             treatment=treatment,
                             confidence=confidence)
    
    return render_template('disease_detection.html')

@app.route('/auth/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter((User.username == username) | (User.email == username)).first()
        
        if user and check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash('Account deactivated.', 'error')
                return render_template('login.html')
            
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            flash('Welcome back!', 'success')
            return redirect(url_for('home'))
        
        flash('Invalid credentials.', 'error')
    
    return render_template('login.html')

@app.route('/auth/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')
        
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('register.html')
        
        if User.query.filter_by(username=username).first():
            flash('Username exists.', 'error')
            return render_template('register.html')
        
        if User.query.filter_by(email=email).first():
            flash('Email exists.', 'error')
            return render_template('register.html')
        
        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            role=request.form.get('role', 'customer')
        )
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        flash('Registration successful!', 'success')
        return redirect(url_for('home'))
    
    return render_template('register.html')

@app.route('/auth/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out.', 'info')
    return redirect(url_for('home'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.bio = request.form.get('bio')
        current_user.location = request.form.get('location')
        current_user.phone = request.form.get('phone')
        
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and file.filename:
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                filename = secure_filename(f'avatar_{current_user.id}_{file.filename}')
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                current_user.avatar = url_for('uploaded_file', filename=filename)
        
        db.session.commit()
        flash('Profile updated!', 'success')
        return redirect(url_for('profile'))
    
    return render_template('profile.html', user=current_user)

@app.route('/dashboard')
@login_required
def dashboard():
    stats = {
        'orders': Order.query.filter_by(user_id=current_user.id).count(),
        'reviews': Review.query.filter_by(user_id=current_user.id).count(),
        'training': TrainingSession.query.filter_by(user_id=current_user.id).count()
    }
    return render_template('dashboard.html', stats=stats)

@app.route('/order/<int:id>', methods=['POST'])
@login_required
def place_order(id):
    product = Product.query.get_or_404(id)
    quantity = request.form.get('quantity', 1, type=int)
    
    if quantity > product.stock:
        flash('Not enough stock.', 'error')
        return redirect(url_for('product_detail', id=id))
    
    order = Order(
        user_id=current_user.id,
        product_id=id,
        quantity=quantity,
        total_price=product.price * quantity,
        delivery_address=request.form.get('delivery_address'),
        delivery_phone=request.form.get('delivery_phone'),
        notes=request.form.get('notes')
    )
    product.stock -= quantity
    db.session.add(order)
    db.session.commit()
    
    flash('Order placed!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/my-orders')
@login_required
def my_orders():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders)

# ========== ADMIN ROUTES ==========

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    stats = {
        'products': Product.query.count(),
        'users': User.query.count(),
        'orders': Order.query.count(),
        'training': TrainingSession.query.filter_by(status='pending').count(),
        'messages': ContactMessage.query.filter_by(read=False).count()
    }
    return render_template('admin/dashboard.html', stats=stats)

@app.route('/admin/product/<int:id>/approve', methods=['POST'])
@admin_required
def approve_product(id):
    product = Product.query.get_or_404(id)
    product.status = 'approved'
    db.session.commit()
    flash('Product approved.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/product/<int:id>/delete', methods=['POST'])
@admin_required
def delete_product(id):
    product = Product.query.get_or_404(id)
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ========== ERROR HANDLERS ==========

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

# ========== CREATE TABLES AND SEED ==========

with app.app_context():
    db.create_all()
    
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
        print('✅ Admin created: admin / Admin@2025')
    
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
        print('✅ Farmer created')
    
    # Seed products
    products = [
        ('Dragon Fruit Red', 'Sweet red dragon fruit with high antioxidants.', 680, 'fruits', True, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 50),
        ('Dragon Fruit White', 'Crisp white dragon fruit with mild sweet flavor.', 620, 'fruits', True, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 45),
        ('Avocado Seedlings Hass', 'Premium Hass avocado seedlings.', 300, 'seedlings', False, 'https://images.unsplash.com/photo-1523049673857-eb18f1d7b578?w=400', 100),
        ('Avocado Seedlings Fuerte', 'Fuerte avocado seedlings with smooth skin.', 280, 'seedlings', False, 'https://images.unsplash.com/photo-1523049673857-eb18f1d7b578?w=400', 80),
        ('Dragon Fruit Seedlings', 'Healthy dragon fruit cuttings ready for planting.', 200, 'seedlings', False, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 150),
        ('Hass Avocado Fruit', 'Creamy Hass avocados.', 180, 'fruits', True, 'https://images.unsplash.com/photo-1601039641847-7857b994d704?w=400', 30),
    ]
    
    for name, desc, price, category, organic, image, stock in products:
        existing = Product.query.filter_by(name=name).first()
        if not existing:
            product = Product(
                name=name,
                description=desc,
                price=price,
                category=category,
                organic=organic,
                image=image,
                stock=stock,
                farmer_id=farmer.id,
                status='approved'
            )
            db.session.add(product)
    db.session.commit()
    print('✅ Products seeded')

# ========== RUN APP ==========

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
