import os
from pathlib import Path

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def init_db(app):
    raw_url = os.environ.get("DATABASE_URL", "")
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql://", 1)

    if not raw_url:
        local_path = Path(__file__).parent / "washon.db"
        raw_url = f"sqlite:///{local_path}"

    app.config["SQLALCHEMY_DATABASE_URI"] = raw_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        # Import all models so SQLAlchemy knows their tables before create_all
        from demo.models import user, analysis, pipeline  # noqa: F401
        db.create_all()
