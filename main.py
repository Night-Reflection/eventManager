import random
import os
from flask import Flask, render_template, request, session, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from mailjet_rest import Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

mailjet = Client(auth=(os.getenv('MAILJET_API_KEY'), os.getenv('MAILJET_API_SECRET')), version='v3.1')

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

def create_tables():
    with app.app_context():
        db.create_all()

create_tables()

@app.route("/")
def home():
    if 'username' in session:
        return render_template("home.html")
    return redirect(url_for('login'))

@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and bcrypt.check_password_hash(user.password, password):
            session['username'] = username
            return redirect(url_for('home'))
        else:
            flash("Invalid username or password!", "danger")
    return render_template("login.html")

@app.route("/register", methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not username or not email or not password or not confirm_password:
            flash("Missing registration fields.", "danger")
            return redirect(url_for('register'))
        
        if password != confirm_password:
            flash("Passwords do not match!", "danger")
            return redirect(url_for('register'))

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        if User.query.filter_by(email=email).first():
            flash("This email is already registered!", "danger")
            return redirect(url_for('register'))

        verification_code = str(random.randint(100000, 999999))
        session['verification_code'] = verification_code
        session['pending_user'] = {"username": username, "email": email, "password": hashed_password}

        send_verification_email(email, verification_code)
        return render_template("verify.html", email=email)
    return render_template("register.html")

@app.route("/verify", methods=['POST'])
def verify():
    entered_code = request.form.get('verification_code')
    
    if entered_code == session.get('verification_code'):
        user_data = session.pop('pending_user')
        new_user = User(username=user_data['username'], email=user_data['email'], password=user_data['password'])
        db.session.add(new_user)
        db.session.commit()

        session['username'] = user_data['username']
        flash("Registration successful!", "success")
        return redirect(url_for('home'))
    
    flash("Invalid verification code!", "danger")
    return render_template("verify.html")

def send_verification_email(email, verification_code):
    try:
        data = {
            'Messages': [
                {
                    "From": {
                        "Email": "lifeeventmanager666@gmail.com",
                        "Name": "EventManager"
                    },
                    "To": [
                        {
                            "Email": email
                        }
                    ],
                    "Subject": "Verification Code for Your EventManager Registration",
                    "HTMLPart": f"""
                    <html>
                    <body style="font-family: Arial, sans-serif; color: #333;">
                        <div style="background-color: #f4f4f9; padding: 20px; border-radius: 8px;">
                            <h2 style="color: #4CAF50; text-align: center;">Welcome to EventManager!</h2>
                            <p style="font-size: 16px; color: #555;">
                                Dear User,<br><br>
                                Thank you for registering with EventManager! We are thrilled to have you join us.
                            </p>
                            <p style="font-size: 16px; color: #555;">
                                To complete your registration, please use the verification code below:
                            </p>
                            <div style="background-color: #e7f4e7; padding: 15px; font-size: 20px; font-weight: bold; text-align: center; color: #333;">
                                {verification_code}
                            </div>
                            <p style="font-size: 16px; color: #555;">
                                If you did not request this code, please disregard this email. Your security is important to us.
                            </p>
                            <p style="font-size: 16px; color: #555;">
                                Thank you for choosing EventManager! We look forward to helping you manage your events with ease.
                            </p>
                            <footer style="text-align: center; font-size: 14px; color: #aaa;">
                                Best regards,<br>
                                The EventManager Team
                            </footer>
                        </div>
                    </body>
                    </html>
                    """
                }
            ]
        }
        result = mailjet.send.create(data=data)
        if result.status_code == 200:
            print("Email sent successfully!")
        else:
            print(f"Failed to send email: {result.status_code} - {result.text}")
    except Exception as e:
        print(f"Error sending email: {e}")

if __name__ == "__main__":
    app.run(debug=True)
