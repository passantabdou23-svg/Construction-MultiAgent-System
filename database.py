#Step 1: Create the Database Manager
import sqlite3 #1.Database Connection
#What it does: Establishes a connection to a local file named construction_mas.db. If the file doesn't exist, 
# SQLite creates it automatically.
##Why we need it: Instead of losing data when the Python script stops running, this file acts as a permanent 
## hard drive for our multi-agent system.
from typing import Dict, Any, List 
#We created three dedicated tables linked by primary and foreign keys

DB_NAME = "construction_mas.db"

def init_db(): #2.Defining Relational Schemas (init_db)
               #We created three dedicated tables linked by primary and foreign keys
    """Initializes the database schema for project state, procurement, and schedule logs."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. Project Revisions Table (Design Agent)
    ##design_revisions: Stores structural updates from the Design Agent (revision_id, affected_element,
    ## specification, quantity, etc.).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS design_revisions (
            revision_id TEXT PRIMARY KEY,
            affected_element TEXT,
            item_id TEXT,
            material_type TEXT,
            specification TEXT,
            quantity REAL,
            unit TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. Procurement Records Table (Procurement Agent)
    ##procurement_records: Stores pricing and lead time from the Procurement Agent (supplier_name, 
    ## total_cost, lead_time_days, earliest_delivery_date).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS procurement_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            revision_id TEXT,
            item_id TEXT,
            supplier_name TEXT,
            unit_cost REAL,
            total_cost REAL,
            lead_time_days INTEGER,
            earliest_delivery_date TEXT,
            FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
        )
    ''')

    # 3. Schedule Impact Log Table (Scheduler Agent)
    ##schedule_logs: Stores Critical Path Method (CPM) impacts from the Scheduler Agent 
    ##(is_critical_path, delay_days, recommended_action).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            revision_id TEXT,
            task_id TEXT,
            is_critical_path BOOLEAN,
            delay_days INTEGER,
            recommended_action TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
        )
    ''')

    ###Relational Integrity (FOREIGN KEY): Both procurement and schedule logs reference revision_id. This creates a complete 
    ###lineage linking why a delay happened back to the exact design change that triggered it.

    ###3. Data Persistence Functions (save_*) & (conn.commit() / conn.close())

    conn.commit()
    conn.close()
    ###Commit & Close (conn.commit() / conn.close()): commit() writes the changes permanently to disk, and close() releases
    ###the database memory lock so other processes can access it safely.
    print("✅ Database initialized successfully (construction_mas.db)")

###save_design_revision(), save_procurement_record(), and save_schedule_log() accept Python dictionaries (derived from our
###Pydantic schemas) and run SQL INSERT or REPLACE queries.

def save_design_revision(data: dict):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    req = data["requirements"][0]
    cursor.execute('''
        INSERT OR REPLACE INTO design_revisions 
        (revision_id, affected_element, item_id, material_type, specification, quantity, unit)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (data["revision_id"], data["affected_element"], req["item_id"], 
          req["material_type"], req["specification"], req["quantity"], req["unit"]))
    conn.commit()
    conn.close()

def save_procurement_record(revision_id: str, data: dict):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO procurement_records 
        (revision_id, item_id, supplier_name, unit_cost, total_cost, lead_time_days, earliest_delivery_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (revision_id, data["item_id"], data["supplier_name"], data["unit_cost"], 
          data["total_cost"], data["actual_lead_days"], data["earliest_delivery_date"]))
    conn.commit()
    conn.close()

def save_schedule_log(revision_id: str, data: dict):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO schedule_logs 
        (revision_id, task_id, is_critical_path, delay_days, recommended_action)
        VALUES (?, ?, ?, ?, ?)
    ''', (revision_id, data["task_id"], data["is_critical_path"], 
          data["delay_days"], data["recommended_action"]))
    conn.commit()
    conn.close()