from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

# -------------------
# USER TABLE
# -------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True)
    password = db.Column(db.String(150))
    role = db.Column(db.String(50))  # "stp", "demand", "tanker", or "admin"


# -------------------
# SUPPLY TABLE
# -------------------
class Supply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    operator_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    available_volume = db.Column(db.Float)
    quality_classification = db.Column(db.String(100))
    report_filename = db.Column(db.String(200))
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)


# -------------------
# STP TABLE
# -------------------
class STP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stp_name = db.Column(db.String(150))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    total_capacity = db.Column(db.Float)
    current_load = db.Column(db.Float, default=0)
    available_capacity = db.Column(db.Float)
    quality_classification = db.Column(db.String(100))
    water_type = db.Column(db.String(100))
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)


# -------------------
# DEMAND REQUEST TABLE
# -------------------
class DemandRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(150))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    requested_volume = db.Column(db.Float)
    status = db.Column(db.String(50), default="Pending")

    buyer_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    stp_id = db.Column(db.Integer, db.ForeignKey('supply.id'))
    water_type = db.Column(db.String(100))
    quality_required = db.Column(db.String(100))
    accepted_at = db.Column(db.DateTime)
    capacity_release_at = db.Column(db.DateTime)
    capacity_released = db.Column(db.Boolean, default=False)
    payment_status = db.Column(db.String(50), default="Pending")
    tanker_id = db.Column(db.Integer, db.ForeignKey('user.id'))