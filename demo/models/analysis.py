from datetime import datetime

from demo.database import db


class Analysis(db.Model):
    __tablename__ = "analyses"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    filename   = db.Column(db.String(255), nullable=False)
    company    = db.Column(db.String(255))
    stage      = db.Column(db.String(80))
    sector     = db.Column(db.String(120))
    result     = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="analyses")

    def __repr__(self):
        return f"<Analysis {self.filename} user={self.user_id}>"
