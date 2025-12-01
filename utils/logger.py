from sqlalchemy import text
from datetime import datetime

def log_entry(db, module, level, message, slice_id=None):
    """
    Registra un evento en la tabla logs.
    """
    try:
        db.execute(
            text("""
                INSERT INTO logs (module, timestamp, level, message, slice_id)
                VALUES (:m, :ts, :lvl, :msg, :sid)
            """),
            {
                "m": module,
                "ts": datetime.utcnow(),
                "lvl": level,
                "msg": message,
                "sid": slice_id
            },
        )
        db.commit()
    except Exception as e:
        print(f"Error escribiendo log en BD: {e}")
