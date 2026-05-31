from datetime import datetime

from flask_login import UserMixin

from demo.database import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id         = db.Column(db.Integer, primary_key=True)
    email      = db.Column(db.String(254), unique=True, nullable=False, index=True)
    name       = db.Column(db.String(120), nullable=False)
    password   = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active  = db.Column(db.Boolean, default=True)

    analyses  = db.relationship("Analysis", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")
    pipelines = db.relationship("Pipeline", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email}>"
