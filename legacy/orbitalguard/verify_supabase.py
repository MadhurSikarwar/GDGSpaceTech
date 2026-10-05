import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()
db_url = os.getenv("DATABASE_URL")

if not db_url:
    print("Error: DATABASE_URL not found in .env")
    exit(1)

print(f"Connecting to Supabase at: {db_url.split('@')[-1]}")
engine = create_engine(db_url)

try:
    with engine.connect() as conn:
        from sqlalchemy import text
        result = conn.execute(text("SELECT COUNT(*) FROM orbital_objects"))
        count = result.scalar()
        print(f"\nSUCCESS: Supabase Database contains {count} orbital objects!")
        
        # Breakdown by type
        print("\nBreakdown by Object Type:")
        types = conn.execute(text("SELECT object_type, COUNT(*) FROM orbital_objects GROUP BY object_type"))
        for obj_type, t_count in types:
            print(f"  - {obj_type}: {t_count}")
except Exception as e:
    print(f"Database error: {e}")
