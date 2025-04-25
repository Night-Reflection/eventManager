import random
import os
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from mailjet_rest import Client
from dotenv import load_dotenv
from datetime import datetime, timedelta

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
    phone_number = db.Column(db.String(255), unique=True, nullable=True)

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(10), nullable=False)
    time = db.Column(db.String(5), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    location = db.Column(db.String(255), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    user = db.relationship('User', backref=db.backref('events', lazy=True))


def create_tables():
    with app.app_context():
        db.create_all()
    print("Databases created")

create_tables()

@app.route("/")
def home():
    session.pop('verification_code', None)
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
            session['user_id'] = user.id
            return redirect(url_for('home'))
        else:
            flash("Invalid username or password!", "danger")
    return render_template("login.html")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

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
        session['pending_user'] = {
            "username": username,
            "email": email,
            "password": hashed_password
        }

        send_verification_email(email, verification_code)
        return render_template("verify.html", email=email, action_url=url_for('verify'))

    return render_template("register.html")



@app.route("/verify", methods=['GET', 'POST'])
def verify():
    if request.method == 'POST':
        entered_code = request.form.get('verification_code')
        
        if entered_code == session.get('verification_code'):
            user_data = session.pop('pending_user')
            new_user = User(
                username=user_data['username'],
                email=user_data['email'],
                password=user_data['password']
            )
            db.session.add(new_user)
            db.session.commit()

            session['username'] = user_data['username']
            flash("Registration successful!", "success")
            return redirect(url_for('home'))
        
        flash("Invalid verification code!", "danger")
    
    email = session.get('pending_user', {}).get('email', None)
    return render_template("verify.html", email=email, action_url=url_for('verify'))


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

@app.route('/events', methods=['GET'])
def events():
    today = datetime.today()

    view = request.args.get('view', 'weekly')
    direction = request.args.get('direction', 'current')
    jump_to_date = request.args.get('jump_to_date')

    if jump_to_date:
        try:
            jump_date = datetime.strptime(jump_to_date, '%Y-%m-%d')
        except ValueError:
            jump_date = today
    else:
        jump_date = today

    if view == 'daily':
        if jump_to_date:
            current_day = jump_date
        else:
            current_day = datetime.strptime(session.get('current_day', today.strftime('%d.%m.%Y')), '%d.%m.%Y')

            if direction == 'prev':
                current_day -= timedelta(days=1)
            elif direction == 'next':
                current_day += timedelta(days=1)
            elif direction == 'current':
                current_day = today

        session['current_day'] = current_day.strftime('%d.%m.%Y')

        day_events = Event.query.filter_by(
            user_id=session['user_id'],
            date=current_day.strftime('%Y-%m-%d')
        ).all()

        current_day_obj = {
            'name': current_day.strftime('%A'),
            'date': current_day.strftime('%d.%m.%Y'),
            'events': day_events
        }

        return render_template(
            'events.html',
            day_events=day_events,
            view=view,
            current_day=current_day_obj,
            current_date=current_day.strftime('%d.%m.%Y'),
            start_of_week=(current_day - timedelta(days=current_day.weekday())).strftime('%d.%m.%Y'),
            end_of_week=(current_day + timedelta(days=6 - current_day.weekday())).strftime('%d.%m.%Y'),
            preset=current_day.strftime('%Y-%m-%d')
        )
        
    if jump_to_date:
        start_of_week = jump_date - timedelta(days=jump_date.weekday())
        end_of_week = start_of_week + timedelta(days=6)
    else:
        start_of_week = session.get('start_of_week')
        end_of_week = session.get('end_of_week')

        if not start_of_week or not end_of_week:
            start_of_week = today - timedelta(days=today.weekday())
            end_of_week = start_of_week + timedelta(days=6)

        if isinstance(start_of_week, str):
            start_of_week = datetime.strptime(start_of_week, '%d.%m.%Y')

        if isinstance(end_of_week, str):
            end_of_week = datetime.strptime(end_of_week, '%d.%m.%Y')

        if direction == 'prev':
            start_of_week -= timedelta(weeks=1)
            end_of_week -= timedelta(weeks=1)
        elif direction == 'next':
            start_of_week += timedelta(weeks=1)
            end_of_week += timedelta(weeks=1)
        elif direction == 'current':
            start_of_week = today - timedelta(days=today.weekday())
            end_of_week = start_of_week + timedelta(days=6)

    session['start_of_week'] = start_of_week.strftime('%d.%m.%Y')
    session['end_of_week'] = end_of_week.strftime('%d.%m.%Y')

    week_days = []
    for i in range(7):
        day = start_of_week + timedelta(days=i)
        day_events = Event.query.filter_by(
            user_id=session['user_id']
        ).filter(Event.date == day.strftime('%Y-%m-%d')).all()

        week_days.append({
            'name': day.strftime('%A'),
            'date': day.strftime('%d-%m-%Y'),
            'events': day_events
        })
    if 'current_day' not in session:
        session['current_day'] = jump_date.strftime('%d.%m.%Y') if jump_to_date else today.strftime('%d.%m.%Y')

    return render_template(
        'events.html',
        week_days=week_days,
        view=view,
        current_date=session['current_day'],
        start_of_week=start_of_week.strftime('%d.%m.%Y'),
        end_of_week=end_of_week.strftime('%d.%m.%Y'),
        preset=start_of_week.strftime('%Y-%m-%d')
    )

@app.route('/event/details/<int:event_id>', methods=['GET'])
def event_details(event_id):
    event = Event.query.get_or_404(event_id)
    return jsonify({
        'id': event.id,
        'title': event.title,
        'time': event.time,
        'description': event.description,
        'location': event.location
    })

@app.route('/edit_event/<int:event_id>', methods=['GET', 'POST'])
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    
    if request.method == 'POST':
        event.title = request.form['event_title']
        event.date = request.form['event_date']
        event.time = request.form['event_time']
        event.description = request.form.get('event_description')
        event.location = request.form.get('event_location')

        db.session.commit()
        
        flash("Event updated successfully!", "success")
        
        return redirect(url_for('events'))
    
    return render_template('calendar.html', event=event)

@app.route('/delete_event/<int:event_id>')
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    flash("Event deleted successfully!", "success")
    return redirect(url_for('events'))

@app.route('/Create_Event', methods=['GET', 'POST'])
def Create_Event():
    if 'user_id' not in session:
        flash("Please log in to access this page.", "danger")
        return redirect(url_for('login'))

    if request.method == 'POST':
        event_title = request.form['event_title']
        event_date = request.form['event_date']
        event_time = request.form['event_time']
        event_description = request.form.get('event_description')
        event_location = request.form.get('event_location')

        if not event_title or not event_date or not event_time:
            flash("Please fill in all required fields!", "danger")
            return redirect(url_for('Create_Event'))

        user = User.query.filter_by(id=session['user_id']).first()
        if user:
            new_event = Event(
                title=event_title,
                date=event_date,
                time=event_time,
                description=event_description,
                location=event_location,
                user_id=user.id
            )
            db.session.add(new_event)
            db.session.commit()
            flash("Event added successfully!", "success")
            return redirect(url_for('events'))

        flash("Error adding event!", "danger")

    today = datetime.today()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    week_days = []
    for i in range(7):
        day = start_of_week + timedelta(days=i)
        day_events = Event.query.filter_by(user_id=session['user_id']).filter(Event.date == day.strftime('%Y-%m-%d')).all()
        week_days.append({
            'name': day.strftime('%A'),
            'date': day.strftime('%Y-%m-%d'),
            'events': day_events
        })

    return render_template('calendar.html', week_days=week_days)

@app.route('/participants')
def participants():
    return render_template('participants.html')

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    user = User.query.filter_by(username=session['username']).first()

    if request.method == 'POST':
        new_email = request.form['email']
        phone = request.form['phone']

        if user:
            if new_email != user.email:
                verification_code = str(random.randint(100000, 999999))

                session['pending_email'] = new_email
                session['email_verification_code'] = verification_code

                send_verification_email(new_email, verification_code)

                flash("A verification code has been sent to your new email. Please confirm to update your email.", "info")
                return redirect(url_for('verify_email'))

            user.phone_number = phone
            db.session.commit()

            session['phone'] = phone
            flash("Phone number updated successfully!", "success")
            return redirect(url_for('settings'))

        flash("Error updating settings!", "danger")
    
    return render_template('settings.html', user=user)

@app.route('/verify-email', methods=['GET', 'POST'])
def verify_email():
    if request.method == 'POST':
        entered_code = request.form.get('verification_code')
        expected_code = session.get('email_verification_code')
        new_email = session.get('pending_email')

        if entered_code == expected_code:
            user = User.query.filter_by(username=session['username']).first()
            if user:
                user.email = new_email
                db.session.commit()

                session['email'] = new_email
                flash("Your email has been updated successfully!", "success")

            session.pop('pending_email', None)
            session.pop('email_verification_code', None)

            return redirect(url_for('settings'))

        flash("Invalid verification code!", "danger")

    return render_template(
        'verify.html',
        email=session.get('pending_email'),
        action_url=url_for('verify_email')
    )


if __name__ == "__main__":
    app.run(debug=True)
