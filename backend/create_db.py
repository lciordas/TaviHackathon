from app import models  # noqa: F401 — import side-effect registers tables
from app.database import Base, engine


def main():
    Base.metadata.create_all(bind=engine)
    print(f"Database initialized: {engine.url}")


if __name__ == "__main__":
    main()
