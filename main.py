import random
import os
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from mailjet_rest import Client
from dotenv import load_dotenv
from datetime import datetime, timedelta
import requests
from zoneinfo import ZoneInfo

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

mailjet = Client(auth=(os.getenv('MAILJET_API_KEY'), os.getenv('MAILJET_API_SECRET')), version='v3.1')
PANDASCORE_API_KEY = os.getenv("E_SPORTS_API_KEY")
SPORTS_API_KEY = os.getenv("SPORTS_API_KEY")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    phone_number = db.Column(db.String(255), unique=True, nullable=True)
    premium = db.Column(db.Boolean, default=False)
    timezone = db.Column(db.String(50), nullable=True)

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(10), nullable=False)
    time = db.Column(db.String(5), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    location = db.Column(db.String(255), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

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

def create_tables():
    with app.app_context():
        db.create_all()
    print("Databases created")

create_tables()

@app.route("/")
def home():
    session.pop('verification_code', None)
    if 'user_id' in session:
        user = User.query.get(session['user_id'])

        if not user.timezone:
            user_timezone = request.headers.get('X-Timezone')
            if user_timezone:
                user.timezone = user_timezone
                db.session.commit()

        if user.timezone:
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
                                        user_id=user.id
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
    except Exception as e:
        print(f"Error sending email: {e}")

@app.route('/events', methods=['GET'])
def events():
    today = datetime.today()

    view = request.args.get('view', 'weekly')
    direction = request.args.get('direction', 'current')
    jump_to_date = request.args.get('jump_to_date')
    search_event = request.args.get('search_event')

    if 'current_day' in session:
        try:
            current_day = datetime.strptime(session['current_day'], '%d.%m.%Y')
        except ValueError:
            current_day = today
    else:
        current_day = today

    if search_event:
        events_found = Event.query.filter(
            Event.user_id == session['user_id'],
            Event.title.ilike(f"%{search_event}%")
        ).order_by(Event.date.asc()).all()

        if events_found:
            today = datetime.today()
            for event in events_found:
                event_date = datetime.strptime(event.date, '%Y-%m-%d')
                if event_date >= today:
                    jump_to_date = event.date
                    break
            else:
                jump_to_date = events_found[-1].date

            view = 'daily'
        else:
            flash('No event found with that title.', 'danger')
            return redirect(url_for('events'))

    if jump_to_date:
        try:
            jump_date = datetime.strptime(jump_to_date, '%Y-%m-%d')
            current_day = jump_date
        except ValueError:
            jump_date = today
            current_day = today

    if view == 'daily':
        if not jump_to_date:
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
                start_of_week = datetime.strptime(start_of_week, '%d.%m.%Y')
            if isinstance(end_of_week, str):
                end_of_week = datetime.strptime(end_of_week, '%d.%m.%Y')

            if direction == 'prev':
                start_of_week -= timedelta(weeks=1)
                end_of_week -= timedelta(weeks=1)
                current_day = start_of_week
            elif direction == 'next':
                start_of_week += timedelta(weeks=1)
                end_of_week += timedelta(weeks=1)
                current_day = start_of_week
            elif direction == 'current':
                start_of_week = today - timedelta(days=today.weekday())
                end_of_week = start_of_week + timedelta(days=6)
                current_day = today

        session['start_of_week'] = start_of_week.strftime('%d.%m.%Y')
        session['end_of_week'] = end_of_week.strftime('%d.%m.%Y')
        session['current_day'] = current_day.strftime('%d.%m.%Y')

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
                results = response.json().get('teams', [])
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
