import random
import smtplib
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_mail import Mail, Message
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Nastavitve za pošiljanje e-pošte
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your_email@gmail.com'
app.config['MAIL_PASSWORD'] = 'your_email_password'
mail = Mail(app)


@app.route("/")
def home():
    if 'username' in session:
        return render_template("home.html")
    return redirect(url_for('login'))


@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        session['username'] = username
        return redirect(url_for('home'))
    return render_template("login.html")


@app.route("/register", methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        verification_code = str(random.randint(100000, 999999))
        
        session['verification_code'] = verification_code
        session['email'] = email
        
        send_verification_email(email, verification_code)
        
        return render_template("verify.html", email=email)
    
    return render_template("register.html")


@app.route("/verify", methods=['POST'])
def verify():
    entered_code = request.form['verification_code']
    
    if entered_code == session.get('verification_code'):
        session['username'] = request.form['username']
        return redirect(url_for('home'))
    else:
        return "Koda za preverjanje je napačna. Poskusite znova."


def send_verification_email(email, verification_code):
    msg = MIMEMultipart()
    msg['From'] = 'your_email@gmail.com'
    msg['To'] = email
    msg['Subject'] = 'Vaša koda za preverjanje registracije'

    body = f'Pozdravljeni,\n\nVaša koda za preverjanje je: {verification_code}\n\nHvala, da ste se registrirali!'
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login('your_email@gmail.com', 'your_email_password')
            text = msg.as_string()
            server.sendmail('your_email@gmail.com', email, text)
        print("E-pošta je bila poslana!")
    except Exception as e:
        print(f"Napaka pri pošiljanju e-pošte: {e}")


if __name__ == "__main__":
    app.run(debug=True)
