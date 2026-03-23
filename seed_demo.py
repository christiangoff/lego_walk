"""
Seed script: creates a demo user with ~3 months of realistic session data.
Usage: python3 seed_demo.py

Re-running will delete and recreate the demo user and all their data.
"""
import random
from datetime import date, timedelta, datetime
from app import app, db
from models import User, Profile, WeightLog, LegoSet, Session

DEMO_EMAIL   = "demo@studstep.local"
DEMO_PASSWORD = "demo123"
DEMO_NAME    = "Alex Demo"

LEGO_SETS = [
    {"set_number": "10300", "name": "Back to the Future Time Machine", "piece_count": 1872, "total_bag_count": 7,  "theme": "Icons",     "image_url": "https://cdn.rebrickable.com/media/sets/10300-1/99954.jpg"},
    {"set_number": "10302", "name": "Optimus Prime",                   "piece_count": 1508, "total_bag_count": 6,  "theme": "Icons",     "image_url": "https://cdn.rebrickable.com/media/sets/10302-1/102780.jpg"},
    {"set_number": "21325", "name": "Medieval Blacksmith",             "piece_count": 2164, "total_bag_count": 8,  "theme": "Ideas",     "image_url": "https://cdn.rebrickable.com/media/sets/21325-1/80427.jpg"},
    {"set_number": "10281", "name": "Bonsai Tree",                     "piece_count":  878, "total_bag_count": 4,  "theme": "Botanical", "image_url": "https://cdn.rebrickable.com/media/sets/10281-1/148031.jpg"},
    {"set_number": "42083", "name": "Bugatti Chiron",                  "piece_count": 3599, "total_bag_count": 10, "theme": "Technic",   "image_url": "https://cdn.rebrickable.com/media/sets/42083-1/8941.jpg"},
    {"set_number": "71741", "name": "NINJAGO City Gardens",            "piece_count": 5685, "total_bag_count": 16, "theme": "Ninjago",   "image_url": "https://cdn.rebrickable.com/media/sets/71741-1/80116.jpg"},
    {"set_number": "75192", "name": "Millennium Falcon",               "piece_count": 7541, "total_bag_count": 23, "theme": "Star Wars", "image_url": "https://cdn.rebrickable.com/media/sets/75192-1/30881.jpg"},
    {"set_number": "10294", "name": "Titanic",                         "piece_count": 9090, "total_bag_count": 28, "theme": "Icons",     "image_url": "https://cdn.rebrickable.com/media/sets/10294-1/93446.jpg"},
]

def make_sessions(start_date, end_date, lego_set_db_obj, bags_per_session=1):
    """Generate realistic sessions across a date range for a given set."""
    sessions = []
    d = start_date
    bag_cursor = 1
    max_bags = lego_set_db_obj.total_bag_count or 1

    while d <= end_date and bag_cursor <= max_bags:
        # ~4 sessions per week: skip ~3 out of every 7 days randomly
        if random.random() < 0.43:
            d += timedelta(days=1)
            continue

        duration = round(random.uniform(35, 75), 1)
        speed    = round(random.uniform(2.6, 3.4), 1)
        distance = round((duration / 60) * speed, 2)

        # MET ~3.5 for brisk walk, assume ~75 kg
        calories = round(3.5 * 75 * (duration / 60), 0)

        bags_this_session = min(random.randint(1, bags_per_session + 1), max_bags - bag_cursor + 1)
        bag_start = bag_cursor
        bag_end   = bag_cursor + bags_this_session - 1
        bag_detail = f"{bag_start}-{bag_end}" if bags_this_session > 1 else str(bag_start)
        bag_cursor += bags_this_session

        sessions.append(Session(
            date=d,
            duration_minutes=duration,
            distance_miles=distance,
            avg_speed_mph=speed,
            calories_burned=calories,
            lego_set_id=lego_set_db_obj.id,
            bags_completed=bags_this_session,
            bag_details=bag_detail,
            created_at=datetime.combine(d, datetime.min.time()),
        ))

        d += timedelta(days=1)

    return sessions, bag_cursor > max_bags  # (sessions, completed)


def seed():
    with app.app_context():
        # Remove existing demo user and all their data
        existing = User.query.filter_by(email=DEMO_EMAIL).first()
        if existing:
            uid = existing.id
            Session.query.filter_by(user_id=uid).delete()
            WeightLog.query.filter_by(user_id=uid).delete()
            for ls in LegoSet.query.filter_by(user_id=uid).all():
                db.session.delete(ls)
            Profile.query.filter_by(user_id=uid).delete()
            db.session.delete(existing)
            db.session.commit()
            print(f"  [--] Removed existing demo user (id={uid})")

        # Create user
        user = User(email=DEMO_EMAIL, display_name=DEMO_NAME, is_active=True, is_admin=False)
        user.set_password(DEMO_PASSWORD)
        db.session.add(user)
        db.session.flush()
        print(f"  [OK] Created user: {DEMO_EMAIL} / {DEMO_PASSWORD}  (id={user.id})")

        # Profile
        profile = Profile(
            name=DEMO_NAME,
            height_inches=70.0,
            age=34,
            current_weight_lbs=195.0,
            user_id=user.id,
        )
        db.session.add(profile)

        # Weight log — weekly entries for ~3 months, slow downward trend
        today = date.today()
        start = today - timedelta(days=90)
        weight = 202.0
        d = start
        while d <= today:
            db.session.add(WeightLog(
                date=d,
                weight_lbs=round(weight, 1),
                user_id=user.id,
                created_at=datetime.combine(d, datetime.min.time()),
            ))
            weight -= random.uniform(0.0, 0.4)
            d += timedelta(weeks=1)
        profile.current_weight_lbs = round(weight, 1)

        # Create sets and distribute sessions across the 90-day window
        # Divide the 90 days into segments, one per set (shorter sets get less time)
        total_bags = sum(s["total_bag_count"] for s in LEGO_SETS)
        cursor_date = start
        all_sessions = []

        for i, set_data in enumerate(LEGO_SETS):
            ls = LegoSet(
                set_number=set_data["set_number"],
                name=set_data["name"],
                piece_count=set_data["piece_count"],
                total_bag_count=set_data["total_bag_count"],
                theme=set_data["theme"],
                image_url=set_data.get("image_url"),
                completed=False,
                user_id=user.id,
                created_at=datetime.combine(cursor_date, datetime.min.time()),
            )
            db.session.add(ls)
            db.session.flush()

            # Allocate days proportional to bag count
            days_for_set = max(7, int((set_data["total_bag_count"] / total_bags) * 90))
            set_end = min(cursor_date + timedelta(days=days_for_set), today)

            sessions, completed = make_sessions(cursor_date, set_end, ls, bags_per_session=2)
            all_sessions.extend(sessions)

            if completed:
                ls.completed = True
                ls.completion_date = set_end
                print(f"  [OK] Set '{ls.name}' — {len(sessions)} sessions, COMPLETED on {set_end}")
            else:
                print(f"  [OK] Set '{ls.name}' — {len(sessions)} sessions, in progress")

            cursor_date = set_end + timedelta(days=1)
            if cursor_date > today:
                # Remaining sets get created but with no sessions yet
                for remaining in LEGO_SETS[i+1:]:
                    ls2 = LegoSet(
                        set_number=remaining["set_number"],
                        name=remaining["name"],
                        piece_count=remaining["piece_count"],
                        total_bag_count=remaining["total_bag_count"],
                        theme=remaining["theme"],
                        image_url=remaining.get("image_url"),
                        completed=False,
                        user_id=user.id,
                        created_at=datetime.combine(today, datetime.min.time()),
                    )
                    db.session.add(ls2)
                    print(f"  [OK] Set '{ls2.name}' — added (not started)")
                break

        for s in all_sessions:
            s.user_id = user.id
            db.session.add(s)

        db.session.commit()
        print(f"\n  Done. {len(all_sessions)} sessions seeded across {len(LEGO_SETS)} sets.")
        print(f"  Login: {DEMO_EMAIL} / {DEMO_PASSWORD}")


if __name__ == "__main__":
    print("Seeding demo data...")
    seed()
