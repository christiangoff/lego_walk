import os
import json
import secrets
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta
from PIL import Image, ImageOps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, InviteCode, Friendship, HighFive, Profile, WeightLog, LegoSet, Session

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "lego-workout-secret-key-2024")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "lego_workout.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit
AVATAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

# Make enumerate available in Jinja2 templates
app.jinja_env.globals['enumerate'] = enumerate


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def calculate_calories(duration_minutes, weight_kg, speed_mph=3.0):
    """Calculate calories burned using MET formula.
    MET values: ~2.8 at 2mph, ~3.5 at 3mph, ~4.3 at 3.5mph, ~5.0 at 4mph
    """
    if not duration_minutes or not weight_kg:
        return None
    if speed_mph <= 2.0:
        met = 2.5
    elif speed_mph <= 2.5:
        met = 2.8
    elif speed_mph <= 3.0:
        met = 3.5
    elif speed_mph <= 3.5:
        met = 4.3
    elif speed_mph <= 4.0:
        met = 5.0
    else:
        met = 5.5
    calories = duration_minutes * (met * weight_kg / 60)
    return round(calories, 1)


def get_or_create_profile(user_id):
    profile = Profile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = Profile(name="Athlete", user_id=user_id)
        db.session.add(profile)
        db.session.commit()
    return profile


def aggregate_sessions_by_day(sessions):
    """Combine same-day sessions into one data point. sessions must be in ascending date order."""
    from collections import OrderedDict
    days = OrderedDict()
    for s in sessions:
        key = s.date
        if key not in days:
            days[key] = {"distance": 0.0, "calories": 0.0, "speed_sum": 0.0, "speed_count": 0}
        days[key]["distance"] = round(days[key]["distance"] + (s.distance_miles or 0), 2)
        days[key]["calories"] = round(days[key]["calories"] + (s.calories_burned or 0), 1)
        if s.avg_speed_mph:
            days[key]["speed_sum"] += s.avg_speed_mph
            days[key]["speed_count"] += 1
    labels, distances, calories, speeds = [], [], [], []
    for d, v in days.items():
        labels.append(d.strftime("%b %d"))
        distances.append(v["distance"])
        calories.append(v["calories"])
        speeds.append(round(v["speed_sum"] / v["speed_count"], 2) if v["speed_count"] else 0)
    return labels, distances, calories, speeds


# --------------------------------------------------------------------------- #
#  Auth routes
# --------------------------------------------------------------------------- #
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        invite_code = request.form.get("invite_code", "").strip()

        if not email or not display_name or not password or not confirm_password or not invite_code:
            flash("All fields are required.", "danger")
            return render_template("register.html")

        code = InviteCode.query.filter_by(code=invite_code).first()
        if not code or code.is_used:
            flash("Invalid or already used invite code.", "danger")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "danger")
            return render_template("register.html")

        user = User(email=email, display_name=display_name)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        code.used_by_id = user.id
        code.used_at = datetime.utcnow()
        db.session.commit()
        login_user(user)
        flash(f"Welcome, {user.display_name}!", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/admin/users", methods=["GET", "POST"])
@login_required
def admin_users():
    if not current_user.is_admin:
        flash("You don't have permission to access that page.", "danger")
        return redirect(url_for("dashboard"))

    temp_password = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_user":
            email = request.form.get("email", "").strip().lower()
            display_name = request.form.get("display_name", "").strip()
            pw = request.form.get("password", "").strip()
            is_admin = bool(request.form.get("is_admin"))
            if not email or not display_name or not pw:
                flash("Email, name, and password are required.", "danger")
            elif User.query.filter_by(email=email).first():
                flash(f"{email} is already registered.", "danger")
            else:
                u = User(email=email, display_name=display_name, is_active=True, is_admin=is_admin)
                u.set_password(pw)
                db.session.add(u)
                db.session.commit()
                flash(f"User {display_name} ({email}) created.", "success")

        elif action == "reset_password":
            user_id = int(request.form.get("user_id"))
            u = User.query.get(user_id)
            if u:
                temp_password = secrets.token_urlsafe(8)
                u.set_password(temp_password)
                db.session.commit()
                flash(f"Password reset for {u.display_name}. Share the temporary password below.", "warning")

        elif action == "toggle_admin":
            user_id = int(request.form.get("user_id"))
            u = User.query.get(user_id)
            if u and u.id != current_user.id:
                u.is_admin = not u.is_admin
                db.session.commit()
                flash(f"{u.display_name} admin status updated.", "success")

        elif action == "toggle_active":
            user_id = int(request.form.get("user_id"))
            u = User.query.get(user_id)
            if u and u.id != current_user.id:
                u.is_active = not u.is_active
                db.session.commit()
                status = "activated" if u.is_active else "deactivated"
                flash(f"{u.display_name} {status}.", "success")

        elif action == "delete_user":
            user_id = int(request.form.get("user_id"))
            u = User.query.get(user_id)
            if u and u.id != current_user.id:
                Session.query.filter_by(user_id=u.id).delete()
                WeightLog.query.filter_by(user_id=u.id).delete()
                for ls in LegoSet.query.filter_by(user_id=u.id).all():
                    db.session.delete(ls)
                Profile.query.filter_by(user_id=u.id).delete()
                InviteCode.query.filter(
                    (InviteCode.created_by_id == u.id) | (InviteCode.used_by_id == u.id)
                ).delete()
                db.session.delete(u)
                db.session.commit()
                flash(f"User deleted.", "warning")

        elif action == "gen_invite":
            code = InviteCode(code=secrets.token_urlsafe(8), created_by_id=current_user.id)
            db.session.add(code)
            db.session.commit()
            flash(f"Invite code created: {code.code}", "success")

    users = User.query.order_by(User.created_at).all()
    codes = InviteCode.query.order_by(InviteCode.created_at.desc()).all()
    return render_template("admin_users.html", users=users, codes=codes, temp_password=temp_password)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))




# --------------------------------------------------------------------------- #
#  Dashboard
# --------------------------------------------------------------------------- #
@app.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/dashboard")
@login_required
def dashboard():
    profile = get_or_create_profile(current_user.id)
    sessions = Session.query.filter_by(user_id=current_user.id).order_by(Session.date.desc(), Session.created_at.desc()).all()
    lego_sets = LegoSet.query.filter_by(user_id=current_user.id).order_by(LegoSet.name).all()
    sets_in_progress = sum(1 for s in lego_sets if not s.completed)
    sets_completed = sum(1 for s in lego_sets if s.completed)

    total_miles = round(sum(s.distance_miles or 0 for s in sessions), 2)
    total_calories = round(sum(s.calories_burned or 0 for s in sessions), 0)
    total_sessions = len(sessions)
    total_minutes = sum(s.duration_minutes or 0 for s in sessions)

    recent_sessions = sessions[:5]

    # Stats for current week
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_sessions = [s for s in sessions if s.date >= week_start]
    week_miles = round(sum(s.distance_miles or 0 for s in week_sessions), 2)
    week_minutes = int(sum(s.duration_minutes or 0 for s in week_sessions))

    # Social stats
    uid = current_user.id
    accepted_friendships = Friendship.query.filter(
        ((Friendship.requester_id == uid) | (Friendship.addressee_id == uid)),
        Friendship.status == "accepted"
    ).all()
    friend_ids = {f.addressee_id if f.requester_id == uid else f.requester_id for f in accepted_friendships}
    friend_count = len(friend_ids)

    pending_requests = Friendship.query.filter_by(addressee_id=uid, status="pending").count()

    recent_high_fives = (
        HighFive.query
        .filter_by(to_user_id=uid)
        .order_by(HighFive.created_at.desc())
        .limit(5)
        .all()
    )
    total_high_fives = HighFive.query.filter_by(to_user_id=uid).count()

    today_start = datetime.combine(date.today(), datetime.min.time())
    received_today = {hf.from_user_id for hf in HighFive.query.filter(HighFive.to_user_id == uid, HighFive.created_at >= today_start).all()}
    sent_today = {hf.to_user_id for hf in HighFive.query.filter(HighFive.from_user_id == uid, HighFive.created_at >= today_start).all()}
    open_today = len(received_today - sent_today)
    closed_today = len(received_today & sent_today)

    return render_template(
        "index.html",
        profile=profile,
        recent_sessions=recent_sessions,
        lego_sets=lego_sets,
        total_miles=total_miles,
        total_calories=int(total_calories),
        total_sessions=total_sessions,
        total_minutes=int(total_minutes),
        sets_in_progress=sets_in_progress,
        sets_completed=sets_completed,
        week_miles=week_miles,
        week_sessions=len(week_sessions),
        week_minutes=week_minutes,
        friend_count=friend_count,
        pending_requests=pending_requests,
        recent_high_fives=recent_high_fives,
        total_high_fives=total_high_fives,
        open_today=open_today,
        closed_today=closed_today,
    )


# --------------------------------------------------------------------------- #
#  Sessions
# --------------------------------------------------------------------------- #
@app.route("/sessions")
@login_required
def sessions():
    all_sessions = Session.query.filter_by(user_id=current_user.id).order_by(Session.date.desc(), Session.created_at.desc()).all()
    lego_sets = LegoSet.query.filter_by(user_id=current_user.id).order_by(LegoSet.name).all()
    return render_template("sessions.html", sessions=all_sessions, lego_sets=lego_sets)


@app.route("/sessions/add", methods=["POST"])
@login_required
def add_session():
    profile = get_or_create_profile(current_user.id)
    try:
        session_date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        duration = float(request.form["duration_minutes"])
        distance = request.form.get("distance_miles", "").strip()
        distance = float(distance) if distance else None
        speed = request.form.get("avg_speed_mph", "").strip()
        speed = float(speed) if speed else None

        # Auto-calculate distance or speed if one is missing
        if distance and not speed and duration:
            speed = round(distance / (duration / 60), 2)
        elif speed and not distance and duration:
            distance = round(speed * (duration / 60), 2)

        calories_input = request.form.get("calories_burned", "").strip()
        if calories_input:
            calories = float(calories_input)
        else:
            weight_kg = profile.weight_kg
            calories = calculate_calories(duration, weight_kg, speed or 3.0)

        notes = request.form.get("notes", "").strip() or None

        # Lego set handling
        lego_set_id = None
        existing_set = request.form.get("lego_set_id", "").strip()
        new_set_number = request.form.get("new_set_number", "").strip()
        new_set_name = request.form.get("new_set_name", "").strip()

        if existing_set and existing_set != "new":
            lego_set_id = int(existing_set)
        elif existing_set == "new" and new_set_number:
            lego_set = LegoSet.query.filter_by(set_number=new_set_number, user_id=current_user.id).first()
            if not lego_set:
                piece_count = request.form.get("new_piece_count", "").strip()
                total_bag_count = request.form.get("new_total_bag_count", "").strip()
                lego_set = LegoSet(
                    set_number=new_set_number,
                    name=new_set_name or f"Set {new_set_number}",
                    piece_count=int(piece_count) if piece_count else None,
                    total_bag_count=int(total_bag_count) if total_bag_count else None,
                    theme=request.form.get("new_theme", "").strip() or None,
                    image_url=request.form.get("image_url", "").strip() or None,
                    user_id=current_user.id,
                )
                db.session.add(lego_set)
                db.session.flush()
            lego_set_id = lego_set.id

        bags_completed = request.form.get("bags_completed", "").strip()
        bags_completed = int(bags_completed) if bags_completed else 0
        bag_details = request.form.get("bag_details", "").strip() or None

        new_session = Session(
            date=session_date,
            duration_minutes=duration,
            distance_miles=distance,
            avg_speed_mph=speed,
            calories_burned=calories,
            notes=notes,
            lego_set_id=lego_set_id,
            bags_completed=bags_completed,
            bag_details=bag_details,
            user_id=current_user.id,
        )
        db.session.add(new_session)
        db.session.commit()
        flash("Session added successfully!", "success")
    except ValueError as e:
        db.session.rollback()
        flash(f"Invalid data: {e}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error adding session: {e}", "danger")
    return redirect(url_for("sessions"))


@app.route("/sessions/edit/<int:session_id>", methods=["GET", "POST"])
@login_required
def edit_session(session_id):
    s = Session.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    profile = get_or_create_profile(current_user.id)
    lego_sets = LegoSet.query.filter_by(user_id=current_user.id).order_by(LegoSet.name).all()

    if request.method == "POST":
        try:
            s.date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
            s.duration_minutes = float(request.form["duration_minutes"])
            distance = request.form.get("distance_miles", "").strip()
            s.distance_miles = float(distance) if distance else None
            speed = request.form.get("avg_speed_mph", "").strip()
            s.avg_speed_mph = float(speed) if speed else None

            if s.distance_miles and not s.avg_speed_mph and s.duration_minutes:
                s.avg_speed_mph = round(s.distance_miles / (s.duration_minutes / 60), 2)
            elif s.avg_speed_mph and not s.distance_miles and s.duration_minutes:
                s.distance_miles = round(s.avg_speed_mph * (s.duration_minutes / 60), 2)

            calories_input = request.form.get("calories_burned", "").strip()
            if calories_input:
                s.calories_burned = float(calories_input)
            else:
                s.calories_burned = calculate_calories(
                    s.duration_minutes, profile.weight_kg, s.avg_speed_mph or 3.0
                )

            s.notes = request.form.get("notes", "").strip() or None

            existing_set = request.form.get("lego_set_id", "").strip()
            new_set_number = request.form.get("new_set_number", "").strip()
            new_set_name = request.form.get("new_set_name", "").strip()

            if existing_set and existing_set not in ("", "none", "new"):
                s.lego_set_id = int(existing_set)
            elif existing_set == "new" and new_set_number:
                lego_set = LegoSet.query.filter_by(set_number=new_set_number, user_id=current_user.id).first()
                if not lego_set:
                    piece_count = request.form.get("new_piece_count", "").strip()
                    total_bag_count = request.form.get("new_total_bag_count", "").strip()
                    lego_set = LegoSet(
                        set_number=new_set_number,
                        name=new_set_name or f"Set {new_set_number}",
                        piece_count=int(piece_count) if piece_count else None,
                        total_bag_count=int(total_bag_count) if total_bag_count else None,
                        theme=request.form.get("new_theme", "").strip() or None,
                        image_url=request.form.get("image_url", "").strip() or None,
                        user_id=current_user.id,
                    )
                    db.session.add(lego_set)
                    db.session.flush()
                s.lego_set_id = lego_set.id
            elif existing_set in ("", "none"):
                s.lego_set_id = None

            bags = request.form.get("bags_completed", "").strip()
            s.bags_completed = int(bags) if bags else 0
            s.bag_details = request.form.get("bag_details", "").strip() or None

            db.session.commit()
            flash("Session updated!", "success")
            return redirect(url_for("sessions"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating session: {e}", "danger")

    return render_template("edit_session.html", wsession=s, lego_sets=lego_sets)


@app.route("/sessions/delete/<int:session_id>", methods=["POST"])
@login_required
def delete_session(session_id):
    s = Session.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    db.session.delete(s)
    db.session.commit()
    flash("Session deleted.", "warning")
    return redirect(url_for("sessions"))


# --------------------------------------------------------------------------- #
#  Lego Sets
# --------------------------------------------------------------------------- #
@app.route("/sets")
@login_required
def sets():
    all_sets = LegoSet.query.filter_by(user_id=current_user.id).order_by(LegoSet.completed, LegoSet.name).all()
    return render_template("sets.html", sets=all_sets)


@app.route("/sets/<int:set_id>")
@login_required
def set_detail(set_id):
    lego_set = LegoSet.query.filter_by(id=set_id, user_id=current_user.id).first_or_404()
    sessions = Session.query.filter_by(lego_set_id=set_id, user_id=current_user.id).order_by(Session.date.desc(), Session.created_at.desc()).all()
    sessions_asc = list(reversed(sessions))

    total_miles = round(sum(s.distance_miles or 0 for s in sessions), 2)
    total_minutes = sum(s.duration_minutes or 0 for s in sessions)
    total_calories = round(sum(s.calories_burned or 0 for s in sessions))
    avg_speed = round(sum(s.avg_speed_mph for s in sessions if s.avg_speed_mph) / len([s for s in sessions if s.avg_speed_mph]), 2) if any(s.avg_speed_mph for s in sessions) else None
    total_bags = sum(s.bags_completed or 0 for s in sessions)
    pct_complete = round((total_bags / lego_set.total_bag_count) * 100) if lego_set.total_bag_count and total_bags else 0

    # Chart data: aggregated by day, oldest to newest
    chart_labels, chart_distance, chart_calories, chart_speed = aggregate_sessions_by_day(sessions_asc)

    return render_template("set_detail.html",
        lego_set=lego_set,
        sessions=sessions,
        total_miles=total_miles,
        total_minutes=int(total_minutes),
        total_calories=total_calories,
        avg_speed=avg_speed,
        total_bags=total_bags,
        chart_labels=chart_labels,
        chart_distance=chart_distance,
        chart_calories=chart_calories,
        chart_speed=chart_speed,
        pct_complete=pct_complete,
    )


@app.route("/sets/add", methods=["POST"])
@login_required
def add_set():
    try:
        set_number = request.form["set_number"].strip()
        if not set_number:
            flash("Set number is required.", "danger")
            return redirect(url_for("sets"))

        existing = LegoSet.query.filter_by(set_number=set_number, user_id=current_user.id).first()
        if existing:
            flash(f"Set {set_number} already exists.", "warning")
            return redirect(url_for("sets"))

        piece_count = request.form.get("piece_count", "").strip()
        total_bag_count = request.form.get("total_bag_count", "").strip()
        new_set = LegoSet(
            set_number=set_number,
            name=request.form.get("name", "").strip() or f"Set {set_number}",
            piece_count=int(piece_count) if piece_count else None,
            total_bag_count=int(total_bag_count) if total_bag_count else None,
            theme=request.form.get("theme", "").strip() or None,
            image_url=request.form.get("image_url", "").strip() or None,
            user_id=current_user.id,
        )
        db.session.add(new_set)
        db.session.commit()
        flash(f"Lego set '{new_set.name}' added!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error adding set: {e}", "danger")
    return redirect(url_for("sets"))


@app.route("/sets/edit/<int:set_id>", methods=["GET", "POST"])
@login_required
def edit_set(set_id):
    lego_set = LegoSet.query.filter_by(id=set_id, user_id=current_user.id).first_or_404()
    if request.method == "POST":
        try:
            lego_set.set_number = request.form["set_number"].strip()
            lego_set.name = request.form["name"].strip()
            piece_count = request.form.get("piece_count", "").strip()
            lego_set.piece_count = int(piece_count) if piece_count else None
            total_bag_count = request.form.get("total_bag_count", "").strip()
            lego_set.total_bag_count = int(total_bag_count) if total_bag_count else None
            lego_set.theme = request.form.get("theme", "").strip() or None
            lego_set.image_url = request.form.get("image_url", "").strip() or None
            db.session.commit()
            flash("Set updated!", "success")
            return redirect(url_for("sets"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating set: {e}", "danger")
    return render_template("edit_set.html", lego_set=lego_set)


@app.route("/sets/complete/<int:set_id>", methods=["POST"])
@login_required
def complete_set(set_id):
    lego_set = LegoSet.query.filter_by(id=set_id, user_id=current_user.id).first_or_404()
    lego_set.completed = True
    lego_set.completion_date = date.today()
    db.session.commit()
    flash(f"Congratulations! '{lego_set.name}' marked as complete!", "success")
    return redirect(url_for("sets"))


@app.route("/sets/reopen/<int:set_id>", methods=["POST"])
@login_required
def reopen_set(set_id):
    lego_set = LegoSet.query.filter_by(id=set_id, user_id=current_user.id).first_or_404()
    lego_set.completed = False
    lego_set.completion_date = None
    db.session.commit()
    flash(f"'{lego_set.name}' reopened.", "info")
    return redirect(url_for("sets"))


@app.route("/sets/delete/<int:set_id>", methods=["POST"])
@login_required
def delete_set(set_id):
    lego_set = LegoSet.query.filter_by(id=set_id, user_id=current_user.id).first_or_404()
    # Unlink sessions
    for s in lego_set.sessions:
        s.lego_set_id = None
    db.session.delete(lego_set)
    db.session.commit()
    flash("Set deleted.", "warning")
    return redirect(url_for("sets"))


# --------------------------------------------------------------------------- #
#  Profile
# --------------------------------------------------------------------------- #
#  Social
# --------------------------------------------------------------------------- #
@app.route("/user/<int:user_id>")
@login_required
def public_profile(user_id):
    user = User.query.get_or_404(user_id)
    profile = Profile.query.filter_by(user_id=user_id).first()
    uid = current_user.id

    friendship = Friendship.query.filter(
        ((Friendship.requester_id == uid) & (Friendship.addressee_id == user_id)) |
        ((Friendship.requester_id == user_id) & (Friendship.addressee_id == uid))
    ).first()
    is_friend = friendship and friendship.status == "accepted"
    request_sent = friendship and friendship.status == "pending" and friendship.requester_id == uid

    sessions = Session.query.filter_by(user_id=user_id).all()
    total_miles = round(sum(s.distance_miles or 0 for s in sessions), 2)
    total_sessions = len(sessions)

    return render_template("public_profile.html",
        user=user,
        profile=profile,
        is_friend=is_friend,
        request_sent=request_sent,
        total_miles=total_miles,
        total_sessions=total_sessions,
    )


@app.route("/friends")
@login_required
def friends():
    uid = current_user.id
    # Accepted friendships
    accepted = Friendship.query.filter(
        ((Friendship.requester_id == uid) | (Friendship.addressee_id == uid)),
        Friendship.status == "accepted"
    ).all()
    friend_ids = set()
    for f in accepted:
        friend_ids.add(f.addressee_id if f.requester_id == uid else f.requester_id)
    friend_users = User.query.filter(User.id.in_(friend_ids)).all() if friend_ids else []

    # Pending received requests
    received = Friendship.query.filter_by(addressee_id=uid, status="pending").all()
    # Pending sent requests
    sent = Friendship.query.filter_by(requester_id=uid, status="pending").all()
    sent_ids = {f.addressee_id for f in sent}

    # Friends who high-fived you today but you haven't returned it
    today_start = datetime.combine(date.today(), datetime.min.time())
    received_today_ids = {
        hf.from_user_id for hf in
        HighFive.query.filter(
            HighFive.to_user_id == uid,
            HighFive.created_at >= today_start
        ).all()
    }
    sent_today_ids = {
        hf.to_user_id for hf in
        HighFive.query.filter(
            HighFive.from_user_id == uid,
            HighFive.created_at >= today_start
        ).all()
    }
    open_high_five_ids = received_today_ids - sent_today_ids

    # Search
    query = request.args.get("q", "").strip()
    results = []
    if query:
        results = User.query.filter(
            User.id != uid,
            User.is_active == True,
            (User.display_name.ilike(f"%{query}%") | User.email.ilike(f"%{query}%"))
        ).all()

    # Social feed — last 30 days of highlights from friends
    feed = []
    if friend_ids:
        cutoff = datetime.utcnow() - timedelta(days=30)
        MILESTONES = [5, 10, 25, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000]

        # Build a display_name lookup
        friend_name = {u.id: u.display_name for u in friend_users}

        # 1. Set completions
        completed_sets = LegoSet.query.filter(
            LegoSet.user_id.in_(friend_ids),
            LegoSet.completion_date != None,
            LegoSet.completion_date >= cutoff.date()
        ).all()
        for ls in completed_sets:
            feed.append({
                "type": "set_complete",
                "date": datetime(ls.completion_date.year, ls.completion_date.month, ls.completion_date.day),
                "user_id": ls.user_id,
                "user_name": friend_name.get(ls.user_id, "Friend"),
                "set_name": ls.name,
                "set_number": ls.set_number,
            })

        # 2. Mile milestones — check each friend's cumulative miles per session
        for fid in friend_ids:
            sessions = Session.query.filter_by(user_id=fid).order_by(Session.date.asc(), Session.id.asc()).all()
            cumulative = 0.0
            next_milestone_idx = 0
            for s in sessions:
                miles = float(s.distance_miles or 0)
                prev = cumulative
                cumulative += miles
                while next_milestone_idx < len(MILESTONES) and cumulative >= MILESTONES[next_milestone_idx]:
                    m = MILESTONES[next_milestone_idx]
                    if prev < m:  # crossed this milestone in this session
                        event_dt = datetime(s.date.year, s.date.month, s.date.day)
                        if event_dt >= cutoff:
                            feed.append({
                                "type": "milestone",
                                "date": event_dt,
                                "user_id": fid,
                                "user_name": friend_name.get(fid, "Friend"),
                                "miles": m,
                            })
                    next_milestone_idx += 1

        # 3. High fives received
        high_fives = HighFive.query.filter(
            HighFive.to_user_id.in_(friend_ids),
            HighFive.created_at >= cutoff
        ).all()
        for hf in high_fives:
            feed.append({
                "type": "high_five",
                "date": hf.created_at,
                "user_id": hf.to_user_id,
                "user_name": friend_name.get(hf.to_user_id, "Friend"),
                "from_user_id": hf.from_user_id,
                "from_name": hf.from_user.display_name if hf.from_user else "Someone",
            })

        feed.sort(key=lambda e: e["date"], reverse=True)
        feed = feed[:50]

    return render_template("friends.html",
        friend_users=friend_users,
        received=received,
        sent_ids=sent_ids,
        results=results,
        query=query,
        feed=feed,
        open_high_five_ids=open_high_five_ids,
        sent_today_ids=sent_today_ids,
    )


@app.route("/friends/request/<int:user_id>", methods=["POST"])
@login_required
def friend_request(user_id):
    if user_id == current_user.id:
        flash("You can't add yourself.", "danger")
        return redirect(url_for("friends"))
    existing = Friendship.query.filter(
        ((Friendship.requester_id == current_user.id) & (Friendship.addressee_id == user_id)) |
        ((Friendship.requester_id == user_id) & (Friendship.addressee_id == current_user.id))
    ).first()
    if not existing:
        db.session.add(Friendship(requester_id=current_user.id, addressee_id=user_id))
        db.session.commit()
        flash("Friend request sent.", "success")
    return redirect(url_for("friends", q=request.form.get("q", "")))


@app.route("/friends/accept/<int:friendship_id>", methods=["POST"])
@login_required
def friend_accept(friendship_id):
    f = Friendship.query.filter_by(id=friendship_id, addressee_id=current_user.id, status="pending").first_or_404()
    f.status = "accepted"
    db.session.commit()
    flash(f"You and {f.requester.display_name} are now friends.", "success")
    return redirect(url_for("friends"))


@app.route("/friends/decline/<int:friendship_id>", methods=["POST"])
@login_required
def friend_decline(friendship_id):
    f = Friendship.query.filter_by(id=friendship_id, addressee_id=current_user.id, status="pending").first_or_404()
    db.session.delete(f)
    db.session.commit()
    return redirect(url_for("friends"))


@app.route("/friends/remove/<int:user_id>", methods=["POST"])
@login_required
def friend_remove(user_id):
    f = Friendship.query.filter(
        ((Friendship.requester_id == current_user.id) & (Friendship.addressee_id == user_id)) |
        ((Friendship.requester_id == user_id) & (Friendship.addressee_id == current_user.id))
    ).first_or_404()
    db.session.delete(f)
    db.session.commit()
    flash("Friend removed.", "warning")
    return redirect(url_for("friends"))


@app.route("/friends/<int:user_id>")
@login_required
def friend_profile(user_id):
    # Must be friends
    friendship = Friendship.query.filter(
        ((Friendship.requester_id == current_user.id) & (Friendship.addressee_id == user_id)) |
        ((Friendship.requester_id == user_id) & (Friendship.addressee_id == current_user.id)),
        Friendship.status == "accepted"
    ).first_or_404()

    friend = User.query.get_or_404(user_id)
    sessions = Session.query.filter_by(user_id=user_id).order_by(Session.date.desc(), Session.created_at.desc()).all()
    sets_in_progress = LegoSet.query.filter_by(user_id=user_id, completed=False).all()
    sets_completed_count = LegoSet.query.filter_by(user_id=user_id, completed=True).count()

    total_miles = round(sum(s.distance_miles or 0 for s in sessions), 2)
    total_calories = int(sum(s.calories_burned or 0 for s in sessions))
    total_sessions = len(sessions)
    recent_sessions = sessions[:5]

    # High five counts
    high_fives_received = HighFive.query.filter_by(to_user_id=user_id).count()
    already_highfived_today = HighFive.query.filter_by(
        from_user_id=current_user.id, to_user_id=user_id
    ).filter(HighFive.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)).first()

    return render_template("friend_profile.html",
        friend=friend,
        recent_sessions=recent_sessions,
        sets_in_progress=sets_in_progress,
        sets_completed_count=sets_completed_count,
        total_miles=total_miles,
        total_calories=total_calories,
        total_sessions=total_sessions,
        high_fives_received=high_fives_received,
        already_highfived_today=already_highfived_today,
    )


@app.route("/friends/<int:user_id>/highfive", methods=["POST"])
@login_required
def high_five(user_id):
    # Must be friends
    Friendship.query.filter(
        ((Friendship.requester_id == current_user.id) & (Friendship.addressee_id == user_id)) |
        ((Friendship.requester_id == user_id) & (Friendship.addressee_id == current_user.id)),
        Friendship.status == "accepted"
    ).first_or_404()

    already = HighFive.query.filter_by(
        from_user_id=current_user.id, to_user_id=user_id
    ).filter(HighFive.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)).first()

    if not already:
        db.session.add(HighFive(from_user_id=current_user.id, to_user_id=user_id))
        db.session.commit()
        flash(f"High five sent to {User.query.get(user_id).display_name}! 🖐️", "success")
    else:
        flash("You already high-fived them today!", "info")
    return redirect(url_for("friend_profile", user_id=user_id))


# --------------------------------------------------------------------------- #
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    p = get_or_create_profile(current_user.id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_profile":
            try:
                p.name = request.form.get("name", "").strip() or "Athlete"
                height = request.form.get("height_inches", "").strip()
                p.height_inches = float(height) if height else None
                age = request.form.get("age", "").strip()
                p.age = int(age) if age else None
                p.location = request.form.get("location", "").strip() or None
                db.session.commit()
                flash("Profile updated!", "success")
            except Exception as e:
                db.session.rollback()
                flash(f"Error updating profile: {e}", "danger")
        elif action == "upload_avatar":
            file = request.files.get("avatar")
            if file and file.filename:
                ext = file.filename.rsplit(".", 1)[-1].lower()
                if ext not in {"jpg", "jpeg", "png", "gif", "webp"}:
                    flash("Invalid file type. Use JPG, PNG, GIF, or WebP.", "danger")
                else:
                    try:
                        img = Image.open(file).convert("RGB")
                        img = ImageOps.fit(img, (200, 200), Image.LANCZOS)
                        filename = f"{current_user.id}.jpg"
                        img.save(os.path.join(AVATAR_DIR, filename), "JPEG", quality=85)
                        p.avatar_filename = filename
                        db.session.commit()
                        flash("Profile picture updated!", "success")
                    except Exception as e:
                        db.session.rollback()
                        flash(f"Error uploading image: {e}", "danger")
        elif action == "add_weight":
            try:
                weight = float(request.form["weight_lbs"])
                weight_date_str = request.form.get("weight_date", "").strip()
                weight_date = (
                    datetime.strptime(weight_date_str, "%Y-%m-%d").date()
                    if weight_date_str
                    else date.today()
                )
                log = WeightLog(
                    date=weight_date,
                    weight_lbs=weight,
                    notes=request.form.get("weight_notes", "").strip() or None,
                    user_id=current_user.id,
                )
                db.session.add(log)
                db.session.flush()
                latest = WeightLog.query.filter_by(user_id=current_user.id).order_by(WeightLog.date.desc(), WeightLog.created_at.desc()).first()
                p.current_weight_lbs = latest.weight_lbs if latest else weight
                db.session.commit()
                flash(f"Weight entry ({weight} lbs) added!", "success")
            except Exception as e:
                db.session.rollback()
                flash(f"Error adding weight: {e}", "danger")
        elif action == "delete_weight":
            try:
                log_id = int(request.form["log_id"])
                log = WeightLog.query.filter_by(id=log_id, user_id=current_user.id).first_or_404()
                db.session.delete(log)
                # Update current weight to latest remaining entry
                latest = WeightLog.query.filter_by(user_id=current_user.id).order_by(WeightLog.date.desc(), WeightLog.created_at.desc()).first()
                p.current_weight_lbs = latest.weight_lbs if latest else None
                db.session.commit()
                flash("Weight entry deleted.", "warning")
            except Exception as e:
                db.session.rollback()
                flash(f"Error deleting weight: {e}", "danger")
        elif action == "change_password":
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_new_password", "")
            if not current_user.check_password(current_pw):
                flash("Current password is incorrect.", "danger")
            elif not new_pw:
                flash("New password cannot be blank.", "danger")
            elif new_pw != confirm_pw:
                flash("New passwords do not match.", "danger")
            else:
                current_user.set_password(new_pw)
                db.session.commit()
                flash("Password updated successfully.", "success")
        return redirect(url_for("profile"))

    weight_logs = WeightLog.query.filter_by(user_id=current_user.id).order_by(WeightLog.date.desc(), WeightLog.created_at.desc()).all()
    return render_template("profile.html", profile=p, weight_logs=weight_logs)


# --------------------------------------------------------------------------- #
#  Data & Visualizations
# --------------------------------------------------------------------------- #
@app.route("/data")
@login_required
def data():
    sessions = Session.query.filter_by(user_id=current_user.id).order_by(Session.date).all()
    weight_logs = WeightLog.query.filter_by(user_id=current_user.id).order_by(WeightLog.date).all()
    sets_list = LegoSet.query.filter_by(user_id=current_user.id).order_by(LegoSet.completion_date).all()

    # Fun stats
    total_miles = round(sum(s.distance_miles or 0 for s in sessions), 2)
    total_calories = int(sum(s.calories_burned or 0 for s in sessions))
    total_pieces = sum(
        (s.lego_set.piece_count or 0)
        for s in sessions
        if s.lego_set and s.lego_set.piece_count
    )
    # Pieces built: full count for completed sets, bag-proportional for in-progress
    seen_sets = set()
    unique_pieces = 0
    for s in sessions:
        if s.lego_set and s.lego_set_id not in seen_sets:
            pb = s.lego_set.pieces_built
            unique_pieces += pb if pb is not None else 0
            seen_sets.add(s.lego_set_id)

    longest = max((s.duration_minutes for s in sessions), default=0)
    fastest = max((s.avg_speed_mph or 0 for s in sessions), default=0)
    total_sessions = len(sessions)

    # Chart data: aggregated by day
    dist_labels, dist_data, cal_data_raw, speed_data_raw = aggregate_sessions_by_day(sessions)
    cal_labels, speed_labels = dist_labels, dist_labels
    cal_data = cal_data_raw
    speed_data = speed_data_raw

    # Chart data: monthly miles bar chart
    monthly = {}
    for s in sessions:
        if s.distance_miles:
            key = s.date.strftime("%Y-%m")
            monthly[key] = round(monthly.get(key, 0) + s.distance_miles, 2)
    monthly_labels = [datetime.strptime(k, "%Y-%m").strftime("%b %Y") for k in sorted(monthly)]
    monthly_data = [monthly[k] for k in sorted(monthly)]

    # Chart data: weight over time
    wt_labels = [w.date.strftime("%b %d") for w in weight_logs]
    wt_data = [w.weight_lbs for w in weight_logs]

    # Chart data: cumulative sessions per month
    sessions_monthly = {}
    for s in sessions:
        key = s.date.strftime("%Y-%m")
        sessions_monthly[key] = sessions_monthly.get(key, 0) + 1
    sm_labels = [datetime.strptime(k, "%Y-%m").strftime("%b %Y") for k in sorted(sessions_monthly)]
    sm_data = [sessions_monthly[k] for k in sorted(sessions_monthly)]

    return render_template(
        "data.html",
        total_miles=total_miles,
        total_calories=total_calories,
        unique_pieces=unique_pieces,
        longest=int(longest),
        fastest=fastest,
        total_sessions=total_sessions,
        sets_completed=LegoSet.query.filter_by(user_id=current_user.id, completed=True).count(),
        dist_labels=json.dumps(dist_labels),
        dist_data=json.dumps(dist_data),
        cal_labels=json.dumps(cal_labels),
        cal_data=json.dumps(cal_data),
        speed_labels=json.dumps(speed_labels),
        speed_data=json.dumps(speed_data),
        monthly_labels=json.dumps(monthly_labels),
        monthly_data=json.dumps(monthly_data),
        wt_labels=json.dumps(wt_labels),
        wt_data=json.dumps(wt_data),
        sm_labels=json.dumps(sm_labels),
        sm_data=json.dumps(sm_data),
    )


# --------------------------------------------------------------------------- #
#  API endpoint for calorie preview
# --------------------------------------------------------------------------- #
@app.route("/api/lookup_set")
@login_required
def api_lookup_set():
    set_number = request.args.get("set_number", "").strip()
    if not set_number:
        return jsonify({"error": "No set number provided"}), 400

    api_key = os.environ.get("REBRICKABLE_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "No Rebrickable API key configured. Set the REBRICKABLE_API_KEY environment variable."}), 503

    # Rebrickable requires the "-1" suffix for most sets
    if "-" not in set_number:
        set_number_rb = set_number + "-1"
    else:
        set_number_rb = set_number

    url = f"https://rebrickable.com/api/v3/lego/sets/{urllib.parse.quote(set_number_rb)}/"
    req = urllib.request.Request(url, headers={"Authorization": f"key {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        # Resolve theme name via a second call
        theme_name = None
        theme_id = data.get("theme_id")
        if theme_id:
            try:
                treq = urllib.request.Request(
                    f"https://rebrickable.com/api/v3/lego/themes/{theme_id}/",
                    headers={"Authorization": f"key {api_key}"},
                )
                with urllib.request.urlopen(treq, timeout=5) as tresp:
                    theme_data = json.loads(tresp.read().decode())
                    theme_name = theme_data.get("name")
            except Exception:
                pass
        return jsonify({
            "set_number": data.get("set_num", set_number_rb).rstrip("-1") if data.get("set_num", "").endswith("-1") else data.get("set_num", set_number),
            "name": data.get("name"),
            "year": data.get("year"),
            "piece_count": data.get("num_parts"),
            "theme": theme_name,
            "image_url": data.get("set_img_url"),
        })
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return jsonify({"error": f"Set {set_number_rb} not found on Rebrickable"}), 404
        return jsonify({"error": f"Rebrickable error: {e.code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/calc_calories")
@login_required
def api_calc_calories():
    try:
        duration = float(request.args.get("duration", 0))
        speed = float(request.args.get("speed", 3.0))
        p = get_or_create_profile(current_user.id)
        weight_kg = p.weight_kg or 70
        calories = calculate_calories(duration, weight_kg, speed)
        distance = round(speed * (duration / 60), 2) if speed and duration else None
        return jsonify({"calories": calories, "distance": distance})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# --------------------------------------------------------------------------- #
#  Init
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5001, debug=True)
