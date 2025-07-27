import random
import os
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from mailjet_rest import Client
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import requests
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
import logging
from logging.handlers import RotatingFileHandler
import math
from PIL import Image, ImageDraw
from io import BytesIO

load_dotenv()

log_file = "logs.txt"
local_tz = ZoneInfo("Europe/Ljubljana")

def get_default_pfp():
    with open("static/default_pfp.png", "rb") as f:
        return f.read()

def parse_event_datetime(event_date, event_time):
    try:
        return datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=local_tz)
    except ValueError:
        return datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)

def custom_time(secs):
    utc_dt = datetime.fromtimestamp(secs, tz=timezone.utc)
    local_dt = utc_dt.astimezone(local_tz)
    return local_dt.timetuple()

formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
formatter.converter = custom_time
file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
stream_handler = logging.StreamHandler()

file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(stream_handler)
logger.info("Logger initialized")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

jobstore_url = os.getenv("JOBSTORE_DATABASE_URI", "sqlite:///jobs.db")
scheduler = BackgroundScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=jobstore_url)},
    job_defaults={"max_instances": 1},
    timezone=local_tz
)

def update_elo_job():
    with app.app_context():
        goals = Goal.query.all()
        headers = {"Authorization": f"Bearer {FACEIT_API_KEY}"}

        for goal in goals:
            try:
                if goal.platform == 'faceit':
                    res = requests.get(f"{FACEIT_API_URL}/players?nickname={goal.nickname}", headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        new_elo = data.get('games', {}).get(goal.game, {}).get('faceit_elo')

                        if new_elo and new_elo != goal.current_elo:
                            logger.info(f"Updating {goal.nickname} ({goal.game}) from {goal.current_elo} ➝ {new_elo}")
                            goal.current_elo = new_elo
                            db.session.commit()
                elif goal.platform == 'youtube':
                    current_subs = get_youtube_subscribers(goal.nickname)
                    if current_subs is not None and current_subs != goal.current_elo:
                        logger.info(f"Updating YouTube channel {goal.nickname} from {goal.current_elo} ➝ {current_subs}")
                        goal.current_elo = current_subs
                        db.session.commit()
            except Exception as e:
                logger.error(f"Failed to update {goal.nickname}: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(
    update_elo_job,
    trigger='interval',
    minutes=15,
    id="update_elo_job",
    replace_existing=True
)

def check_games_on_sale():
    with app.app_context():
        users = User.query.all()
        for user in users:
            games_on_sale = []
            for wish in user.wishlisted_games:
                game = wish.game
                deals_resp = requests.get(f"{CHEAPSHARK_API_BASE}/games", params={"id": game.cheapshark_id})
                if deals_resp.status_code == 200:
                    deals = deals_resp.json().get("deals", [])
                    if deals:
                        best_deal = min(deals, key=lambda d: float(d.get("price", 9999)))
                        if float(best_deal["price"]) < (wish.last_notified_price or float('inf')):
                            games_on_sale.append({
                                "title": game.name,
                                "store": best_deal["storeID"],
                                "price": best_deal["price"],
                                "url": f"https://www.cheapshark.com/redirect?dealID={best_deal['dealID']}"
                            })
                            wish.last_notified_price = float(best_deal["price"])
                            db.session.commit()
            if games_on_sale:
                game_lines = "".join(f"""
                    <li style='margin-bottom: 18px; border-bottom:1px solid #eee; padding-bottom:12px;'>
                        <a href='{g['url']}' style='text-decoration:none; color:#222; font-weight:bold; font-size:17px;'>{g['title']}</a><br>
                        <span style='color:#4e54c8; font-size:16px;'><b>Now: ${g['price']}</b></span><br>
                        <a href='{g['url']}' style='display:inline-block; margin-top:6px; background:#4e54c8; color:#fff; padding:7px 16px; border-radius:6px; text-decoration:none; font-size:15px;'>View Deal</a>
                    </li>
                """ for g in games_on_sale)
                email_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; background-color:#f7f7f7; padding: 20px;">
                <div style="max-width: 500px; margin: 0 auto; background: #fff; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.07); overflow: hidden;">
                    <div style="background: linear-gradient(90deg, #4e54c8 0%, #8f94fb 100%); color: #fff; text-align: center; padding: 24px 0;">
                    <h2 style="margin:0 0 8px;">🎮 Game Sale Alert!</h2>
                    <p style="margin:0; font-size:16px;">Some of your wishlisted games are now on sale!</p>
                    </div>
                    <div style="padding: 24px;">
                    <ul style="list-style:none; padding:0; margin:0;">
                        {game_lines}
                    </ul>
                    <p style="font-size: 13px; color: #888; margin-top: 20px;">You are receiving this notification because you wishlisted these games on EventManager.</p>
                    <div style="text-align:center; margin-top: 10px;">
                        <img src="https://cdn-icons-png.flaticon.com/512/833/833314.png" style="width:50px; opacity:0.5;" alt="Game controller">
                    </div>
                    </div>
                    <div style="background:#f7f7f7; color:#aaa; text-align:center; font-size:12px; padding:12px;">
                    &copy; EventManager | Happy gaming!
                    </div>
                </div>
                </body>
                </html>
                """
                data = {
                    'Messages': [
                        {
                            "From": {"Email": "lifeeventmanager666@gmail.com", "Name": "EventManager"},
                            "To": [{"Email": user.email}],
                            "Subject": "Game Sale Alert! 🎮",
                            "HTMLPart": email_body,
                        }
                    ]
                }
                try:
                    mailjet.send.create(data=data)
                    logger.info(f"Sale notification sent to {user.email}")
                except Exception as e:
                    logger.error(f"Failed to send sale email to {user.email}: {e}")

scheduler.add_job(
    check_games_on_sale,
    trigger="interval",
    hours=1,
    id="check_games_on_sale",
    replace_existing=True,
)

scheduler.start()

mailjet = Client(auth=(os.getenv('MAILJET_API_KEY'), os.getenv('MAILJET_API_SECRET')), version='v3.1')
PANDASCORE_API_KEY = os.getenv("E_SPORTS_API_KEY")
SPORTS_API_KEY = os.getenv("SPORTS_API_KEY")
FACEIT_API_KEY = os.getenv("FACEIT_API")
FACEIT_API_URL = "https://open.faceit.com/data/v4"
LEMON_API_KEY = os.getenv("LEMON_SQUEEZY_API")
LEMON_VARIANT_ID = os.getenv("LEMON_VARIANT_ID")
CHEAPSHARK_API_BASE = "https://www.cheapshark.com/api/1.0"

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=False, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    phone_number = db.Column(db.String(255), unique=True, nullable=True)
    premium = db.Column(db.Boolean, default=False)
    timezone = db.Column(db.String(50), nullable=True)
    pfp = db.Column(db.LargeBinary, nullable=True)
    role = db.Column(db.String(50), nullable=True)

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(10), nullable=False)
    time = db.Column(db.String(5), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    location = db.Column(db.String(255), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    notification_enabled = db.Column(db.Boolean, default=False)
    reminder_time = db.Column(db.Integer, nullable=True)

    user = db.relationship('User', backref=db.backref('events', lazy=True))

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    game = db.Column(db.String(50), nullable=True)
    external_id = db.Column(db.Integer, nullable=False, unique=True)

class FollowedTeam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)

    user = db.relationship('User', backref=db.backref('followed_teams', lazy=True))
    team = db.relationship('Team', backref=db.backref('followers', lazy=True))

class SportsTeam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    sport = db.Column(db.String(50), nullable=True)

class FollowedSportsTeam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('sports_team.id'), nullable=False)

    user = db.relationship('User', backref=db.backref('followed_sports_teams', lazy=True))
    team = db.relationship('SportsTeam', backref=db.backref('followers', lazy=True))

class Goal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String(80))
    game = db.Column(db.String(50), nullable=True)
    game_name = db.Column(db.String(50), nullable=True)
    current_elo = db.Column(db.Integer)
    goal_elo = db.Column(db.Integer)
    platform = db.Column(db.String(20))
    custom_name = db.Column(db.String(100), nullable=True)
    last_updated = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    user = db.relationship('User', backref=db.backref('goals', lazy=True))

    def progress(self):
        if self.goal_elo == 0:
            return 0
        return min(100, int((self.current_elo / self.goal_elo) * 100))

class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cheapshark_id = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    thumb = db.Column(db.String(300))
    cheapest_price = db.Column(db.Float)

class WishlistedGame(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)
    last_notified_price = db.Column(db.Float, nullable=True)

    user = db.relationship('User', backref=db.backref('wishlisted_games', lazy=True))
    game = db.relationship('Game', backref=db.backref('wishlists', lazy=True))
    
class Suggestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    user = db.relationship('User', backref=db.backref('suggestions', lazy=True))
    
class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='tickets')
    messages = db.relationship('TicketMessage', backref='ticket', cascade='all, delete-orphan', passive_deletes=False, lazy=True)

class TicketMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    sender = db.relationship('User')

def create_tables():
    with app.app_context():
        db.create_all()
    logger.info("Databases created")

create_tables()

def schedule_notification(event, reminder_minutes):
    user = User.query.get(event.user_id)
    user_tz = ZoneInfo(user.timezone or 'UTC')

    local_event_dt = datetime.strptime(f"{event.date} {event.time}", '%Y-%m-%d %H:%M:%S')
    local_event_dt = local_event_dt.replace(tzinfo=user_tz)

    reminder_time_utc = (local_event_dt - timedelta(minutes=reminder_minutes)).astimezone(ZoneInfo("UTC"))

    def send_notification_email():
        with app.app_context():
            refreshed_event = Event.query.get(event.id)
            if not refreshed_event:
                logger.error(f"Event with ID {event.id} not found in the database.")
                return

            user = refreshed_event.user
            event_time_local = local_event_dt

            email_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; color: #333; background-color: #f9f9f9; padding: 20px;">
                <div style="background-color: white; border-radius: 8px; padding: 20px; box-shadow: 0px 4px 10px rgba(0, 0, 0, 0.1);">
                    <h2 style="color: #4CAF50; text-align: center;">Event Reminder</h2>
                    <p style="font-size: 16px;">Dear {user.username},</p>
                    <p style="font-size: 16px;">This is a friendly reminder for your upcoming event:</p>
                    <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                        <tr><td style="padding: 8px; font-weight: bold;">Event:</td><td style="padding: 8px;">{refreshed_event.title}</td></tr>
                        <tr><td style="padding: 8px; font-weight: bold;">Date:</td><td style="padding: 8px;">{event_time_local.strftime('%Y-%m-%d')}</td></tr>
                        <tr><td style="padding: 8px; font-weight: bold;">Time:</td><td style="padding: 8px;">{event_time_local.strftime('%H:%M')}</td></tr>
                        <tr><td style="padding: 8px; font-weight: bold;">Location:</td><td style="padding: 8px;">{refreshed_event.location}</td></tr>
                        <tr><td style="padding: 8px; font-weight: bold;">Description:</td><td style="padding: 8px;">{refreshed_event.description}</td></tr>
                    </table>
                    <p style="font-size: 16px;">Thank you for using EventManager!</p>
                    <footer style="text-align: center; font-size: 14px; color: #aaa;">
                        Best regards,<br>
                        The EventManager Team
                    </footer>
                </div>
            </body>
            </html>
            """

            data = {
                'Messages': [
                    {
                        "From": {"Email": "lifeeventmanager666@gmail.com", "Name": "EventManager"},
                        "To": [{"Email": refreshed_event.user.email}],
                        "Subject": f"Reminder: {refreshed_event.title}",
                        "HTMLPart": email_body
                    }
                ]
            }
            try:
                result = mailjet.send.create(data=data)
                logger.info(f"Mailjet Response: {result.status_code}, {result.json()}")
            except Exception as e:
                logger.error(f"Failed to send email for event ID {event.id}: {e}")

    trigger = DateTrigger(run_date=reminder_time_utc)
    scheduler.add_job(send_notification_email, trigger=trigger)
    logger.info(f"Scheduled notification for event '{event.title}' at {reminder_time_utc} UTC")

@app.route("/get_timezone_status")
def get_timezone_status():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        return jsonify({"timezone_set": bool(user.timezone)})
    return jsonify({"timezone_set": False})
    
@app.route("/set_timezone", methods=["POST"])
def set_timezone():
    print("hello")
    logger.info(f"Headers: {request.headers}")
    logger.info(f"Raw data: {request.data}")
    logger.info(f"JSON: {request.get_json()}")
    
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        user_timezone = request.json.get("timezone")
        logger.info(f"Received timezone in POST: {user_timezone}")
        if user_timezone:
            user.timezone = user_timezone
            db.session.commit()
            return jsonify({"status": "success"}), 200
    return jsonify({"status": "failed"}), 400

@app.route('/terms-of-service')
def terms_of_service():
    return render_template('terms_of_service.html')

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')

@app.route('/user_pfp/<int:user_id>')
def user_pfp(user_id):
    user = User.query.get(user_id)
    if not user or not user.pfp:
        return redirect(url_for('static', filename='default_pfp.png'))
    
    return Response(user.pfp, mimetype='image/png')

@app.template_filter('localtime')
def localtime_filter(utc_dt, tz_str):
    if not utc_dt or not tz_str:
        return utc_dt
    try:
        local_dt = utc_dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_str))
        return local_dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return utc_dt.strftime('%Y-%m-%d %H:%M')

@app.route("/")
def home():
    session.pop('verification_code', None)

    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        
        if not user.pfp:
            user.pfp=get_default_pfp()
        
        elif not user.role:
            user.role='user'

        elif not user.timezone:
            print("hello")
            return render_template("home.html", user=user)

        user_tz = ZoneInfo(user.timezone)
        headers = {'Authorization': f'Bearer {PANDASCORE_API_KEY}'}
        now = datetime.now(tz=ZoneInfo("UTC"))
        now_iso = now.isoformat()
        end_iso = (now + timedelta(days=365)).isoformat()

        follows = FollowedTeam.query.filter_by(user_id=user.id).all()
        for follow in follows:
            external_team_id = follow.team.external_id
            m_resp = requests.get(
                f'https://api.pandascore.co/teams/{external_team_id}/matches',
                headers=headers,
                params={'range[begin_at]': f'{now_iso},{end_iso}'}
            )
            if m_resp.status_code != 200:
                continue
            try:
                matches = m_resp.json()
            except ValueError:
                continue
            for match in matches:
                begin = match.get('begin_at')
                if begin:
                    match_time_utc = datetime.fromisoformat(begin.replace('Z', '+00:00'))
                    match_time_local = match_time_utc.astimezone(user_tz)
                    if match_time_local > now.astimezone(user_tz):
                        opps = match.get('opponents', [])
                        if len(opps) == 2:
                            t1 = opps[0]['opponent']['name']
                            t2 = opps[1]['opponent']['name']
                            title = f"{t1} vs {t2}"
                            date = match_time_local.strftime('%Y-%m-%d')
                            time = match_time_local.strftime('%H:%M')
                            location = match.get('videogame', {}).get('name', 'Unknown')
                            exists = Event.query.filter_by(
                                user_id=user.id,
                                title=title,
                                date=date,
                                time=time
                            ).first()
                            if not exists:
                                ev = Event(
                                    title=title,
                                    date=date,
                                    time=time,
                                    description=title,
                                    location=location,
                                    user_id=user.id,
                                    notification_enabled=False,
                                    reminder_time=None
                                )
                                db.session.add(ev)

        followed_sports = FollowedSportsTeam.query.filter_by(user_id=user.id).all()
        for followed in followed_sports:
            sports_team = followed.team
            matches_resp = requests.get(f'https://www.thesportsdb.com/api/v1/json/{SPORTS_API_KEY}/searchevents.php?e={sports_team.name}')
            if matches_resp.status_code != 200:
                continue
            try:
                matches = matches_resp.json().get('event', [])
            except Exception:
                matches = []
            if not matches:
                continue
            filtered_matches = [
                match for match in matches
                if match.get('strHomeTeam') == sports_team.name or match.get('strAwayTeam') == sports_team.name
            ]
            for match in filtered_matches:
                event_date = match.get('dateEvent')
                event_time = match.get('strTime')
                if event_date and event_time:
                    match_time_utc = datetime.strptime(f"{event_date} {event_time}", '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo("UTC"))
                    match_time_local = match_time_utc.astimezone(user_tz)
                    if match_time_local > datetime.now(tz=user_tz):
                        title = match.get('strEvent', 'Unknown Match')
                        location = match.get('strVenue', 'Unknown Venue')
                        date = match_time_local.strftime('%Y-%m-%d')
                        time = match_time_local.strftime('%H:%M')
                        exists = Event.query.filter_by(
                            user_id=user.id,
                            title=title,
                            date=date,
                            time=time
                        ).first()
                        if not exists:
                            ev = Event(
                                title=title,
                                date=date,
                                time=time,
                                description=title,
                                location=location,
                                user_id=user.id
                            )
                            db.session.add(ev)

        db.session.commit()
        return render_template("home.html", user=user)

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

        if User.query.filter_by(email=email).first():
            flash("This email is already registered!", "danger")
            return redirect(url_for('register'))

        if User.query.filter_by(username=username).first():
            flash("This username is already taken!", "danger")
            return redirect(url_for('register'))

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

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
                password=user_data['password'],
                pfp=get_default_pfp(),
                role='user'
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
    except Exception as e:
        logger.error(f"Error sending creation verification email: {e}")

@app.route('/events', methods=['GET'])
def events():
    if 'user_id' not in session:
        flash("Please log in to access this page.", "danger")
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    user_tz = ZoneInfo(user.timezone) if user.timezone else local_tz

    today = datetime.now(tz=ZoneInfo("UTC"))
    today_user = today.astimezone(user_tz)

    view = request.args.get('view', 'weekly')
    direction = request.args.get('direction', 'current')
    jump_to_date = request.args.get('jump_to_date')
    search_event = request.args.get('search_event')

    if 'current_day' in session:
        try:
            current_day = datetime.strptime(session['current_day'], '%d.%m.%Y').replace(tzinfo=user_tz)
        except ValueError:
            current_day = today_user
    else:
        current_day = today_user

    if search_event:
        events_found = Event.query.filter(
            Event.user_id == session['user_id'],
            Event.title.ilike(f"%{search_event}%")
        ).order_by(Event.date.asc()).all()

        if events_found:
            for event in events_found:
                event_datetime_utc = parse_event_datetime(event.date, event.time)
                event_datetime_user = event_datetime_utc.astimezone(user_tz)

                if event_datetime_user >= today_user:
                    jump_to_date = event_datetime_user.strftime('%Y-%m-%d')
                    break
            else:
                last_event = events_found[-1]
                jump_to_date = f"{last_event.date}"

            view = 'daily'
        else:
            flash('No event found with that title.', 'danger')
            return redirect(url_for('events'))

    if jump_to_date:
        try:
            jump_date = datetime.strptime(jump_to_date, '%Y-%m-%d').replace(tzinfo=user_tz)
            current_day = jump_date
        except ValueError:
            current_day = today_user

    if view == 'daily':
        if not jump_to_date:
            if direction == 'prev':
                current_day -= timedelta(days=1)
            elif direction == 'next':
                current_day += timedelta(days=1)
            elif direction == 'current':
                current_day = today_user

        session['current_day'] = current_day.strftime('%d.%m.%Y')

        day_events = Event.query.filter_by(user_id=session['user_id']).filter(
            Event.date == current_day.strftime('%Y-%m-%d')
        ).all()

        formatted_day_events = []
        for event in day_events:
            event_datetime = parse_event_datetime(event.date, event.time)
            formatted_day_events.append({
                'id': event.id,
                'title': event.title,
                'date': event_datetime.strftime('%Y-%m-%d'),
                'time': event_datetime.strftime('%H:%M'),
                'description': event.description,
                'location': event.location
            })

        current_day_obj = {
            'name': current_day.strftime('%A'),
            'date': current_day.strftime('%d.%m.%Y'),
            'events': formatted_day_events
        }

        return render_template(
            'events.html',
            day_events=formatted_day_events,
            view=view,
            current_day=current_day_obj,
            current_date=current_day.strftime('%d.%m.%Y'),
            start_of_week=(current_day - timedelta(days=current_day.weekday())).strftime('%d.%m.%Y'),
            end_of_week=(current_day + timedelta(days=6 - current_day.weekday())).strftime('%d.%m.%Y'),
            preset=current_day.strftime('%Y-%m-%d')
        )

    if view == 'weekly':
        if jump_to_date:
            start_of_week = current_day - timedelta(days=current_day.weekday())
            end_of_week = start_of_week + timedelta(days=6)
        else:
            start_of_week = session.get('start_of_week')
            end_of_week = session.get('end_of_week')

            if not start_of_week or not end_of_week:
                start_of_week = current_day - timedelta(days=current_day.weekday())
                end_of_week = start_of_week + timedelta(days=6)

            if isinstance(start_of_week, str):
                start_of_week = datetime.strptime(start_of_week, '%d.%m.%Y').replace(tzinfo=user_tz)
            if isinstance(end_of_week, str):
                end_of_week = datetime.strptime(end_of_week, '%d.%m.%Y').replace(tzinfo=user_tz)

            if direction == 'prev':
                start_of_week -= timedelta(weeks=1)
                end_of_week -= timedelta(weeks=1)
                current_day = start_of_week
            elif direction == 'next':
                start_of_week += timedelta(weeks=1)
                end_of_week += timedelta(weeks=1)
                current_day = start_of_week
            elif direction == 'current':
                start_of_week = today_user - timedelta(days=today_user.weekday())
                end_of_week = start_of_week + timedelta(days=6)
                current_day = today_user

        session['start_of_week'] = start_of_week.strftime('%d.%m.%Y')
        session['end_of_week'] = end_of_week.strftime('%d.%m.%Y')
        session['current_day'] = current_day.strftime('%d.%m.%Y')

        week_days = []
        for i in range(7):
            day = start_of_week + timedelta(days=i)
            day_events = Event.query.filter_by(
                user_id=session['user_id']
            ).filter(Event.date == day.strftime('%Y-%m-%d')).all()

            formatted_day_events = []
            for event in day_events:
                event_datetime = parse_event_datetime(event.date, event.time)
                formatted_day_events.append({
                    'id': event.id,
                    'title': event.title,
                    'date': event_datetime.strftime('%Y-%m-%d'),
                    'time': event_datetime.strftime('%H:%M'),
                    'description': event.description,
                    'location': event.location
                })

            week_days.append({
                'name': day.strftime('%A'),
                'date': day.strftime('%d-%m-%Y'),
                'events': formatted_day_events
            })

        return render_template(
            'events.html',
            week_days=week_days,
            view=view,
            current_date=current_day.strftime('%d.%m.%Y'),
            start_of_week=start_of_week.strftime('%d.%m.%Y'),
            end_of_week=end_of_week.strftime('%d.%m.%Y'),
            preset=start_of_week.strftime('%Y-%m-%d')
        )

@app.route('/search_events')
def search_events():
    term = request.args.get('term', '')
    results = Event.query.filter(
        Event.user_id == session['user_id'],
        Event.title.ilike(f'%{term}%')
    ).order_by(Event.date.asc()).all()

    return jsonify([
        {'id': e.id, 'title': e.title, 'date': e.date, 'time': e.time}
        for e in results
    ])

@app.route('/event/details/<int:event_id>', methods=['GET'])
def event_details(event_id):
    event = Event.query.get_or_404(event_id)
    event_datetime = parse_event_datetime(event.date, event.time)
    return jsonify({
        'id': event.id,
        'title': event.title,
        'time': event_datetime.strftime('%H:%M'),
        'description': event.description,
        'location': event.location
    })

@app.route('/edit_event/<int:event_id>', methods=['GET', 'POST'])
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)

    if request.method == 'POST':
        try:
            event.title = request.form['event_title']
            event.date = request.form['event_date']
            event.time = request.form['event_time'] + ":00"
            event.description = request.form.get('event_description')
            event.location = request.form.get('event_location')

            notification_enabled = request.form.get('notification_enabled') == 'on'
            reminder_time = int(request.form.get('reminder_time', 0)) if notification_enabled else None

            event.notification_enabled = notification_enabled
            event.reminder_time = reminder_time

            db.session.commit()

            if notification_enabled and reminder_time:
                schedule_notification(event, reminder_time)

            flash("Event updated successfully!", "success")
            return redirect(url_for('events'))
        except ValueError:
            flash("Invalid input. Please check your data.", "danger")
        except Exception as e:
            flash("An error occurred while updating the event.", "danger")
            logger.error(f"Error in edit_event: {e}")

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
        try:
            event_title = request.form['event_title']
            event_date = request.form['event_date']
            event_time = request.form['event_time'] + ":00"
            event_description = request.form.get('event_description')
            event_location = request.form.get('event_location')
            notification_enabled = request.form.get('notification_enabled') == 'on'
            reminder_time = int(request.form.get('reminder_time', 0)) if notification_enabled else None

            if not event_title or not event_date or not event_time:
                flash("Please fill in all required fields!", "danger")
                return redirect(url_for('Create_Event'))

            new_event = Event(
                title=event_title,
                date=event_date,
                time=event_time,
                description=event_description,
                location=event_location,
                user_id=session['user_id'],
                notification_enabled=notification_enabled,
                reminder_time=reminder_time
            )
            db.session.add(new_event)
            db.session.commit()

            if notification_enabled and reminder_time:
                schedule_notification(new_event, reminder_time)

            flash("Event added successfully!", "success")
            return redirect(url_for('events'))

        except ValueError as ve:
            flash(str(ve), "danger")
            logger.error(f"ValueError in Create_Event: {ve}")
        except Exception as e:
            flash("An error occurred while creating the event.", "danger")
            logger.error(f"Error in Create_Event: {e}")

    return render_template('calendar.html')


@app.route('/E-sports', methods=['GET', 'POST'])
def Esports():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    search_query = request.args.get('search', '').strip()
    selected_games = request.args.getlist('game_ids')
    page = int(request.args.get('page', 1))
    per_page = 12
    headers = {'Authorization': f'Bearer {PANDASCORE_API_KEY}'}

    games_resp = requests.get('https://api.pandascore.co/videogames', headers=headers)
    games_list = games_resp.json() if games_resp.status_code == 200 else []
    game_id_map = {g['name']: g['id'] for g in games_list}
    selected_game_ids = [game_id_map[n] for n in selected_games if n in game_id_map]

    if request.method == 'POST':
        external_team_id = int(request.form['team_id'])
        current_count = FollowedTeam.query.join(Team).filter(FollowedTeam.user_id == user.id).count()
        if not user.premium and current_count >= 5:
            flash('Upgrade to premium to follow more than 5 teams.', 'warning')
        else:
            team = Team.query.filter_by(external_id=external_team_id).first()
            if not team:
                team_resp = requests.get(f'https://api.pandascore.co/teams/{external_team_id}', headers=headers)
                if team_resp.status_code == 200:
                    data = team_resp.json()
                    team = Team(
                        name=data['name'],
                        game=data['current_videogame']['name'] if data.get('current_videogame') else None,
                        external_id=external_team_id
                    )
                    db.session.add(team)
            if team and not FollowedTeam.query.filter_by(user_id=user.id, team_id=team.id).first():
                db.session.add(FollowedTeam(user_id=user.id, team_id=team.id))
                db.session.commit()
                flash(f'You are now following {team.name}', 'success')

    followed_links = FollowedTeam.query.filter_by(user_id=user.id).join(Team).all()
    followed_teams_data = []
    for link in followed_links:
        team = link.team
        api_resp = requests.get(f"https://api.pandascore.co/teams/{team.external_id}", headers=headers)
        if api_resp.status_code == 200:
            followed_teams_data.append(api_resp.json())

    followed_ids = {t['id'] for t in followed_teams_data}

    api_params = {'page': page, 'per_page': per_page}
    if search_query:
        api_params['search[name]'] = search_query
    if selected_game_ids:
        api_params['filter[videogame_id]'] = ','.join(map(str, selected_game_ids))

    other_resp = requests.get('https://api.pandascore.co/teams', headers=headers, params=api_params)
    other_teams_data = other_resp.json() if other_resp.status_code == 200 else []

    other_teams_data = [t for t in other_teams_data if t['id'] not in followed_ids]

    return render_template(
        'E-sports.html',
        user=user,
        followed_teams=followed_teams_data,
        other_teams=other_teams_data,
        page=page,
        user_following=[t['id'] for t in followed_teams_data],
        search_query=search_query,
        selected_games=selected_games,
        games_list=games_list
    )

@app.route('/unfollow', methods=['POST'])
def unfollow():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    external_team_id = int(request.form['team_id'])
    team = Team.query.filter_by(external_id=external_team_id).first()
    if team:
        follow = FollowedTeam.query.filter_by(user_id=user.id, team_id=team.id).first()
        if follow:
            now = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
            Event.query.filter_by(user_id=user.id, title=follow.team.name)
            db.session.delete(follow)
            db.session.commit()
            flash(f'Unfollowed {team.name}', 'info')
    return redirect(url_for('Esports', **request.args))

@app.route('/team/<int:team_id>', methods=['GET', 'POST'])
def team_detail(team_id):
    headers = {'Authorization': f'Bearer {PANDASCORE_API_KEY}'}
    
    team_resp = requests.get(f'https://api.pandascore.co/teams/{team_id}', headers=headers)
    if team_resp.status_code != 200:
        flash('Could not load team data.', 'danger')
        return redirect(url_for('Esports'))
    
    team = team_resp.json()

    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    match_resp = requests.get(
        f'https://api.pandascore.co/teams/{team_id}/matches',
        headers=headers,
        params={'sort': '-begin_at', 'per_page': 5, 'filter[begin_at]': f'>{thirty_days_ago}'}
    )
    matches = match_resp.json() if match_resp.status_code == 200 else []

    is_following = False
    follow_limit_reached = False
    followed_teams_count = 0
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        followed_teams_count = FollowedTeam.query.filter_by(user_id=user.id).count()
        follow_limit_reached = not user.premium and followed_teams_count >= 5

        local_team = Team.query.filter_by(external_id=team_id).first()
        if local_team:
            is_following = FollowedTeam.query.filter_by(user_id=user.id, team_id=local_team.id).first() is not None

        if request.method == 'POST':
            if is_following:
                follow_record = FollowedTeam.query.filter_by(user_id=user.id, team_id=local_team.id).first()
                db.session.delete(follow_record)
                db.session.commit()
                flash(f'You have unfollowed {team["name"]}.', 'success')
                return redirect(url_for('team_detail', team_id=team_id))
            elif not follow_limit_reached:
                if not local_team:
                    local_team = Team(name=team['name'], external_id=team_id, game=team['current_videogame']['name'])
                    db.session.add(local_team)
                    db.session.commit()
                db.session.add(FollowedTeam(user_id=user.id, team_id=local_team.id))
                db.session.commit()
                flash(f'You are now following {team["name"]}.', 'success')
                return redirect(url_for('team_detail', team_id=team_id))
            else:
                flash('Follow limit reached. Upgrade to premium to follow more teams.', 'warning')

    return render_template(
        'team_detail.html', 
        team=team, 
        matches=matches,
        is_following=is_following,
        follow_limit_reached=follow_limit_reached,
        followed_teams_count=followed_teams_count
    )

API_KEY = "3"

sports_list = [
    "Soccer", "Basketball", "American Football", "Baseball", 
    "Ice Hockey", "Cricket", "Rugby", "Motorsport", "Golf"
]

sport_leagues = {
    "Soccer": [
        "English Premier League", "English League Championship", "Scottish Premier League", 
        "Greek Superleague Greece", "Dutch Eredivisie", "Belgian Pro League", "Turkish Super Lig", 
        "Danish Superliga", "Portuguese Primeira Liga", "American Major League Soccer", 
        "Swedish Allsvenskan", "Mexican Primera League", "Brazilian Serie A", "Ukrainian Premier League", 
        "Russian Football Premier League", "Australian A-League", "Norwegian Eliteserien", "Chinese Super League", 
        "Italian Serie B", "Scottish Championship", "English League 1", "English League 2", 
        "Italian Serie C Girone C", "German 2. Bundesliga", "Spanish La Liga 2", "Swedish Superettan", 
        "Brazilian Serie B"
    ],
    "Basketball": [
        "NBA", "WNBA", "Spanish Liga ACB"
    ],
    "American Football": [
        "NFL", "CFL"
    ],
    "Baseball": [
        "MLB"
    ],
    "Ice Hockey": [
        "NHL", "UK Elite Ice Hockey League"
    ],
    "Cricket": [
        "Indian Premier League", "Argentinian Primera Division"
    ],
    "Rugby": [
        "Six Nations Championship", "Super Rugby"
    ],
    "Motorsport": [
        "Formula 1", "Formula E", "IndyCar Series", "British GT Championship", "WTCC"
    ],
    "Golf": [
        "PGA Tour"
    ]
}

def get_teams_by_name(team_name, selected_sports=None):
    if not team_name:
        return []

    url = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}/searchteams.php?t={team_name}"
    
    response = requests.get(url)
    if response.status_code != 200:
        return []
    
    try:
        teams = response.json().get('teams', [])
        if not teams:
            return []
        
        filtered_teams = []
        for team in teams:
            if 'strSport' in team and (selected_sports is None or team['strSport'] in selected_sports):
                filtered_teams.append({
                    "name": team.get('strTeam', 'Unknown'),
                    "logo": team.get('strTeamBadge') or team.get('strLogo'),
                    "league": team.get('strLeague', 'Unknown'),
                    "sport": team.get('strSport', 'Unknown'),
                    "id": team.get('idTeam')
                })
        return filtered_teams
    except Exception as e:
        return []

def get_random_teams(sports_list, sport_leagues, num_teams=12, teams_per_league=3):
    random_teams = []
    selected_leagues = random.sample([league for leagues in sport_leagues.values() for league in leagues], 4)

    for random_league in selected_leagues:
        url = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}/search_all_teams.php?l={random_league}"
        response = requests.get(url)

        if response.status_code == 200:
            teams = response.json().get('teams', [])
            for team in teams[:teams_per_league]:
                random_teams.append({
                    "name": team['strTeam'],
                    "logo": team.get('strTeamBadge') or team.get('strLogo'),
                    "league": team.get('strLeague', 'Unknown'),
                    "sport": team.get('strSport', 'Unknown'),
                    "id": team.get('idTeam')
                })
                if len(random_teams) >= num_teams:
                    break
        if len(random_teams) >= num_teams:
            break

    return random_teams

@app.route('/Sports', methods=['GET', 'POST'])
def Sports():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    search_query = request.args.get('search', '').strip()
    per_page = 12

    followed_links = FollowedSportsTeam.query.filter_by(user_id=user.id).join(SportsTeam).all()
    followed_teams_data = []

    for link in followed_links:
        team = link.team
        team_data = {
            "id": team.id,
            "name": team.name,
            "sport": team.sport,
            "logo": None,
            "league": "Unknown"
        }
        if team.name:
            url = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}/searchteams.php?t={team.name}"
            response = requests.get(url)
            if response.status_code == 200:
                results = response.json().get('teams') or []
                for result in results:
                    if result.get('strTeam', '').lower() == team.name.lower():
                        team_data["logo"] = result.get('strTeamBadge') or result.get('strLogo')
                        team_data["league"] = result.get('strLeague', 'Unknown')
                        break
        followed_teams_data.append(team_data)

    followed_team_names = [t["name"] for t in followed_teams_data]

    if search_query:
        other_teams_data = get_teams_by_name(search_query)
    else:
        other_teams_data = get_random_teams(sports_list, sport_leagues, num_teams=per_page)

    other_teams_data = [team for team in other_teams_data if team.get('name') and team['name'] not in followed_team_names]

    if request.method == 'POST':
        team_name = request.form.get('team_name')
        team_sport = request.form.get('team_sport')
        team_logo = request.form.get('team_logo')
        team_league = request.form.get('team_league')
        team_id = request.form.get('team_id')

        if team_name:
            try:
                team = SportsTeam.query.filter_by(name=team_name).first()
                if not team:
                    team = SportsTeam(name=team_name, sport=team_sport)
                    db.session.add(team)
                    db.session.commit()

                existing_follow = FollowedSportsTeam.query.filter_by(user_id=user.id, team_id=team.id).first()
                if not existing_follow:
                    if not user.premium and len(followed_teams_data) >= 5:
                        flash('You can only follow up to 5 teams as a non-premium user.', 'warning')
                    else:
                        db.session.add(FollowedSportsTeam(user_id=user.id, team_id=team.id))
                        db.session.commit()
                        flash(f'You are now following {team.name}', 'success')
                        return redirect(url_for('Sports'))
                else:
                    flash('You are already following this team.', 'warning')
            except Exception as e:
                flash(f'Error following team: {str(e)}', 'danger')
                db.session.rollback()

    return render_template(
        'sports.html',
        user=user,
        followed_teams=followed_teams_data,
        other_teams=other_teams_data,
        search_query=search_query,
        sports_list=sports_list,
        per_page=per_page,
        user_following=followed_team_names
    )

@app.route('/unfollow_sports', methods=['POST'])
def unfollow_sports():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    team_id = int(request.form['team_id'])
    
    team = SportsTeam.query.get(team_id)
    
    if team:
        follow = FollowedSportsTeam.query.filter_by(user_id=user.id, team_id=team.id).first()
        if follow:
            db.session.delete(follow)
            db.session.commit()
            flash(f'You have unfollowed {team.name}', 'info')
    
    return redirect(url_for('Sports', **request.args))

@app.route('/sports_team/<team_id>')
def sports_team_detail(team_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.get(session['user_id'])

    local_team = None
    external_id = None
    team_data = None

    try:
        local_id = int(team_id)
        local_team = SportsTeam.query.get(local_id)
    except ValueError:
        external_id = team_id

    team_name = local_team.name if local_team else request.args.get('team_name')
    
    if not team_name and external_id:
        ext_team_url = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}/lookupteam.php?id={external_id}"
        ext_team_resp = requests.get(ext_team_url)
        if ext_team_resp.status_code == 200 and ext_team_resp.json().get('teams'):
            team_data = ext_team_resp.json()['teams'][0]
            team_name = team_data['strTeam']

    if not team_data and team_name:
        team_url = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}/searchteams.php?t={team_name}"
        team_resp = requests.get(team_url)
        if team_resp.status_code == 200 and team_resp.json().get('teams'):
            teams = team_resp.json()['teams']
            for t in teams:
                if t['strTeam'].lower() == team_name.lower():
                    team_data = t
                    break
            if not team_data:
                team_data = teams[0]

    if not team_data:
        flash('Team not found', 'danger')
        return redirect(url_for('Sports'))

    manager = team_data.get('strManager')
    team_nickname = team_data.get('strAlternate')
    team_awards = team_data.get('strAwards')
    team_colors = team_data.get('strTeamColors')

    is_following = False
    team_in_db = SportsTeam.query.filter_by(name=team_data['strTeam']).first()
    if team_in_db:
        is_following = FollowedSportsTeam.query.filter_by(user_id=user.id, team_id=team_in_db.id).first() is not None

    followed_sports_teams_count = FollowedSportsTeam.query.filter_by(user_id=user.id).count()
    follow_limit_reached = not user.premium and followed_sports_teams_count >= 5

    return render_template(
        'sports_team_detail.html', 
        team=team_data,
        manager=manager,
        team_nickname=team_nickname,
        team_awards=team_awards,
        team_colors=team_colors,
        is_following=is_following,
        team_in_db=team_in_db,
        user=user,
        followed_teams_count=followed_sports_teams_count,
        follow_limit_reached=follow_limit_reached
    )

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    user = User.query.filter_by(username=session['username']).first()

    if request.method == 'POST':
        new_email = request.form.get('email')
        phone = request.form.get('phone')
        uploaded_file = request.files.get('pfp')

        updated = False

        if uploaded_file and uploaded_file.filename != '':
            im = Image.open(uploaded_file)
            im = im.convert("RGBA")
            im = im.resize((256, 256), Image.Resampling.LANCZOS)

            mask = Image.new('L', im.size, 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, im.size[0], im.size[1]), fill=255)
            im.putalpha(mask)

            img_byte_arr = BytesIO()
            im.save(img_byte_arr, format='PNG')
            img_byte_arr = img_byte_arr.getvalue()

            user.pfp = img_byte_arr
            updated = True
            flash("Profile picture updated successfully!", "success")

        if user:
            if new_email != user.email:
                verification_code = str(random.randint(100000, 999999))
                session['pending_email'] = new_email
                session['email_verification_code'] = verification_code
                send_update_email(new_email, verification_code)
                flash("A verification code has been sent to your new email. Please confirm to update your email.", "info")
                return redirect(url_for('verify_email'))

            if phone != user.phone_number:
                user.phone_number = phone
                updated = True
                flash("Phone number updated successfully!", "success")

        if updated:
            db.session.commit()
            session['phone'] = phone
            return redirect(url_for('settings'))

        flash("No changes were made.", "info")

    return render_template('settings.html', user=user)

def send_update_email(email, update_code):
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
                    "Subject": "Email Update Verification Code – EventManager",
                    "HTMLPart": f"""
                    <html>
                    <body style="font-family: Arial, sans-serif; color: #333;">
                        <div style="background-color: #f4f9ff; padding: 20px; border-radius: 8px; border: 1px solid #bee5eb;">
                            <h2 style="color: #0d6efd; text-align: center;">Verify Your Email Address</h2>
                            <p style="font-size: 16px; color: #555;">
                                Dear User,<br><br>
                                We received a request to change the email address associated with your EventManager account.
                            </p>
                            <p style="font-size: 16px; color: #555;">
                                <strong>Please enter the verification code below to confirm the email update:</strong>
                            </p>
                            <div style="background-color: #d1ecf1; padding: 15px; font-size: 20px; font-weight: bold; text-align: center; color: #0c5460;">
                                {update_code}
                            </div>
                            <p style="font-size: 16px; color: #555;">
                                If you did not request this email change, please ignore this message or contact our support team immediately.
                            </p>
                            <p style="font-size: 16px; color: #555;">
                                This code is valid for a limited time. Your account will not be updated until you confirm the new email address.
                            </p>
                            <footer style="text-align: center; font-size: 14px; color: #aaa; margin-top: 30px;">
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
    except Exception as e:
        logger.error(f"Error sending update verification email: {e}")

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

FACEIT_GAMES = {
    "cs2": "Counter-Strike 2",
    "csgo": "Counter-Strike: Global Offensive",
    "valorant": "Valorant",
    "dota2": "Dota 2",
    "rocket_league": "Rocket League",
    "rainbow6": "Rainbow Six Siege",
    "apex": "Apex Legends",
    "r6": "Rainbow Six Siege",
    "leagueoflegends": "League of Legends",
    "fortnite": "Fortnite",
    "pubg": "PUBG: Battlegrounds",
    "smite": "Smite",
    "overwatch": "Overwatch",
    "teamfortress2": "Team Fortress 2",
    "battalion1944": "Battalion 1944",
    "h1z1": "H1Z1",
    "paladins": "Paladins",
    "tft": "Teamfight Tactics",
    "crossfire": "Crossfire",
    "warface": "Warface",
    "worldofwarships": "World of Warships",
    "warframe": "Warframe",
    "blackdesert": "Black Desert Online",
    "fallguys": "Fall Guys",
    "worldofwarcraft": "World of Warcraft",
    "hearthstone": "Hearthstone",
    "fifa": "FIFA",
    "nba2k": "NBA 2K",
    "starcraft": "StarCraft II",
    "heroesofthestorm": "Heroes of the Storm",
    "gwent": "Gwent",
    "hearthstone": "Hearthstone",
    "magic": "Magic: The Gathering Arena"
}

def get_youtube_subscribers(channel_identifier):
    try:
        api_key = os.getenv("YOUTUBE_API")
        if not api_key:
            raise ValueError("YouTube API key is missing")

        channel_id = None
        if channel_identifier.startswith('@'):
            url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet&forHandle={channel_identifier[1:]}&key={api_key}"
            res = requests.get(url)
            res.raise_for_status()
            data = res.json()
            if 'items' in data and data["items"]:
                channel_id = data["items"][0]["id"]
            else:
                raise ValueError(f"Channel not found for handle: {channel_identifier}")
        else:
            channel_id = channel_identifier

        url = f"https://www.googleapis.com/youtube/v3/channels?part=statistics&id={channel_id}&key={api_key}"
        res = requests.get(url)
        res.raise_for_status()
        data = res.json()
        if 'items' in data and data["items"]:
            return int(data["items"][0]["statistics"]["subscriberCount"])
        else:
            raise ValueError(f"No statistics found for channel: {channel_id}")
    except Exception as e:
        logger.error(f"Error in get_youtube_subscribers: {e}")
        return None

@app.route('/goals', methods=['GET', 'POST'])
def goals():
    error = None
    user = User.query.get(session['user_id']) if 'user_id' in session else None

    if request.method == 'POST':
        if not user:
            flash('Please log in to manage your goals.', 'danger')
            return redirect(url_for('login'))

        user_goals_count = Goal.query.filter_by(user_id=user.id).count()
        if not user.premium and user_goals_count >= 5:
            flash('You can only have up to 5 goals as a non-premium user. Upgrade to premium to add more goals.', 'warning')
            return redirect(url_for('goals'))

        platform = request.form['platform']

        if platform == 'faceit':
            nickname = request.form['nickname']
            game = request.form['game']
            goal_elo = int(request.form['goal_elo'])

            headers = {"Authorization": f"Bearer {FACEIT_API_KEY}"}
            player_url = f"{FACEIT_API_URL}/players?nickname={nickname}"
            res = requests.get(player_url, headers=headers)

            if res.status_code == 200:
                data = res.json()
                player_games = data.get('games', {})
                current_elo = player_games.get(game, {}).get('faceit_elo')

                if current_elo is not None:
                    goal = Goal(
                        nickname=nickname,
                        game=game,
                        game_name=FACEIT_GAMES.get(game, game),
                        current_elo=current_elo,
                        goal_elo=goal_elo,
                        platform='faceit',
                        last_updated=datetime.now(),
                        user_id=user.id
                    )
                    db.session.add(goal)
                    db.session.commit()
                    flash('FACEIT Goal added!', 'success')
                    return redirect(url_for('goals'))
                else:
                    error = f"No ELO data found for {game}."
            else:
                error = "FACEIT player not found."

        elif platform == 'youtube':
            channel_id = request.form['channel_id']
            goal_subs = int(request.form['goal_subs'])
            current_subs = get_youtube_subscribers(channel_id)

            if current_subs is not None:
                goal = Goal(
                    nickname=channel_id,
                    current_elo=current_subs,
                    goal_elo=goal_subs,
                    game_name="YouTube Subscribers",
                    platform='youtube',
                    last_updated=datetime.now(),
                    user_id=user.id
                )
                db.session.add(goal)
                db.session.commit()
                flash("YouTube goal added!", "success")
                return redirect(url_for('goals'))
            else:
                error = "Failed to retrieve YouTube data."

        elif platform == 'custom':
            custom_name = request.form['custom_name']
            current_value = int(request.form['current_value'])
            goal_value = int(request.form['goal_value'])

            goal = Goal(
                nickname="Custom",
                custom_name=custom_name,
                current_elo=current_value,
                goal_elo=goal_value,
                game_name=custom_name,
                platform='custom',
                last_updated=datetime.now(),
                user_id=user.id
            )
            db.session.add(goal)
            db.session.commit()
            flash("Custom goal added!", "success")
            return redirect(url_for('goals'))

        if error:
            flash(error, 'danger')

    goals = Goal.query.filter_by(user_id=user.id).all() if user else []
    return render_template("goals.html", goals=goals, faceit_games=FACEIT_GAMES)

@app.route('/goals/update/<int:goal_id>', methods=['POST'])
def update_custom_goal(goal_id):
    goal = Goal.query.get_or_404(goal_id)
    
    if goal.platform == 'custom':
        try:
            new_value = int(request.form['current_value'])
            goal.current_elo = new_value
            goal.last_updated = datetime.now()
            db.session.commit()
            flash("Goal progress updated!", "success")
        except ValueError:
            flash("Invalid value entered.", "danger")
            
    return redirect(url_for('goals'))

@app.route('/goals/delete/<int:goal_id>', methods=['POST'])
def delete_goal(goal_id):
    goal = Goal.query.get_or_404(goal_id)
    db.session.delete(goal)
    db.session.commit()
    return redirect(url_for('goals'))

@app.route("/games", methods=["GET", "POST"])
def games():
    if 'user_id' not in session:
        flash("Please log in to access this page.", "danger")
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    page = int(request.args.get('page', 1))
    per_page = 12
    search_query = request.args.get('search', '').strip()
    on_sale = request.args.get('on_sale', '') == "1"

    if request.method == 'POST':
        game_id = request.form.get('game_id')
        game_title = request.form.get('game_title')
        game_thumb = request.form.get('game_thumb')
        game_price = request.form.get('game_price')
        if not user.premium and WishlistedGame.query.filter_by(user_id=user.id).count() >= 5:
            flash('Upgrade to premium to wishlist more than 5 games.', 'warning')
        else:
            game = Game.query.filter_by(cheapshark_id=game_id).first()
            if not game:
                game = Game(cheapshark_id=game_id, name=game_title, thumb=game_thumb, cheapest_price=game_price)
                db.session.add(game)
                db.session.commit()
            if not WishlistedGame.query.filter_by(user_id=user.id, game_id=game.id).first():
                db.session.add(WishlistedGame(user_id=user.id, game_id=game.id, last_notified_price=None))
                db.session.commit()
                flash(f"{game_title} added to your wishlist!", "success")
            else:
                flash("Game already in wishlist.", "info")

    wishlisted_games = []
    for wg in user.wishlisted_games:
        game = wg.game
        resp = requests.get(f"{CHEAPSHARK_API_BASE}/games", params={"id": game.cheapshark_id})
        data = resp.json() if resp.status_code == 200 else {}
        info = data.get('info', {})
        deals = data.get('deals', [])
        price = deals[0]['price'] if deals else (game.cheapest_price or "N/A")
        title = info.get('title') or game.name
        thumb = info.get('thumb') or game.thumb
        wishlisted_games.append({
            "cheapshark_id": game.cheapshark_id,
            "title": title,
            "thumb": thumb,
            "price": price
        })

    if search_query:
        if on_sale:
            deals_resp = requests.get(
                f"{CHEAPSHARK_API_BASE}/deals",
                params={"title": search_query, "storeID": "1", "pageSize": per_page, "pageNumber": page-1}
            )
            games_list = deals_resp.json() if deals_resp.status_code == 200 else []
        else:
            games_resp = requests.get(
                f"{CHEAPSHARK_API_BASE}/games",
                params={"title": search_query, "limit": per_page, "pageNumber": page-1}
            )
            games_list = games_resp.json() if games_resp.status_code == 200 else []
        total_games = len(games_list)
        page_count = 1
    else:
        deals_resp = requests.get(
            f"{CHEAPSHARK_API_BASE}/deals",
            params={"storeID": "1", "pageSize": per_page, "pageNumber": page-1}
        )
        games_list = deals_resp.json() if deals_resp.status_code == 200 else []
        total_games = 100
        page_count = math.ceil(total_games / per_page)

    wishlisted_ids = {w.game.cheapshark_id for w in user.wishlisted_games}
    has_next = len(games_list) == per_page
    has_prev = page > 1

    return render_template(
        "games.html",
        user=user,
        games_list=games_list,
        wishlisted_ids=wishlisted_ids,
        wishlisted_games=wishlisted_games,
        page=page,
        has_next=has_next,
        has_prev=has_prev,
        search_query=search_query,
        on_sale=on_sale
    )

@app.route("/games/details/<cheapshark_id>")
def game_details(cheapshark_id):
    info_resp = requests.get(f"https://www.cheapshark.com/api/1.0/games", params={"id": cheapshark_id})
    if info_resp.status_code == 200:
        return jsonify(info_resp.json())
    return jsonify({"error": "Game not found"}), 404

@app.route("/games/unwishlist/<cheapshark_id>", methods=["POST"])
def unwishlist_game(cheapshark_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    game = Game.query.filter_by(cheapshark_id=cheapshark_id).first()
    if game:
        w = WishlistedGame.query.filter_by(user_id=user.id, game_id=game.id).first()
        if w:
            db.session.delete(w)
            db.session.commit()
            flash(f"{game.name} removed from your wishlist.", "info")
    return redirect(url_for('games', **request.args))

def send_deletion_email(email, deletion_code):
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
                    "Subject": "Your Account Deletion Code – EventManager",
                    "HTMLPart": f"""
                    <html>
                    <body style="font-family: Arial, sans-serif; color: #333;">
                        <div style="background-color: #fff3f3; padding: 20px; border-radius: 8px; border: 1px solid #f5c2c7;">
                            <h2 style="color: #c82333; text-align: center;">Account Deletion Request</h2>
                            <p style="font-size: 16px; color: #555;">
                                Dear User,<br><br>
                                We have received a request to permanently delete your EventManager account.
                            </p>
                            <p style="font-size: 16px; color: #555;">
                                <strong>Please use the confirmation code below to proceed with the deletion:</strong>
                            </p>
                            <div style="background-color: #f8d7da; padding: 15px; font-size: 20px; font-weight: bold; text-align: center; color: #721c24;">
                                {deletion_code}
                            </div>
                            <p style="font-size: 16px; color: #555;">
                                <strong>⚠️ WARNING:</strong> This action is <u>permanent</u> and <u>cannot be undone</u>.
                                Once deleted, your account and all associated data will be permanently removed and cannot be recovered.
                            </p>
                            <p style="font-size: 16px; color: #555;">
                                If you did not request this deletion, please contact us immediately or ignore this email to cancel the request.
                            </p>
                            <p style="font-size: 16px; color: #555;">
                                Your security is important to us. We will not proceed unless this code is confirmed from your account.
                            </p>
                            <footer style="text-align: center; font-size: 14px; color: #aaa; margin-top: 30px;">
                                Regards,<br>
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
    except Exception as e:
        logger.error(f"Error sending deletion verification email: {e}")

@app.route('/request_delete_account', methods=['POST'])
def request_delete_account():
    user = User.query.filter_by(username=session['username']).first()

    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('settings'))

    delete_code = str(random.randint(100000, 999999))
    session['delete_account_code'] = delete_code
    session['delete_account_user_id'] = user.id

    send_deletion_email(user.email, delete_code)
    flash("A confirmation code has been sent to your email. Please enter it to delete your account.", "info")
    return redirect(url_for('confirm_delete_account'))

@app.route('/confirm_delete_account', methods=['GET', 'POST'])
def confirm_delete_account():
    if request.method == 'POST':
        input_code = request.form.get('code')
        if input_code == session.get('delete_account_code'):
            user_id = session.get('delete_account_user_id')
            user = User.query.get(user_id)
            if user:
                db.session.delete(user)
                db.session.commit()
                session.clear()
                flash("Your account has been permanently deleted.", "success")
                return redirect(url_for('login'))
            flash("User not found.", "danger")
        else:
            flash("Incorrect confirmation code. Please try again.", "danger")

    return render_template('confirm_delete_account.html')

@app.route('/suggestions', methods=['GET', 'POST'])
def suggestions():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user = User.query.get(user_id)

    if user.role == 'blacklisted':
        flash("You are blacklisted from submitting suggestions!", "danger")
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        category = request.form.get('category')

        if not title or not description:
            flash("Title and description are required.", "danger")
            return redirect(url_for('suggestions'))

        suggestion = Suggestion(
            title=title,
            description=description,
            category=category,
            user_id=user.id
        )
        db.session.add(suggestion)
        db.session.commit()
        flash("Suggestion submitted successfully!", "success")
        return redirect(url_for('suggestions'))

    return render_template('suggestions.html', user=user)

@app.route('/suggestions_admin', methods=['GET', 'POST'])
def suggestions_admin():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user = User.query.get(user_id)
    if not user or user.role != 'admin':
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        blacklist_user_id = request.form.get('blacklist_user_id')
        if blacklist_user_id:
            user_to_blacklist = User.query.get(int(blacklist_user_id))
            if user_to_blacklist:
                user_to_blacklist.role = 'blacklisted'
                Suggestion.query.filter_by(user_id=user_to_blacklist.id).delete()
                db.session.commit()
                flash(f'User {user_to_blacklist.username} has been blacklisted and their suggestions deleted.', 'success')
            else:
                flash('User not found.', 'danger')

        return redirect(url_for('suggestions_admin'))

    all_suggestions = Suggestion.query.order_by(Suggestion.timestamp.desc()).all()
    return render_template('suggestions_admin.html', suggestions=all_suggestions, user_timezone=user.timezone)


@app.route('/suggestion/delete/<int:suggestion_id>', methods=['POST'])
def delete_suggestion(suggestion_id):
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    if not user.role == 'admin':
        flash("Unauthorized", "danger")
        return redirect(url_for('home'))

    suggestion = Suggestion.query.get_or_404(suggestion_id)
    db.session.delete(suggestion)
    db.session.commit()
    flash("Suggestion deleted successfully.", "success")
    return redirect(url_for('suggestions_admin'))

@app.route('/tickets', methods=['GET', 'POST'])
def tickets():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    if not user:
        return redirect(url_for('login'))

    if user.role == 'blacklisted':
        flash('You are blacklisted from accessing the ticket system.', 'danger')
        return redirect(url_for('home'))

    max_tickets = 3 if user.premium else 1
    open_tickets = Ticket.query.filter_by(user_id=user.id).count()

    if request.method == 'POST':
        if 'ticket_id' in request.form:
            ticket_id = int(request.form['ticket_id'])
            message = request.form['message']
            db.session.add(TicketMessage(
                ticket_id=ticket_id,
                sender_id=user.id,
                message=message
            ))
            db.session.commit()
            flash('Reply sent.', 'success')
        else:
            if open_tickets >= max_tickets:
                flash(f"You've reached your ticket limit ({max_tickets}).", 'warning')
            else:
                title = request.form['title']
                message = request.form['message']
                ticket = Ticket(title=title, user_id=user.id)
                db.session.add(ticket)
                db.session.flush()
                initial_message = TicketMessage(
                    ticket_id=ticket.id,
                    sender_id=user.id,
                    message=message
                )
                db.session.add(initial_message)
                db.session.commit()
                flash('Ticket submitted successfully.', 'success')
        return redirect(url_for('tickets'))

    user_tickets = Ticket.query.filter_by(user_id=user.id).order_by(Ticket.timestamp.desc()).all()
    return render_template('tickets.html', tickets=user_tickets, user_timezone=user.timezone)

@app.route('/tickets_admin', methods=['GET', 'POST'])
def tickets_admin():
    user_id = session.get('user_id')
    if not user_id:
        flash('Please login first!', 'warning')
        return redirect(url_for('login'))

    user = User.query.get(user_id)
    if not user or user.role not in ['admin', 'support']:
        flash('You are not allowed to access this!', 'danger')
        return redirect(url_for('home'))

    if request.method == 'POST':
        if 'blacklist_user_id' in request.form:
            uid = int(request.form['blacklist_user_id'])
            target_user = User.query.get(uid)
            if target_user:
                target_user.role = 'blacklisted'
                tickets = Ticket.query.filter_by(user_id=uid).all()
                for ticket in tickets:
                    db.session.delete(ticket)
                db.session.commit()
                flash(f'{target_user.username} has been blacklisted and their tickets deleted.', 'warning')
        elif 'delete_ticket_id' in request.form:
            tid = int(request.form['delete_ticket_id'])
            ticket = Ticket.query.get(tid)
            if ticket:
                db.session.delete(ticket)
            db.session.commit()
            flash('Ticket deleted.', 'info')
        elif 'message' in request.form:
            ticket_id = int(request.form['ticket_id'])
            message = request.form['message']
            db.session.add(TicketMessage(
                ticket_id=ticket_id,
                sender_id=user.id,
                message=message
            ))
            db.session.commit()
            flash('Response sent.', 'success')
        return redirect(url_for('tickets_admin'))

    premium_tickets = Ticket.query.join(User).filter(User.premium == True).order_by(Ticket.timestamp.desc()).all()
    regular_tickets = Ticket.query.join(User).filter(User.premium == False).order_by(Ticket.timestamp.desc()).all()
    tickets = premium_tickets + regular_tickets

    return render_template('tickets_admin.html', tickets=tickets, user_timezone=user.timezone)

if __name__ == "__main__":
    app.run(debug=True)
