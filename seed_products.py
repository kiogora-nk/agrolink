# run this once to seed products
from app import app, db, Product, User
from werkzeug.security import generate_password_hash

with app.app_context():
    # Create a demo farmer if not exists
    farmer = User.query.filter_by(username='biofarm_farmer').first()
    if not farmer:
        farmer = User(
            username='biofarm_farmer',
            email='farm@biofarmfruits.co.ke',
            password_hash=generate_password_hash('Farmer@2025'),
            role='farmer'
        )
        db.session.add(farmer)
        db.session.commit()
    
    products = [
        ('Dragon Fruit Red', 'Sweet red dragon fruit with high antioxidants. Perfect for fresh eating and smoothies.', 680, 'fruits', 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', True),
        ('Dragon Fruit White', 'Crisp white dragon fruit with mild sweet flavor. Great for salads and desserts.', 620, 'fruits', 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', True),
        ('Avocado Seedlings (Hass)', 'Premium Hass avocado seedlings. Ready for planting. Disease-free and high-yield variety.', 300, 'seedlings', 'https://images.unsplash.com/photo-1523049673857-eb18f1d7b578?w=400', False),
        ('Avocado Seedlings (Fuerte)', 'Fuerte avocado seedlings known for their smooth skin and creamy texture. Very productive.', 280, 'seedlings', 'https://images.unsplash.com/photo-1523049673857-eb18f1d7b578?w=400', False),
        ('Dragon Fruit Seedlings', 'Healthy dragon fruit cuttings ready for planting. Fast-growing variety.', 200, 'seedlings', 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', False),
        ('Avocado (Hass) Fruit', 'Creamy Hass avocados. Rich in healthy fats. Perfect for export quality.', 180, 'fruits', 'https://images.unsplash.com/photo-1601039641847-7857b994d704?w=400', True),
        ('Avocado (Fuerte) Fruit', 'Smooth green Fuerte avocados. Great for restaurants and fresh markets.', 150, 'fruits', 'https://images.unsplash.com/photo-1590431306482-f700ee050c59?w=400', False),
    ]
    
    for name, desc, price, category, image, organic in products:
        existing = Product.query.filter_by(name=name).first()
        if not existing:
            product = Product(
                name=name,
                description=desc,
                price=price,
                category=category,
                organic=organic,
                image=image,
                farmer_id=farmer.id,
                status='approved'
            )
            db.session.add(product)
    
    db.session.commit()
    print('Products seeded successfully!')
