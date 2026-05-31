from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_migrate import Migrate

from demo.database import db

bcrypt = Bcrypt()
login_manager = LoginManager()
migrate = Migrate()


def init_extensions(app):
    from demo.models.user import User  # avoid circular import

    bcrypt.init_app(app)
    migrate.init_app(app, db)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
