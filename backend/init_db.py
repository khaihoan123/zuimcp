from database import engine, Base
import models 

print("models", dir(models))
Base.metadata.create_all(bind=engine)
print("Database tables created successfully.")