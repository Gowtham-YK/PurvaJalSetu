import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:

    SECRET_KEY = "supersecretkey"

    SQLALCHEMY_DATABASE_URI = (
        "sqlite:///"
        + os.path.join(BASE_DIR, "database", "app.db")
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.path.join(
        BASE_DIR,
        "static",
        "uploads"
    )