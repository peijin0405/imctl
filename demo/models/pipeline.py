from datetime import datetime

from demo.database import db


class Pipeline(db.Model):
    __tablename__ = "pipelines"

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    investor_id = db.Column(db.String(120))
    firm_name   = db.Column(db.String(255))
    stage       = db.Column(db.String(80))
    note        = db.Column(db.Text)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="pipelines")

    def __repr__(self):
        return f"<Pipeline {self.firm_name} user={self.user_id}>"
