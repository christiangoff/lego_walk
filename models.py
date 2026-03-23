from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date

db = SQLAlchemy()


class InviteCode(db.Model):
    __tablename__ = "invite_code"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False, unique=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    used_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    used_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_used(self):
        return self.used_by_id is not None

    def __repr__(self):
        return f"<InviteCode {self.code} used={self.is_used}>"


class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    display_name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.email}>"


class Profile(db.Model):
    __tablename__ = "profile"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, default="Athlete")
    height_inches = db.Column(db.Float, nullable=True)  # stored in inches
    age = db.Column(db.Integer, nullable=True)
    # current weight kept here for quick access; history in WeightLog
    current_weight_lbs = db.Column(db.Float, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    def __repr__(self):
        return f"<Profile {self.name}>"

    @property
    def weight_kg(self):
        if self.current_weight_lbs:
            return self.current_weight_lbs * 0.453592
        return None

    @property
    def height_cm(self):
        if self.height_inches:
            return self.height_inches * 2.54
        return None

    @property
    def bmi(self):
        if self.weight_kg and self.height_cm:
            height_m = self.height_cm / 100
            return round(self.weight_kg / (height_m ** 2), 1)
        return None

    @property
    def bmi_category(self):
        bmi = self.bmi
        if bmi is None:
            return "Unknown"
        if bmi < 18.5:
            return "Underweight"
        elif bmi < 25:
            return "Normal"
        elif bmi < 30:
            return "Overweight"
        else:
            return "Obese"


class WeightLog(db.Model):
    __tablename__ = "weight_log"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    weight_lbs = db.Column(db.Float, nullable=False)
    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    def __repr__(self):
        return f"<WeightLog {self.date}: {self.weight_lbs} lbs>"


class LegoSet(db.Model):
    __tablename__ = "lego_set"
    id = db.Column(db.Integer, primary_key=True)
    set_number = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    piece_count = db.Column(db.Integer, nullable=True)
    total_bag_count = db.Column(db.Integer, nullable=True)
    theme = db.Column(db.String(100), nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    completed = db.Column(db.Boolean, default=False)
    completion_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    sessions = db.relationship("Session", backref="lego_set", lazy=True)

    def __repr__(self):
        return f"<LegoSet {self.set_number}: {self.name}>"

    @property
    def total_session_count(self):
        return len(self.sessions)

    @property
    def total_distance(self):
        return round(sum(s.distance_miles or 0 for s in self.sessions), 2)

    @property
    def total_duration(self):
        return sum(s.duration_minutes or 0 for s in self.sessions)

    @property
    def total_calories(self):
        return int(sum(s.calories_burned or 0 for s in self.sessions))

    @property
    def pieces_built(self):
        """Pieces built: full count if completed, else proportional based on bags finished."""
        if not self.piece_count:
            return None
        if self.completed:
            return self.piece_count
        if not self.total_bag_count:
            return None
        bags_done = sum(s.bags_completed or 0 for s in self.sessions)
        if not bags_done:
            return None
        return round((bags_done / self.total_bag_count) * self.piece_count)


class Session(db.Model):
    __tablename__ = "session"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    duration_minutes = db.Column(db.Float, nullable=False)
    distance_miles = db.Column(db.Float, nullable=True)
    avg_speed_mph = db.Column(db.Float, nullable=True)
    calories_burned = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    lego_set_id = db.Column(db.Integer, db.ForeignKey("lego_set.id"), nullable=True)
    bags_completed = db.Column(db.Integer, nullable=True, default=0)
    # comma-separated bag numbers, e.g. "1,2,3"
    bag_details = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    def __repr__(self):
        return f"<Session {self.date}: {self.distance_miles} mi>"

    @property
    def bag_list(self):
        """Return individual bag numbers, expanding ranges like '1-3' to ['1','2','3']."""
        if not self.bag_details:
            return []
        bags = []
        for part in self.bag_details.split(","):
            part = part.strip()
            if "-" in part:
                try:
                    lo, hi = part.split("-", 1)
                    bags.extend(str(n) for n in range(int(lo.strip()), int(hi.strip()) + 1))
                except ValueError:
                    bags.append(part)
            elif part:
                bags.append(part)
        return bags
