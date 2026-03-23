import os
import json
import secrets
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, InviteCode, Profile, WeightLog, LegoSet, Session

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "lego-workout-secret-key-2024")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "lego_workout.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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


# --------------------------------------------------------------------------- #
#  Auth routes
# --------------------------------------------------------------------------- #
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
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
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/invites", methods=["GET", "POST"])
@login_required
def invites():
    if request.method == "POST":
        code = InviteCode(
            code=secrets.token_urlsafe(8),
            created_by_id=current_user.id,
        )
        db.session.add(code)
        db.session.commit()
        flash(f"Invite code created: {code.code}", "success")
    codes = InviteCode.query.filter_by(created_by_id=current_user.id).order_by(InviteCode.created_at.desc()).all()
    return render_template("invites.html", codes=codes)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("index"))
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
@login_required
def index():
    profile = get_or_create_profile(current_user.id)
    sessions = Session.query.filter_by(user_id=current_user.id).order_by(Session.date.desc()).all()
    sets_in_progress = LegoSet.query.filter_by(user_id=current_user.id, completed=False).count()
    sets_completed = LegoSet.query.filter_by(user_id=current_user.id, completed=True).count()

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

    return render_template(
        "index.html",
        profile=profile,
        recent_sessions=recent_sessions,
        total_miles=total_miles,
        total_calories=int(total_calories),
        total_sessions=total_sessions,
        total_minutes=int(total_minutes),
        sets_in_progress=sets_in_progress,
        sets_completed=sets_completed,
        week_miles=week_miles,
        week_sessions=len(week_sessions),
    )


# --------------------------------------------------------------------------- #
#  Sessions
# --------------------------------------------------------------------------- #
@app.route("/sessions")
@login_required
def sessions():
    all_sessions = Session.query.filter_by(user_id=current_user.id).order_by(Session.date.desc()).all()
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
    in_progress = [s for s in all_sets if not s.completed]
    completed_sets = [s for s in all_sets if s.completed]
    return render_template("sets.html", in_progress=in_progress, completed_sets=completed_sets)


@app.route("/sets/<int:set_id>")
@login_required
def set_detail(set_id):
    lego_set = LegoSet.query.filter_by(id=set_id, user_id=current_user.id).first_or_404()
    sessions = Session.query.filter_by(lego_set_id=set_id, user_id=current_user.id).order_by(Session.date).all()

    total_miles = round(sum(s.distance_miles or 0 for s in sessions), 2)
    total_minutes = sum(s.duration_minutes or 0 for s in sessions)
    total_calories = round(sum(s.calories_burned or 0 for s in sessions))
    avg_speed = round(sum(s.avg_speed_mph for s in sessions if s.avg_speed_mph) / len([s for s in sessions if s.avg_speed_mph]), 2) if any(s.avg_speed_mph for s in sessions) else None
    total_bags = sum(s.bags_completed or 0 for s in sessions)

    # Chart data: distance per session
    chart_labels = [s.date.strftime("%b %d") for s in sessions]
    chart_distance = [s.distance_miles or 0 for s in sessions]
    chart_calories = [s.calories_burned or 0 for s in sessions]
    chart_speed = [s.avg_speed_mph or 0 for s in sessions]

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
                db.session.commit()
                flash("Profile updated!", "success")
            except Exception as e:
                db.session.rollback()
                flash(f"Error updating profile: {e}", "danger")
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

    # Chart data: distance over time
    dist_labels = [s.date.strftime("%b %d") for s in sessions if s.distance_miles]
    dist_data = [s.distance_miles for s in sessions if s.distance_miles]

    # Chart data: calories over time
    cal_labels = [s.date.strftime("%b %d") for s in sessions if s.calories_burned]
    cal_data = [round(s.calories_burned, 1) for s in sessions if s.calories_burned]

    # Chart data: speed over time
    speed_labels = [s.date.strftime("%b %d") for s in sessions if s.avg_speed_mph]
    speed_data = [s.avg_speed_mph for s in sessions if s.avg_speed_mph]

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
