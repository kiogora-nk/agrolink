from app import app, db
from sqlalchemy import text

with app.app_context():
    # Check if stock column exists
    result = db.session.execute(text("PRAGMA table_info(product)"))
    columns = [row[1] for row in result]
    
    if 'stock' not in columns:
        print('Adding stock column to product table...')
        db.session.execute(text('ALTER TABLE product ADD COLUMN stock INTEGER DEFAULT 0'))
        db.session.commit()
        print('✅ Stock column added!')
    else:
        print('✅ Stock column already exists.')
    
    # Create farmer if not exists
    from app import User, Product
    from werkzeug.security import generate_password_hash
    
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
    
    # Products to seed
    products = [
        ('Dragon Fruit Red', 'Sweet red dragon fruit with high antioxidants. Perfect for fresh eating and smoothies.', 680, 'fruits', True, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 50),
        ('Dragon Fruit White', 'Crisp white dragon fruit with mild sweet flavor. Great for salads and desserts.', 620, 'fruits', True, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 45),
        ('Dragon Fruit Yellow', 'Sweet yellow dragon fruit with firm skin and high market value.', 760, 'fruits', True, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 30),
        ('Dragon Fruit Purple', 'Purple dragon fruit variety with vivid flesh for juices and fruit bowls.', 720, 'fruits', True, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 25),
        ('Avocado Seedlings Hass', 'Premium Hass avocado seedlings. Disease-free and high-yield variety.', 300, 'seedlings', False, 'https://images.unsplash.com/photo-1523049673857-eb18f1d7b578?w=400', 100),
        ('Avocado Seedlings Fuerte', 'Fuerte avocado seedlings with smooth skin and creamy texture.', 280, 'seedlings', False, 'https://images.unsplash.com/photo-1523049673857-eb18f1d7b578?w=400', 80),
        ('Avocado Seedlings Pinkerton', 'Pinkerton avocado seedlings with small seed and excellent slicing quality.', 320, 'seedlings', False, 'https://images.unsplash.com/photo-1523049673857-eb18f1d7b578?w=400', 60),
        ('Dragon Fruit Seedlings', 'Healthy dragon fruit cuttings ready for planting. Fast-growing variety.', 200, 'seedlings', False, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 150),
        ('Dragon Fruit Cuttings', 'Premium dragon fruit cuttings for propagation. High-yield variety.', 180, 'seedlings', False, 'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400', 200),
    ]
    
    count = 0
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
            count += 1
    
    db.session.commit()
    print(f'✅ {count} products added successfully!')
    print('Added: Dragon Fruits (Red, White, Yellow, Purple)')
    print('Added: Seedlings (Avocado Hass, Fuerte, Pinkerton, Dragon Fruit)')
