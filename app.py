# ============================================
# CollabStudy - Main Application File
# ============================================

# ---------- Imports (Libraries ko import karna) ----------
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import bcrypt

# ---------- Flask App Initialize ----------
app = Flask(__name__)


app.secret_key = "PaKisTan"  

# ---------- Database Configuration ----------

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)  

# ---------- Flask-Login Configuration ----------
login_manager = LoginManager()  
login_manager.init_app(app)      
login_manager.login_view = 'login'  
login_manager.login_message = "Please login first!"  

# ---------- User Loader Function ----------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ---------- Database Model (User Table) ----------
class User(db.Model, UserMixin):  
    id = db.Column(db.Integer, primary_key=True) 
    name = db.Column(db.String(100), nullable=False)  
    username = db.Column(db.String(80), unique=True, nullable=False)  
    email = db.Column(db.String(120), unique=True, nullable=False)   
    country = db.Column(db.String(50), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)  

    
    def __init__(self, name, username, email, country, password_hash):
        self.name = name
        self.username = username
        self.email = email
        self.country = country
        
        self.password_hash = bcrypt.hashpw(
            password_hash.encode("utf-8"),  
            bcrypt.gensalt()                
        ).decode('utf-8')                   

    
    def check_password(self, plain_password):
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),  
            self.password_hash.encode('utf-8') 
        )

# ---------- Create Database Tables ----------
with app.app_context():
    db.create_all()
    print("Database tables created successfully!")

# ============================================
# ROUTES (URLs ke liye functions)
# ============================================

# ---------- Home / Welcome Page ----------
@app.route("/")
def welcome():
    """Landing page - Welcome screen"""
    return render_template("Wellcome.html")  

# ---------- Login Route ----------
@app.route("/login", methods=['GET', 'POST'])
def login():
    """User login - GET par form dikhao, POST par authenticate karo"""
    
    if request.method == 'GET':
        return render_template("login.html")
    
  
    username = request.form.get('username')
    password = request.form.get('password')
    
  
    if not username or not password:
        flash("Both username and password are required!")
        return redirect(url_for('login'))
    

    user = User.query.filter_by(username=username).first()
    
    if not user:
        flash("Username does not exist. Please register first!")
        return redirect(url_for('login'))
    

    if not user.check_password(password):
        flash("Incorrect password. Please try again!")
        return redirect(url_for('login'))
    
  
    login_user(user)
    flash(f"Welcome {user.username}!")
    
    return redirect(url_for('dashboard'))

# ---------- Register Route ----------
@app.route("/register", methods=['GET', 'POST'])
def register():
    """New user registration - GET par form, POST par save karo"""
    
    if request.method == 'GET':
        return render_template("register.html")
    

    name = request.form['name']
    username = request.form['username']
    email = request.form['email']
    country = request.form['country']
    password = request.form['password']
    confirm = request.form['confirmPassword']
    

    if password != confirm:
        flash("Passwords don't match")
        return redirect(url_for('register'))
    
    
    if User.query.filter_by(username=username).first():
        flash("Username already taken")
        return redirect(url_for('register'))
    
    
    if User.query.filter_by(email=email).first():
        flash("Email already registered")
        return redirect(url_for('register'))
    
    
    new_user = User(
        name=name,
        username=username,
        email=email,
        country=country,
        password_hash=password  
    )
    
    
    db.session.add(new_user)
    db.session.commit()
    
    flash("Registration successful! Please login.")
    return redirect(url_for('login'))

# ---------- Dashboard Route (Protected) ----------
@app.route("/dashboard")
@login_required  
def dashboard():
    """User dashboard - after login"""
    return render_template("dashboard.html", user=current_user)

# ---------- Forgot Password Route ----------
@app.route("/forgotPassword", methods=['GET', 'POST'])
def forgotPassword():
    """Forgot password page - GET par form, POST par email send"""
    
    if request.method == 'GET':
        return render_template("forgotPassword.html")
    
    
    email = request.form.get('email')
    
    if not email:
        flash("Please enter your email address.")
        return redirect(url_for('forgotPassword'))
    
    user = User.query.filter_by(email=email).first()
    
    if user:
        flash(f"Password reset link sent to {email} (demo - implement email sending)")
    else:
        flash("No account found with that email address.")
    
    return redirect(url_for('login'))

# ---------- Logout Route ----------
@app.route("/logout")
@login_required  
def logout():
    """User logout - session destroy"""
    logout_user()  
    flash("You have been logged out successfully.")
    return redirect(url_for('welcome'))

# ---------- Run Application ----------
if __name__ == "__main__":
    app.run(debug=True)